"""Resource limits (docs/SPEC.md §6)."""

from dataclasses import dataclass, fields, replace
from typing import Any

from crowley.domain.common import ByteSize, Duration, Rate


@dataclass(frozen=True, slots=True, kw_only=True)
class Limits:
    """Limits from one level (SDK, template or operation). ``None`` means "not set here"."""

    max_requests: int | None = None
    max_duration: Duration | None = None
    max_items: int | None = None
    max_depth: int | None = None
    max_loop_iterations: int | None = None
    max_concurrency: int | None = None
    max_response_bytes: ByteSize | None = None
    max_retries_per_step: int | None = None
    rate_per_host: Rate | None = None

    @staticmethod
    def strictest(*levels: "Limits") -> "Limits":
        """Combine levels: for every limit, the strictest value that any level sets."""
        combined: dict[str, Any] = {}
        for f in fields(Limits):
            values = [
                getattr(level, f.name) for level in levels if getattr(level, f.name) is not None
            ]
            if not values:
                combined[f.name] = None
            elif f.name == "rate_per_host":
                combined[f.name] = min(values, key=lambda r: r.per_second)
            else:
                combined[f.name] = min(values)
        return Limits(**combined)

    def resolved(self) -> "Limits":
        """Fill every unset limit with its default (SPEC §6)."""
        return replace(
            self,
            **{
                f.name: getattr(DEFAULT_LIMITS, f.name)
                for f in fields(Limits)
                if getattr(self, f.name) is None
            },
        )


DEFAULT_LIMITS = Limits(
    max_requests=1000,
    max_duration=Duration(30 * 60.0),
    max_items=100_000,
    max_depth=16,
    max_loop_iterations=10_000,
    max_concurrency=16,
    max_response_bytes=ByteSize(10 * 1024**2),
    max_retries_per_step=5,
    rate_per_host=Rate(10, 1.0),
)
