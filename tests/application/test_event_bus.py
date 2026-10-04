import asyncio
from typing import Any

import pytest

from crowley.application.events import EventBus, NotifierRegistry, NotifierSet
from crowley.domain.errors import ControlError
from crowley.domain.events import (
    Action,
    ExchangeBefore,
    FunctionAfter,
    LogEvent,
    Replace,
    RunInfo,
    StepAfter,
    StepBefore,
    StepInfo,
)

RUN = RunInfo(id="r1", template_id="acme/site", operation="people")


def step_after(step_id: str = "rows", function: str | None = "http.get") -> StepAfter:
    return StepAfter(
        run=RUN, step=StepInfo(id=step_id, kind="use", path="steps[0]", function=function)
    )


def recorder(log: list[str], label: str, action: Action | None = None) -> Any:
    def fn(event: Any) -> Action | None:
        log.append(label)
        return action

    return fn


async def test_scope_order_and_priority() -> None:
    log: list[str] = []
    glob, process = NotifierSet("global"), NotifierSet("process")
    registry = NotifierRegistry("bundle")
    glob.add_notifier("step.after", recorder(log, "global"))
    process.add_notifier("step.after", recorder(log, "process"))
    registry.add_notifier("step.after", recorder(log, "registry"))
    process.add_notifier_registry(registry)
    process.add_notifier("step.after", recorder(log, "urgent"), priority=5)
    bus = EventBus(glob, process)
    outcome = await bus.publish(step_after())
    assert log == ["urgent", "global", "registry", "process"]
    assert outcome.action is None
    assert outcome.changed


async def test_first_action_wins() -> None:
    log: list[str] = []
    glob = NotifierSet("global")
    glob.add_notifier("step.after", recorder(log, "a", Replace(value=1)))
    glob.add_notifier("step.after", recorder(log, "b", Replace(value=2)))
    outcome = await EventBus(glob).intercept(step_after())
    assert outcome.action == Replace(value=1)
    assert log == ["a"]


async def test_async_notifiers_are_awaited() -> None:
    glob = NotifierSet("global")

    async def later(event: StepAfter) -> Action:
        await asyncio.sleep(0)
        return event.replace("async")

    glob.add_notifier("step.*", later)
    outcome = await EventBus(glob).intercept(step_after())
    assert outcome.action == Replace(value="async")


@pytest.mark.parametrize(
    ("filters", "selected"),
    [
        ({}, True),
        ({"step": "rows"}, True),
        ({"step": "ro*"}, True),
        ({"step": "other"}, False),
        ({"function": "http.*"}, True),
        ({"function": "local.*"}, False),
        ({"operation": "people"}, True),
        ({"template": "acme/*"}, True),
        ({"template": "other/*"}, False),
        ({"adapter": "http"}, False),
        ({"step": "rows", "function": "local.*"}, False),
    ],
)
async def test_filters(filters: dict[str, str], selected: bool) -> None:
    log: list[str] = []
    glob = NotifierSet("global")
    glob.add_notifier("step.after", recorder(log, "x"), **filters)
    await EventBus(glob).publish(step_after())
    assert bool(log) is selected


async def test_function_filter_uses_event_function() -> None:
    log: list[str] = []
    glob = NotifierSet("global")
    glob.add_notifier("function.after", recorder(log, "x"), function="local.fetch")
    await EventBus(glob).publish(FunctionAfter(function="local.fetch"))
    assert log == ["x"]


async def test_adapter_filter_and_event_globs() -> None:
    log: list[str] = []
    glob = NotifierSet("global")
    glob.add_notifier("exchange.*", recorder(log, "exchange"), adapter="http")
    glob.add_notifier("*", recorder(log, "all"))
    bus = EventBus(glob)
    await bus.publish(ExchangeBefore(adapter="http"))
    await bus.publish(ExchangeBefore(adapter="ws"))
    await bus.publish(step_after())
    assert log == ["exchange", "all", "all", "all"]


async def test_changes_apply_from_next_event_and_cache_invalidates() -> None:
    log: list[str] = []
    glob, process = NotifierSet("global"), NotifierSet("process")
    registry = NotifierRegistry("late")
    process.add_notifier_registry(registry)
    bus = EventBus(glob, process)
    await bus.publish(step_after())
    registry.add_notifier("step.after", recorder(log, "late"))  # by reference
    await bus.publish(step_after())
    handle = glob.add_notifier("step.after", recorder(log, "global"))
    await bus.publish(step_after())
    glob.remove_notifier(handle)
    await bus.publish(step_after())
    assert log == ["late", "global", "late", "late"]


async def test_registry_counted_once_and_nested() -> None:
    log: list[str] = []
    inner = NotifierRegistry("inner")
    inner.add_notifier("step.after", recorder(log, "inner"))
    outer = NotifierRegistry("outer")
    outer.include(inner)
    outer.include(inner)
    glob, process = NotifierSet("global"), NotifierSet("process")
    glob.add_notifier_registry(outer)
    process.add_notifier_registry(inner)
    process.add_notifier_registry(inner)
    inner.include(outer)  # a cycle is harmless
    await EventBus(glob, process).publish(step_after())
    assert log == ["inner"]
    assert len(process) == 1


def test_registry_cannot_include_itself_and_foreign_handles() -> None:
    registry = NotifierRegistry("self")
    with pytest.raises(ValueError, match="itself"):
        registry.include(registry)
    a, b = NotifierSet("a"), NotifierSet("b")
    handle = a.add_notifier("log", lambda e: None)
    with pytest.raises(ValueError, match="belongs to"):
        b.remove_notifier(handle)
    with pytest.raises(TypeError):
        a.add_notifier("log", "not callable")  # type: ignore[arg-type]


async def test_remove_registry_and_clear() -> None:
    log: list[str] = []
    glob = NotifierSet("global")
    registry = NotifierRegistry("r")
    registry.add_notifier("step.after", recorder(log, "r"))
    handle = glob.add_notifier_registry(registry)
    glob.remove_notifier(handle)
    glob.remove_notifier(handle)  # already removed: ignored
    await EventBus(glob).publish(step_after())
    glob.add_notifier("step.after", recorder(log, "g"))
    glob.clear()
    await EventBus(glob).publish(step_after())
    assert log == []


async def test_decorator_form() -> None:
    glob = NotifierSet("global")

    @glob.on("step.before", step="rows")
    def bump(event: StepBefore) -> None:
        event.prev = (event.prev or 0) + 1

    event = StepBefore(step=StepInfo(id="rows", kind="use", path="p"), prev=1)
    await EventBus(glob).publish(event)
    assert event.prev == 2
    assert bump.__name__ == "bump"


async def test_abort_is_e801() -> None:
    glob = NotifierSet("global")
    glob.add_notifier("step.after", lambda e: e.abort("stop now"), name="guard")
    with pytest.raises(ControlError) as info:
        await EventBus(glob).intercept(step_after())
    assert info.value.code == "E801"
    assert "guard" in info.value.message
    assert "stop now" in info.value.message
    assert info.value.path == "steps[0]"


async def test_disallowed_action_and_bad_return_are_e802() -> None:
    glob = NotifierSet("global")
    glob.add_notifier("step.after", lambda e: e.skip())
    with pytest.raises(ControlError) as info:
        await EventBus(glob).intercept(step_after())
    assert info.value.code == "E802"
    assert "allowed: abort, replace" in info.value.message
    glob.clear()
    glob.add_notifier("step.after", lambda e: 42)
    with pytest.raises(ControlError) as info:
        await EventBus(glob).intercept(step_after())
    assert "returned int" in info.value.message


async def test_raising_interceptor_is_e802_naming_scope() -> None:
    def broken(event: Any) -> None:
        raise RuntimeError("boom")

    process = NotifierSet("process")
    process.add_notifier("step.after", broken)
    with pytest.raises(ControlError) as info:
        await EventBus(process).intercept(step_after())
    assert info.value.code == "E802"
    assert "broken" in info.value.message
    assert "(process)" in info.value.message
    assert isinstance(info.value.cause, RuntimeError)


async def test_observers_get_read_only_snapshots_after_interceptors() -> None:
    seen: list[Any] = []
    glob = NotifierSet("global")

    def observer(event: StepAfter) -> Action:
        seen.append(event.result)
        event.result.append("observer")  # edits a copy only
        with pytest.raises(AttributeError):
            event.result = None
        return event.replace("ignored")

    glob.add_notifier("step.after", observer, observe=True, priority=100)
    glob.add_notifier("step.after", lambda e: e.result.append("interceptor"))
    event = step_after()
    event.result = []
    outcome = await EventBus(glob).publish(event)
    assert seen == [["interceptor", "observer"]]
    assert event.result == ["interceptor"]
    assert outcome.action is None


async def test_observer_errors_become_log_events_unless_strict() -> None:
    logs: list[LogEvent] = []

    def broken(event: Any) -> None:
        raise ValueError("bad observer")

    glob = NotifierSet("global")
    glob.add_notifier("step.after", broken, observe=True)
    glob.add_notifier("log", logs.append, observe=True)
    glob.add_notifier("log", broken, observe=True)  # a failing log observer is dropped
    await EventBus(glob).publish(step_after())
    assert [e.level for e in logs] == ["error"]
    assert "bad observer" in logs[0].message
    with pytest.raises(ControlError) as info:
        await EventBus(glob, strict_observers=True).publish(step_after())
    assert info.value.code == "E802"


async def test_revalidate_false_does_not_mark_changed() -> None:
    glob = NotifierSet("global")
    glob.add_notifier("step.after", lambda e: None, revalidate=False)
    outcome = await EventBus(glob).intercept(step_after())
    assert not outcome.changed
    assert not EventBus(NotifierSet("x")).has_listeners("step.after")
