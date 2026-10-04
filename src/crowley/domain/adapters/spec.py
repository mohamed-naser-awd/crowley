"""Adapter specs: what an adapter declares about itself (docs/SPEC.md §13.1)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from crowley.domain.common import SemVer, is_identifier
from crowley.domain.errors import ConfigurationError


class TargetKind(Enum):
    """Which permission checks apply to an adapter's exchange targets (SPEC §4, §13.3)."""

    NETWORK = "network"
    LOCAL = "local"
    NONE = "none"


@dataclass(frozen=True, slots=True, kw_only=True)
class AdapterSpec:
    name: str
    version: SemVer
    schemes: tuple[str, ...] = ()
    target_kind: TargetKind = TargetKind.NETWORK
    config_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    defaults_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    request_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    response_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    builtin: bool = False

    def __post_init__(self) -> None:
        if not is_identifier(self.name):
            raise ConfigurationError("E903", f"invalid adapter name {self.name!r}")
        if self.target_kind is TargetKind.LOCAL:
            raise ConfigurationError(
                "E903",
                f"adapter {self.name!r}: target_kind 'local' is reserved for a later version",
            )
        if self.target_kind is TargetKind.NETWORK and not self.schemes:
            raise ConfigurationError(
                "E903", f"network adapter {self.name!r} must declare its URI schemes"
            )


__all__ = ["AdapterSpec", "TargetKind"]
