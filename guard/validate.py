"""Validation helpers for request bodies described by a small schema subset.

The validator is secure by default:

* every property absent from the schema's ``properties`` mapping is rejected;
* every entry in ``required`` must be present and non-null;
* numeric ``minimum`` / ``maximum`` and string ``min_length`` /
  ``max_length`` boundaries are inclusive;
* ``enum`` membership is checked;
* nested objects and array ``items`` are validated recursively;
* every reported issue carries the JSON-style path of the offending value.

``validate`` is a pure function of its arguments: it never mutates the body or
schema, keeps no state between calls, and validating the same request twice
yields identical issues.  A body for which ``validate`` reports any issue must
be stopped before it reaches the database or any other downstream flow.
"""

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


def _is_integral(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
        return _is_integral(value)
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


def _field_path(base: str, name: Any) -> str:
    base = base or "$"
    if base in ("", "$"):
        return f"$.{name}"
    return f"{base}.{name}"


def _index_path(base: str, index: int) -> str:
    return f"{base or '$'}[{index}]"


def _spec_value(spec: dict, snake: str, camel: str) -> Any:
    """Read a constraint accepting both ``snake_case`` and ``camelCase``."""
    if snake in spec:
        return spec[snake]
    if camel in spec:
        return spec[camel]
    return None


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


def check_required(body: dict, schema: dict, path: str = "$") -> list[str]:
    """Report required properties that are absent or null."""
    if not isinstance(body, dict) or not isinstance(schema, dict):
        return []

    issues: list[str] = []
    for name in schema.get("required", []) or []:
        if name not in body or body[name] is None:
            issues.append(
                f"{_field_path(path, name)}: required field is missing"
            )
    return issues


def check_additional(body: dict, schema: dict, path: str = "$") -> list[str]:
    """Report properties that the schema does not declare.

    The contract is secure-by-default: an absent ``additionalProperties`` /
    ``additional_properties`` key does not make undeclared fields acceptable.
    Every key not listed under ``properties`` is rejected.
    """
    if not isinstance(body, dict) or not isinstance(schema, dict):
        return []

    declared = schema.get("properties", {})
    if not isinstance(declared, dict):
        declared = {}

    issues: list[str] = []
    for name in body:
        if name not in declared:
            issues.append(
                f"{_field_path(path, str(name))}: "
                "additional field is not allowed"
            )
    return issues


def check_types(body: dict, schema: dict, path: str = "$") -> list[str]:
    """Report declared properties whose type or enum membership is invalid."""
    if not isinstance(body, dict) or not isinstance(schema, dict):
        return []

    issues: list[str] = []
    properties = schema.get("properties", {}) or {}
    for name, spec in properties.items():
        if name not in body or not isinstance(spec, dict):
            continue

        value = body[name]
        field_path = _field_path(path, str(name))

        expected = spec.get("type")
        if expected and not _matches_type(value, expected):
            issues.append(
                f"{field_path}: expected type {expected} "
                f"but got {_type_name(value)}"
            )

        allowed = spec.get("enum")
        if isinstance(allowed, (list, tuple)) and value not in allowed:
            issues.append(
                f"{field_path}: value {value!r} is not in enum "
                f"allowed values {list(allowed)!r}"
            )

    return issues


def check_bounds(value: Any, spec: dict, path: str = "$") -> list[str]:
    """Report numeric boundary and string-length violations from a spec.

    ``minimum`` / ``maximum`` and ``min_length`` / ``max_length`` are
    inclusive: a value exactly on a boundary is valid.
    """
    if not isinstance(spec, dict):
        return []

    issues: list[str] = []

    if _is_number(value):
        minimum = spec.get("minimum")
        if _is_number(minimum) and value < minimum:
            issues.append(
                f"{path}: value {value!r} is less than the "
                f"inclusive minimum {minimum}"
            )

        maximum = spec.get("maximum")
        if _is_number(maximum) and value > maximum:
            issues.append(
                f"{path}: value {value!r} is greater than the "
                f"inclusive maximum {maximum}"
            )

    if isinstance(value, str):
        length = len(value)

        min_length = _spec_value(spec, "min_length", "minLength")
        if _is_integral(min_length) and length < min_length:
            issues.append(
                f"{path}: string length {length} is less than "
                f"min_length {min_length}"
            )

        max_length = _spec_value(spec, "max_length", "maxLength")
        if _is_integral(max_length) and length > max_length:
            issues.append(
                f"{path}: string length {length} is greater than "
                f"max_length {max_length}"
            )

    return issues


def _validate_node(value: Any, spec: Any, path: str, issues: list[str]) -> None:
    """Recursively validate one value against one schema node."""
    if not isinstance(spec, dict):
        # A malformed schema is reported by validate_schema; never invent
        # constraints from a non-mapping schema.
        return

    expected = spec.get("type")
    if expected and not _matches_type(value, expected):
        issues.append(
            f"{path}: expected type {expected} but got {_type_name(value)}"
        )

    allowed = spec.get("enum")
    if isinstance(allowed, (list, tuple)) and value not in allowed:
        issues.append(
            f"{path}: value {value!r} is not in enum "
            f"allowed values {list(allowed)!r}"
        )

    issues.extend(check_bounds(value, spec, path))

    if isinstance(value, dict):
        # Required keys, undeclared keys, then one recursive pass per
        # declared property that is present.
        issues.extend(check_required(value, spec, path))
        issues.extend(check_additional(value, spec, path))

        properties = spec.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                if not isinstance(child, dict) or name not in value:
                    continue
                _validate_node(
                    value[name],
                    child,
                    _field_path(path, str(name)),
                    issues,
                )

    if isinstance(value, list):
        items = spec.get("items")
        if isinstance(items, dict):
            for index, element in enumerate(value):
                _validate_node(
                    element,
                    items,
                    _index_path(path, index),
                    issues,
                )


def validate(body: Any, schema: dict, path: str = "$") -> list[str]:
    """Validate a body and return all problems found in the request.

    The returned list is empty exactly when the body satisfies the schema.
    Validation never mutates ``body`` or ``schema`` and is deterministic for
    the same inputs, so calling it twice yields equal results.
    """
    if not isinstance(schema, dict):
        return []

    issues: list[str] = []
    _validate_node(body, schema, path or "$", issues)
    return issues


def is_valid(body: Any, schema: dict, path: str = "$") -> bool:
    """Gate helper: ``True`` only when the body has no validation issues.

    Handlers must stop the request (and never pass it downstream) whenever
    this returns ``False``.
    """
    return not validate(body, schema, path)


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
    """Validate independent requests against one schema.

    Every request is validated from scratch against the same immutable
    schema.  No result is cached or shared, so outcomes never leak between
    entries and reordering requests only reorders the results.
    """
    if not isinstance(requests, (list, tuple)):
        raise ValueError("requests must be a sequence")

    results: list[dict] = []
    for index, request in enumerate(requests):
        issues = validate(request, schema, "$")
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
