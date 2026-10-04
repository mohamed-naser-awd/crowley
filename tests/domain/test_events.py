import pytest

from crowley.domain.events import (
    CATALOG,
    Abort,
    CustomEvent,
    Event,
    FunctionBefore,
    Replace,
    Retry,
    Skip,
    StepAfter,
    StepBefore,
)

SPEC_EVENTS = {
    "run.start", "run.end", "run.error", "template.loaded", "template.validated",
    "inputs.resolve", "step.before", "step.after", "step.error", "step.skipped",
    "function.before", "function.after", "function.error", "condition.evaluated",
    "loop.start", "loop.iteration.before", "loop.iteration.after", "loop.end",
    "page.before", "page.after", "exchange.before", "exchange.after", "exchange.error",
    "exchange.retry", "selector.fallback", "variable.set", "item.emit",
    "output.before_validate", "output.validated", "log", "expression.evaluated",
}  # fmt: skip


def test_catalog_matches_spec() -> None:
    assert set(CATALOG) == SPEC_EVENTS
    for name, cls in CATALOG.items():
        assert cls().name == name
        for attr in cls.MUTABLE:
            assert hasattr(cls(), attr), (name, attr)
        assert Abort in cls.ALLOWED or not cls.ALLOWED or name == "step.skipped"


def test_allowed_actions_follow_spec_table() -> None:
    assert set(StepBefore.ALLOWED) == {Skip, Replace, Abort}
    assert set(StepAfter.ALLOWED) == {Replace, Abort}
    assert set(FunctionBefore.ALLOWED) == {Skip, Replace, Abort}
    assert set(CATALOG["function.after"].ALLOWED) == {Replace, Retry, Abort}
    assert set(CATALOG["step.error"].ALLOWED) == {Retry, Replace, Abort}
    assert set(CATALOG["run.end"].ALLOWED) == set()
    assert set(CATALOG["item.emit"].ALLOWED) == {Skip, Abort}


def test_action_builders() -> None:
    event = StepBefore()
    assert event.skip(3) == Skip(result=3)
    assert event.replace([1]) == Replace(value=[1])
    assert event.retry(0.5) == Retry(after=0.5)
    assert event.abort("no") == Abort(reason="no")


def test_snapshot_is_deep_and_read_only() -> None:
    event = StepBefore(prev={"a": [1]}, kwargs={"url": "x"})
    copy = event.snapshot()
    with pytest.raises(AttributeError):
        copy.prev = 1
    assert isinstance(copy.kwargs, dict)
    copy.kwargs["url"] = "changed"
    assert event.kwargs == {"url": "x"}
    event.prev = 2  # the original stays mutable
    assert event.prev == 2


def test_custom_event_name() -> None:
    event = CustomEvent(event_name="mycorp.token.refresh", data={"n": 1})
    assert event.name == "mycorp.token.refresh"
    assert isinstance(event, Event)
