"""Domain-service protocols: Clock, RandomSource, RegexEngine.

The domain declares what it needs; ``crowley.infrastructure.system`` and
``crowley.infrastructure.regex`` provide the implementations.
"""

from datetime import datetime, tzinfo
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as a timezone-aware UTC datetime."""
        ...

    def tz(self, name: str) -> tzinfo:
        """Timezone by IANA name (``Europe/Berlin``). Raises ``ValueError`` if unknown."""
        ...


class RandomSource(Protocol):
    def uuid4(self) -> str:
        """A random UUID (version 4) in canonical text form."""
        ...


RegexGroup = str | None


class RegexEngine(Protocol):
    """Regex operations with a per-call time budget.

    Implementations raise ``ExecutionError('E501')`` for invalid patterns and
    ``LimitError('E703')`` when the time budget is exceeded.
    """

    def search(self, pattern: str, text: str) -> bool:
        """Whether ``pattern`` matches anywhere in ``text``."""
        ...

    def find(self, pattern: str, text: str, group: int | str = 0) -> RegexGroup:
        """The given group of the first match, or ``None``."""
        ...

    def find_all(self, pattern: str, text: str) -> list[str | list[RegexGroup]]:
        """All matches: whole-match strings, or lists of groups when the pattern has groups."""
        ...

    def replace(self, pattern: str, text: str, replacement: str) -> str:
        """Replace every match; ``\\1`` / ``\\g<name>`` refer to groups."""
        ...


__all__ = ["Clock", "RandomSource", "RegexEngine", "RegexGroup"]
