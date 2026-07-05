from __future__ import annotations

from typing import Any, Dict, List

from app.database_client import DatabaseAgentClient
from app.models import RecommendedSkill, SkillRecommendationRequest, SkillRecommendationResponse, SkillRecord
from app.registry import APPROVAL_APPROVED, SkillRegistry


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
    result: Dict[str, Dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and item.get("skill_id"):
            result[str(item["skill_id"])] = item
    return result


def _fallback_score(skill: SkillRecord, request: SkillRecommendationRequest) -> float:
    score = 0.50
    if request.tags and {tag.lower() for tag in request.tags}.intersection({tag.lower() for tag in skill.tags}):
        score += 0.10
    context = skill.market_context or {}
    if request.strategy_bucket and context.get("strategy_bucket") == request.strategy_bucket:
        score += 0.10
    if request.market_regime and context.get("regime") == request.market_regime:
        score += 0.10
    return round(min(score, 0.75), 4)


def recommend_skills(
    *,
    registry: SkillRegistry,
    database_client: DatabaseAgentClient,
    request: SkillRecommendationRequest,
) -> SkillRecommendationResponse:
    approved_skills = [
        skill
        for skill in registry.list(validation_status="validated", approval_status=APPROVAL_APPROVED)
        if _context_matches(skill, request)
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
    for skill in approved_skills:
        performance = performance_lookup.get(skill.skill_id, {})
        score = float(performance.get("skill_score") or _fallback_score(skill, request))
        if performance:
            reason = "Ranked by Database_Agent skill performance history."
        elif database_client.enabled:
            reason = "Approved skill with no matching performance history yet; using context fallback score."
        else:
            reason = "Database_Agent not configured; using approved-skill context fallback score."
        recommendations.append(
            RecommendedSkill(
                skill_id=skill.skill_id,
                name=skill.name,
                score=max(0.0, min(1.0, score)),
                approval_status=skill.approval_status,
                validation_status=skill.validation_status,
                reason=reason,
                performance=performance,
                tags=skill.tags,
                market_context=skill.market_context,
            )
        )

    recommendations.sort(key=lambda item: item.score, reverse=True)
    selected = recommendations[: request.top_k]
    return SkillRecommendationResponse(
        recommendation_state="ranked" if database_rank_available else "fallback_no_database",
        recommended_skills=selected,
        rejected_count=max(0, len(recommendations) - len(selected)),
        metadata={
            "database_client_enabled": database_client.enabled,
            "database_rank_status": rank_response.get("status") if isinstance(rank_response, dict) else None,
            "advisory_only": True,
            "risk_gate_required": True,
        },
    )
