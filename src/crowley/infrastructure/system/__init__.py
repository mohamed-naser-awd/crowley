"""System clock and randomness, plus frozen/seeded variants for tests and replays."""

import random
import uuid
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone {name!r}") from None


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def tz(self, name: str) -> tzinfo:
        return _zone(name)


class FrozenClock:
    """Always returns the same instant (default 2026-01-01T00:00:00Z, as in template tests)."""

    def __init__(self, moment: datetime | None = None) -> None:
        moment = moment or datetime(2026, 1, 1, tzinfo=UTC)
        if moment.tzinfo is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        self.moment = moment.astimezone(UTC)

    def now(self) -> datetime:
        return self.moment

    def tz(self, name: str) -> tzinfo:
        return _zone(name)


class SystemRandom:
    def uuid4(self) -> str:
        return str(uuid.uuid4())


class SeededRandom:
    """Deterministic UUIDs for tests."""

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)  # deterministic test data, not security

    def uuid4(self) -> str:
        return str(uuid.UUID(int=self._rng.getrandbits(128), version=4))


__all__ = ["FrozenClock", "SeededRandom", "SystemClock", "SystemRandom"]
