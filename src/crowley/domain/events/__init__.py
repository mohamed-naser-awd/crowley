"""Event types for every catalog entry and notifier actions (docs/SPEC.md §17).

Events are plain mutable dataclasses. A notifier changes an event by assigning to (or editing
in place) one of the event's ``MUTABLE`` fields, and steers the runtime by returning an action
built with ``event.skip()``, ``event.replace()``, ``event.retry()`` or ``event.abort()``.
Each event class lists the actions it accepts in ``ALLOWED`` (§17.1).
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar, Self

from crowley.domain.values import Value

_EMPTY_SCOPE: Mapping[str, Value] = MappingProxyType({})

# ── actions ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class Action:
    """Base class for what a notifier returns to change the flow."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Skip(Action):
    """Do not perform the operation; use ``result`` instead (where the event has one)."""

    result: Value = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Replace(Action):
    """Use ``value`` as the outcome of the operation."""

    value: Value = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Retry(Action):
    """Run the operation again, optionally after ``after`` seconds."""

    after: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Abort(Action):
    """Fail the run with E801."""

    reason: str = "aborted by notifier"


# ── common payload (§17.2) ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class RunInfo:
    id: str
    template_id: str
    template_version: str | None = None
    operation: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StepInfo:
    id: str | None
    kind: str
    path: str
    function: str | None = None


@dataclass(slots=True, kw_only=True)
class Event:
    """Common payload of every event. Subclasses add their own fields."""

    NAME: ClassVar[str] = ""
    MUTABLE: ClassVar[tuple[str, ...]] = ()
    ALLOWED: ClassVar[frozenset[type[Action]]] = frozenset()

    timestamp: datetime | None = None
    run: RunInfo | None = None
    step: StepInfo | None = None
    adapter: str | None = None
    extractor: str | None = None
    iteration: int | None = None
    page: Value = None
    depth: int = 0
    scope: Mapping[str, Value] = field(default_factory=lambda: _EMPTY_SCOPE)
    _frozen: bool = field(default=False, init=False, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.NAME

    def __setattr__(self, key: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(f"{self.name} event is read-only for observers")
        object.__setattr__(self, key, value)

    def snapshot(self) -> Self:
        """A deep, read-only copy (what observers receive)."""
        clone = copy.copy(self)
        object.__setattr__(clone, "_frozen", False)
        for name in self.MUTABLE:
            setattr(clone, name, copy.deepcopy(getattr(self, name)))
        object.__setattr__(clone, "_frozen", True)
        return clone

    # Action builders. Whether an action is allowed is checked by the event bus.
    def skip(self, result: Value = None) -> Skip:
        return Skip(result=result)

    def replace(self, value: Value) -> Replace:
        return Replace(value=value)

    def retry(self, after: float | None = None) -> Retry:
        return Retry(after=after)

    def abort(self, reason: str = "aborted by notifier") -> Abort:
        return Abort(reason=reason)


def _allow(*actions: type[Action]) -> frozenset[type[Action]]:
    return frozenset(actions)


_ABORT = _allow(Abort)
_SKIP_REPLACE_ABORT = _allow(Skip, Replace, Abort)
_RETRY_REPLACE_ABORT = _allow(Retry, Replace, Abort)
_REPLACE_RETRY_ABORT = _allow(Replace, Retry, Abort)

# ── run, template, inputs ──────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class RunStart(Event):
    NAME = "run.start"
    MUTABLE = ("inputs",)
    ALLOWED = _ABORT
    inputs: dict[str, Value] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class RunEnd(Event):
    NAME = "run.end"
    MUTABLE = ("output",)
    output: Value = None


@dataclass(slots=True, kw_only=True)
class RunErrorEvent(Event):
    NAME = "run.error"
    error: BaseException | None = None


@dataclass(slots=True, kw_only=True)
class TemplateLoaded(Event):
    NAME = "template.loaded"
    MUTABLE = ("document",)
    ALLOWED = _ABORT
    document: Value = None


@dataclass(slots=True, kw_only=True)
class TemplateValidated(Event):
    NAME = "template.validated"
    ALLOWED = _ABORT
    template: Any = None


@dataclass(slots=True, kw_only=True)
class InputsResolve(Event):
    NAME = "inputs.resolve"
    MUTABLE = ("inputs",)
    ALLOWED = _ABORT
    inputs: dict[str, Value] = field(default_factory=dict)


# ── steps ──────────────────────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class StepBefore(Event):
    NAME = "step.before"
    MUTABLE = ("prev", "kwargs")
    ALLOWED = _SKIP_REPLACE_ABORT
    prev: Value = None
    kwargs: dict[str, Value] | None = None  # `use` steps: the evaluated `with:` args


@dataclass(slots=True, kw_only=True)
class StepAfter(Event):
    NAME = "step.after"
    MUTABLE = ("result",)
    ALLOWED = _allow(Replace, Abort)
    result: Value = None


@dataclass(slots=True, kw_only=True)
class StepErrorEvent(Event):
    NAME = "step.error"
    ALLOWED = _RETRY_REPLACE_ABORT
    error: BaseException | None = None
    attempt: int = 1


@dataclass(slots=True, kw_only=True)
class StepSkipped(Event):
    NAME = "step.skipped"


# ── functions ──────────────────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class FunctionBefore(Event):
    NAME = "function.before"
    MUTABLE = ("kwargs",)
    ALLOWED = _SKIP_REPLACE_ABORT
    function: str = ""
    kwargs: dict[str, Value] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class FunctionAfter(Event):
    NAME = "function.after"
    MUTABLE = ("result",)
    ALLOWED = _REPLACE_RETRY_ABORT
    function: str = ""
    result: Value = None


@dataclass(slots=True, kw_only=True)
class FunctionErrorEvent(Event):
    NAME = "function.error"
    ALLOWED = _RETRY_REPLACE_ABORT
    function: str = ""
    error: BaseException | None = None
    attempt: int = 1


# ── conditions and loops ───────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class ConditionEvaluated(Event):
    NAME = "condition.evaluated"
    MUTABLE = ("value",)
    ALLOWED = _ABORT
    value: Value = None


@dataclass(slots=True, kw_only=True)
class LoopStart(Event):
    NAME = "loop.start"
    MUTABLE = ("items",)
    ALLOWED = _ABORT
    items: list[Value] | None = None  # `for_each` only


@dataclass(slots=True, kw_only=True)
class LoopIterationBefore(Event):
    NAME = "loop.iteration.before"
    MUTABLE = ("binding",)
    ALLOWED = _allow(Skip, Abort)
    binding: Value = None


@dataclass(slots=True, kw_only=True)
class LoopIterationAfter(Event):
    NAME = "loop.iteration.after"
    MUTABLE = ("value", "stop")
    ALLOWED = _ABORT
    value: Value = None
    stop: bool = False


@dataclass(slots=True, kw_only=True)
class LoopEnd(Event):
    NAME = "loop.end"
    MUTABLE = ("result",)
    ALLOWED = _ABORT
    result: list[Value] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class PageBefore(Event):
    NAME = "page.before"
    MUTABLE = ("page",)
    ALLOWED = _allow(Skip, Abort)


@dataclass(slots=True, kw_only=True)
class PageAfter(Event):
    NAME = "page.after"
    MUTABLE = ("value", "stop")
    ALLOWED = _ABORT
    value: Value = None
    stop: bool = False


# ── exchanges and extraction (fired by the M3 pipelines) ───────────────────────


@dataclass(slots=True, kw_only=True)
class ExchangeBefore(Event):
    NAME = "exchange.before"
    MUTABLE = ("exchange",)
    ALLOWED = _SKIP_REPLACE_ABORT
    exchange: Any = None


@dataclass(slots=True, kw_only=True)
class ExchangeAfter(Event):
    NAME = "exchange.after"
    MUTABLE = ("result",)
    ALLOWED = _REPLACE_RETRY_ABORT
    exchange: Any = None
    result: Any = None


@dataclass(slots=True, kw_only=True)
class ExchangeErrorEvent(Event):
    NAME = "exchange.error"
    ALLOWED = _RETRY_REPLACE_ABORT
    exchange: Any = None
    error: BaseException | None = None
    attempt: int = 1


@dataclass(slots=True, kw_only=True)
class ExchangeRetry(Event):
    NAME = "exchange.retry"
    MUTABLE = ("delay",)
    ALLOWED = _ABORT
    exchange: Any = None
    delay: float = 0.0
    attempt: int = 1


@dataclass(slots=True, kw_only=True)
class SelectorFallback(Event):
    NAME = "selector.fallback"
    ALLOWED = _ABORT
    field: str = ""
    index: int = 0
    query: str = ""


# ── data ───────────────────────────────────────────────────────────────────────


@dataclass(slots=True, kw_only=True)
class VariableSet(Event):
    NAME = "variable.set"
    MUTABLE = ("value",)
    ALLOWED = _ABORT
    variable: str = ""
    value: Value = None


@dataclass(slots=True, kw_only=True)
class ItemEmit(Event):
    NAME = "item.emit"
    MUTABLE = ("item",)
    ALLOWED = _allow(Skip, Abort)
    item: Value = None


@dataclass(slots=True, kw_only=True)
class OutputBeforeValidate(Event):
    NAME = "output.before_validate"
    MUTABLE = ("output",)
    ALLOWED = _ABORT
    output: Value = None


@dataclass(slots=True, kw_only=True)
class OutputValidated(Event):
    NAME = "output.validated"
    output: Value = None


@dataclass(slots=True, kw_only=True)
class LogEvent(Event):
    NAME = "log"
    level: str = "info"
    message: str = ""


@dataclass(slots=True, kw_only=True)
class ExpressionEvaluated(Event):
    NAME = "expression.evaluated"
    MUTABLE = ("value",)
    ALLOWED = _ABORT
    source: str = ""
    value: Value = None


@dataclass(slots=True, kw_only=True)
class CustomEvent(Event):
    """A plugin-declared event emitted through ``ctx.emit_event``, e.g. ``mycorp.token.refresh``."""

    MUTABLE = ("data",)
    ALLOWED = _SKIP_REPLACE_ABORT
    event_name: str = ""
    data: dict[str, Value] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.event_name


CATALOG: Mapping[str, type[Event]] = MappingProxyType(
    {
        cls.NAME: cls
        for cls in (
            RunStart,
            RunEnd,
            RunErrorEvent,
            TemplateLoaded,
            TemplateValidated,
            InputsResolve,
            StepBefore,
            StepAfter,
            StepErrorEvent,
            StepSkipped,
            FunctionBefore,
            FunctionAfter,
            FunctionErrorEvent,
            ConditionEvaluated,
            LoopStart,
            LoopIterationBefore,
            LoopIterationAfter,
            LoopEnd,
            PageBefore,
            PageAfter,
            ExchangeBefore,
            ExchangeAfter,
            ExchangeErrorEvent,
            ExchangeRetry,
            SelectorFallback,
            VariableSet,
            ItemEmit,
            OutputBeforeValidate,
            OutputValidated,
            LogEvent,
            ExpressionEvaluated,
        )
    }
)
"""Every built-in event name (§17.1) mapped to its class."""

__all__ = [
    "CATALOG",
    "Abort",
    "Action",
    "ConditionEvaluated",
    "CustomEvent",
    "Event",
    "ExchangeAfter",
    "ExchangeBefore",
    "ExchangeErrorEvent",
    "ExchangeRetry",
    "ExpressionEvaluated",
    "FunctionAfter",
    "FunctionBefore",
    "FunctionErrorEvent",
    "InputsResolve",
    "ItemEmit",
    "LogEvent",
    "LoopEnd",
    "LoopIterationAfter",
    "LoopIterationBefore",
    "LoopStart",
    "OutputBeforeValidate",
    "OutputValidated",
    "PageAfter",
    "PageBefore",
    "Replace",
    "Retry",
    "RunEnd",
    "RunErrorEvent",
    "RunInfo",
    "RunStart",
    "SelectorFallback",
    "Skip",
    "StepAfter",
    "StepBefore",
    "StepErrorEvent",
    "StepInfo",
    "StepSkipped",
    "TemplateLoaded",
    "TemplateValidated",
    "VariableSet",
]
