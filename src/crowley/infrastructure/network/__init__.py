"""Host resolution for the SSRF guard (``getaddrinfo``)."""

import asyncio
import socket
from collections.abc import Mapping, Sequence


class SystemHostResolver:
    """Resolves host names with the operating system's resolver."""

    async def resolve(self, host: str) -> list[str]:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({str(info[4][0]) for info in infos})


class StaticHostResolver:
    """A fixed host → addresses table (tests and offline runs). Unknown hosts fail."""

    def __init__(self, table: Mapping[str, Sequence[str]]) -> None:
        self._table = {host.lower(): list(addresses) for host, addresses in table.items()}

    async def resolve(self, host: str) -> list[str]:
        try:
            return list(self._table[host.lower()])
        except KeyError:
            raise OSError(f"cannot resolve {host!r}") from None


__all__ = ["StaticHostResolver", "SystemHostResolver"]
