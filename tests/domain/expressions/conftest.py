from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from crowley.domain.expressions import EvalEnv, evaluate, parse_expression
from crowley.domain.values import Value
from crowley.infrastructure.regex import RegexLibEngine
from crowley.infrastructure.system import FrozenClock, SeededRandom

FROZEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def env() -> EvalEnv:
    return EvalEnv(clock=FrozenClock(FROZEN), random=SeededRandom(7), regex=RegexLibEngine())


Run = Callable[..., Value]


@pytest.fixture
def run(env: EvalEnv) -> Run:
    """``run("1 + x", x=2)`` parses and evaluates with the given scope."""

    def _run(source: str, /, **scope: Value) -> Value:
        return evaluate(parse_expression(source), scope, env)

    return _run
