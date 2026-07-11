from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.models import StandardResponse
from app.registry import APPROVAL_APPROVED, SkillRegistry


ELIGIBLE_STAGES = {"champion", "challenger", "shadow"}
STAGE_WEIGHTS = {"champion": 1.0, "challenger": 0.8, "shadow": 0.6}
VALID_SIGNALS = {"buy", "hold", "sell"}


class ShadowEnsembleRequest(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    skill_family_ids: List[str] = Field(default_factory=list, max_length=20)
    skill_ids: List[str] = Field(default_factory=list, max_length=20)
    max_skills: int = Field(default=8, ge=1, le=20)
    function_name: Optional[str] = Field(default=None, max_length=120)
    timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    minimum_agreement: float = Field(default=0.60, ge=0.0, le=1.0)


def _diversity_group(skill: Any) -> str:
    context = skill.market_context or {}
    for key in ("correlation_group", "feature_family", "strategy_family"):
        value = context.get(key)
        if value:
            return str(value)
    return skill.skill_family_id


def _select_skills(registry: SkillRegistry, request: ShadowEnsembleRequest) -> List[Any]:
    candidates = [
        skill
        for skill in registry.list(
            validation_status="validated",
            approval_status=APPROVAL_APPROVED,
        )
        if skill.deployment_stage in ELIGIBLE_STAGES
    ]
    if request.skill_family_ids:
        allowed_families = set(request.skill_family_ids)
        candidates = [skill for skill in candidates if skill.skill_family_id in allowed_families]
    if request.skill_ids:
        allowed_ids = set(request.skill_ids)
        candidates = [skill for skill in candidates if skill.skill_id in allowed_ids]

    stage_order = {"champion": 0, "challenger": 1, "shadow": 2}
    candidates.sort(
        key=lambda skill: (
            stage_order.get(skill.deployment_stage, 9),
            skill.skill_family_id,
            skill.version,
            skill.skill_id,
        )
    )

    selected: List[Any] = []
    seen_groups: set[str] = set()
    deferred: List[Any] = []
    for skill in candidates:
        group = _diversity_group(skill)
        if group in seen_groups:
            deferred.append(skill)
            continue
        selected.append(skill)
        seen_groups.add(group)
        if len(selected) >= request.max_skills:
            return selected
    for skill in deferred:
        selected.append(skill)
        if len(selected) >= request.max_skills:
            break
    return selected


def _consensus(executions: List[Dict[str, Any]], minimum_agreement: float) -> Dict[str, Any]:
    votes: Dict[str, float] = defaultdict(float)
    total_weight = 0.0
    successful = 0
    for item in executions:
        if item.get("execution_status") != "success":
            continue
        output = item.get("output") if isinstance(item.get("output"), dict) else {}
        signal = str(output.get("signal", "")).lower()
        if signal not in VALID_SIGNALS:
            continue
        confidence = output.get("calibrated_confidence", output.get("confidence", 0.5))
        try:
            confidence_value = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence_value = 0.5
        weight = STAGE_WEIGHTS.get(str(item.get("deployment_stage")), 0.5) * confidence_value
        votes[signal] += weight
        total_weight += weight
        successful += 1

    if not votes or total_weight <= 0:
        return {
            "signal": "hold",
            "agreement": 0.0,
            "state": "no_valid_votes",
            "vote_weights": dict(votes),
            "successful_votes": successful,
        }

    signal, winning_weight = max(votes.items(), key=lambda item: (item[1], item[0]))
    agreement = winning_weight / total_weight
    accepted = agreement >= minimum_agreement
    return {
        "signal": signal if accepted else "hold",
        "leading_signal": signal,
        "agreement": round(agreement, 6),
        "state": "consensus" if accepted else "insufficient_agreement",
        "minimum_agreement": minimum_agreement,
        "vote_weights": {key: round(value, 6) for key, value in sorted(votes.items())},
        "successful_votes": successful,
    }


def build_shadow_ensemble_router(registry: SkillRegistry, executor: Any) -> APIRouter:
    router = APIRouter()

    @router.post("/skills/shadow-ensemble", response_model=StandardResponse)
    async def execute_shadow_ensemble(request: ShadowEnsembleRequest) -> StandardResponse:
        skills = _select_skills(registry, request)
        if not skills:
            raise HTTPException(status_code=404, detail="no_eligible_shadow_ensemble_skills")

        executions: List[Dict[str, Any]] = []
        diversity_groups: set[str] = set()
        for skill in skills:
            result = executor.execute(
                skill_id=skill.skill_id,
                code=skill.code,
                inputs=request.inputs,
                function_name=request.function_name,
                timeout_seconds=request.timeout_seconds,
            )
            diversity_group = _diversity_group(skill)
            diversity_groups.add(diversity_group)
            executions.append(
                {
                    "skill_id": skill.skill_id,
                    "skill_family_id": skill.skill_family_id,
                    "version": skill.version,
                    "deployment_stage": skill.deployment_stage,
                    "diversity_group": diversity_group,
                    **result,
                }
            )

        consensus = _consensus(executions, request.minimum_agreement)
        return StandardResponse(
            data={
                "execution_mode": "shadow_ensemble",
                "advisory_only": True,
                "broker_access": False,
                "order_placement": False,
                "selected_skill_count": len(skills),
                "diversity_group_count": len(diversity_groups),
                "diversity_score": round(len(diversity_groups) / len(skills), 6),
                "consensus": consensus,
                "executions": executions,
                "manager_contract": {
                    "trusted_signal": consensus["signal"],
                    "requires_risk_gate": True,
                    "direct_execution_allowed": False,
                },
            }
        )

    return router


def attach_shadow_ensemble_routes(app: FastAPI, registry: SkillRegistry, executor: Any) -> FastAPI:
    app.include_router(build_shadow_ensemble_router(registry, executor))
    app.state.shadow_ensemble_enabled = True
    return app
