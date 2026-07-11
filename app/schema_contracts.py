from __future__ import annotations

from typing import Any, Dict, List


_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def validate_schema_contract(
    value: Any,
    schema: Dict[str, Any] | None,
    *,
    path: str = "$",
) -> List[str]:
    """Validate the supported, deterministic subset of JSON Schema.

    Empty schemas remain backward-compatible and accept any value. Unsupported
    keywords are ignored deliberately so Curator does not claim broader JSON
    Schema compliance than it provides.
    """
    if not isinstance(schema, dict) or not schema:
        return []

    errors: List[str] = []
    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        checker = _TYPE_CHECKS.get(expected_type)
        if checker is not None and not checker(value):
            return [f"{path}: expected {expected_type}, got {type(value).__name__}"]

    if "enum" in schema and isinstance(schema["enum"], list):
        if value not in schema["enum"]:
            errors.append(f"{path}: value is not in allowed enum")

    if isinstance(value, dict):
        required = schema.get("required") or []
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key}: required property is missing")

        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    errors.extend(
                        validate_schema_contract(
                            value[key],
                            child_schema,
                            path=f"{path}.{key}",
                        )
                    )

        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            unexpected = sorted(set(value) - set(properties))
            for key in unexpected:
                errors.append(f"{path}.{key}: additional property is not allowed")

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path}: expected at least {min_items} items")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path}: expected at most {max_items} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    validate_schema_contract(
                        item,
                        item_schema,
                        path=f"{path}[{index}]",
                    )
                )

    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: string is shorter than {min_length}")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: string is longer than {max_length}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path}: value must be >= {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path}: value must be <= {maximum}")
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            errors.append(f"{path}: value must be > {exclusive_minimum}")
        if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
            errors.append(f"{path}: value must be < {exclusive_maximum}")

    return errors
