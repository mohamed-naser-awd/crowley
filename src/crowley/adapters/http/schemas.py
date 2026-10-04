"""JSON Schemas of the ``http`` adapter's requests, responses, config and defaults (§13.7)."""

from typing import Any

from crowley.domain.functions import TARGET

DURATION: dict[str, Any] = {
    "anyOf": [
        {"type": "number", "minimum": 0},
        {"type": "string", "pattern": r"^\s*\d+(\.\d+)?\s*(ms|s|m|h)\s*$"},
    ]
}
STATUS: dict[str, Any] = {
    "type": "array",
    "items": {"anyOf": [{"type": "integer"}, {"type": "string", "pattern": "^[1-5]xx$"}]},
}
RETRY: dict[str, Any] = {
    "type": "object",
    "required": ["times"],
    "properties": {
        "times": {"type": "integer", "minimum": 0},
        "backoff": {"enum": ["fixed", "exponential"]},
        "delay": DURATION,
        "max_delay": DURATION,
        "on_status": {"type": "array", "items": {"type": "integer"}},
        "on_network_error": {"type": "boolean"},
        "respect_retry_after": {"type": "boolean"},
    },
    "additionalProperties": False,
}
BODY: dict[str, Any] = {
    "type": "object",
    "minProperties": 1,
    "maxProperties": 1,
    "properties": {
        "json": {},
        "form": {"type": "object"},
        "raw": {"type": "string"},
        "bytes": {"type": "string"},
        "multipart": {"type": "array", "items": {"type": "object", "required": ["name"]}},
    },
    "additionalProperties": False,
}
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

REQUEST_PROPERTIES: dict[str, Any] = {
    "method": {"enum": METHODS, "default": "GET"},
    "url": {"type": "string", "minLength": 1, TARGET: True},
    "query": {"type": "object"},
    "headers": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
    "cookies": {"type": "object", "additionalProperties": {"type": "string"}},
    "body": BODY,
    "auth": {
        "type": "object",
        "properties": {
            "bearer": {"type": "string"},
            "basic": {
                "type": "object",
                "required": ["username", "password"],
                "properties": {"username": {"type": "string"}, "password": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "minProperties": 1,
        "maxProperties": 1,
        "additionalProperties": False,
    },
    "timeout": DURATION,
    "follow_redirects": {"type": "boolean"},
    "max_redirects": {"type": "integer", "minimum": 0},
    "proxy": {"type": "string"},
    "verify_tls": {"type": "boolean"},
    "expect_status": STATUS,
    "response_type": {"enum": ["auto", "json", "text", "bytes"]},
    "encoding": {"type": "string"},
    "retry": RETRY,
}

RESPONSE: dict[str, Any] = {
    "type": "object",
    "required": ["status", "ok", "url", "headers", "body"],
    "properties": {
        "status": {"type": "integer"},
        "ok": {"type": "boolean"},
        "url": {"type": "string"},
        "headers": {"type": "object"},
        "media_type": {"type": ["string", "null"]},
        "cookies": {"type": "object"},
        "body": {},
        "elapsed_ms": {"type": "number"},
        "redirects": {"type": "array", "items": {"type": "string"}},
        "request": {"type": "object"},
    },
}

DEFAULT_KEYS = (
    "headers",
    "query",
    "cookies",
    "timeout",
    "follow_redirects",
    "max_redirects",
    "verify_tls",
    "retry",
    "expect_status",
    "response_type",
)

CONFIG: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user_agent": {"type": "string"},
        "proxy": {"type": "string"},
        "timeout": DURATION,
        "http2": {"type": "boolean"},
        "allow_private_networks": {"type": "boolean"},
        "max_connections": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}
DEFAULTS: dict[str, Any] = {
    "type": "object",
    "properties": {k: REQUEST_PROPERTIES[k] for k in DEFAULT_KEYS},
    "additionalProperties": False,
}
REQUEST: dict[str, Any] = {
    "type": "object",
    "required": ["url"],
    "properties": REQUEST_PROPERTIES,
    "additionalProperties": False,
}
