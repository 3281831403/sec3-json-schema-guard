from guard import request_batch, validate

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer", "minimum": 0, "maximum": 120},
        "role": {"type": "string", "enum": ["admin", "user"]},
        "tags": {
            "type": "array",
            "items": {"type": "string", "min_length": 2},
        },
        "profile": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "min_length": 2},
                "zip": {"type": "string"},
            },
            "required": ["city"],
        },
    },
    "required": ["name", "age"],
}


def test_required_fields_are_enforced():
    issues = validate({"age": 30}, SCHEMA, "$")
    assert issues


def test_types_are_enforced():
    issues = validate({"name": "Ada", "age": "thirty"}, SCHEMA, "$")
    assert any("$.age" in issue and "integer" in issue for issue in issues)


def test_numeric_bounds_include_endpoints():
    at_minimum = validate({"name": "Ada", "age": 0}, SCHEMA, "$")
    at_maximum = validate({"name": "Ada", "age": 120}, SCHEMA, "$")
    assert at_minimum == []
    assert at_maximum == []


def test_nested_objects_are_validated():
    issues = validate(
        {"name": "Ada", "age": 30, "profile": {"city": ""}},
        SCHEMA,
        "$",
    )
    assert any("$.profile.city" in issue for issue in issues)


def test_batch_requests_are_independent():
    first = {"name": "Ada", "age": 200}
    second = {"name": "Grace", "age": 30}
    result = request_batch([first, second], SCHEMA)
    assert result["results"][0]["valid"] is False
    assert result["results"][1]["valid"] is True
    assert result["results"][1]["issues"] == []