from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict


SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}
FORBIDDEN_OUTPUT_KEYS = {
    "order",
    "orders",
    "broker",
    "submit_order",
    "place_order",
    "cancel_order",
    "account_id",
    "api_key",
    "secret",
}


def _choose_function(namespace: Dict[str, Any], requested: str | None):
    if requested:
        function = namespace.get(requested)
        if not callable(function):
            raise ValueError("requested_function_not_found")
        return function
    functions = [value for key, value in namespace.items() if callable(value) and not key.startswith("_")]
    if len(functions) != 1:
        raise ValueError("skill_must_expose_exactly_one_public_function")
    return functions[0]


def _validate_output(output: Any) -> Dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("skill_output_must_be_object")
    lowered = {str(key).lower() for key in output}
    forbidden = sorted(lowered.intersection(FORBIDDEN_OUTPUT_KEYS))
    if forbidden:
        raise ValueError(f"forbidden_output_keys:{','.join(forbidden)}")
    confidence = output.get("confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence_must_be_numeric")
        if not 0 <= float(confidence) <= 1:
            raise ValueError("confidence_must_be_between_0_and_1")
    json.dumps(output, allow_nan=False)
    return output


def main() -> None:
    payload = json.loads(Path("/input.json").read_text(encoding="utf-8"))
    skill_id = str(payload.get("skill_id") or "unknown")
    code = str(payload.get("code") or "")
    inputs = payload.get("inputs") or {}
    function_name = payload.get("function_name")
    try:
        ast.parse(code)
        namespace: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
        exec(compile(code, "<curator-skill>", "exec"), namespace, namespace)
        function = _choose_function(namespace, function_name)
        output = _validate_output(function(**inputs))
        result = {
            "skill_id": skill_id,
            "execution_status": "success",
            "output": output,
            "safety": {
                "broker_access": False,
                "network_access": False,
                "file_access": False,
                "order_placement": False,
            },
        }
    except Exception as exc:
        result = {
            "skill_id": skill_id,
            "execution_status": "failed",
            "error": str(exc),
            "output": {},
            "safety": {
                "broker_access": False,
                "network_access": False,
                "file_access": False,
                "order_placement": False,
            },
        }
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
