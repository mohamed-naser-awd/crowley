"""Notifiers, NotifierSet scopes and reusable NotifierRegistry bundles (docs/SPEC.md §17.3)."""

import itertools
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, TypeVar

from crowley.domain.events import Action, Event

NotifierFn = Callable[[Any], Action | Awaitable[Action | None] | None]
F = TypeVar("F", bound=NotifierFn)

_FILTERS = ("step", "function", "operation", "template", "adapter", "extractor")
_ids = itertools.count(1)


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class Notifier:
    """A callable attached to an event name (or glob), with optional filters."""

    event: str
    fn: NotifierFn
    step: str | None = None
    function: str | None = None
    operation: str | None = None
    template: str | None = None
    adapter: str | None = None
    extractor: str | None = None
    priority: int = 0
    observe: bool = False
    revalidate: bool = True
    name: str | None = None

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        module = getattr(self.fn, "__module__", None)
        qualname = getattr(self.fn, "__qualname__", None) or repr(self.fn)
        return f"{module}.{qualname}" if module else qualname

    def matches(self, event: Event) -> bool:
        """Whether this notifier is selected for ``event``: name glob and every filter match."""
        if not fnmatchcase(event.name, self.event):
            return False
        return all(
            _match(getattr(self, key), _subject(event, key))
            for key in _FILTERS
            if getattr(self, key) is not None
        )


def _subject(event: Event, key: str) -> str | None:
    if key == "step":
        return event.step.id if event.step else None
    if key == "function":
        own = getattr(event, "function", None)
        if isinstance(own, str) and own:
            return own
        return event.step.function if event.step else None
    if key == "operation":
        return event.run.operation if event.run else None
    if key == "template":
        return event.run.template_id if event.run else None
    value = getattr(event, key)
    return value if isinstance(value, str) else None


def _match(pattern: str | None, subject: str | None) -> bool:
    return pattern is not None and subject is not None and fnmatchcase(subject, pattern)


@dataclass(frozen=True, slots=True, eq=False)
class NotifierHandle:
    """Returned by ``add_notifier``/``add_notifier_registry``; pass it to ``remove_notifier``."""

    owner: "NotifierSet"
    target: "Notifier | NotifierRegistry"


class NotifierSet:
    """An ordered, versioned collection of notifiers and attached registries.

    The ``Crowley`` instance (global scope), every ``NotifierRegistry`` and every ``Process``
    own one. Registries are held by reference, so later changes to them apply everywhere they
    are attached, starting from the next event.
    """

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self._notifiers: list[Notifier] = []
        self._registries: list[NotifierRegistry] = []
        self._version = next(_ids)

    def add_notifier(
        self,
        event: str,
        fn: NotifierFn,
        *,
        step: str | None = None,
        function: str | None = None,
        operation: str | None = None,
        template: str | None = None,
        adapter: str | None = None,
        extractor: str | None = None,
        priority: int = 0,
        observe: bool = False,
        revalidate: bool = True,
        name: str | None = None,
    ) -> NotifierHandle:
        if not callable(fn):
            raise TypeError(f"notifier for {event!r} is not callable: {fn!r}")
        notifier = Notifier(
            event=event,
            fn=fn,
            step=step,
            function=function,
            operation=operation,
            template=template,
            adapter=adapter,
            extractor=extractor,
            priority=priority,
            observe=observe,
            revalidate=revalidate,
            name=name,
        )
        self._notifiers.append(notifier)
        self._touch()
        return NotifierHandle(self, notifier)

    def add(self, notifier: Notifier) -> NotifierHandle:
        """Attach an already-built :class:`Notifier`."""
        self._notifiers.append(notifier)
        self._touch()
        return NotifierHandle(self, notifier)

    def on(self, event: str, **filters: Any) -> Callable[[F], F]:
        """Decorator form of :meth:`add_notifier`."""

        def decorate(fn: F) -> F:
            self.add_notifier(event, fn, **filters)
            return fn

        return decorate

    def add_notifier_registry(self, registry: "NotifierRegistry") -> NotifierHandle:
        """Attach a registry by reference. Attaching the same registry twice is a no-op."""
        if registry is self:
            raise ValueError("a notifier registry cannot include itself")
        if registry not in self._registries:
            self._registries.append(registry)
            self._touch()
        return NotifierHandle(self, registry)

    def remove_notifier(self, handle: NotifierHandle) -> None:
        """Remove one notifier or a whole registry. Unknown handles are ignored."""
        if handle.owner is not self:
            raise ValueError(f"handle belongs to {handle.owner.scope!r}, not {self.scope!r}")
        target = handle.target
        if isinstance(target, NotifierRegistry):
            if target in self._registries:
                self._registries.remove(target)
                self._touch()
        elif target in self._notifiers:
            self._notifiers.remove(target)
            self._touch()

    def clear(self) -> None:
        self._notifiers.clear()
        self._registries.clear()
        self._touch()

    def __len__(self) -> int:
        return len(self._notifiers) + len(self._registries)

    def _touch(self) -> None:
        self._version = next(_ids)

    def version_key(self, _seen: frozenset[int] = frozenset()) -> tuple[int, ...]:
        """Versions of this set and every reachable registry; changes when anything changes."""
        seen = _seen | {id(self)}
        key: tuple[int, ...] = (self._version,)
        for registry in self._registries:
            if id(registry) not in seen:
                key += registry.version_key(seen)
        return key

    def entries(self, _seen: set[int] | None = None) -> Iterator[tuple[Notifier, str]]:
        """Notifiers with their scope label: attached registries first, then this set's own.

        Each registry is visited once, even when it is reachable along several paths.
        """
        seen = set() if _seen is None else _seen
        seen.add(id(self))
        for registry in self._registries:
            if id(registry) not in seen:
                yield from registry.entries(seen)
        for notifier in self._notifiers:
            yield notifier, self.scope


class NotifierRegistry(NotifierSet):
    """A reusable, named bundle of notifiers that can be attached globally or to processes."""

    def __init__(self, name: str) -> None:
        super().__init__(f"registry:{name}")
        self.name = name

    def include(self, other: "NotifierRegistry") -> NotifierHandle:
        """Nest another registry inside this one."""
        return self.add_notifier_registry(other)

    def __repr__(self) -> str:
        return f"NotifierRegistry({self.name!r})"


class NotifierScope:
    """Mixin giving ``Crowley`` and ``Process`` the notifier methods of their ``NotifierSet``."""

    _notifier_set: NotifierSet

    @property
    def notifiers(self) -> NotifierSet:
        return self._notifier_set

    def add_notifier(self, event: str, fn: NotifierFn, **filters: Any) -> NotifierHandle:
        return self._notifier_set.add_notifier(event, fn, **filters)

    def on(self, event: str, **filters: Any) -> Callable[[F], F]:
        return self._notifier_set.on(event, **filters)

    def add_notifier_registry(self, registry: NotifierRegistry) -> NotifierHandle:
        return self._notifier_set.add_notifier_registry(registry)

    def remove_notifier(self, handle: NotifierHandle) -> None:
        self._notifier_set.remove_notifier(handle)
