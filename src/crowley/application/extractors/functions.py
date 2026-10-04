"""The functions generated for every extractor (docs/SPEC.md §13.9).

``<name>.parse``, ``<name>.select``, ``<name>.select_all`` and ``<name>.extract``. Their handlers
look the extractor up through the function context at call time, so a per-process override
(``cw.init(..., extractors={...})``) is used automatically.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from crowley.domain.extractors import ExtractorSpec
from crowley.domain.functions import FROM_PREV, FunctionKind, FunctionSpec
from crowley.domain.values import Value

if TYPE_CHECKING:
    from crowley.application.registry.functions import RegisteredFunction
    from crowley.application.runtime.context import FunctionContext


_SOURCE: dict[str, Any] = {
    "description": "Content to read. Defaults to prev (a response's body, a string or a node).",
    FROM_PREV: True,
}
_QUERY: dict[str, Any] = {"type": "string", "minLength": 1}
_QUERIES: dict[str, Any] = {"anyOf": [_QUERY, {"type": "array", "items": _QUERY, "minItems": 1}]}


def extractor_function_specs(spec: ExtractorSpec) -> list[FunctionSpec]:
    """``<name>.parse``, ``<name>.select``, ``<name>.select_all`` and ``<name>.extract``."""
    common: dict[str, Any] = {
        "version": spec.version,
        "kind": FunctionKind.PLAIN,
        "pure": True,
        "extractor": spec.name,
        "builtin": spec.builtin,
    }
    select_input = {
        "type": "object",
        "required": ["source", "selector"],
        "properties": {
            "source": _SOURCE,
            "selector": _QUERY,
            "language": {"enum": list(spec.query_languages)},
            "attr": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    return [
        FunctionSpec(
            name=f"{spec.name}.parse",
            description=f"Parse content into a {spec.name} node handle.",
            input={
                "type": "object",
                "required": ["source"],
                "properties": {"source": _SOURCE},
                "additionalProperties": False,
            },
            **common,
        ),
        FunctionSpec(
            name=f"{spec.name}.select",
            description="Value of the first match, or null.",
            input=select_input,
            **common,
        ),
        FunctionSpec(
            name=f"{spec.name}.select_all",
            description="Values of every match.",
            input=select_input,
            output={"type": "array"},
            **common,
        ),
        FunctionSpec(
            name=f"{spec.name}.extract",
            description="Extract fields: one object, or one object per root match.",
            input={
                "type": "object",
                "required": ["source", "fields"],
                "properties": {
                    "source": _SOURCE,
                    "root": _QUERIES,
                    "fields": {"type": "object", "minProperties": 1},
                },
                "additionalProperties": False,
            },
            output={"type": ["object", "array"]},
            **common,
        ),
    ]


def extractor_functions(spec: ExtractorSpec) -> "list[RegisteredFunction]":
    """The generated specs with their handlers."""
    from crowley.application.registry.functions import RegisteredFunction

    name = spec.name

    async def parse(ctx: "FunctionContext", source: Value, **kwargs: Value) -> Value:
        return ctx.extractor(name).parse(source)

    async def select(
        ctx: "FunctionContext",
        source: Value,
        selector: str,
        language: str | None = None,
        attr: str | None = None,
        **kwargs: Value,
    ) -> Value:
        return ctx.extractor(name).select(source, selector, language=language, attr=attr)

    async def select_all(
        ctx: "FunctionContext",
        source: Value,
        selector: str,
        language: str | None = None,
        attr: str | None = None,
        **kwargs: Value,
    ) -> Value:
        return ctx.extractor(name).select_all(source, selector, language=language, attr=attr)

    async def extract(
        ctx: "FunctionContext", source: Value, fields: Value, root: Value = None, **kwargs: Value
    ) -> Value:
        return await ctx.extractor(name).extract(source, fields, root=root)

    handlers: dict[str, Callable[..., Any]] = {
        "parse": parse,
        "select": select,
        "select_all": select_all,
        "extract": extract,
    }
    return [
        RegisteredFunction.create(fn_spec, handlers[fn_spec.name.rsplit(".", 1)[1]])
        for fn_spec in extractor_function_specs(spec)
    ]


__all__ = ["extractor_function_specs", "extractor_functions"]
