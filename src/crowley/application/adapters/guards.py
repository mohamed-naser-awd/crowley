"""Exchange guards: host permissions with SSRF protection (SPEC §4) and per-host rate limits."""

import ipaddress
import time
from collections.abc import Awaitable, Callable
from typing import Any

from crowley.application.adapters.base import BaseAdapter
from crowley.application.ports import HostResolver
from crowley.domain.adapters import Target, TargetKind
from crowley.domain.common import Rate
from crowley.domain.errors import ExchangeError
from crowley.domain.template import Permissions


def is_blocked_address(address: str) -> bool:
    """Loopback, private, link-local, multicast, unspecified or reserved (SSRF guard)."""
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


class PermissionGuard:
    """Checks every exchange target (and every hop) of ``network`` adapters (E604)."""

    def __init__(self, permissions: Permissions, resolver: HostResolver | None) -> None:
        self._permissions = permissions
        self._resolver = resolver
        self._resolved: dict[str, list[str]] = {}

    async def check(self, adapter: BaseAdapter, uri: str, path: str | None = None) -> None:
        if adapter.target_kind is not TargetKind.NETWORK:
            return
        try:
            target = Target.parse(uri)
        except ValueError as exc:
            raise ExchangeError("E604", f"invalid exchange target: {exc}", path=path) from None
        if target.scheme not in adapter.schemes:
            raise ExchangeError(
                "E604",
                f"adapter {adapter.name!r} does not handle {target.scheme!r} targets ({uri})",
                path=path,
            )
        if not self._permissions.allows(target.host, target.port):
            host = target.host if target.port is None else f"{target.host}:{target.port}"
            raise ExchangeError(
                "E604",
                f"host {host!r} is not permitted",
                path=path,
                value=uri,
                hint=f"add {host!r} to permissions.hosts",
            )
        if adapter.allow_private_networks:
            return
        for address in await self._addresses(target.host, path):
            if is_blocked_address(address):
                raise ExchangeError(
                    "E604",
                    f"host {target.host!r} resolves to a non-public address ({address})",
                    path=path,
                    hint="configure the adapter with allow_private_networks=True to allow it",
                )

    async def _addresses(self, host: str, path: str | None) -> list[str]:
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        cached = self._resolved.get(host)
        if cached is not None:
            return cached
        if self._resolver is None:
            return []
        try:
            addresses = await self._resolver.resolve(host)
        except OSError as exc:
            raise ExchangeError(
                "E602", f"cannot resolve host {host!r}: {exc}", path=path, cause=exc
            ) from exc
        self._resolved[host] = addresses
        return addresses


class RateLimiter:
    """A token bucket per key (host). Waits instead of failing when the bucket is empty."""

    def __init__(
        self,
        rate: Rate | None,
        *,
        sleep: Callable[[float], Awaitable[Any]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate
        self._sleep = sleep
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    async def acquire(self, key: str) -> None:
        rate = self._rate
        if rate is None:
            return
        capacity = max(1.0, rate.count)
        per_second = rate.per_second
        now = self._clock()
        tokens, last = self._buckets.get(key, (capacity, now))
        tokens = min(capacity, tokens + (now - last) * per_second) - 1.0
        self._buckets[key] = (tokens, now)
        if tokens < 0:
            await self._sleep(-tokens / per_second)
