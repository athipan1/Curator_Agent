from __future__ import annotations

import ast
import multiprocessing as mp
import queue
import time
from types import MappingProxyType
from typing import Any, Dict


SAFE_BUILTINS = MappingProxyType(
    {
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
        "pow": pow,
        "range": range,
        "round": round,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }
)

FORBIDDEN_OUTPUT_KEYS = {
    "broker_order_id",
    "client_order_id",
    "order_id",
    "risk_approval_id",
    "secret_key",
    "api_key",
    "private_key",
}

FORBIDDEN_INTROSPECTION_ATTRIBUTES = {
    "__bases__",
    "__class__",
    "__closure__",
    "__code__",
    "__func__",
    "__globals__",
    "__mro__",
    "__self__",
    "__subclasses__",
}


class SkillExecutionError(Exception):
    """Raised when a curated skill cannot be executed by the best-effort runner."""


def _validate_best_effort_code(code: str) -> None:
    """Reject common Python introspection escapes before process-level exec().

    This is defense in depth only. Python-level AST and builtins restrictions are
    not a security boundary and must not replace container isolation.
    """

    try:
        tree = ast.parse(code, filename="<curator_skill>", mode="exec")
    except SyntaxError as exc:
        raise SkillExecutionError(f"skill_syntax_error: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (
            node.attr in FORBIDDEN_INTROSPECTION_ATTRIBUTES
            or (node.attr.startswith("__") and node.attr.endswith("__"))
        ):
            raise SkillExecutionError(f"forbidden_introspection_attribute: {node.attr}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_INTROSPECTION_ATTRIBUTES:
            raise SkillExecutionError(f"forbidden_introspection_name: {node.id}")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    raise SkillExecutionError(f"non_json_serializable_output: {type(value).__name__}")


def _select_callable(namespace: Dict[str, Any], function_name: str | None) -> tuple[str, Any]:
    if function_name:
        candidate = namespace.get(function_name)
        if not callable(candidate):
            raise SkillExecutionError(f"skill_function_not_found: {function_name}")
        return function_name, candidate

    callables = [
        (name, value)
        for name, value in namespace.items()
        if callable(value) and not name.startswith("_")
    ]
    if not callables:
        raise SkillExecutionError("skill_has_no_callable_function")
    callables.sort(key=lambda item: item[0])
    return callables[0]


def _validate_signal_output(output: Any) -> Dict[str, Any]:
    safe_output = _json_safe(output)
    if not isinstance(safe_output, dict):
        raise SkillExecutionError("skill_output_must_be_json_object")

    forbidden = sorted(FORBIDDEN_OUTPUT_KEYS & {str(key).lower() for key in safe_output.keys()})
    if forbidden:
        raise SkillExecutionError(f"forbidden_output_keys: {', '.join(forbidden)}")

    if "confidence" in safe_output:
        try:
            confidence = float(safe_output["confidence"])
        except (TypeError, ValueError) as exc:
            raise SkillExecutionError("confidence_must_be_numeric") from exc
        if confidence < 0 or confidence > 1:
            raise SkillExecutionError("confidence_must_be_between_0_and_1")
        safe_output["confidence"] = confidence

    return safe_output


def _execute_in_child(
    *,
    code: str,
    inputs: Dict[str, Any],
    function_name: str | None,
    result_queue: mp.Queue,
) -> None:
    try:
        namespace: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
        exec(compile(code, "<curator_skill>", "exec"), namespace, namespace)
        resolved_function_name, fn = _select_callable(namespace, function_name)
        raw_output = fn(**inputs)
        output = _validate_signal_output(raw_output)
        result_queue.put(
            {
                "status": "success",
                "function_name": resolved_function_name,
                "output": output,
            }
        )
    except Exception as exc:  # pragma: no cover - exercised through parent process responses
        result_queue.put(
            {
                "status": "failed",
                "error": str(exc),
            }
        )


class SafeSkillExecutor:
    """Best-effort isolation for approved skills; this is not a true sandbox.

    The runner uses a short-lived process, restricted builtins, an AST check for
    common introspection escapes, and a timeout. These controls reduce accidental
    misuse but cannot make Python ``exec()`` safe against hostile code. Production
    execution must use ``ContainerSandboxExecutor``; this runner exists only for an
    explicit operator opt-out or emergency compatibility fallback.
    """

    def execute(
        self,
        *,
        skill_id: str,
        code: str,
        inputs: Dict[str, Any],
        function_name: str | None = None,
        timeout_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        try:
            _validate_best_effort_code(code)
        except SkillExecutionError as exc:
            return {
                "skill_id": skill_id,
                "execution_status": "rejected_unsafe_code",
                "error": str(exc),
                "output": {},
                "isolation": "best_effort_not_a_true_sandbox",
            }

        timeout = max(0.1, min(float(timeout_seconds or 1.0), 5.0))
        result_queue: mp.Queue = mp.Queue(maxsize=1)
        started = time.perf_counter()
        process = mp.Process(
            target=_execute_in_child,
            kwargs={
                "code": code,
                "inputs": inputs,
                "function_name": function_name,
                "result_queue": result_queue,
            },
            daemon=True,
        )
        process.start()
        process.join(timeout)

        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if process.is_alive():
            process.terminate()
            process.join(0.2)
            return {
                "skill_id": skill_id,
                "execution_status": "timeout",
                "error": f"skill_execution_timeout_after_{timeout}_seconds",
                "elapsed_ms": elapsed_ms,
            }

        try:
            result = result_queue.get_nowait()
        except queue.Empty:
            return {
                "skill_id": skill_id,
                "execution_status": "failed",
                "error": "skill_process_exited_without_result",
                "elapsed_ms": elapsed_ms,
            }

        if result.get("status") != "success":
            return {
                "skill_id": skill_id,
                "execution_status": "failed",
                "error": result.get("error") or "unknown_skill_execution_error",
                "elapsed_ms": elapsed_ms,
            }

        return {
            "skill_id": skill_id,
            "execution_status": "success",
            "function_name": result.get("function_name"),
            "output": result.get("output") or {},
            "elapsed_ms": elapsed_ms,
            "safety": {
                "broker_access": False,
                "network_access": False,
                "file_access": False,
                "order_placement": False,
            },
            "isolation": "best_effort_not_a_true_sandbox",
        }
