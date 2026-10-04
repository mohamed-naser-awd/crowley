"""Collects emitted items and feeds ``stream()`` consumers (docs/SPEC.md §14)."""

import asyncio

from crowley.domain.values import Value

_DONE = object()


class OutputCollector:
    """Every emitted item, in emit order. Optionally mirrors items into an async queue."""

    def __init__(self, *, streaming: bool = False) -> None:
        self.items: list[Value] = []
        self._queue: asyncio.Queue[object] | None = asyncio.Queue() if streaming else None

    def add(self, item: Value) -> None:
        self.items.append(item)
        if self._queue is not None:
            self._queue.put_nowait(item)

    def close(self) -> None:
        """Signal the end of the stream (success or failure)."""
        if self._queue is not None:
            self._queue.put_nowait(_DONE)

    async def next(self) -> tuple[bool, Value]:
        """The next streamed item as ``(True, item)``, or ``(False, None)`` once closed."""
        if self._queue is None:
            raise RuntimeError("this collector does not stream")
        item = await self._queue.get()
        if item is _DONE:
            return False, None
        return True, item  # type: ignore[return-value]

    def __len__(self) -> int:
        return len(self.items)
