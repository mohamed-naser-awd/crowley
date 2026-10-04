"""Layer 3 (stdlib): built-in functions (paginate, transform, control, extract).

M1 declares their contracts so templates can be validated; handlers arrive with the runtime (M3).
"""

from typing import Any

from crowley.application.registry import Handler, RegisteredFunction, Registry
from crowley.domain.common import SemVer
from crowley.domain.functions import BINDINGS, FROM_PREV, LAZY, FunctionKind, FunctionSpec

VERSION = SemVer(1, 0, 0)

_DURATION: dict[str, Any] = {
    "anyOf": [
        {"type": "number", "minimum": 0},
        {"type": "string", "pattern": r"^\s*\d+(\.\d+)?\s*(ms|s|m|h)\s*$"},
    ]
}
_MAX_PAGES: dict[str, Any] = {"type": "integer", "minimum": 1, "default": 100}
_FLATTEN: dict[str, Any] = {"type": "boolean", "default": False}
_ITEMS: dict[str, Any] = {"type": "array", FROM_PREV: True}


def _lazy(*bindings: str, **extra: Any) -> dict[str, Any]:
    return {LAZY: True, BINDINGS: list(bindings), **extra}


def _object(required: list[str], **properties: Any) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


def _spec(name: str, description: str, input: dict[str, Any], **kwargs: Any) -> FunctionSpec:
    return FunctionSpec(
        name=name, version=VERSION, description=description, input=input, builtin=True, **kwargs
    )


def _paginate() -> list[FunctionSpec]:
    block: dict[str, Any] = {
        "kind": FunctionKind.BLOCK,
        "body_bindings": ("page",),
        "output": {"type": "array"},
        "events": ("page.before", "page.after"),
    }
    stop_when = _lazy("result", "page")
    return [
        _spec(
            "paginate.by_page",
            "Page numbers: start, start+step, …",
            _object(
                [],
                start={"type": "integer", "default": 1},
                step={"type": "integer", "minimum": 1, "default": 1},
                max_pages=_MAX_PAGES,
                stop_when=stop_when,
                stop_on_empty={"type": "boolean", "default": True},
                flatten=_FLATTEN,
            ),
            **block,
        ),
        _spec(
            "paginate.by_offset",
            "Offsets: start, start+limit, …",
            _object(
                ["limit"],
                start={"type": "integer", "minimum": 0, "default": 0},
                limit={"type": "integer", "minimum": 1},
                max_pages=_MAX_PAGES,
                max_items={"type": "integer", "minimum": 1},
                stop_when=stop_when,
                stop_on_short_page={"type": "boolean", "default": True},
                flatten=_FLATTEN,
            ),
            **block,
        ),
        _spec(
            "paginate.by_cursor",
            "Cursors: next_cursor is evaluated after each page.",
            _object(
                ["next_cursor"],
                initial={},
                next_cursor=_lazy("result", "page"),
                max_pages=_MAX_PAGES,
                flatten=_FLATTEN,
            ),
            **block,
        ),
        _spec(
            "paginate.by_next_link",
            "Next-page links: next is evaluated after each page.",
            _object(
                ["start_url", "next"],
                start_url={"type": "string", "minLength": 1},
                next=_lazy("result", "page"),
                max_pages=_MAX_PAGES,
                flatten=_FLATTEN,
            ),
            **block,
        ),
    ]


def _transform() -> list[FunctionSpec]:
    plain: dict[str, Any] = {"pure": True, "output": {"type": "array"}}
    return [
        _spec(
            "transform.map",
            "Build one object per item from expressions.",
            _object(
                ["items", "fields"], items=_ITEMS, fields=_lazy("item", "index", type="object")
            ),
            **plain,
        ),
        _spec(
            "transform.filter",
            "Keep items where the expression is true.",
            _object(["items", "where"], items=_ITEMS, where=_lazy("item", "index")),
            **plain,
        ),
        _spec(
            "transform.dedupe",
            "Drop items whose key was already seen (keeps the first).",
            _object(["items", "by"], items=_ITEMS, by=_lazy("item")),
            **plain,
        ),
        _spec(
            "transform.sort",
            "Sort items by a key.",
            _object(
                ["items", "by"],
                items=_ITEMS,
                by=_lazy("item"),
                order={"enum": ["asc", "desc"], "default": "asc"},
            ),
            **plain,
        ),
        _spec(
            "transform.group_by",
            "Group items into an object of lists.",
            _object(["items", "by"], items=_ITEMS, by=_lazy("item")),
            pure=True,
            output={"type": "object"},
        ),
        _spec(
            "transform.flatten",
            "Flatten nested lists.",
            _object(["items"], items=_ITEMS, depth={"type": "integer", "minimum": 0, "default": 1}),
            **plain,
        ),
    ]


def _control() -> list[FunctionSpec]:
    return [
        _spec(
            "control.retry",
            "Re-run the body on catchable errors, or until a condition holds.",
            _object(
                ["times"],
                times={"type": "integer", "minimum": 0},
                backoff={"enum": ["fixed", "exponential"], "default": "exponential"},
                delay=_DURATION,
                max_delay=_DURATION,
                until=_lazy("result"),
            ),
            kind=FunctionKind.BLOCK,
        ),
        _spec("control.sleep", "Wait.", _object(["duration"], duration=_DURATION)),
        _spec(
            "control.parallel",
            "Run the body N times concurrently.",
            _object(["branches"], branches={"type": "integer", "minimum": 1}),
            kind=FunctionKind.BLOCK,
            body_bindings=("branch",),
            output={"type": "array"},
        ),
    ]


def _extract() -> list[FunctionSpec]:
    query: dict[str, Any] = {"type": "string", "minLength": 1}
    return [
        _spec(
            "extract.auto",
            "Pick the extractor from the source's media type, then extract fields.",
            _object(
                ["source", "fields"],
                source={FROM_PREV: True},
                using={"type": "string"},
                root={"anyOf": [query, {"type": "array", "items": query, "minItems": 1}]},
                fields={"type": "object", "minProperties": 1},
            ),
            pure=True,
            output={"type": ["object", "array"]},
        )
    ]


BUILTIN_FUNCTIONS: tuple[FunctionSpec, ...] = (
    *_paginate(),
    *_transform(),
    *_control(),
    *_extract(),
)


def _handlers() -> dict[str, Handler]:
    from crowley.stdlib import extract

    return {"extract.auto": extract.auto}


class StdlibPlugin:
    def register(self, registry: Registry) -> None:
        handlers = _handlers()
        for spec in BUILTIN_FUNCTIONS:
            handler = handlers.get(spec.name)
            registry.add_function(
                RegisteredFunction.create(spec, handler) if handler is not None else spec
            )


__all__ = ["BUILTIN_FUNCTIONS", "StdlibPlugin"]
