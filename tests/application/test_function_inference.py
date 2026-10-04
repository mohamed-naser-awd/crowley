"""``@function`` with schemas inferred from type hints, and the shortest ways to register."""

import enum
from typing import Annotated, Any, Literal, Optional, TypedDict

import pytest

from crowley import Crowley, FunctionContext, Schema, function
from crowley.application.registry import infer_schemas, type_to_schema
from crowley.application.registry.inference import wants_context
from crowley.domain.errors import ValidationError


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Book(TypedDict):
    title: str
    price: float


class Unknown:
    pass


@pytest.mark.parametrize(
    ("tp", "schema"),
    [
        (str, {"type": "string"}),
        (int, {"type": "integer"}),
        (float, {"type": "number"}),
        (bool, {"type": "boolean"}),
        (None, {"type": "null"}),
        (Any, {}),
        (Unknown, {}),
        (dict, {"type": "object"}),
        (list, {"type": "array"}),
        (list[int], {"type": "array", "items": {"type": "integer"}}),
        (set[str], {"type": "array", "items": {"type": "string"}}),
        (tuple[int, ...], {"type": "array", "items": {"type": "integer"}}),
        (
            tuple[int, str],
            {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "string"}]},
        ),
        (dict[str, int], {"type": "object", "additionalProperties": {"type": "integer"}}),
        (dict[str, Any], {"type": "object"}),
        (Optional[str], {"type": ["string", "null"]}),  # noqa: UP045 - testing the old spelling
        (int | str | None, {"type": ["integer", "string", "null"]}),
        (int | Any, {}),
        (
            list[int] | None,
            {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]},
        ),
        (Literal["asc", "desc"], {"enum": ["asc", "desc"]}),
        (Color, {"enum": ["red", "blue"]}),
        (
            Book,
            {
                "type": "object",
                "properties": {"title": {"type": "string"}, "price": {"type": "number"}},
                "required": ["price", "title"],
            },
        ),
        (Annotated[int, Schema(minimum=1)], {"type": "integer", "minimum": 1}),
        (Annotated[str, "the page URL"], {"type": "string", "description": "the page URL"}),
    ],
)
def test_type_to_schema(tp: Any, schema: dict[str, Any]) -> None:
    assert type_to_schema(tp) == schema


def test_infer_schemas_from_a_signature() -> None:
    def slugify(text: str, sep: str = "-", *, limit: int | None = None, prev: list[str]) -> str:
        return ""

    inferred = infer_schemas(slugify)
    assert inferred.input == {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "sep": {"type": "string", "default": "-"},
            "limit": {"type": ["integer", "null"]},
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    assert inferred.prev == {"type": "array", "items": {"type": "string"}}
    assert inferred.output == {"type": "string"}


def test_kwargs_and_context_are_not_arguments() -> None:
    async def handler(ctx: FunctionContext, url: str, *args: Any, **kwargs: Any) -> None:
        return None

    inferred = infer_schemas(handler)
    assert inferred.input == {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    }
    assert inferred.output == {"type": "null"}
    assert repr(Schema(minimum=1)) == "Schema(minimum=1)"


def test_uninspectable_callables_accept_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    def broken(_: Any) -> Any:
        raise ValueError("no signature")

    monkeypatch.setattr(inspect, "signature", broken)
    assert infer_schemas(len).input == {"type": "object"}
    assert not wants_context(len)


def test_unresolvable_annotations_accept_anything() -> None:
    def handler(x: "NoSuchType") -> "NoSuchType":  # type: ignore[name-defined]  # noqa: F821
        return x

    inferred = infer_schemas(handler)
    assert inferred.input["properties"] == {"x": {}}
    assert inferred.output == {}


def test_context_detection() -> None:
    def by_name(ctx, x: int) -> None: ...  # type: ignore[no-untyped-def]
    def by_other_name(context, x: int) -> None: ...  # type: ignore[no-untyped-def]
    def by_annotation(c: FunctionContext, x: int) -> None: ...
    def by_string(c: "FunctionContext", x: int) -> None: ...
    def without(x: int) -> None: ...
    def keyword_only(*, ctx: Any) -> None: ...

    assert wants_context(by_name)
    assert wants_context(by_other_name)
    assert wants_context(by_annotation)
    assert wants_context(by_string)
    assert not wants_context(without)
    assert not wants_context(keyword_only)
    assert not wants_context(lambda: None)
    assert not wants_context(len)


# ── registering ──────────────────────────────────────────────────────────────

TEMPLATE = """\
crowley: 1
id: me/registration
version: 1.0.0
name: Registration
description: The shortest ways to register functions.
permissions:
  hosts: [example.com]
requires: ["app.*@^1", "acme.*@^1"]
operations:
  op:
    description: Calls every way of registering.
    steps:
      - id: plain
        use: app.shout
        with: { text: hi }
      - id: decorated
        use: app.slug
        with: { text: Hello World }
      - id: named
        use: acme.title
        with: { text: hello world }
      - id: method
        use: app.double
        with: { n: 21 }
      - id: method_named
        use: acme.triple
        with: { n: 2 }
      - id: options
        use: acme.join
        with: { parts: [a, b] }
    output:
      value: ${{ steps }}
      schema: {}
"""


def shout(text: str) -> str:
    return text.upper()


@function
def slug(text: str, sep: str = "-") -> str:
    """Lower-case words joined by ``sep``."""
    return sep.join(text.lower().split())


@function("acme.title")
async def title(text: str) -> str:
    return text.title()


def test_every_way_of_registering() -> None:
    cw = Crowley(functions=[shout, slug, title])

    @cw.function
    async def double(n: int) -> int:
        return n * 2

    @cw.function("acme.triple")
    def triple(ctx: FunctionContext, n: int) -> int:
        assert ctx.function.name == "acme.triple"
        return n * 3

    @cw.function(name="acme.join", pure=True)
    def join(parts: list[str], prev: Any = None) -> str:
        return "+".join(parts)

    result = cw.run_sync(cw.load_text(TEMPLATE), "op")
    assert result.output == {
        "plain": "HI",
        "decorated": "hello-world",
        "named": "Hello World",
        "method": 42,
        "method_named": 6,
        "options": "a+b",
    }
    assert slug("A B") == "a-b"  # decorated functions stay callable
    assert slug.spec.description == "Lower-case words joined by ``sep``."
    assert join.spec.pure
    assert cw.registry.function("app.shout") is not None


def test_names_without_namespace_and_register() -> None:
    cw = Crowley()
    registered = cw.register(lambda_free)
    assert registered.name == "app.lambda_free"
    named = function("short")(lambda_free)
    assert named.name == "app.short"


def lambda_free(x: int = 1) -> int:
    return x


ARGS_TEMPLATE = """\
crowley: 1
id: me/args
version: 1.0.0
name: Args
description: Inferred schemas are enforced.
permissions:
  hosts: [example.com]
requires: ["app.*@^1"]
operations:
  bad_arg:
    description: d
    inputs: { n: {} }
    steps:
      - use: app.square
        with: { n: "${{ inputs.n }}" }
    output: { schema: {} }
  bad_result:
    description: d
    steps:
      - use: app.liar
    output: { schema: {} }
  bad_prev:
    description: d
    steps:
      - use: app.needs_list
    output: { schema: {} }
"""


def test_inferred_schemas_are_enforced() -> None:
    cw = Crowley()

    @cw.function
    def square(n: int) -> int:
        return n * n

    @cw.function
    def liar() -> int:
        return "not an int"  # type: ignore[return-value]

    @cw.function
    def needs_list(prev: list[int]) -> int:
        return len(prev)

    tpl = cw.load_text(ARGS_TEMPLATE)
    assert cw.run_sync(tpl, "bad_arg", inputs={"n": 4}).output == 16
    with pytest.raises(ValidationError) as info:
        cw.run_sync(tpl, "bad_arg", inputs={"n": "four"})
    assert info.value.code == "E403"
    with pytest.raises(ValidationError) as info:
        cw.run_sync(tpl, "bad_result")
    assert info.value.code == "E407"
    with pytest.raises(ValidationError, match="invalid prev"):
        cw.run_sync(tpl, "bad_prev")  # prev is the operation's inputs object, not a list
