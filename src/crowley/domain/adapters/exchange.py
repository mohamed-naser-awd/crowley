"""Exchanges: one request to a target through an adapter, and its result (SPEC §13.1, §13.3).

The domain never names HTTP: requests and responses are adapter-specific objects, described to
the core only by each adapter's JSON Schemas.
"""

import copy
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from crowley.domain.common import Duration

DEFAULT_PORTS: Mapping[str, int] = {"http": 80, "https": 443, "ws": 80, "wss": 443}

HopCheck = Callable[[str], Awaitable[None]]


@dataclass(slots=True, kw_only=True)
class Exchange:
    """What an adapter is asked to do. Notifiers may change ``target`` and ``request``."""

    adapter: str
    target: str
    request: dict[str, Any]
    options: dict[str, Any] = field(default_factory=dict)
    check_hop: HopCheck | None = field(default=None, repr=False, compare=False)
    """Adapters that follow redirect-like hops MUST await this before contacting each hop."""
    max_bytes: int | None = None
    """Adapters SHOULD stop reading a response larger than this (the pipeline enforces E606)."""


@dataclass(slots=True, kw_only=True)
class ExchangeResult:
    """What came back. ``meta`` carries ``elapsed_ms``, ``bytes_in`` and ``hops``."""

    response: Any
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def bytes_in(self) -> int:
        value = self.meta.get("bytes_in", 0)
        return value if isinstance(value, int) else 0

    @property
    def hops(self) -> list[str]:
        hops = self.meta.get("hops", [])
        return [h for h in hops if isinstance(h, str)] if isinstance(hops, list) else []


@dataclass(frozen=True, slots=True, kw_only=True)
class Target:
    """A parsed exchange target. ``port`` is ``None`` for the scheme's default port."""

    uri: str
    scheme: str
    host: str
    port: int | None

    @classmethod
    def parse(cls, uri: str) -> "Target":
        """Parse an absolute URI; raises ``ValueError`` if it has no scheme or host."""
        parts = urlsplit(uri)
        scheme = parts.scheme.lower()
        try:
            port = parts.port
        except ValueError as exc:
            raise ValueError(f"invalid port in {uri!r}") from exc
        host = (parts.hostname or "").lower()
        if not scheme or not host:
            raise ValueError(f"{uri!r} is not an absolute URI with a host")
        if port is not None and DEFAULT_PORTS.get(scheme) == port:
            port = None
        return cls(uri=uri, scheme=scheme, host=host, port=port)


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    """How often and how long to wait before re-sending an exchange."""

    times: int
    backoff: Literal["fixed", "exponential"] = "exponential"
    delay: Duration = Duration(1.0)
    max_delay: Duration = Duration(30.0)

    def delay_for(self, attempt: int) -> float:
        """Delay before retry number ``attempt`` (1-based)."""
        if self.backoff == "fixed":
            return self.delay.seconds
        return min(self.delay.seconds * 2.0 ** (attempt - 1), self.max_delay.seconds)


def deep_merge(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """Merge layers, lowest priority first: objects merge recursively, other values replace."""
    merged: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            current = merged.get(key)
            if isinstance(current, dict) and isinstance(value, Mapping):
                merged[key] = deep_merge(current, value)
            else:
                merged[key] = copy.deepcopy(dict(value) if isinstance(value, Mapping) else value)
    return merged


# ── media types (SPEC §13.10) ──────────────────────────────────────────────────


def media_type_essence(value: str | None) -> str | None:
    """``"Text/HTML; charset=utf-8"`` → ``"text/html"``."""
    if not value:
        return None
    essence = value.split(";", 1)[0].strip().lower()
    return essence or None


def media_type_rank(pattern: str, media_type: str) -> int:
    """How well ``pattern`` matches: 3 exact, 2 ``+suffix`` / ``type/*``, 1 ``*``, 0 no match.

    Patterns: ``text/html``, ``*+json`` (any ``+json`` structured syntax), ``text/*``, ``*``.
    """
    pattern = pattern.strip().lower()
    if pattern == media_type:
        return 3
    if pattern.startswith("*+"):
        return 2 if media_type.endswith(pattern[1:]) else 0
    if pattern.endswith("/*"):
        return 2 if media_type.startswith(pattern[:-1]) else 0
    if pattern == "*":
        return 1
    return 0
