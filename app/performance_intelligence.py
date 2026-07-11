from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


_SAMPLE_KEYS = (
    "sample_size",
    "total_executions",
    "execution_count",
    "observations",
    "trade_count",
    "closed_trades",
)
_RELIABILITY_KEYS = (
    "precision",
    "accuracy",
    "hit_rate",
    "win_rate",
    "success_rate",
)
_SCORE_KEYS = ("skill_score", "performance_score", "score")
_PROFIT_FACTOR_KEYS = ("profit_factor", "latest_profit_factor")


def _first_number(payload: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _normalize_rate(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if value > 1.0 and value <= 100.0:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def extract_skill_performance(
    rank_response: Dict[str, Any],
    skill_id: str,
) -> Dict[str, Any]:
    data = rank_response.get("data") if isinstance(rank_response, dict) else None
    if not isinstance(data, list):
        return {}
    for item in data:
        if isinstance(item, dict) and str(item.get("skill_id")) == str(skill_id):
            return item
    return {}


def calibrate_confidence(
    raw_confidence: Any,
    performance: Dict[str, Any],
    *,
    prior_strength: int = 30,
) -> Dict[str, Any]:
    try:
        raw = float(raw_confidence)
    except (TypeError, ValueError):
        return {
            "raw_confidence": raw_confidence,
            "calibrated_confidence": None,
            "calibration_status": "raw_confidence_not_numeric",
            "sample_size": 0,
            "empirical_reliability": None,
            "evidence_weight": 0.0,
        }

    raw = max(0.0, min(1.0, raw))
    sample_size_value = _first_number(performance, _SAMPLE_KEYS)
    sample_size = max(0, int(sample_size_value or 0))
    reliability = _normalize_rate(_first_number(performance, _RELIABILITY_KEYS))

    if reliability is None or sample_size <= 0:
        return {
            "raw_confidence": raw,
            "calibrated_confidence": raw,
            "calibration_status": "insufficient_performance_history",
            "sample_size": sample_size,
            "empirical_reliability": reliability,
            "evidence_weight": 0.0,
        }

    evidence_weight = sample_size / float(sample_size + max(1, prior_strength))
    calibrated = (raw * (1.0 - evidence_weight)) + (reliability * evidence_weight)
    return {
        "raw_confidence": round(raw, 6),
        "calibrated_confidence": round(max(0.0, min(1.0, calibrated)), 6),
        "calibration_status": "calibrated",
        "sample_size": sample_size,
        "empirical_reliability": round(reliability, 6),
        "evidence_weight": round(evidence_weight, 6),
    }


def assess_performance_decay(
    performance: Dict[str, Any],
    *,
    minimum_sample_size: int = 30,
) -> Dict[str, Any]:
    sample_size_value = _first_number(performance, _SAMPLE_KEYS)
    sample_size = max(0, int(sample_size_value or 0))
    reliability = _normalize_rate(_first_number(performance, _RELIABILITY_KEYS))
    skill_score = _normalize_rate(_first_number(performance, _SCORE_KEYS))
    profit_factor = _first_number(performance, _PROFIT_FACTOR_KEYS)

    reasons: list[str] = []
    if sample_size < minimum_sample_size:
        return {
            "health": "insufficient_data",
            "recommended_stage": "keep_current",
            "sample_size": sample_size,
            "minimum_sample_size": minimum_sample_size,
            "reliability": reliability,
            "skill_score": skill_score,
            "profit_factor": profit_factor,
            "reasons": ["minimum_observation_threshold_not_met"],
            "auto_transition_allowed": False,
        }

    severe = False
    degraded = False
    if reliability is not None:
        if reliability < 0.35:
            severe = True
            reasons.append("reliability_below_0.35")
        elif reliability < 0.48:
            degraded = True
            reasons.append("reliability_below_0.48")
    if skill_score is not None:
        if skill_score < 0.35:
            severe = True
            reasons.append("skill_score_below_0.35")
        elif skill_score < 0.50:
            degraded = True
            reasons.append("skill_score_below_0.50")
    if profit_factor is not None:
        if profit_factor < 0.80:
            severe = True
            reasons.append("profit_factor_below_0.80")
        elif profit_factor < 1.00:
            degraded = True
            reasons.append("profit_factor_below_1.00")

    if severe:
        health = "critical"
        recommended_stage = "quarantined"
    elif degraded:
        health = "degraded"
        recommended_stage = "degraded"
    else:
        health = "healthy"
        recommended_stage = "keep_current"
        reasons.append("performance_within_guardrails")

    return {
        "health": health,
        "recommended_stage": recommended_stage,
        "sample_size": sample_size,
        "minimum_sample_size": minimum_sample_size,
        "reliability": reliability,
        "skill_score": skill_score,
        "profit_factor": profit_factor,
        "reasons": reasons,
        "auto_transition_allowed": False,
    }
