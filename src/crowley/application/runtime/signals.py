"""Control flow signals. They travel as return values, never as exceptions (ARCHITECTURE §4.4)."""

from dataclasses import dataclass
from typing import TypeAlias

from crowley.domain.values import Value


@dataclass(frozen=True, slots=True)
class Return:
    value: Value = None


@dataclass(frozen=True, slots=True)
class Break:
    pass


@dataclass(frozen=True, slots=True)
class Continue:
    pass


Signal: TypeAlias = Return | Break | Continue

BREAK = Break()
CONTINUE = Continue()


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What a step hands to the next one: the outgoing ``prev``, or a signal leaving the block."""

    prev: Value = None
    signal: Signal | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class HandlerResult:
    """What a step handler returns to the pipeline.

    ``passthrough`` steps (``set``, ``emit``, ``log``, ``assert``) hand the incoming ``prev`` on
    unchanged (SPEC §8.4).
    """

    value: Value = None
    signal: Signal | None = None
    passthrough: bool = False


PASSTHROUGH = HandlerResult(passthrough=True)
