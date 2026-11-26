"""Validation helpers for request bodies described by a small schema subset."""

from __future__ import annotations

from typing import Any

_KNOWN_TYPES = {
    "object",
    "array",
    "string",
    "integer",
    "number",
    "boolean",
    "null",
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return _is_number(value)
    return False


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _field_path(base: str, name: str) -> str:
    if base in ("", "$"):
        return f"$.{name}"
    return f"{base}.{name}"


def _index_path(base: str, index: int) -> str:
    return f"{base}[{index}]"


def parse_schema(schema: dict) -> dict:
    """Return a normalized copy of a schema mapping."""
    if not isinstance(schema, dict):
        raise ValueError("schema must be a mapping")

    raw_properties = schema.get("properties", {})
    raw_required = schema.get("required", [])
    if not isinstance(raw_properties, dict):
        raise ValueError("schema properties must be a mapping")
    if not isinstance(raw_required, (list, tuple)):
        raise ValueError("schema required must be a sequence")

    parsed = dict(schema)
    parsed["properties"] = {}
    for name, spec in raw_properties.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise ValueError("each property needs a name and a schema mapping")
        parsed["properties"][name] = dict(spec)
    parsed["required"] = list(raw_required)
    return parsed


def check_required(body: dict, schema: dict) -> list[str]:
    """Report required properties that are absent or null."""
    if not isinstance(body, dict) or not isinstance(schema, dict):
        return []

    issues: list[str] = []
    for name in schema.get("required", []) or []:
        if name in body and body[name] is None:
            issues.append(f"{name}: required field is missing")
    return issues


def check_additional(body: dict, schema: dict) -> list[str]:
    """Report properties that the schema does not declare."""
    if not isinstance(body, dict) or not isinstance(schema, dict):
        return []

    if (
        "additional_properties" not in schema
        and "additionalProperties" not in schema
    ):
        return []

    declared = schema.get("properties", {}) or {}
    issues: list[str] = []
    for name in body:
        if name not in declared:
            issues.append(f"{name}: additional field is not allowed")
    return issues


def check_types(body: dict, schema: dict) -> list[str]:
    """Report declared properties whose values do not match their type."""
    if not isinstance(body, dict) or not isinstance(schema, dict):
        return []

    issues: list[str] = []
    properties = schema.get("properties", {}) or {}
    for name, spec in properties.items():
        if name not in body or not isinstance(spec, dict):
            continue

        expected = spec.get("type")
        if not expected:
            continue

        value = body[name]
        if not _matches_type(value, expected):
            issues.append(
                f"{name}: expected type {expected} but got {_type_name(value)}"
            )
    return issues


def check_bounds(value: Any, spec: dict) -> list[str]:
    """Report numeric and length boundary violations described by a spec."""
    if not isinstance(spec, dict):
        return []

    issues: list[str] = []
    if _is_number(value):
        maximum = spec.get("maximum")
        if _is_number(maximum) and value >= maximum:
            issues.append(
                f"value is greater than the inclusive maximum {maximum}"
            )
    return issues


def validate(body: dict, schema: dict, path: str = "$") -> list[str]:
    """Validate a body and return all problems found in the request."""
    if not isinstance(body, dict) or not isinstance(schema, dict):
        return []

    issues: list[str] = []
    issues.extend(check_required(body, schema))
    issues.extend(check_additional(body, schema))
    issues.extend(check_types(body, schema))

    properties = schema.get("properties", {}) or {}
    for name, spec in properties.items():
        if name not in body or not isinstance(spec, dict):
            continue
        issues.extend(check_bounds(body[name], spec))

    return issues


def validate_schema(schema: dict) -> list[str]:
    """Report structural problems in a schema itself."""
    issues: list[str] = []
    _collect_schema_issues(schema, "$", issues)
    return issues


def _collect_schema_issues(spec: Any, path: str, issues: list[str]) -> None:
    if not isinstance(spec, dict):
        issues.append(f"{path}: schema must be a mapping")
        return

    expected = spec.get("type")
    if not expected:
        issues.append(f"{path}: schema type is missing")
    elif expected not in _KNOWN_TYPES:
        issues.append(f"{path}: unknown schema type {expected}")

    minimum = spec.get("minimum")
    maximum = spec.get("maximum")
    if _is_number(minimum) and _is_number(maximum) and minimum > maximum:
        issues.append(f"{path}: minimum is greater than maximum")

    min_length = spec.get("min_length")
    max_length = spec.get("max_length")
    if (
        isinstance(min_length, int)
        and not isinstance(min_length, bool)
        and isinstance(max_length, int)
        and not isinstance(max_length, bool)
        and min_length > max_length
    ):
        issues.append(f"{path}: min_length is greater than max_length")

    properties = spec.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            issues.append(f"{path}: properties must be a mapping")
        else:
            for name, child in properties.items():
                _collect_schema_issues(child, _field_path(path, str(name)), issues)

    items = spec.get("items")
    if items is not None:
        _collect_schema_issues(items, f"{path}[]", issues)


def request_batch(requests: list[dict], schema: dict) -> dict:
    """Validate independent requests against one schema."""
    if not isinstance(requests, (list, tuple)):
        raise ValueError("requests must be a sequence")

    results: list[dict] = []
    cached: list[str] | None = None
    for index, request in enumerate(requests):
        if cached is None:
            cached = validate(request, schema, "$")
        issues = list(cached)
        results.append(
            {
                "index": index,
                "valid": not issues,
                "issues": issues,
            }
        )

    return {
        "results": results,
        "valid": all(item["valid"] for item in results),
    }