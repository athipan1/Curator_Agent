from __future__ import annotations

from typing import Any, Dict

from app.executor import SafeSkillExecutor
from app.registry import SkillRegistry
from app.schema_contracts import validate_schema_contract


class SchemaEnforcingExecutor:
    """Decorates the existing sandbox executor with registry-backed contracts."""

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        delegate: SafeSkillExecutor | None = None,
    ) -> None:
        self.registry = registry
        self.delegate = delegate or SafeSkillExecutor()

    def execute(
        self,
        *,
        skill_id: str,
        code: str,
        inputs: Dict[str, Any],
        function_name: str | None = None,
        timeout_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        record = self.registry.get(skill_id)
        input_errors = validate_schema_contract(
            inputs,
            record.input_schema,
            path="$input",
        )
        if input_errors:
            return {
                "skill_id": skill_id,
                "execution_status": "input_schema_rejected",
                "error": "skill_input_schema_validation_failed",
                "schema_contract": {
                    "input_valid": False,
                    "output_valid": None,
                    "errors": input_errors,
                    "input_schema_enforced": bool(record.input_schema),
                    "output_schema_enforced": bool(record.output_schema),
                },
                "output": {},
                "safety": {
                    "broker_access": False,
                    "network_access": False,
                    "file_access": False,
                    "order_placement": False,
                },
            }

        result = self.delegate.execute(
            skill_id=skill_id,
            code=code,
            inputs=inputs,
            function_name=function_name,
            timeout_seconds=timeout_seconds,
        )
        output = result.get("output") if isinstance(result, dict) else None
        if result.get("execution_status") != "success":
            result["schema_contract"] = {
                "input_valid": True,
                "output_valid": None,
                "errors": [],
                "input_schema_enforced": bool(record.input_schema),
                "output_schema_enforced": bool(record.output_schema),
            }
            return result

        output_errors = validate_schema_contract(
            output,
            record.output_schema,
            path="$output",
        )
        if output_errors:
            return {
                **result,
                "execution_status": "output_schema_rejected",
                "error": "skill_output_schema_validation_failed",
                "output": {},
                "rejected_output": output,
                "schema_contract": {
                    "input_valid": True,
                    "output_valid": False,
                    "errors": output_errors,
                    "input_schema_enforced": bool(record.input_schema),
                    "output_schema_enforced": bool(record.output_schema),
                },
            }

        result["schema_contract"] = {
            "input_valid": True,
            "output_valid": True,
            "errors": [],
            "input_schema_enforced": bool(record.input_schema),
            "output_schema_enforced": bool(record.output_schema),
        }
        return result
