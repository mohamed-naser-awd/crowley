from datetime import UTC, datetime, timedelta, timezone

import pytest

from crowley.domain.errors import ExecutionError, LimitError
from crowley.infrastructure.regex import RegexLibEngine
from crowley.infrastructure.system import FrozenClock, SeededRandom, SystemClock, SystemRandom


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_named_timezones() -> None:
    berlin = SystemClock().tz("Europe/Berlin")
    assert datetime(2026, 7, 1, tzinfo=berlin).utcoffset() == timedelta(hours=2)
    with pytest.raises(ValueError, match="unknown timezone"):
        SystemClock().tz("Mars/Base")
    with pytest.raises(ValueError, match="unknown timezone"):
        FrozenClock().tz("../etc")


def test_frozen_clock() -> None:
    assert FrozenClock().now() == datetime(2026, 1, 1, tzinfo=UTC)
    shifted = FrozenClock(datetime(2026, 1, 1, 5, tzinfo=timezone(timedelta(hours=5))))
    assert shifted.now() == datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 1, 1))


def test_random_sources() -> None:
    assert SeededRandom(1).uuid4() == SeededRandom(1).uuid4()
    assert SeededRandom(1).uuid4() != SeededRandom(2).uuid4()
    assert len(SystemRandom().uuid4()) == 36


def test_regex_engine_operations() -> None:
    engine = RegexLibEngine()
    assert engine.search(r"\d", "a1")
    assert engine.find(r"(\d)(\d)?", "a1", 2) is None
    assert engine.find(r"x", "abc") is None
    assert engine.find_all(r"\d", "a1b2") == ["1", "2"]
    assert engine.replace(r"(\w)=", "a=1", r"\1:") == "a:1"


def test_regex_engine_errors() -> None:
    engine = RegexLibEngine(timeout=0.01)
    with pytest.raises(ExecutionError):
        engine.search("(", "x")
    with pytest.raises(ExecutionError):
        engine.find("a", "a", "nope")
    with pytest.raises(ExecutionError):
        engine.replace("a", "a", r"\5")
    evil, text = r"(a|aa)+$", "a" * 60 + "!"
    for call in (
        lambda: engine.search(evil, text),
        lambda: engine.find(evil, text),
        lambda: engine.find_all(evil, text),
        lambda: engine.replace(evil, text, "x"),
    ):
        with pytest.raises(LimitError):
            call()
