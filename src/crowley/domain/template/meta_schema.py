"""The template meta-schema for ``crowley: 1`` (JSON Schema 2020-12).

Published by ``crowley schema``.

It checks structure only. Exactly-one-step-kind, cross-field rules and everything that needs
the function registry are checked by the compiler and the static validator, which produce
clearer messages than ``oneOf`` errors would.
"""

from typing import Any

from crowley.domain.common.identifiers import IDENTIFIER, SECRET_NAME, TEMPLATE_ID
from crowley.domain.steps import STEP_KINDS

META_SCHEMA_ID = (
    "https://github.com/mohamed-naser-awd/crowley/blob/main/schema/crowley-1.schema.json"
)

_SEMVER = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_DURATION = r"^\s*\d+(\.\d+)?\s*(ms|s|m|h)\s*$"
_BYTESIZE = r"(?i)^\s*\d+(\.\d+)?\s*(b|kb|mb|gb)\s*$"
_RATE = r"^\s*\d+(\.\d+)?\s*/\s*(s|m|h)\s*$"
_ERROR_CODE = r"^E\d{3}$"
_EXTENSION: dict[str, Any] = {"^x-": {}}


def _string(**extra: Any) -> dict[str, Any]:
    return {"type": "string", **extra}


def _strings() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def _ident() -> dict[str, Any]:
    return _string(pattern=IDENTIFIER.pattern)


def _positive_int() -> dict[str, Any]:
    return {"type": "integer", "minimum": 1}


_STEP_FIELDS: dict[str, Any] = {
    # common fields (SPEC §7.1)
    "id": _ident(),
    "name": _string(),
    "description": _string(),
    "when": {"type": ["string", "boolean"]},
    "on_error": {"$ref": "#/$defs/on_error"},
    "timeout": {"$ref": "#/$defs/duration"},
    # kind keys (SPEC §7.2)
    "use": _string(minLength=1),
    "if": {"type": ["string", "boolean"]},
    "for_each": {"type": ["string", "array"]},
    "while": {"type": ["string", "boolean"]},
    "set": {"type": "object", "propertyNames": {"pattern": IDENTIFIER.pattern}},
    "emit": {},
    "return": {},
    "break": {"const": True},
    "continue": {"const": True},
    "fail": _string(),
    "assert": {"type": ["string", "boolean"]},
    "log": _string(),
    # kind-specific fields
    "with": {"type": "object"},
    "do": {"$ref": "#/$defs/block"},
    "secrets": {"type": "object"},
    "then": {"$ref": "#/$defs/block"},
    "else": {"$ref": "#/$defs/block"},
    "as": _ident(),
    "index_as": _ident(),
    "concurrency": {"type": ["integer", "string"], "minimum": 1},
    "max_iterations": _positive_int(),
    "code": _string(),
    "message": _string(),
    "level": {"enum": ["debug", "info", "warning", "error"]},
}

_OUTCOME: dict[str, Any] = {
    "anyOf": [
        {"enum": ["fail", "skip"]},
        {
            "type": "object",
            "required": ["default"],
            "properties": {"default": {}},
            "additionalProperties": False,
        },
    ]
}

META_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": META_SCHEMA_ID,
    "title": "Crowley template (spec version 1)",
    "description": "A Crowley scraping template: one site or API, split into named operations.",
    "type": "object",
    "required": ["crowley", "id", "version", "name", "description", "permissions", "operations"],
    "properties": {
        "crowley": {"type": "integer", "description": "Spec version. Must be 1."},
        "id": _string(pattern=TEMPLATE_ID.pattern, description="owner/name"),
        "version": _string(pattern=_SEMVER, description="Semantic version, e.g. 1.2.0"),
        "name": _string(minLength=1),
        "description": _string(minLength=1),
        "tags": _strings(),
        "authors": _strings(),
        "license": _string(),
        "homepage": _string(format="uri"),
        "requires": _strings(),
        "secrets": {
            "type": "object",
            "propertyNames": {"pattern": SECRET_NAME.pattern},
            "additionalProperties": {
                "type": "object",
                "properties": {"description": _string(), "required": {"type": "boolean"}},
                "additionalProperties": False,
            },
        },
        "permissions": {
            "type": "object",
            "required": ["hosts"],
            "properties": {"hosts": {"type": "array", "items": _string(minLength=1)}},
            "additionalProperties": False,
        },
        "defaults": {
            "type": "object",
            "propertyNames": {"pattern": IDENTIFIER.pattern},
            "additionalProperties": {"type": "object"},
        },
        "limits": {"$ref": "#/$defs/limits"},
        "schemas": {
            "type": "object",
            "propertyNames": {"pattern": r"^[A-Za-z_][A-Za-z0-9_-]*$"},
            "additionalProperties": {"type": ["object", "boolean"]},
        },
        "functions": {
            "type": "object",
            "propertyNames": {"pattern": IDENTIFIER.pattern},
            "additionalProperties": {"$ref": "#/$defs/function"},
        },
        "operations": {
            "type": "object",
            "propertyNames": {"pattern": IDENTIFIER.pattern},
            "additionalProperties": {"$ref": "#/$defs/operation"},
        },
    },
    "patternProperties": _EXTENSION,
    "additionalProperties": False,
    "$defs": {
        "duration": {"anyOf": [{"type": "number", "minimum": 0}, _string(pattern=_DURATION)]},
        "bytesize": {"anyOf": [{"type": "integer", "minimum": 0}, _string(pattern=_BYTESIZE)]},
        "limits": {
            "type": "object",
            "properties": {
                "max_requests": _positive_int(),
                "max_duration": {"$ref": "#/$defs/duration"},
                "max_items": _positive_int(),
                "max_depth": _positive_int(),
                "max_loop_iterations": _positive_int(),
                "max_concurrency": _positive_int(),
                "max_response_bytes": {"$ref": "#/$defs/bytesize"},
                "max_retries_per_step": {"type": "integer", "minimum": 0},
                "rate": {
                    "type": "object",
                    "properties": {"per_host": _string(pattern=_RATE)},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "operation": {
            "type": "object",
            "required": ["description", "steps", "output"],
            "properties": {
                "name": _string(),
                "description": _string(minLength=1),
                "tags": _strings(),
                "inputs": {
                    "type": "object",
                    "propertyNames": {"pattern": IDENTIFIER.pattern},
                    "additionalProperties": {"type": ["object", "boolean"]},
                },
                "limits": {"$ref": "#/$defs/limits"},
                "steps": {"$ref": "#/$defs/block"},
                "output": {"$ref": "#/$defs/output"},
                "tests": {"type": "array", "items": {"$ref": "#/$defs/test"}},
            },
            "patternProperties": _EXTENSION,
            "additionalProperties": False,
        },
        "function": {
            "type": "object",
            "required": ["description", "output", "steps"],
            "properties": {
                "description": _string(minLength=1),
                "input": {"type": "object"},
                "prev": {"type": ["object", "boolean"]},
                "output": {"type": ["object", "boolean"]},
                "steps": {"$ref": "#/$defs/block"},
            },
            "patternProperties": _EXTENSION,
            "additionalProperties": False,
        },
        "block": {"type": "array", "items": {"$ref": "#/$defs/step"}},
        "step": {
            "type": "object",
            "description": f"A step has exactly one kind key: {', '.join(STEP_KINDS)}.",
            "properties": _STEP_FIELDS,
            "additionalProperties": False,
        },
        "on_error": {
            "anyOf": [
                {"enum": ["fail", "skip"]},
                {
                    "type": "object",
                    "properties": {
                        "default": {},
                        "retry": {
                            "type": "object",
                            "required": ["times"],
                            "properties": {
                                "times": {"type": "integer", "minimum": 0},
                                "backoff": {"enum": ["fixed", "exponential"]},
                                "delay": {"$ref": "#/$defs/duration"},
                                "max_delay": {"$ref": "#/$defs/duration"},
                            },
                            "additionalProperties": False,
                        },
                        "then": _OUTCOME,
                        "catch": {"type": "array", "items": _string(pattern=_ERROR_CODE)},
                    },
                    "additionalProperties": False,
                },
            ]
        },
        "output": {
            "type": "object",
            "required": ["schema"],
            "properties": {"value": {}, "schema": {"type": ["object", "boolean"]}},
            "additionalProperties": False,
        },
        "test": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": _string(minLength=1),
                "inputs": {"type": "object"},
                "secrets": {"type": "object", "additionalProperties": _string()},
                "fixtures": _string(),
                "match": {"type": "array", "items": _string()},
                "expect": {
                    "type": "object",
                    "properties": {
                        "min_items": {"type": "integer", "minimum": 0},
                        "max_items": {"type": "integer", "minimum": 0},
                        "snapshot": _string(),
                        "assert": _strings(),
                        "error": _string(pattern=r"^[EW]\d{3}$"),
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    },
}
"""The published meta-schema. ``schema/crowley-1.schema.json`` is generated from this dict."""
