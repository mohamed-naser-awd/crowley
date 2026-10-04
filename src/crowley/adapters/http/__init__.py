"""Built-in ``http`` adapter (docs/SPEC.md §13.7).

M1 declares the adapter and its ``http.*`` function contracts; the httpx transport arrives in M3.
"""

from typing import Any, ClassVar

from crowley.application.adapters import BaseAdapter
from crowley.domain.adapters import TargetKind
from crowley.domain.common import SemVer
from crowley.domain.functions import TARGET, FunctionKind, FunctionSpec

_DURATION: dict[str, Any] = {
    "anyOf": [
        {"type": "number", "minimum": 0},
        {"type": "string", "pattern": r"^\s*\d+(\.\d+)?\s*(ms|s|m|h)\s*$"},
    ]
}
_STATUS: dict[str, Any] = {
    "type": "array",
    "items": {"anyOf": [{"type": "integer"}, {"type": "string", "pattern": "^[1-5]xx$"}]},
}
_RETRY: dict[str, Any] = {
    "type": "object",
    "required": ["times"],
    "properties": {
        "times": {"type": "integer", "minimum": 0},
        "backoff": {"enum": ["fixed", "exponential"]},
        "delay": _DURATION,
        "max_delay": _DURATION,
        "on_status": {"type": "array", "items": {"type": "integer"}},
        "on_network_error": {"type": "boolean"},
        "respect_retry_after": {"type": "boolean"},
    },
    "additionalProperties": False,
}
_BODY: dict[str, Any] = {
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
_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

_REQUEST_PROPERTIES: dict[str, Any] = {
    "method": {"enum": _METHODS, "default": "GET"},
    "url": {"type": "string", "minLength": 1, TARGET: True},
    "query": {"type": "object"},
    "headers": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
    "cookies": {"type": "object", "additionalProperties": {"type": "string"}},
    "body": _BODY,
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
    "timeout": _DURATION,
    "follow_redirects": {"type": "boolean"},
    "max_redirects": {"type": "integer", "minimum": 0},
    "proxy": {"type": "string"},
    "verify_tls": {"type": "boolean"},
    "expect_status": _STATUS,
    "response_type": {"enum": ["auto", "json", "text", "bytes"]},
    "encoding": {"type": "string"},
    "retry": _RETRY,
}

_RESPONSE: dict[str, Any] = {
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

_DEFAULT_KEYS = (
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


class HttpAdapter(BaseAdapter):
    name: ClassVar[str] = "http"
    version: ClassVar[str] = "1.0.0"
    schemes: ClassVar[tuple[str, ...]] = ("http", "https")
    target_kind: ClassVar[TargetKind] = TargetKind.NETWORK
    builtin: ClassVar[bool] = True
    config_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "user_agent": {"type": "string"},
            "proxy": {"type": "string"},
            "timeout": _DURATION,
            "http2": {"type": "boolean"},
            "allow_private_networks": {"type": "boolean"},
            "max_connections": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    }
    defaults_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {k: _REQUEST_PROPERTIES[k] for k in _DEFAULT_KEYS},
        "additionalProperties": False,
    }
    request_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "required": ["url"],
        "properties": _REQUEST_PROPERTIES,
        "additionalProperties": False,
    }
    response_schema: ClassVar[dict[str, Any]] = _RESPONSE

    def functions(self) -> list[FunctionSpec]:
        common: dict[str, Any] = {
            "version": SemVer.parse(self.version),
            "adapter": self.name,
            "builtin": self.builtin,
            "output": _RESPONSE,
        }
        specs = [
            FunctionSpec(
                name="http.request",
                description="Send an HTTP request (SPEC §13.7).",
                input=self.request_schema,
                **common,
            )
        ]
        without_method = {k: v for k, v in _REQUEST_PROPERTIES.items() if k != "method"}
        for method in ("get", "post", "put", "patch", "delete", "head"):
            specs.append(
                FunctionSpec(
                    name=f"http.{method}",
                    description=f"http.request with method {method.upper()}.",
                    input={
                        "type": "object",
                        "required": ["url"],
                        "properties": without_method,
                        "additionalProperties": False,
                    },
                    **common,
                )
            )
        specs.append(
            FunctionSpec(
                name="http.session",
                description="Run the body with an isolated cookie jar and connection pool.",
                kind=FunctionKind.BLOCK,
                input={
                    "type": "object",
                    "properties": {
                        "headers": _REQUEST_PROPERTIES["headers"],
                        "cookies": _REQUEST_PROPERTIES["cookies"],
                    },
                    "additionalProperties": False,
                },
                version=SemVer.parse(self.version),
                adapter=self.name,
                builtin=self.builtin,
            )
        )
        return specs


__all__ = ["HttpAdapter"]
