"""JSON Schemas inferred from Python signatures, so ``@function`` needs no hand-written schemas.

``def slugify(text: str, sep: str = "-") -> str`` becomes an input schema with ``text``
(required) and ``sep`` (default ``"-"``) and an output schema ``{"type": "string"}``.
"""

import collections.abc
import enum
import inspect
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Union, get_args, get_origin

CONTEXT_NAMES = frozenset({"ctx", "context"})


class Schema:
    """Extra JSON Schema keywords for a parameter: ``Annotated[int, Schema(minimum=1)]``."""

    __slots__ = ("keywords",)

    def __init__(self, **keywords: Any) -> None:
        self.keywords = keywords

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.keywords.items())
        return f"Schema({args})"


_JSON_SCALARS = (str, int, float, bool, type(None))


def type_to_schema(tp: Any) -> dict[str, Any]:
    """A JSON Schema for a type annotation. Unknown types accept anything (``{}``)."""
    if tp is Any or tp is inspect.Parameter.empty or tp is object:
        return {}
    if tp is None or tp is type(None):
        return {"type": "null"}
    simple = {bool: "boolean", int: "integer", float: "number", str: "string"}
    if tp in simple:
        return {"type": simple[tp]}
    if tp in (dict, collections.abc.Mapping, collections.abc.MutableMapping):
        return {"type": "object"}
    if tp in (list, tuple, set, frozenset, collections.abc.Sequence):
        return {"type": "array"}
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return {"enum": [member.value for member in tp]}
    if typing.is_typeddict(tp):
        hints = typing.get_type_hints(tp)
        return {
            "type": "object",
            "properties": {key: type_to_schema(value) for key, value in hints.items()},
            "required": sorted(getattr(tp, "__required_keys__", hints)),
        }

    origin, args = get_origin(tp), get_args(tp)
    if origin is typing.Annotated:
        schema = type_to_schema(args[0])
        for extra in args[1:]:
            if isinstance(extra, Schema):  # Annotated[int, Schema(minimum=1)]
                schema = {**schema, **extra.keywords}
            elif isinstance(extra, Mapping):
                schema = {**schema, **extra}
            elif isinstance(extra, str):  # Annotated[str, "the page URL"]
                schema = {**schema, "description": extra}
        return schema
    if origin in (Union, types.UnionType):
        options = [type_to_schema(arg) for arg in args]
        if any(option == {} for option in options):
            return {}
        if all(set(option) == {"type"} for option in options):
            names: list[str] = []
            for option in options:
                kind = option["type"]
                for name in kind if isinstance(kind, list) else [kind]:
                    if name not in names:
                        names.append(name)
            return {"type": names[0] if len(names) == 1 else names}
        return {"anyOf": options}
    if origin is Literal:
        return {"enum": list(args)}
    if origin in (list, set, frozenset, collections.abc.Sequence, collections.abc.Iterable):
        return {"type": "array", "items": type_to_schema(args[0])} if args else {"type": "array"}
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return {"type": "array", "items": type_to_schema(args[0])}
        return {"type": "array", "prefixItems": [type_to_schema(a) for a in args]}
    if origin in (dict, collections.abc.Mapping, collections.abc.MutableMapping):
        if len(args) == 2 and type_to_schema(args[1]) != {}:
            return {"type": "object", "additionalProperties": type_to_schema(args[1])}
        return {"type": "object"}
    return {}


@dataclass(frozen=True, slots=True)
class InferredSchemas:
    input: dict[str, Any]
    prev: dict[str, Any] | None
    output: dict[str, Any]


def wants_context(handler: Callable[..., Any]) -> bool:
    """A handler gets the function context only if its first parameter asks for it:
    named ``ctx``/``context`` or annotated ``FunctionContext``."""
    try:
        parameters = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        return False
    if not parameters:
        return False
    first = parameters[0]
    if first.kind not in (first.POSITIONAL_ONLY, first.POSITIONAL_OR_KEYWORD):
        return False
    if first.name in CONTEXT_NAMES:
        return True
    annotation = first.annotation
    label = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")
    return "FunctionContext" in str(label)


def infer_schemas(handler: Callable[..., Any]) -> InferredSchemas:
    """Input, prev and output schemas from ``handler``'s parameters and return annotation."""
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):  # not inspectable: accept anything (binding reports E903)
        return InferredSchemas(input={"type": "object"}, prev=None, output={})
    try:
        hints = typing.get_type_hints(handler, include_extras=True)
    except Exception:  # unresolvable forward references: fall back to "anything"
        hints = {}
    parameters = list(signature.parameters.values())
    if wants_context(handler):
        parameters = parameters[1:]
    properties: dict[str, Any] = {}
    required: list[str] = []
    prev: dict[str, Any] | None = None
    open_ended = False
    for param in parameters:
        if param.kind is param.VAR_KEYWORD:
            open_ended = True
            continue
        if param.kind is param.VAR_POSITIONAL:
            continue
        schema = type_to_schema(hints.get(param.name, param.annotation))
        if param.name == "prev":
            prev = schema or None
            continue
        if param.default is param.empty:
            required.append(param.name)
        elif isinstance(param.default, _JSON_SCALARS) and param.default is not None:
            schema = {**schema, "default": param.default}
        properties[param.name] = schema
    input_schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required
    if not open_ended:
        input_schema["additionalProperties"] = False
    output = type_to_schema(hints.get("return", signature.return_annotation))
    return InferredSchemas(input=input_schema, prev=prev, output=output)
