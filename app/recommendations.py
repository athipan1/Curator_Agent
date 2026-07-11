from __future__ import annotations

from typing import Any, Dict, List

from app.database_client import DatabaseAgentClient
from app.models import RecommendedSkill, SkillRecommendationRequest, SkillRecommendationResponse, SkillRecord
from app.registry import APPROVAL_APPROVED, DEPLOYMENT_CHAMPION, DEPLOYMENT_RETIRED, SkillRegistry


def _context_matches(skill: SkillRecord, request: SkillRecommendationRequest) -> bool:
    if request.tags:
        skill_tags = {tag.lower() for tag in skill.tags}
        requested_tags = {tag.lower() for tag in request.tags}
        if requested_tags and not requested_tags.intersection(skill_tags):
            return False
    context = skill.market_context or {}
    if request.asset_class and context.get("asset_class") and context.get("asset_class") != request.asset_class:
        return False
    if request.strategy_bucket and context.get("strategy_bucket") and context.get("strategy_bucket") != request.strategy_bucket:
        return False
    if request.market_regime and context.get("regime") and context.get("regime") != request.market_regime:
        return False
    return True


def _performance_by_skill(rank_response: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    data = rank_response.get("data") if isinstance(rank_response, dict) else None
    if not isinstance(data, list):
        return {}
    return {
        str(item["skill_id"]): item
        for item in data
        if isinstance(item, dict) and item.get("skill_id")
    }


def _backtest_data(status_response: Dict[str, Any]) -> Dict[str, Any]:
    data = status_response.get("data") if isinstance(status_response, dict) else None
    return data if isinstance(data, dict) else {}


def _backtest_passed(status_response: Dict[str, Any]) -> bool:
    return bool(_backtest_data(status_response).get("passed"))


def _fallback_score(skill: SkillRecord, request: SkillRecommendationRequest) -> float:
    score = 0.50
    if request.tags and {tag.lower() for tag in request.tags}.intersection({tag.lower() for tag in skill.tags}):
        score += 0.10
    context = skill.market_context or {}
    if request.strategy_bucket and context.get("strategy_bucket") == request.strategy_bucket:
        score += 0.10
    if request.market_regime and context.get("regime") == request.market_regime:
        score += 0.10
    if skill.deployment_stage == DEPLOYMENT_CHAMPION:
        score += 0.05
    return round(min(score, 0.80), 4)


def recommend_skills(
    *,
    registry: SkillRegistry,
    database_client: DatabaseAgentClient,
    request: SkillRecommendationRequest,
) -> SkillRecommendationResponse:
    approved_skills = [
        skill
        for skill in registry.list(validation_status="validated", approval_status=APPROVAL_APPROVED)
        if skill.deployment_stage != DEPLOYMENT_RETIRED and _context_matches(skill, request)
    ]
    if not approved_skills:
        return SkillRecommendationResponse(
            recommendation_state="no_approved_skills",
            recommended_skills=[],
            rejected_count=0,
            metadata={"database_client_enabled": database_client.enabled},
        )

    rank_response = database_client.rank_skills(
        account_id=request.account_id,
        symbol=request.symbol,
        strategy_bucket=request.strategy_bucket,
        market_regime=request.market_regime,
        limit=max(request.top_k, 20),
    )
    performance_lookup = _performance_by_skill(rank_response)
    database_rank_available = bool(performance_lookup)

    recommendations: List[RecommendedSkill] = []
    rejected_for_backtest = 0
    backtest_status_by_skill: Dict[str, Dict[str, Any]] = {}
    for skill in approved_skills:
        backtest_response = database_client.get_skill_backtest_status(skill.skill_id)
        backtest_status = _backtest_data(backtest_response)
        backtest_status_by_skill[skill.skill_id] = backtest_status
        if request.require_backtest_passed and not _backtest_passed(backtest_response):
            rejected_for_backtest += 1
            continue

        performance = performance_lookup.get(skill.skill_id, {})
        score = float(performance.get("skill_score") or _fallback_score(skill, request))
        if backtest_status.get("passed"):
            score = min(1.0, score + 0.05)
        if skill.deployment_stage == DEPLOYMENT_CHAMPION:
            score = min(1.0, score + 0.03)
        if performance:
            reason = "Ranked by Database_Agent skill performance history."
        elif database_client.enabled:
            reason = "Approved skill with no matching performance history yet; using context fallback score."
        else:
            reason = "Database_Agent not configured; using approved-skill context fallback score."
        if backtest_status.get("passed"):
            reason = f"{reason} Backtest passed."
        if skill.deployment_stage == DEPLOYMENT_CHAMPION:
            reason = f"{reason} Current champion for its skill family."

        recommendations.append(
            RecommendedSkill(
                skill_id=skill.skill_id,
                skill_family_id=skill.skill_family_id,
                version=skill.version,
                deployment_stage=skill.deployment_stage,
                name=skill.name,
                score=max(0.0, min(1.0, score)),
                approval_status=skill.approval_status,
                validation_status=skill.validation_status,
                reason=reason,
                performance=performance,
                backtest_status=backtest_status,
                tags=skill.tags,
                market_context=skill.market_context,
            )
        )

    if request.require_backtest_passed and not recommendations:
        return SkillRecommendationResponse(
            recommendation_state="no_backtest_passed_skills",
            recommended_skills=[],
            rejected_count=rejected_for_backtest,
            metadata={
                "database_client_enabled": database_client.enabled,
                "database_rank_status": rank_response.get("status") if isinstance(rank_response, dict) else None,
                "require_backtest_passed": True,
                "backtest_status_by_skill": backtest_status_by_skill,
                "advisory_only": True,
                "risk_gate_required": True,
            },
        )

    recommendations.sort(
        key=lambda item: (
            item.deployment_stage == DEPLOYMENT_CHAMPION,
            item.score,
            item.version,
        ),
        reverse=True,
    )
    selected = recommendations[: request.top_k]
    return SkillRecommendationResponse(
        recommendation_state="ranked" if database_rank_available else "fallback_no_database",
        recommended_skills=selected,
        rejected_count=max(0, len(recommendations) - len(selected)) + rejected_for_backtest,
        metadata={
            "database_client_enabled": database_client.enabled,
            "database_rank_status": rank_response.get("status") if isinstance(rank_response, dict) else None,
            "require_backtest_passed": request.require_backtest_passed,
            "rejected_for_backtest": rejected_for_backtest,
            "advisory_only": True,
            "risk_gate_required": True,
            "version_aware": True,
            "retired_skills_excluded": True,
        },
    )
