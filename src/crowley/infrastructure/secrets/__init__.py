"""Secrets providers: dict and environment variables."""

import os
from collections.abc import Mapping


class DictSecrets:
    """Secrets from a mapping (tests, or values your application already holds)."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str) -> str | None:
        return self._values.get(name)


class EnvSecrets:
    """Secrets from environment variables. ``EnvSecrets(prefix="CROWLEY_")`` reads ``CROWLEY_X``."""

    def __init__(self, prefix: str = "", environ: Mapping[str, str] | None = None) -> None:
        self._prefix = prefix
        self._environ = environ

    def get(self, name: str) -> str | None:
        environ = os.environ if self._environ is None else self._environ
        return environ.get(self._prefix + name)


__all__ = ["DictSecrets", "EnvSecrets"]
