"""The per-process event bus: notifier selection, interception and observation (§17.3)."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase

from crowley.application.events.notifiers import Notifier, NotifierSet
from crowley.domain.errors import ControlError, CrowleyError
from crowley.domain.events import Abort, Action, Event, LogEvent


@dataclass(frozen=True, slots=True)
class Interception:
    """What the interceptors of one event decided."""

    action: Action | None = None
    changed: bool = False
    """True if a notifier with ``revalidate=True`` ran, so mutable fields must be revalidated."""


_NOTHING = Interception()

ObserverErrorHook = Callable[[Notifier, BaseException, Event], Awaitable[None] | None]

_Index = tuple[tuple[tuple[Notifier, str], ...], tuple[tuple[Notifier, str], ...]]


class EventBus:
    """Dispatches events over notifier sets, in scope order (global → registries → process).

    Matching notifiers are sorted by priority (highest first); ties keep the scope order and then
    registration order. Interceptors run first and the first returned action wins. Observers then
    receive read-only snapshots.
    """

    def __init__(self, *sets: NotifierSet, strict_observers: bool = False) -> None:
        self._sets = sets
        self._strict_observers = strict_observers
        self._cache: dict[str, tuple[tuple[tuple[int, ...], ...], _Index]] = {}

    # ── selection ──────────────────────────────────────────────────────────────

    def _version(self) -> tuple[tuple[int, ...], ...]:
        return tuple(s.version_key() for s in self._sets)

    def _candidates(self, name: str) -> _Index:
        """Notifiers whose event pattern matches ``name``, cached until any set changes."""
        version = self._version()
        cached = self._cache.get(name)
        if cached is not None and cached[0] == version:
            return cached[1]
        seen: set[int] = set()
        ordered = [
            entry
            for notifier_set in self._sets
            for entry in notifier_set.entries(seen)
            if fnmatchcase(name, entry[0].event)
        ]
        ordered.sort(key=lambda entry: -entry[0].priority)  # stable: keeps scope order on ties
        index: _Index = (
            tuple(e for e in ordered if not e[0].observe),
            tuple(e for e in ordered if e[0].observe),
        )
        self._cache[name] = (version, index)
        return index

    def has_listeners(self, name: str) -> bool:
        interceptors, observers = self._candidates(name)
        return bool(interceptors or observers)

    # ── dispatch ───────────────────────────────────────────────────────────────

    async def intercept(self, event: Event) -> Interception:
        """Run the interceptors of ``event``; the first action returned wins.

        Raises E801 for ``abort`` and E802 when a notifier raises or returns an action the event
        does not allow.
        """
        interceptors, _ = self._candidates(event.name)
        changed = False
        for notifier, scope in interceptors:
            if not notifier.matches(event):
                continue
            try:
                result = notifier.fn(event)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:  # every notifier failure becomes E802
                raise _notifier_error(notifier, scope, event, exc) from exc
            changed = changed or notifier.revalidate
            if result is None:
                continue
            if not isinstance(result, Action):
                raise ControlError(
                    "E802",
                    f"notifier {notifier.label} ({scope}) returned {type(result).__name__} "
                    f"from {event.name}; expected an action or None",
                )
            if type(result) not in event.ALLOWED:
                allowed = ", ".join(sorted(a.__name__.lower() for a in event.ALLOWED)) or "none"
                raise ControlError(
                    "E802",
                    f"notifier {notifier.label} ({scope}) returned "
                    f"{type(result).__name__.lower()} from {event.name}; allowed: {allowed}",
                )
            if isinstance(result, Abort):
                raise ControlError(
                    "E801",
                    f"aborted by notifier {notifier.label} ({scope}) on {event.name}: "
                    f"{result.reason}",
                    path=event.step.path if event.step else None,
                )
            return Interception(result, changed)
        return Interception(None, changed) if changed else _NOTHING

    async def notify(self, event: Event) -> None:
        """Give observers a read-only snapshot of ``event``."""
        _, observers = self._candidates(event.name)
        selected = [(n, s) for n, s in observers if n.matches(event)]
        if not selected:
            return
        snapshot = event.snapshot()
        for notifier, scope in selected:
            try:
                result = notifier.fn(snapshot)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # observers must not break the run
                if self._strict_observers:
                    raise _notifier_error(notifier, scope, event, exc) from exc
                if not isinstance(event, LogEvent):
                    await self.publish(
                        LogEvent(
                            timestamp=event.timestamp,
                            run=event.run,
                            step=event.step,
                            depth=event.depth,
                            level="error",
                            message=f"observer {notifier.label} ({scope}) failed on "
                            f"{event.name}: {type(exc).__name__}: {exc}",
                        )
                    )

    async def publish(self, event: Event) -> Interception:
        """Intercept, then notify observers of the final state."""
        if not self.has_listeners(event.name):
            return _NOTHING
        outcome = await self.intercept(event)
        await self.notify(event)
        return outcome


def _notifier_error(notifier: Notifier, scope: str, event: Event, exc: Exception) -> CrowleyError:
    return ControlError(
        "E802",
        f"notifier {notifier.label} ({scope}) raised on {event.name}: {type(exc).__name__}: {exc}",
        path=event.step.path if event.step else None,
        cause=exc,
    )
