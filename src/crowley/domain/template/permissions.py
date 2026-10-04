"""``permissions.hosts`` (docs/SPEC.md §4)."""

import ipaddress
import re
from dataclasses import dataclass

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PORTED = re.compile(r"^(.*):(\d{1,5})$")


def _normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".").removeprefix("[").removesuffix("]")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True, kw_only=True)
class HostPattern:
    """A host pattern: ``api.example.com``, ``*.example.com`` (subdomains only),
    ``example.com:8443`` or ``[::1]:8080``.

    A pattern without a port only matches the scheme's default port (``port=None``).
    """

    host: str
    port: int | None = None
    wildcard: bool = False
    source: str = ""

    @classmethod
    def parse(cls, text: str) -> "HostPattern":
        raw = text.strip().lower()
        if not raw:
            raise ValueError("empty host pattern")
        host, port = raw, None
        if raw.startswith("["):  # [IPv6]:port
            end = raw.find("]")
            if end == -1:
                raise ValueError(f"invalid host pattern {text!r}")
            host, rest = raw[1:end], raw[end + 1 :]
            if rest:
                if not rest.startswith(":") or not rest[1:].isdigit():
                    raise ValueError(f"invalid port in host pattern {text!r}")
                port = int(rest[1:])
        elif (m := _PORTED.match(raw)) and raw.count(":") == 1:
            host, port = m.group(1), int(m.group(2))
        if port is not None and not 0 < port < 65536:
            raise ValueError(f"invalid port in host pattern {text!r}")

        if _is_ip(host):
            return cls(host=str(ipaddress.ip_address(host)), port=port, source=text)
        wildcard = host.startswith("*.")
        name = host[2:] if wildcard else host
        if "*" in name:
            raise ValueError(
                f"invalid host pattern {text!r}; '*' is only allowed as a leading '*.'"
            )
        labels = name.rstrip(".").split(".")
        if not all(_LABEL.match(label) for label in labels):
            raise ValueError(f"invalid host name in pattern {text!r}")
        if wildcard and len(labels) < 2:
            raise ValueError(f"host pattern {text!r} is too broad; use at least '*.example.com'")
        return cls(host=".".join(labels), port=port, wildcard=wildcard, source=text)

    def matches(self, host: str, port: int | None = None) -> bool:
        """Does a request to ``host`` (``port=None`` = default port) fall under this pattern?"""
        if port != self.port:
            return False
        candidate = _normalize_host(host)
        if _is_ip(candidate):
            return not self.wildcard and str(ipaddress.ip_address(candidate)) == self.host
        if self.wildcard:
            return candidate.endswith("." + self.host)
        return candidate == self.host

    def __str__(self) -> str:
        return self.source or self.host


@dataclass(frozen=True, slots=True, kw_only=True)
class Permissions:
    hosts: tuple[HostPattern, ...] = ()

    def allows(self, host: str, port: int | None = None) -> bool:
        return any(p.matches(host, port) for p in self.hosts)
