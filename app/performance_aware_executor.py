from __future__ import annotations

from typing import Any, Dict

from app.database_client import DatabaseAgentClient
from app.performance_intelligence import (
    assess_performance_decay,
    calibrate_confidence,
    extract_skill_performance,
)


class PerformanceAwareExecutor:
    """Attach performance intelligence without violating the skill output schema.

    SchemaEnforcingExecutor validates the skill output before this decorator runs.
    Therefore this layer may update an existing schema-approved ``confidence``
    value, but it must never append calibration-only fields to ``output`` after
    validation. Raw and calibrated values belong in ``performance_intelligence``.
    """

    def __init__(self, *, delegate: Any, database_client: DatabaseAgentClient) -> None:
        self.delegate = delegate
        self.database_client = database_client

    def execute(
        self,
        *,
        skill_id: str,
        code: str,
        inputs: Dict[str, Any],
        function_name: str | None = None,
        timeout_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        result = self.delegate.execute(
            skill_id=skill_id,
            code=code,
            inputs=inputs,
            function_name=function_name,
            timeout_seconds=timeout_seconds,
        )
        if result.get("execution_status") != "success":
            result["performance_intelligence"] = {
                "status": "not_applied",
                "reason": "execution_not_successful",
            }
            return result

        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        rank_response = self.database_client.rank_skills(limit=100)
        performance = extract_skill_performance(rank_response, skill_id)
        calibration = calibrate_confidence(output.get("confidence"), performance)
        decay = assess_performance_decay(performance)

        calibrated_confidence = calibration.get("calibrated_confidence")
        confidence_applied = calibrated_confidence is not None and "confidence" in output
        if confidence_applied:
            # Updating an existing numeric field preserves the registered schema.
            # Calibration metadata must remain outside the skill-defined output.
            output["confidence"] = calibrated_confidence

        result["output"] = output
        result["performance_intelligence"] = {
            "status": "applied" if performance else "no_history",
            "database_status": (
                rank_response.get("status")
                if isinstance(rank_response, dict)
                else "unknown"
            ),
            "calibration": calibration,
            "confidence_applied_to_output": confidence_applied,
            "output_schema_preserved": True,
            "decay_assessment": decay,
            "advisory_only": True,
            "auto_stage_transition": False,
        }
        return result
