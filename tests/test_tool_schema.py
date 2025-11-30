from __future__ import annotations

from openrouter_responses_pipe.openrouter_responses_pipe import _strictify_schema


def test_strictify_preserves_required_fields():
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["path"],
    }

    strict = _strictify_schema(schema)

    assert strict["required"] == sorted(["path", "timeout"])
    assert strict["properties"]["path"]["type"] == "string"
    assert strict["properties"]["timeout"]["type"] == ["integer", "null"]


def test_strictify_keeps_optional_fields_optional():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        # No required list → everything optional
    }

    strict = _strictify_schema(schema)

    assert strict["required"] == sorted(["query", "limit"])
    assert strict["properties"]["query"]["type"] == ["string", "null"]
    assert strict["properties"]["limit"]["type"] == ["integer", "null"]


def test_strictify_handles_nested_objects():
    schema = {
        "type": "object",
        "properties": {
            "filters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["status"],
            },
            "limit": {"type": "integer"},
        },
        "required": ["filters"],
    }

    strict = _strictify_schema(schema)

    assert strict["required"] == sorted(["filters", "limit"])
    assert strict["properties"]["limit"]["type"] == ["integer", "null"]

    nested = strict["properties"]["filters"]
    assert nested["required"] == ["status", "tags"]
    assert nested["properties"]["status"]["type"] == "string"
    assert nested["properties"]["tags"]["type"] == ["array", "null"]
