"""The exchange pipeline (SPEC §13.3) exercised through a fake ``echo`` adapter."""

from typing import Any

import pytest

from crowley import Crowley, Limits
from crowley.application.adapters import BaseAdapter
from crowley.domain.common import ByteSize
from crowley.domain.errors import (
    ConfigurationError,
    ExchangeError,
    LimitError,
    ValidationError,
)
from crowley.infrastructure.network import StaticHostResolver
from tests.adapters.echo import EchoAdapter, IncompatibleEcho, OtherEcho

TEMPLATE = """\
crowley: 1
id: acme/echo
version: 1.0.0
name: Echo
description: Adapter pipeline tests.
permissions:
  hosts: [api.example.com, "*.example.com", "10.0.0.9"]
requires: ["adapter:echo@^1", "echo.*@^1"]
defaults:
  echo:
    headers: { x-layer: defaults, x-defaults: "yes" }
operations:
  send:
    description: One exchange.
    inputs:
      request: { type: object, default: { url: "echo://api.example.com/x" } }
    steps:
      - id: sent
        use: echo.send
        with: { request: "${{ inputs.request }}" }
    output:
      schema: {}
  twice:
    description: Two exchanges share the process session.
    steps:
      - use: echo.send
        with: { request: { url: "echo://api.example.com/a" } }
      - use: echo.send
        with: { request: { url: "echo://api.example.com/b" } }
    output:
      schema: {}
  isolated:
    description: Nested isolated session.
    steps:
      - use: echo.isolated
        with: { seed: s1 }
        do:
          - use: echo.send
            with: { request: { url: "echo://api.example.com/in" } }
    output:
      schema: {}
  handle:
    description: Adapter handle.
    inputs:
      adapter: { type: string, default: echo }
    steps:
      - use: echo.raw
        with: { adapter: "${{ inputs.adapter }}" }
    output:
      schema: {}
"""

RESOLVER = StaticHostResolver(
    {
        "api.example.com": ["93.184.216.34"],
        "other.example.com": ["93.184.216.35"],
        "internal.example.com": ["10.0.0.5"],
        "mapped.example.com": ["::ffff:127.0.0.1"],
    }
)


def make(adapter: EchoAdapter | None = None, **options: Any) -> tuple[Crowley, Any, EchoAdapter]:
    adapter = adapter or EchoAdapter()
    cw = Crowley(adapters=[adapter], resolver=RESOLVER, **options)
    return cw, cw.load_text(TEMPLATE, name="echo.yml"), adapter


async def send(request: dict[str, Any], **options: Any) -> Any:
    cw, tpl, _ = make(**options)
    return (await cw.run(tpl, "send", inputs={"request": request})).output


# ── the happy path ───────────────────────────────────────────────────────────


async def test_exchange_merges_layers_and_manages_sessions() -> None:
    cw, tpl, adapter = make()
    result = await cw.run(tpl, "twice")
    assert result.output["url"] == "echo://api.example.com/b"
    assert result.output["headers"] == {
        "x-config": "config",
        "x-layer": "defaults",
        "x-defaults": "yes",
    }
    assert [s["id"] for s in adapter.opened] == [0]  # one lazy session per process
    assert adapter.closed == [0]  # closed when the run ends
    assert result.stats["requests"] == 2
    assert result.stats["bytes_in"] == 20
    second = await cw.run(tpl, "twice")
    assert second.output["session"] == 1  # never shared between processes


async def test_args_override_defaults() -> None:
    output = await send({"url": "echo://api.example.com/x", "headers": {"x-layer": "args"}})
    assert output["headers"]["x-layer"] == "args"


async def test_isolated_session_and_seed() -> None:
    cw, tpl, adapter = make()
    result = await cw.run(tpl, "isolated")
    assert result.output == {
        "url": "echo://api.example.com/in",
        "payload": None,
        "headers": {"x-config": "config", "x-layer": "defaults", "x-defaults": "yes"},
        "session": 0,
        "seed": {"seed": "s1"},
    }
    assert adapter.closed == [0]


async def test_adapter_handle_and_unknown_adapter() -> None:
    cw, tpl, _ = make()
    result = await cw.run(tpl, "handle")
    assert result.output == {"name": "echo", "version": "1.0.0", "session": {"id": 0, "seed": {}}}
    with pytest.raises(ExchangeError) as info:
        await cw.run(tpl, "handle", inputs={"adapter": "nope"})
    assert info.value.code == "E607"


# ── notifiers ────────────────────────────────────────────────────────────────


async def test_exchange_before_changes_request_and_target() -> None:
    cw, tpl, adapter = make()

    def before(event: Any) -> None:
        event.exchange.request["payload"] = "patched"
        event.exchange.target = "echo://other.example.com/moved"

    cw.add_notifier("exchange.before", before, adapter="echo")
    output = (await cw.run(tpl, "send")).output
    assert output["payload"] == "patched"
    assert output["url"] == "echo://other.example.com/moved"
    assert adapter.sent[0].request["url"] == "echo://other.example.com/moved"


async def test_changed_request_is_revalidated() -> None:
    cw, tpl, _ = make()
    cw.add_notifier("exchange.before", lambda e: e.exchange.request.update(unknown=1))
    with pytest.raises(ValidationError) as info:
        await cw.run(tpl, "send")
    assert info.value.code == "E403"


async def test_skip_and_replace_answer_without_sending() -> None:
    cw, tpl, adapter = make()
    handle = cw.add_notifier("exchange.before", lambda e: e.replace({"url": "synthetic"}))
    assert (await cw.run(tpl, "send")).output == {"url": "synthetic"}
    cw.remove_notifier(handle)
    cw.add_notifier("exchange.before", lambda e: e.skip({"url": "skipped"}))
    assert (await cw.run(tpl, "send")).output == {"url": "skipped"}
    assert adapter.sent == []


async def test_exchange_after_replace_retry_and_revalidation() -> None:
    cw, tpl, adapter = make()
    handle = cw.add_notifier(
        "exchange.after", lambda e: e.retry() if len(adapter.sent) < 2 else None
    )
    cw.add_notifier("exchange.after", lambda e: e.replace({"url": "after"}))
    result = await cw.run(tpl, "send")
    assert result.output == {"url": "after"}
    assert len(adapter.sent) == 2
    assert result.stats["retries"] == 1
    cw.remove_notifier(handle)
    cw.notifiers.clear()
    cw.add_notifier("exchange.after", lambda e: e.result.response.pop("url") and None)
    with pytest.raises(ValidationError) as info:
        await cw.run(tpl, "send")
    assert info.value.code == "E407"


async def test_invalid_replacement_is_e407() -> None:
    cw, tpl, _ = make()
    cw.add_notifier("exchange.after", lambda e: e.replace({"no": "url"}))
    with pytest.raises(ValidationError):
        await cw.run(tpl, "send")


# ── permissions and SSRF ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("echo://evil.org/x", "not permitted"),
        ("echo://api.example.com:8443/x", "'api.example.com:8443' is not permitted"),
        ("http://api.example.com/x", "does not handle 'http'"),
        ("not a uri", "invalid exchange target"),
        ("echo://internal.example.com/x", r"non-public address \(10\.0\.0\.5\)"),
        ("echo://mapped.example.com/x", "non-public address"),
        ("echo://10.0.0.9/x", r"non-public address \(10\.0\.0\.9\)"),
    ],
)
async def test_permission_denials_are_e604(url: str, message: str) -> None:
    with pytest.raises(ExchangeError, match=message) as info:
        await send({"url": url})
    assert info.value.code == "E604"


async def test_private_networks_can_be_allowed() -> None:
    adapter = EchoAdapter(allow_private_networks=True)
    output = await send({"url": "echo://internal.example.com/x"}, adapter=adapter)
    assert output["url"] == "echo://internal.example.com/x"


async def test_unresolvable_host_is_e602() -> None:
    with pytest.raises(ExchangeError, match="cannot resolve") as info:
        await send({"url": "echo://unknown.example.com/x"})
    assert info.value.code == "E602"


async def test_every_hop_is_checked() -> None:
    ok = await send({"url": "echo://api.example.com/x", "hops": ["echo://other.example.com/y"]})
    assert ok["url"] == "echo://api.example.com/x"
    with pytest.raises(ExchangeError, match=r"evil\.org") as info:
        await send({"url": "echo://api.example.com/x", "hops": ["echo://evil.org/y"]})
    assert info.value.code == "E604"


async def test_permission_errors_cannot_be_replaced_by_notifiers() -> None:
    cw, tpl, _ = make()
    cw.add_notifier("exchange.error", lambda e: e.replace({"url": "sneaky"}))
    with pytest.raises(ExchangeError) as info:
        await cw.run(tpl, "send", inputs={"request": {"url": "echo://evil.org/x"}})
    assert info.value.code == "E604"


# ── limits ───────────────────────────────────────────────────────────────────


async def test_max_requests_is_e701() -> None:
    cw, tpl, _ = make(limits=Limits(max_requests=1))
    with pytest.raises(LimitError, match="max_requests"):
        await cw.run(tpl, "twice")


async def test_max_response_bytes_is_e606() -> None:
    cw, tpl, _ = make(limits=Limits(max_response_bytes=ByteSize(100)))
    request = {"url": "echo://api.example.com/x", "size": 101}
    with pytest.raises(ExchangeError) as info:
        await cw.run(tpl, "send", inputs={"request": request})
    assert info.value.code == "E606"


# ── errors and retries ───────────────────────────────────────────────────────


async def test_transport_failures_are_e602() -> None:
    cw, tpl, adapter = make()
    with pytest.raises(ExchangeError) as info:
        await cw.run(
            tpl,
            "send",
            inputs={"request": {"url": "echo://api.example.com/x", "fail": "transport"}},
        )
    assert info.value.code == "E602"
    assert "ConnectionError: connection reset" in info.value.message
    assert adapter.closed == [0]  # sessions are closed on failure too


async def test_adapter_retry_policy_fires_exchange_retry() -> None:
    adapter = EchoAdapter(retries=2)
    cw, tpl, _ = make(adapter)
    delays: list[float] = []
    cw.add_notifier("exchange.retry", lambda e: delays.append(e.delay))
    request = {"url": "echo://api.example.com/x", "fail": "E601"}
    with pytest.raises(ExchangeError) as info:
        await cw.run(tpl, "send", inputs={"request": request})
    assert info.value.code == "E601"
    assert len(adapter.sent) == 3
    assert delays == [0.0, 0.0]


async def test_exchange_error_retry_and_replace() -> None:
    cw, tpl, adapter = make()
    attempts: list[int] = []

    def on_error(event: Any) -> Any:
        attempts.append(event.attempt)
        return event.retry() if event.attempt < 2 else event.replace({"url": "recovered"})

    cw.add_notifier("exchange.error", on_error, adapter="echo")
    request = {"url": "echo://api.example.com/x", "fail": "E603"}
    result = await cw.run(tpl, "send", inputs={"request": request})
    assert result.output == {"url": "recovered"}
    assert attempts == [1, 2]
    assert len(adapter.sent) == 2


async def test_invalid_adapter_response_is_e407() -> None:
    with pytest.raises(ValidationError) as info:
        await send({"url": "echo://api.example.com/x", "bad_response": True})
    assert info.value.code == "E407"


async def test_on_error_catches_exchange_errors() -> None:
    cw, tpl, _ = make()
    text = TEMPLATE.replace(
        "      - id: sent\n        use: echo.send\n",
        "      - id: sent\n        use: echo.send\n"
        "        on_error: { default: { url: caught } }\n",
    )
    tpl = cw.load_text(text, name="echo.yml")
    request = {"url": "echo://api.example.com/x", "fail": "E601"}
    assert (await cw.run(tpl, "send", inputs={"request": request})).output == {"url": "caught"}


# ── per-process overrides ────────────────────────────────────────────────────


async def test_process_override_replaces_adapter_for_one_process() -> None:
    cw, tpl, registered = make()
    other = OtherEcho()
    result = await cw.init(tpl, "send", adapters={"echo": other}).run()
    assert result.output["url"] == "echo://api.example.com/x"
    assert len(other.sent) == 1
    assert registered.sent == []


def test_incompatible_or_unknown_overrides_are_e906() -> None:
    cw, tpl, _ = make()
    with pytest.raises(ConfigurationError, match="not compatible") as info:
        cw.init(tpl, "send", adapters={"echo": IncompatibleEcho()})
    assert info.value.code == "E906"
    with pytest.raises(ConfigurationError, match="unknown adapter"):
        cw.init(tpl, "send", adapters={"ws": EchoAdapter()})


class _Renamed(EchoAdapter):
    name = "echo2"


def test_override_with_another_name_is_e906() -> None:
    cw, tpl, _ = make()
    with pytest.raises(ConfigurationError, match="cannot replace"):
        cw.init(tpl, "send", adapters={"echo": _Renamed()})


async def test_base_adapter_defaults() -> None:
    class Minimal(BaseAdapter):
        name = "minimal"
        version = "1.0.0"
        schemes = ("x",)

    adapter = Minimal()
    assert await adapter.open(None) is None  # type: ignore[arg-type]
    await adapter.close(None)
    assert adapter.target_field() is None
    assert adapter.target_of({"url": "x://a"}) == ""
    assert adapter.with_target({"a": 1}, "x://b") == {"a": 1}
    assert list(adapter.functions()) == []
    assert adapter.retry_delay(None, None, None, 1) is None  # type: ignore[arg-type]
    from crowley.domain.adapters import Exchange, ExchangeResult

    with pytest.raises(ExchangeError) as info:
        await adapter.send(None, Exchange(adapter="minimal", target="", request={}))
    assert info.value.code == "E607"
    exchange = Exchange(adapter="minimal", target="x://a", request={"q": 1})
    result = ExchangeResult(response={"ok": True}, meta={"bytes_in": 3})
    data = adapter.serialize(exchange, result)
    again, back = adapter.deserialize(data)
    assert (again.target, again.request, back.response, back.meta) == (
        "x://a",
        {"q": 1},
        {"ok": True},
        {"bytes_in": 3},
    )
