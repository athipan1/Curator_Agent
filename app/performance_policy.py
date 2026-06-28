from __future__ import annotations

from typing import List

from app.models import (
    CuratedPolicyAction,
    PerformanceLearningResult,
    PerformancePolicyCurationRequest,
    PerformancePolicyCurationResponse,
)

MIN_ACTION_DELTA = 0.000001
HIGH_PRIORITY_RISK_REDUCTION = -0.002


def _human_review_required(result: PerformanceLearningResult) -> bool:
    guardrails = result.policy_deltas.guardrails or {}
    return bool(guardrails.get("requires_human_review", True))


def _learning_allows_auto_apply(result: PerformanceLearningResult) -> bool:
    guardrails = result.policy_deltas.guardrails or {}
    return bool(guardrails.get("auto_apply", False))


def _auto_apply_allowed(request: PerformancePolicyCurationRequest) -> bool:
    return (
        _learning_allows_auto_apply(request.learning_result)
        and not _human_review_required(request.learning_result)
        and request.learning_result.confidence_score >= request.min_confidence_to_apply
    )


def _priority(delta: float) -> str:
    magnitude = abs(delta)
    if magnitude >= 0.10:
        return "high"
    if magnitude >= 0.03:
        return "medium"
    return "low"


def _curate_strategy_bucket_actions(request: PerformancePolicyCurationRequest) -> List[CuratedPolicyAction]:
    auto_apply = _auto_apply_allowed(request)
    actions: List[CuratedPolicyAction] = []
    for bucket, delta in request.learning_result.policy_deltas.strategy_bucket_weights.items():
        if abs(delta) <= MIN_ACTION_DELTA:
            continue
        if delta <= request.pause_threshold:
            actions.append(
                CuratedPolicyAction(
                    target_type="strategy_bucket",
                    target=bucket,
                    action="pause_strategy_bucket",
                    delta=delta,
                    auto_apply=False,
                    priority="high",
                    reason=f"Learning_Agent recommended a large negative bucket delta ({delta}); pause requires review.",
                )
            )
            continue
        action = "increase_weight" if delta > 0 else "decrease_weight"
        actions.append(
            CuratedPolicyAction(
                target_type="strategy_bucket",
                target=bucket,
                action=action,
                delta=delta,
                auto_apply=auto_apply,
                priority=_priority(delta),
                reason=f"Learning_Agent recommended {action} for strategy bucket '{bucket}' with delta {delta}.",
            )
        )
    return actions


def _curate_symbol_actions(request: PerformancePolicyCurationRequest) -> List[CuratedPolicyAction]:
    auto_apply = _auto_apply_allowed(request)
    actions: List[CuratedPolicyAction] = []
    for symbol, delta in request.learning_result.policy_deltas.asset_biases.items():
        if abs(delta) <= MIN_ACTION_DELTA:
            continue
        action = "increase_bias" if delta > 0 else "decrease_bias"
        actions.append(
            CuratedPolicyAction(
                target_type="symbol",
                target=symbol,
                action=action,
                delta=delta,
                auto_apply=auto_apply,
                priority=_priority(delta),
                reason=f"Learning_Agent recommended {action} for symbol '{symbol}' with delta {delta}.",
            )
        )
    return actions


def _curate_risk_actions(request: PerformancePolicyCurationRequest) -> List[CuratedPolicyAction]:
    actions: List[CuratedPolicyAction] = []
    for risk_key, delta in request.learning_result.policy_deltas.risk.items():
        if abs(delta) <= MIN_ACTION_DELTA:
            continue
        if delta < 0:
            actions.append(
                CuratedPolicyAction(
                    target_type="risk",
                    target=risk_key,
                    action="reduce_risk",
                    delta=delta,
                    auto_apply=False,
                    priority="high" if delta <= HIGH_PRIORITY_RISK_REDUCTION else "medium",
                    reason=f"Learning_Agent recommended reducing risk parameter '{risk_key}' by {delta}.",
                )
            )
        else:
            actions.append(
                CuratedPolicyAction(
                    target_type="risk",
                    target=risk_key,
                    action="keep_observing",
                    delta=delta,
                    auto_apply=False,
                    priority="low",
                    reason=f"Positive risk delta for '{risk_key}' is not auto-curated; keep observing.",
                )
            )
    return actions


def curate_performance_policy(request: PerformancePolicyCurationRequest) -> PerformancePolicyCurationResponse:
    result = request.learning_result
    reasoning: List[str] = []
    actions: List[CuratedPolicyAction] = []
    rejected_actions: List[CuratedPolicyAction] = []

    if result.learning_state in {"warmup", "insufficient_data"}:
        reasoning.append(f"Learning state is {result.learning_state}; policy changes are observation-only.")
        return PerformancePolicyCurationResponse(
            curation_state="observation_only",
            action_count=0,
            actions=[],
            rejected_actions=[],
            reasoning=reasoning,
            metadata={
                "learning_state": result.learning_state,
                "confidence_score": result.confidence_score,
                "reviewed_closed_plans": result.reviewed_closed_plans,
            },
        )

    actions.extend(_curate_strategy_bucket_actions(request))
    actions.extend(_curate_symbol_actions(request))
    actions.extend(_curate_risk_actions(request))

    if _human_review_required(result):
        actions.append(
            CuratedPolicyAction(
                target_type="guardrail",
                target="human_review",
                action="require_human_review",
                auto_apply=False,
                priority="high",
                reason="Learning_Agent guardrails require human review before applying policy changes.",
            )
        )
        reasoning.append("Human review is required by Learning_Agent guardrails.")

    if result.confidence_score < request.min_confidence_to_apply:
        for action in actions:
            action.auto_apply = False
        reasoning.append(
            f"Confidence {result.confidence_score:.2f} is below apply threshold {request.min_confidence_to_apply:.2f}; auto_apply disabled."
        )

    if not actions:
        actions.append(
            CuratedPolicyAction(
                target_type="guardrail",
                target="policy",
                action="keep_observing",
                auto_apply=False,
                priority="low",
                reason="No meaningful policy delta was provided by Learning_Agent.",
            )
        )
        reasoning.append("No actionable deltas found; keep observing.")

    curation_state = "review_required" if any(action.action == "require_human_review" for action in actions) else "approved_for_review"

    return PerformancePolicyCurationResponse(
        curation_state=curation_state,
        action_count=len(actions),
        actions=actions,
        rejected_actions=rejected_actions,
        reasoning=reasoning + list(result.reasoning or []),
        metadata={
            "learning_state": result.learning_state,
            "learning_mode": result.learning_mode,
            "confidence_score": result.confidence_score,
            "performance_score": result.performance_score,
            "reviewed_closed_plans": result.reviewed_closed_plans,
            "auto_apply_allowed": _auto_apply_allowed(request),
        },
    )
