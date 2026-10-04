"""Regex engine provider (``regex`` package) with a per-call timeout."""

from functools import lru_cache
from typing import Any

import regex

from crowley.domain.errors import ExecutionError, LimitError
from crowley.domain.services import RegexGroup

DEFAULT_TIMEOUT_SECONDS = 0.1


@lru_cache(maxsize=512)
def _compile(pattern: str) -> Any:
    try:
        return regex.compile(pattern)
    except regex.error as exc:
        raise ExecutionError("E501", f"invalid regex {pattern!r}: {exc}", value=pattern) from None


class RegexLibEngine:
    """``RegexEngine`` on the ``regex`` package; each call is limited to ``timeout`` seconds."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    def _timed_out(self, pattern: str) -> LimitError:
        return LimitError(
            "E703",
            f"regex {pattern!r} took longer than {self.timeout * 1000:g} ms",
            value=pattern,
            hint="simplify the pattern or avoid nested quantifiers",
        )

    def search(self, pattern: str, text: str) -> bool:
        try:
            return _compile(pattern).search(text, timeout=self.timeout) is not None
        except TimeoutError:
            raise self._timed_out(pattern) from None

    def find(self, pattern: str, text: str, group: int | str = 0) -> RegexGroup:
        try:
            match = _compile(pattern).search(text, timeout=self.timeout)
        except TimeoutError:
            raise self._timed_out(pattern) from None
        if match is None:
            return None
        try:
            found = match.group(group)
        except IndexError:
            raise ExecutionError(
                "E501", f"regex {pattern!r} has no group {group!r}", value=group
            ) from None
        return found if found is None else str(found)

    def find_all(self, pattern: str, text: str) -> list[str | list[RegexGroup]]:
        compiled = _compile(pattern)
        try:
            matches = list(compiled.finditer(text, timeout=self.timeout))
        except TimeoutError:
            raise self._timed_out(pattern) from None
        if compiled.groups == 0:
            return [m.group(0) for m in matches]
        return [list(m.groups()) for m in matches]

    def replace(self, pattern: str, text: str, replacement: str) -> str:
        try:
            return str(_compile(pattern).sub(replacement, text, timeout=self.timeout))
        except TimeoutError:
            raise self._timed_out(pattern) from None
        except (regex.error, IndexError) as exc:
            raise ExecutionError("E501", f"invalid replacement {replacement!r}: {exc}") from None


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "RegexLibEngine"]
