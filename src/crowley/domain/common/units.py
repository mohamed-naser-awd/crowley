"""Durations, byte sizes and rates as written in templates (docs/SPEC.md §1, §6)."""

import re
from dataclasses import dataclass

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)\s*$")
_DURATION_FACTORS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

_BYTESIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(b|kb|mb|gb)\s*$", re.IGNORECASE)
_BYTESIZE_FACTORS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}

_RATE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(s|m|h)\s*$")
_RATE_PERIODS = {"s": 1.0, "m": 60.0, "h": 3600.0}


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True, order=True)
class Duration:
    """A non-negative span of time, stored in seconds."""

    seconds: float

    @classmethod
    def parse(cls, value: object) -> "Duration":
        """Parse ``1.5`` (seconds) or ``"500ms" | "20s" | "2m" | "1h"``."""
        if _is_number(value):
            seconds = float(value)  # type: ignore[arg-type]
        elif isinstance(value, str) and (m := _DURATION.match(value)):
            seconds = float(m.group(1)) * _DURATION_FACTORS[m.group(2)]
        else:
            raise ValueError(f"invalid duration {value!r}; use seconds or e.g. '500ms', '20s'")
        if seconds < 0:
            raise ValueError(f"duration must not be negative: {value!r}")
        return cls(seconds)

    def __str__(self) -> str:
        return f"{self.seconds:g}s"


@dataclass(frozen=True, slots=True, order=True)
class ByteSize:
    """A non-negative size in bytes (binary units: 1KB = 1024 bytes)."""

    bytes: int

    @classmethod
    def parse(cls, value: object) -> "ByteSize":
        """Parse an integer byte count or ``"512KB" | "10MB" | "1GB"``."""
        if isinstance(value, int) and not isinstance(value, bool):
            size = value
        elif isinstance(value, str) and (m := _BYTESIZE.match(value)):
            size = int(float(m.group(1)) * _BYTESIZE_FACTORS[m.group(2).lower()])
        else:
            raise ValueError(f"invalid size {value!r}; use bytes or e.g. '512KB', '10MB'")
        if size < 0:
            raise ValueError(f"size must not be negative: {value!r}")
        return cls(size)


@dataclass(frozen=True, slots=True)
class Rate:
    """``count`` events per ``period_seconds``."""

    count: float
    period_seconds: float

    @classmethod
    def parse(cls, value: object) -> "Rate":
        """Parse ``"5/s" | "100/m" | "1000/h"``."""
        if isinstance(value, str) and (m := _RATE.match(value)):
            count = float(m.group(1))
            if count <= 0:
                raise ValueError(f"rate must be positive: {value!r}")
            return cls(count, _RATE_PERIODS[m.group(2)])
        raise ValueError(f"invalid rate {value!r}; use e.g. '5/s' or '100/m'")

    @property
    def per_second(self) -> float:
        return self.count / self.period_seconds

    def is_stricter_than(self, other: "Rate") -> bool:
        return self.per_second < other.per_second
