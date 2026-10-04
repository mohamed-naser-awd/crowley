"""Protocol-agnostic I/O model (docs/SPEC.md §13.1). The domain never names HTTP."""

from crowley.domain.adapters.exchange import (
    DEFAULT_PORTS,
    Exchange,
    ExchangeResult,
    HopCheck,
    RetryPolicy,
    Target,
    deep_merge,
    media_type_essence,
    media_type_rank,
)
from crowley.domain.adapters.spec import AdapterSpec, TargetKind

__all__ = [
    "DEFAULT_PORTS",
    "AdapterSpec",
    "Exchange",
    "ExchangeResult",
    "HopCheck",
    "RetryPolicy",
    "Target",
    "TargetKind",
    "deep_merge",
    "media_type_essence",
    "media_type_rank",
]
