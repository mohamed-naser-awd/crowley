"""Function calls: handler binding, invocation pipeline, block bodies and template functions."""

from typing import Any

import pytest

from crowley import FunctionContext, RegisteredFunction, function
from crowley.application.events import NotifierSet
from crowley.application.registry import HandlerBinding, Registry
from crowley.domain.common import SemVer
from crowley.domain.errors import (
    ConfigurationError,
    ExchangeError,
    ExecutionError,
    ValidationError,
)
from crowley.domain.functions import FunctionKind, FunctionSpec
from tests.application.runtime_support import execute, operation, template

REQUIRES = 'requires: ["acme.*@^1"]'
TEXT_INPUT = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "sep": {"type": "string", "default": "-"}},
    "additionalProperties": False,
}
received: list[dict[str, Any]] = []


@function(name="acme.slug", input=TEXT_INPUT, output={"type": "string"})
async def slug(ctx: FunctionContext, **kwargs: Any) -> str:
    received.append(kwargs)
    text = kwargs.get("text") or kwargs["prev"]
    return str(text).lower().replace(" ", kwargs["sep"])


@function(name="acme.named", input=TEXT_INPUT, output={})
async def named(ctx: FunctionContext, text: str = "", prev: Any = None, **rest: Any) -> Any:
    return {"text": text, "prev": prev, "rest": rest}


@function(name="acme.strict", input=TEXT_INPUT, output={})
async def strict(ctx: FunctionContext, text: str = "none") -> Any:
    return {"text": text}


@function(name="acme.sync", input=TEXT_INPUT, output={"type": "string"})
def sync_upper(ctx: FunctionContext, text: str = "") -> str:
    return text.upper()


@function(
    name="acme.each",
    kind="block",
    input={"type": "object", "required": ["regions"], "properties": {"regions": {"type": "array"}}},
    output={"type": "array"},
    body_bindings=("region",),
)
async def each_region(ctx: FunctionContext, regions: list[Any], **kwargs: Any) -> list[Any]:
    results = []
    assert ctx.body is not None
    for region in regions:
        outcome = await ctx.body.run(input=region, bindings={"region": region})
        if outcome.is_break:
            break
        if outcome.has_value:
            results.append(outcome.value)
    return results


@function(
    name="acme.filter",
    input={
        "type": "object",
        "required": ["items", "keep"],
        "properties": {
            "items": {"type": "array", "x-crowley-from-prev": True},
            "keep": {"x-crowley-lazy": True, "x-crowley-bindings": ["x"]},
        },
    },
    output={"type": "array"},
)
async def keep_if(ctx: FunctionContext, items: list[Any], keep: Any, **kwargs: Any) -> list[Any]:
    return [x for x in items if ctx.evaluate(keep, {"x": x})]


@function(name="acme.boom", output={})
async def boom(ctx: FunctionContext, **kwargs: Any) -> Any:
    raise ValueError("kaput")


@function(name="acme.bad_result", output={"type": "integer"})
async def bad_result(ctx: FunctionContext, **kwargs: Any) -> Any:
    return "not an int"


@function(
    name="acme.typed_prev",
    prev={"type": "integer"},
    output={},
)
async def typed_prev(ctx: FunctionContext, prev: Any) -> Any:
    return prev


@function(name="acme.ctx", output={}, events=("acme.ping",))
async def use_ctx(ctx: FunctionContext, **kwargs: Any) -> Any:
    emitted = await ctx.emit_event("acme.ping", n=1)
    await ctx.log("info", "from ctx")
    return {
        "data": emitted.data,
        "action": type(emitted.action).__name__ if emitted.action else None,
        "defaults": ctx.defaults,
        "run": dict(ctx.run),
        "step": ctx.step.id,
        "cancelled": ctx.cancelled,
        "function": ctx.function.name,
        "evaluated": ctx.evaluate(5),
    }


ALL = [slug, named, strict, sync_upper, each_region, keep_if, boom, bad_result, typed_prev, use_ctx]


def op(steps: str, **kwargs: Any) -> Any:
    kwargs.setdefault("extra", REQUIRES)
    return operation(steps, functions=ALL, **kwargs)


async def run(tpl: Any, **kwargs: Any) -> Any:
    return await execute(tpl, functions=ALL, **kwargs)


# ── handler binding (SPEC §11.1) ─────────────────────────────────────────────


async def test_var_kw_handler_gets_args_defaults_and_prev() -> None:
    received.clear()
    tpl = op(
        """
        - id: a
          use: acme.slug
          with: { text: Hello World }
        - id: b
          use: acme.slug
        """
    )
    result = await run(tpl, inputs={"x": 1})
    assert result.frame.steps == {"a": "hello-world", "b": "hello-world"}
    assert received[0] == {"text": "Hello World", "sep": "-", "prev": {"x": 1}}
    assert received[1] == {"sep": "-", "prev": "hello-world"}  # absent optional arg not passed


async def test_named_params_and_dropped_kwargs() -> None:
    tpl = op(
        """
        - id: named
          use: acme.named
          with: { text: t }
        - id: strict
          use: acme.strict
          with: { sep: "+" }
        """
    )
    result = await run(tpl, inputs={"in": 1})
    assert result.frame.steps["named"] == {"text": "t", "prev": {"in": 1}, "rest": {"sep": "-"}}
    assert result.frame.steps["strict"] == {"text": "none"}  # prev and sep dropped


async def test_sync_handlers_run_in_a_thread() -> None:
    tpl = op("- use: acme.sync\n  with: { text: abc }")
    assert (await run(tpl)).prev == "ABC"


def spec(name: str = "acme.f", **kwargs: Any) -> FunctionSpec:
    return FunctionSpec(name=name, version=SemVer.parse("1.0.0"), **kwargs)


@pytest.mark.parametrize(
    ("handler", "kind", "message"),
    [
        (lambda: None, FunctionKind.PLAIN, "function context as its first parameter"),
        (lambda *, ctx: None, FunctionKind.PLAIN, "first parameter"),
        (lambda ctx, needed: None, FunctionKind.PLAIN, "has no default"),
        (lambda ctx, a, /: None, FunctionKind.PLAIN, "positional-only"),
        (lambda ctx, **kw: None, FunctionKind.BLOCK, "must be async"),
    ],
)
def test_incompatible_signatures_are_e903(handler: Any, kind: FunctionKind, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message) as info:
        HandlerBinding.from_handler(handler, spec(kind=kind))
    assert info.value.code == "E903"


def test_binding_accepts_required_params_and_prev() -> None:
    required = spec(input={"type": "object", "required": ["a"], "properties": {"a": {}}})
    binding = HandlerBinding.from_handler(lambda ctx, a, prev, *args: None, required)
    assert binding.params == {"a", "prev"}
    assert binding.bind({"a": 1, "b": 2, "prev": 3}) == {"a": 1, "prev": 3}
    with pytest.raises(ConfigurationError, match="not callable"):
        RegisteredFunction.create(required, "nope")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="not inspectable"):
        HandlerBinding.from_handler(type, required)


def test_registry_keeps_handlers_and_specs_in_step() -> None:
    registry = Registry()
    registry.add_function(slug)
    assert registry.handler("acme.slug") is slug
    assert registry.function("acme.slug") is slug.spec
    registry.add_function(slug.spec, replace=True)
    assert registry.handler("acme.slug") is None


def test_decorator_uses_docstring_as_description() -> None:
    @function(name="acme.doc")
    async def documented(ctx: FunctionContext) -> None:
        """Does a thing."""

    assert documented.spec.description == "Does a thing."
    assert documented.name == "acme.doc"


# ── arguments ────────────────────────────────────────────────────────────────


async def test_from_prev_and_lazy_arguments() -> None:
    tpl = op(
        """
        - for_each: ${{ [1, 2, 3, 4] }}
          do:
            - return: ${{ item }}
        - use: acme.filter
          with:
            keep: ${{ x % 2 == 0 }}
        """
    )
    assert (await run(tpl)).prev == [2, 4]


async def test_invalid_arguments_are_e403() -> None:
    tpl = op('- use: acme.slug\n  with: { text: "${{ 5 }}" }')
    with pytest.raises(ValidationError) as info:
        await run(tpl)
    assert info.value.code == "E403"
    assert info.value.path == "operations.op.steps[0].with.text"
    prev = op("- use: acme.typed_prev")
    with pytest.raises(ValidationError, match="invalid prev"):
        await run(prev, inputs={"not": "int"})


async def test_invalid_result_is_e407() -> None:
    tpl = op("- use: acme.bad_result")
    with pytest.raises(ValidationError) as info:
        await run(tpl)
    assert info.value.code == "E407"


async def test_handler_exceptions_are_e502() -> None:
    tpl = op("- use: acme.boom")
    with pytest.raises(ExecutionError) as info:
        await run(tpl)
    assert info.value.code == "E502"
    assert "ValueError: kaput" in info.value.message
    assert isinstance(info.value.cause, ValueError)


# ── notifiers on function calls ──────────────────────────────────────────────


async def test_step_before_and_function_before_change_kwargs() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("step.before", lambda e: e.kwargs.update(text="From Step"))
    notifiers.add_notifier("function.before", lambda e: e.kwargs.update(sep="_"))
    tpl = op("- use: acme.slug\n  with: { text: ignored }")
    assert (await run(tpl, notifiers=notifiers)).prev == "from_step"


async def test_changed_kwargs_are_revalidated_unless_disabled() -> None:
    notifiers = NotifierSet("global")
    handle = notifiers.add_notifier("function.before", lambda e: e.kwargs.update(text=42))
    tpl = op("- use: acme.named\n  with: { text: ok }")
    with pytest.raises(ValidationError) as info:
        await run(tpl, notifiers=notifiers)
    assert info.value.code == "E403"
    notifiers.remove_notifier(handle)
    notifiers.add_notifier("function.before", lambda e: e.kwargs.update(text=42), revalidate=False)
    assert (await run(tpl, notifiers=notifiers)).prev["text"] == 42


async def test_function_before_skip_and_replace() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("function.before", lambda e: e.skip("skipped"), step="a")
    notifiers.add_notifier("function.before", lambda e: e.replace(7), step="b")
    tpl = op(
        """
        - id: a
          use: acme.boom
        - id: b
          use: acme.bad_result
        """
    )
    result = await run(tpl, notifiers=notifiers)
    assert result.frame.steps == {"a": "skipped", "b": 7}


async def test_function_after_replace_retry_and_revalidation() -> None:
    received.clear()
    notifiers = NotifierSet("global")
    notifiers.add_notifier("function.after", lambda e: e.retry() if len(received) < 2 else None)
    notifiers.add_notifier("function.after", lambda e: e.replace(e.result + "!"))
    tpl = op("- use: acme.slug\n  with: { text: hi }")
    assert (await run(tpl, notifiers=notifiers)).prev == "hi!"
    assert len(received) == 2
    notifiers.clear()
    notifiers.add_notifier("function.after", lambda e: e.replace(3))
    with pytest.raises(ValidationError) as info:
        await run(tpl, notifiers=notifiers)
    assert info.value.code == "E407"


async def test_step_after_replacement_is_revalidated() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("step.after", lambda e: e.replace(3))
    tpl = op("- use: acme.slug\n  with: { text: hi }")
    with pytest.raises(ValidationError, match="invalid result"):
        await run(tpl, notifiers=notifiers)


async def test_function_error_retry_and_replace() -> None:
    notifiers = NotifierSet("global")
    errors: list[int] = []

    def on_error(event: Any) -> Any:
        errors.append(event.attempt)
        return event.retry() if event.attempt < 3 else event.replace({"recovered": True})

    notifiers.add_notifier("function.error", on_error, function="acme.boom")
    tpl = op("- use: acme.boom")
    result = await run(tpl, notifiers=notifiers)
    assert result.prev == {"recovered": True}
    assert errors == [1, 2, 3]
    assert result.state.stats.retries == 2


async def test_on_error_default_is_validated_against_output() -> None:
    tpl = op(
        """
        - id: recovered
          use: acme.boom
          on_error:
            default: 1
        - id: validation_errors_are_not_caught
          use: acme.bad_result
          on_error:
            default: 0
        """
    )
    with pytest.raises(ValidationError) as info:
        await run(tpl)
    assert info.value.code == "E407"
    tpl = op(
        """
        - id: ok
          use: local.failing
          on_error: { default: 3 }
        """,
        extra=REQUIRES + "\n" + FUNCTIONS,
    )
    assert (await run(tpl)).frame.steps == {"ok": 3}
    tpl = op(
        """
        - use: local.failing
          on_error: { default: nope }
        """,
        extra=REQUIRES + "\n" + FUNCTIONS,
    )
    with pytest.raises(ValidationError) as info:
        await run(tpl)
    assert info.value.code == "E407"


async def test_argument_errors_still_fire_step_before() -> None:
    notifiers = NotifierSet("global")
    seen: list[str] = []
    notifiers.add_notifier("step.*", lambda e: seen.append(e.name), observe=True)
    tpl = op('- use: acme.slug\n  with: { text: "${{ str([1][3]) }}" }')
    with pytest.raises(ExecutionError):
        await run(tpl, notifiers=notifiers)
    assert seen == ["step.before", "step.error"]


# ── block functions ──────────────────────────────────────────────────────────


async def test_block_function_body() -> None:
    tpl = op(
        """
        - id: regions
          use: acme.each
          with: { regions: [a, b, c, d, e] }
          do:
            - if: ${{ region == 'b' }}
              then:
                - continue: true
            - if: ${{ region == 'd' }}
              then:
                - break: true
            - if: ${{ region == 'c' }}
              then:
                - return: ${{ 'C' }}
            - log: ${{ prev }}
        """
    )
    assert (await run(tpl)).frame.steps["regions"] == ["a", "C"]


# ── template functions (local.*) ─────────────────────────────────────────────

FUNCTIONS = """
functions:
  label:
    description: Label a value; continues the caller's pipe.
    input:
      type: object
      required: [prefix]
      properties:
        prefix: { type: string }
    output: { type: string }
    steps:
      - log: ${{ args.prefix }}
      - return: ${{ args.prefix + ':' + str(args.prev) + ':' + str(prev) }}
  passthrough:
    description: Ends with the body's last output.
    output: {}
    steps:
      - set: { kept: 1 }
  failing:
    description: Always fails.
    output: { type: integer }
    steps:
      - fail: nope
  wrong:
    description: Returns the wrong type.
    output: { type: integer }
    steps:
      - return: text
"""


async def test_template_function_args_prev_and_value() -> None:
    tpl = op(
        """
        - id: one
          use: local.label
          with: { prefix: a }
        - id: looped
          for_each: ${{ [1, 2] }}
          do:
            - use: local.label
              with: { prefix: "${{ 'n' + str(item) }}" }
        - use: local.passthrough
        """,
        extra=REQUIRES + "\n" + FUNCTIONS,
    )
    result = await run(tpl, inputs={"k": 1})
    assert result.frame.steps["one"] == 'a:{"k":1}:{"k":1}'
    assert result.frame.steps["looped"] == ["n1:1:1", "n2:2:2"]
    assert result.prev == ["n1:1:1", "n2:2:2"]  # passthrough returns its caller's prev


async def test_template_function_events_and_validation() -> None:
    notifiers = NotifierSet("global")
    calls: list[tuple[str, str | None]] = []
    notifiers.add_notifier(
        "function.*",
        lambda e: calls.append((e.name, e.function)),
        function="local.*",
        observe=True,
    )
    tpl = op("- use: local.label\n  with: { prefix: p }", extra=REQUIRES + "\n" + FUNCTIONS)
    await run(tpl, notifiers=notifiers)
    assert calls == [("function.before", "local.label"), ("function.after", "local.label")]
    wrong = op("- use: local.wrong", extra=REQUIRES + "\n" + FUNCTIONS)
    with pytest.raises(ValidationError) as info:
        await run(wrong)
    assert info.value.code == "E407"


async def test_template_function_scope_is_isolated() -> None:
    tpl = template(
        REQUIRES
        + """
functions:
  peek:
    description: Looks at its own scope.
    output: {}
    steps:
      - set: { inner: 1 }
      - return: ${{ [vars, steps, args.prev] }}
operations:
  op:
    description: d
    steps:
      - set: { outer: 1 }
      - use: local.peek
    output:
      schema: {}
""",
        ALL,
    )
    result = await execute(tpl, functions=ALL, inputs={"i": 1})
    assert result.prev == [{"inner": 1}, {}, {"i": 1}]


# ── FunctionContext ──────────────────────────────────────────────────────────


async def test_function_context() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("acme.ping", lambda e: e.data.update(n=2) or e.replace(None))
    logs: list[str] = []
    notifiers.add_notifier("log", lambda e: logs.append(e.message))
    tpl = op("- id: probe\n  use: acme.ctx")
    result = await run(tpl, notifiers=notifiers)
    assert result.prev == {
        "data": {"n": 2},
        "action": "Replace",
        "defaults": {},
        "run": {"id": "run-1"},
        "step": "probe",
        "cancelled": False,
        "function": "acme.ctx",
        "evaluated": 5,
    }
    assert logs == ["from ctx"]


async def test_context_rejects_undeclared_events_and_has_no_adapters_yet() -> None:
    from crowley.application.runtime import FunctionContext as Ctx

    tpl = op("- use: acme.ctx")
    result = await run(tpl)
    ctx = Ctx(
        state=result.state,
        spec=use_ctx.spec,
        step=None,  # type: ignore[arg-type]
        frame=result.frame,
        prev=None,
    )
    with pytest.raises(ConfigurationError, match="undeclared event"):
        await ctx.emit_event("acme.other")
    with pytest.raises(ExchangeError):
        await ctx.exchange("http", {})
    with pytest.raises(ExchangeError):
        ctx.adapter("http")
    with pytest.raises(ExchangeError):
        ctx.extractor("html")
    assert ctx.defaults == {}
    with_defaults = op(
        "- use: acme.ctx", extra=REQUIRES + "\ndefaults:\n  http: { proxy: \"${{ 'p' }}\" }"
    )
    ctx = Ctx(
        state=result.state,
        spec=use_ctx.spec,
        step=None,  # type: ignore[arg-type]
        frame=result.frame,
        prev=None,
        defaults={"acme": with_defaults.defaults["http"]},
    )
    assert ctx.defaults == {"proxy": "p"}


async def test_unknown_or_unimplemented_functions_are_e903() -> None:
    declared = spec("acme.declared")
    tpl = operation("- use: acme.declared", extra=REQUIRES, functions=[declared])
    with pytest.raises(ConfigurationError, match="no implementation"):
        await execute(tpl, functions=[declared])


async def test_null_optional_arguments_count_as_omitted() -> None:
    received.clear()
    tpl = op("- use: acme.slug\n  with: { text: Hi There, sep: null }")
    assert (await run(tpl)).prev == "hi-there"  # the schema default applies
    assert received[0]["sep"] == "-"
