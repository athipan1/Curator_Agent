from __future__ import annotations

import ast
from typing import Iterable

from app.models import SkillValidationResult


FORBIDDEN_NODE_TYPES = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
)

FORBIDDEN_CALL_NAMES = {
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "__import__",
    "breakpoint",
}

FORBIDDEN_ATTRIBUTE_CALLS = {
    "os.system",
    "os.popen",
    "subprocess.call",
    "subprocess.run",
    "subprocess.Popen",
    "shutil.rmtree",
    "pathlib.Path.unlink",
    "pathlib.Path.write_text",
    "pathlib.Path.write_bytes",
}

FORBIDDEN_NAME_PREFIXES = ("__",)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _contains_trading_side_effect_name(names: Iterable[str]) -> bool:
    suspicious_terms = {
        "place_order",
        "submit_order",
        "create_order",
        "cancel_order",
        "send_order",
        "broker_order",
        "alpaca",
    }
    lowered = {name.lower() for name in names}
    return bool(lowered & suspicious_terms)


class SafeSkillValidator:
    """Static validator for MVP skill registration.

    The Curator MVP stores candidate Python skills but does not execute them.
    This validator rejects obvious unsafe constructs early so unsafe code does
    not enter the skill registry unnoticed.
    """

    def validate(self, code: str) -> SkillValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return SkillValidationResult(
                approved=False,
                errors=[f"syntax_error: {exc.msg} at line {exc.lineno}"],
            )

        function_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, FORBIDDEN_NODE_TYPES):
                errors.append(f"forbidden_ast_node: {type(node).__name__}")

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_names.append(node.name)
                if node.name.startswith(FORBIDDEN_NAME_PREFIXES):
                    errors.append(f"forbidden_dunder_function: {node.name}")

            if isinstance(node, ast.Call):
                call_name = _call_name(node.func)
                if call_name in FORBIDDEN_CALL_NAMES:
                    errors.append(f"forbidden_call: {call_name}")
                if call_name in FORBIDDEN_ATTRIBUTE_CALLS:
                    errors.append(f"forbidden_attribute_call: {call_name}")

            if isinstance(node, ast.Name) and node.id.startswith(FORBIDDEN_NAME_PREFIXES):
                errors.append(f"forbidden_dunder_name: {node.id}")

        if not function_names:
            errors.append("skill_must_define_at_least_one_function")

        if _contains_trading_side_effect_name(function_names):
            warnings.append(
                "skill_function_name_mentions_order_or_broker; keep skills signal-only and broker-free"
            )

        return SkillValidationResult(approved=not errors, errors=sorted(set(errors)), warnings=warnings)
