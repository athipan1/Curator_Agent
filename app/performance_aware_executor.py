from __future__ import annotations

from typing import Any, Dict

from app.database_client import DatabaseAgentClient
from app.performance_intelligence import (
    assess_performance_decay,
    calibrate_confidence,
    extract_skill_performance,
)


class PerformanceAwareExecutor:
    """Attach advisory performance intelligence without mutating validated output.

    ``SchemaEnforcingExecutor`` validates the skill-defined output before this
    decorator runs. Any mutation here could invalidate constraints such as
    ``additionalProperties``, ``const``, ``minimum`` or ``maximum`` while leaving
    ``schema_contract.output_valid`` incorrectly set to true. Calibration therefore
    remains advisory metadata and the validated skill output is preserved exactly.
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

        # Preserve the schema-validated output byte-for-byte at the value level.
        # Consumers that opt into calibration can read effective_confidence from
        # performance_intelligence without changing the skill's declared contract.
        result["output"] = output
        result["performance_intelligence"] = {
            "status": "applied" if performance else "no_history",
            "database_status": (
                rank_response.get("status")
                if isinstance(rank_response, dict)
                else "unknown"
            ),
            "calibration": calibration,
            "effective_confidence": calibration.get("calibrated_confidence"),
            "confidence_applied_to_output": False,
            "output_schema_preserved": True,
            "decay_assessment": decay,
            "advisory_only": True,
            "auto_stage_transition": False,
        }
        return result
