"""Processes: init validation, run/stream/start/cancel, output modes and isolation."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from crowley import (
    Crowley,
    DictSecrets,
    EnvSecrets,
    FunctionContext,
    Limits,
    NotifierRegistry,
    function,
)
from crowley.application.events import Notifier
from crowley.domain.common import Duration
from crowley.domain.errors import (
    ConfigurationError,
    ControlError,
    ExecutionError,
    LimitError,
    ValidationError,
)

TEMPLATE = """\
crowley: 1
id: acme/directory
version: 1.2.0
name: Directory
description: Process tests.
permissions:
  hosts: [api.example.com]
requires: ["acme.*@^1"]
secrets:
  API_TOKEN: { description: token }
  OPTIONAL: { required: false }
schemas:
  person:
    type: object
    required: [name]
    properties:
      name: { type: string }
      rank: { type: integer }
functions:
  greet:
    description: Greets.
    input:
      type: object
      required: [name]
      properties:
        name: { type: string }
    output: { type: string }
    steps:
      - return: ${{ 'hello ' + args.name }}
operations:
  people:
    description: Emit people.
    inputs:
      company: { type: string, minLength: 1 }
      count: { type: integer, default: 3 }
      bad: { type: boolean, default: false }
    steps:
      - use: acme.people
        with:
          company: ${{ inputs.company }}
          count: ${{ inputs.count }}
          bad: ${{ inputs.bad }}
      - for_each: ${{ prev }}
        do:
          - emit: ${{ item }}
    output:
      schema:
        type: array
        items: { $ref: "#/schemas/person" }
  info:
    description: Value output.
    inputs:
      company: { type: string }
    steps:
      - id: greeting
        use: local.greet
        with:
          name: ${{ inputs.company }}
    output:
      value:
        greeting: ${{ steps.greeting }}
        token_length: ${{ len(secrets.API_TOKEN) }}
      schema:
        type: object
        required: [greeting]
        properties:
          greeting: { type: string }
  piped:
    description: Pipe output.
    inputs:
      count: { type: integer, default: 2 }
    steps:
      - use: acme.people
        with:
          company: x
          count: ${{ inputs.count }}
    output:
      schema: { type: array, maxItems: 3 }
  slow:
    description: Waits forever.
    steps:
      - use: acme.wait
    output:
      schema: {}
  leaky:
    description: Fails with the secret in the message.
    steps:
      - fail: ${{ 'token is ' + secrets.API_TOKEN }}
    output:
      schema: {}
"""

SECRETS = {"API_TOKEN": "s3cr3t-token"}


@function(
    name="acme.people",
    input={
        "type": "object",
        "required": ["company", "count"],
        "properties": {
            "company": {"type": "string"},
            "count": {"type": "integer"},
            "bad": {"type": "boolean", "default": False},
        },
    },
    output={"type": "array"},
)
async def people(ctx: FunctionContext, company: str, count: int, bad: bool = False) -> Any:
    await asyncio.sleep(0)
    return [{"name": f"{company}-{i}", "rank": "x" if bad else i} for i in range(count)]


@function(name="acme.wait", output={})
async def wait(ctx: FunctionContext) -> Any:
    await asyncio.Event().wait()


@pytest.fixture
def cw() -> Crowley:
    return Crowley(functions=[people, wait], secrets=SECRETS)


@pytest.fixture
def tpl(cw: Crowley) -> Any:
    return cw.load_text(TEMPLATE, name="directory.yml")


# ── init ─────────────────────────────────────────────────────────────────────


def test_init_validates_inputs_and_secrets(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "people", inputs={"company": "acme"})
    assert process.inputs == {"company": "acme", "count": 3, "bad": False}
    assert process.status == "created"
    assert process.operation.name == "people"
    assert process.template is tpl
    assert "acme/directory#people created" in repr(process)
    with pytest.raises(ValidationError) as info:
        cw.init(tpl, "people", inputs={})
    assert info.value.code == "E401"
    with pytest.raises(ValidationError) as info:
        cw.init(tpl, "people", inputs={"company": ""})
    assert info.value.path == "inputs.company"
    with pytest.raises(ValidationError) as info:
        Crowley(functions=[people]).init(tpl, "people", inputs={"company": "a"})
    assert info.value.code == "E402"
    explicit = Crowley(functions=[people]).init(
        tpl, "people", inputs={"company": "a"}, secrets={"API_TOKEN": "t"}
    )
    assert explicit.status == "created"


def test_operation_selection_is_e904(cw: Crowley, tpl: Any) -> None:
    with pytest.raises(ConfigurationError, match="several operations") as info:
        cw.init(tpl, inputs={"company": "a"})
    assert info.value.code == "E904"
    with pytest.raises(ConfigurationError, match="no operation 'nope'"):
        cw.init(tpl, "nope")


# ── run modes ────────────────────────────────────────────────────────────────


async def test_emit_mode(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "people", inputs={"company": "acme", "count": 2})
    result = await process.run()
    expected = [{"name": "acme-0", "rank": 0}, {"name": "acme-1", "rank": 1}]
    assert result.output == expected
    assert result.items == expected
    assert result.stats["items"] == 2
    assert result.run_id == process.id
    assert result.operation == "people"
    assert result.trace is None
    assert process.status == "succeeded"


async def test_value_mode_with_template_function_and_secrets(cw: Crowley, tpl: Any) -> None:
    result = await cw.run(tpl, "info", inputs={"company": "acme"})
    assert result.output == {"greeting": "hello acme", "token_length": 12}
    assert result.items == []


async def test_pipe_mode_and_output_validation(cw: Crowley, tpl: Any) -> None:
    result = await cw.run(tpl, "piped")
    assert result.output == [{"name": "x-0", "rank": 0}, {"name": "x-1", "rank": 1}]
    with pytest.raises(ValidationError) as info:
        await cw.run(tpl, "piped", inputs={"count": 5})
    assert info.value.code == "E405"


async def test_invalid_emitted_item_is_e404(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "people", inputs={"company": "a", "bad": True})
    with pytest.raises(ValidationError) as info:
        await process.run()
    assert info.value.code == "E404"
    assert process.status == "failed"


def test_run_sync(cw: Crowley, tpl: Any) -> None:
    result = cw.run_sync(tpl, "info", inputs={"company": "x"})
    assert result.output["greeting"] == "hello x"
    assert cw.init(tpl, "piped").run_sync().output[0]["name"] == "x-0"


async def test_sync_api_inside_loop_is_e902(cw: Crowley, tpl: Any) -> None:
    with pytest.raises(ConfigurationError) as info:
        cw.init(tpl, "piped").run_sync()
    assert info.value.code == "E902"


def test_start_needs_a_loop(cw: Crowley, tpl: Any) -> None:
    with pytest.raises(ConfigurationError, match="running event loop"):
        cw.init(tpl, "piped").start()


async def test_a_process_runs_once(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "piped")
    with pytest.raises(ConfigurationError, match="not been started"):
        await process.result()
    await process.run()
    for again in (process.run(), process.stream().__anext__()):
        with pytest.raises(ConfigurationError) as info:
            await again
        assert info.value.code == "E905"
    with pytest.raises(ConfigurationError):
        process.start()


async def test_start_and_result(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "piped")
    process.start()
    assert process.status == "running"
    assert (await process.result()).output[1]["name"] == "x-1"


# ── streaming ────────────────────────────────────────────────────────────────


async def test_stream_yields_items_in_order(cw: Crowley, tpl: Any) -> None:
    seen = [item async for item in cw.init(tpl, "people", inputs={"company": "s"}).stream()]
    assert [p["name"] for p in seen] == ["s-0", "s-1", "s-2"]
    shortcut = [i async for i in cw.stream(tpl, "people", inputs={"company": "t", "count": 1})]
    assert shortcut == [{"name": "t-0", "rank": 0}]


async def test_stream_of_value_operation_yields_output(cw: Crowley, tpl: Any) -> None:
    items = [item async for item in cw.stream(tpl, "info", inputs={"company": "v"})]
    assert items == [{"greeting": "hello v", "token_length": 12}]


async def test_stream_errors_propagate_and_early_exit_cancels(cw: Crowley, tpl: Any) -> None:
    with pytest.raises(ValidationError):
        async for _ in cw.stream(tpl, "people", inputs={"company": "a", "bad": True}):
            pass
    process = cw.init(tpl, "people", inputs={"company": "e", "count": 50})
    stream = process.stream()
    first = await stream.__anext__()
    await stream.aclose()
    assert first["name"] == "e-0"
    assert process.status in {"cancelled", "succeeded"}


# ── cancellation and limits ──────────────────────────────────────────────────


async def test_cancel_is_e803(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "slow")
    process.start()
    await asyncio.sleep(0.01)
    process.cancel()
    with pytest.raises(ControlError) as info:
        await process.result()
    assert info.value.code == "E803"
    assert process.status == "cancelled"


async def test_cancel_before_start_stops_at_first_step(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "piped")
    process.cancel()
    with pytest.raises(ControlError) as info:
        await process.run()
    assert info.value.code == "E803"
    assert process.status == "cancelled"


async def test_max_duration_is_e701(cw: Crowley, tpl: Any) -> None:
    with pytest.raises(LimitError) as info:
        await cw.run(tpl, "slow", limits=Limits(max_duration=Duration(0.02)))
    assert info.value.code == "E701"


async def test_outer_cancellation_propagates(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "slow")
    task = asyncio.ensure_future(process.run())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.status == "cancelled"


# ── run-level events ─────────────────────────────────────────────────────────


async def test_run_events(cw: Crowley, tpl: Any) -> None:
    names: list[str] = []
    process = cw.init(tpl, "piped")
    process.add_notifier("run.*", lambda e: names.append(e.name), observe=True)
    process.add_notifier("output.*", lambda e: names.append(e.name), observe=True)
    process.add_notifier("inputs.resolve", lambda e: e.inputs.update(count=1))
    process.add_notifier("output.before_validate", lambda e: e.output.append({"name": "late"}))
    result = await process.run()
    assert [p["name"] for p in result.output] == ["x-0", "late"]
    assert names == ["run.start", "output.before_validate", "output.validated", "run.end"]


async def test_changed_inputs_and_output_are_revalidated(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "piped")
    process.add_notifier("run.start", lambda e: e.inputs.update(count="many"))
    with pytest.raises(ValidationError) as info:
        await process.run()
    assert info.value.code == "E401"
    process = cw.init(tpl, "piped")
    process.add_notifier("run.end", lambda e: setattr(e, "output", "not a list"))
    errors: list[str] = []
    process.add_notifier("run.error", lambda e: errors.append(e.error.code), observe=True)
    with pytest.raises(ValidationError) as info:
        await process.run()
    assert info.value.code == "E405"
    assert errors == ["E405"]


async def test_abort_fails_the_process(cw: Crowley, tpl: Any) -> None:
    process = cw.init(tpl, "piped")
    process.add_notifier("step.before", lambda e: e.abort("no"))
    with pytest.raises(ControlError) as info:
        await process.run()
    assert info.value.code == "E801"
    assert process.status == "failed"


async def test_secrets_are_redacted(cw: Crowley, tpl: Any) -> None:
    scopes: list[Any] = []
    process = cw.init(tpl, "leaky")
    process.add_notifier("step.before", lambda e: scopes.append(dict(e.scope)), observe=True)
    with pytest.raises(ExecutionError) as info:
        await process.run()
    assert "s3cr3t" not in str(info.value)
    assert info.value.message == "token is ***"
    assert scopes[0]["secrets"] == {"API_TOKEN": "***"}


# ── isolation and notifier scopes ────────────────────────────────────────────


async def test_concurrent_processes_are_isolated(cw: Crowley, tpl: Any) -> None:
    global_seen: list[str] = []
    cw.add_notifier("function.after", lambda e: global_seen.append(e.run.operation), observe=True)
    shared = NotifierRegistry("shared")
    shared_seen: list[str] = []
    shared.add_notifier("run.end", lambda e: shared_seen.append(e.run.operation), observe=True)

    people_process = cw.init(tpl, "people", inputs={"company": "p"})
    people_process.add_notifier_registry(shared)
    own_items: list[Any] = []
    people_process.add_notifier("item.emit", lambda e: own_items.append(e.item["name"]))
    people_process.add_notifier(
        "function.after", lambda e: e.replace([{"name": "patched", "rank": 9}]), function="acme.*"
    )

    info_process = cw.init(tpl, "info", inputs={"company": "i"})
    info_process.add_notifier_registry(shared)

    @info_process.on("function.after", function="local.greet")
    def shout(event: Any) -> None:
        event.result = event.result.upper()

    people_result, info_result = await asyncio.gather(people_process.run(), info_process.run())
    assert people_result.output == [{"name": "patched", "rank": 9}]
    assert info_result.output["greeting"] == "HELLO I"
    assert own_items == ["patched"]
    assert sorted(global_seen) == ["info", "people"]
    assert sorted(shared_seen) == ["info", "people"]


async def test_run_shortcut_attaches_notifiers(cw: Crowley, tpl: Any) -> None:
    registry = NotifierRegistry("r")
    registry.add_notifier("function.after", lambda e: e.replace([]))
    assert (await cw.run(tpl, "piped", notifiers=registry)).output == []
    notifier = Notifier(event="function.after", fn=lambda e: e.replace([{"n": 1}]))
    assert (await cw.run(tpl, "piped", notifiers=[notifier])).output == [{"n": 1}]
    with pytest.raises(TypeError):
        await cw.run(tpl, "piped", notifiers=["nope"])  # type: ignore[list-item]
    handle = cw.add_notifier("function.after", lambda e: e.replace(["global"]))
    assert (await cw.run(tpl, "piped")).output == ["global"]
    cw.remove_notifier(handle)
    assert len(cw.notifiers) == 0


async def test_references(cw: Crowley, tmp_path: Path) -> None:
    path = tmp_path / "directory.yml"
    path.write_text(TEMPLATE, encoding="utf-8")
    result = await cw.run(f"{path}#info", inputs={"company": "ref"})
    assert result.output["greeting"] == "hello ref"
    result = await cw.run(str(path), "info", inputs={"company": "ref"})
    assert result.output["greeting"] == "hello ref"
    with pytest.raises(ConfigurationError, match="conflicts"):
        await cw.run(f"{path}#info", "people")


def test_init_with_reference_outside_loop(cw: Crowley, tmp_path: Path) -> None:
    path = tmp_path / "directory.yml"
    path.write_text(TEMPLATE, encoding="utf-8")
    process = cw.init(f"{path}#piped")
    assert process.run_sync().output[0]["name"] == "x-0"


# ── secrets providers ────────────────────────────────────────────────────────


def test_secrets_providers() -> None:
    assert DictSecrets({"A": "1"}).get("A") == "1"
    assert DictSecrets({}).get("A") is None
    env = EnvSecrets(prefix="CW_", environ={"CW_TOKEN": "x"})
    assert env.get("TOKEN") == "x"
    assert env.get("OTHER") is None
    assert EnvSecrets().get("CROWLEY_SURELY_NOT_SET_12345") is None


async def test_env_secrets_provider(tpl: Any) -> None:
    cw = Crowley(functions=[people], secrets=EnvSecrets(environ={"API_TOKEN": "abc"}))
    result = await cw.run(tpl, "info", inputs={"company": "e"})
    assert result.output["token_length"] == 3
