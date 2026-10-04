"""Semantic versions and version ranges (``^1``, ``~1.2``, ``>=1.0,<2``, ``1.x``, ``*``)."""

import re
from dataclasses import dataclass
from functools import total_ordering

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_PARTIAL = re.compile(r"^(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?$")
_COMPARATOR = re.compile(r"^(>=|<=|>|<|=|\^|~)?\s*(.+)$")


@total_ordering
@dataclass(frozen=True, slots=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> "SemVer":
        m = _SEMVER.match(text.strip())
        if not m:
            raise ValueError(f"invalid semantic version {text!r}")
        pre = tuple(m.group(4).split(".")) if m.group(4) else ()
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)

    def _key(self) -> tuple[int, int, int, int, tuple[tuple[int, int, str], ...]]:
        # A release sorts after its prereleases; numeric identifiers sort before alphanumeric.
        pre = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.prerelease)
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1, pre)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{'.'.join(self.prerelease)}" if self.prerelease else base


@dataclass(frozen=True, slots=True)
class _Comparator:
    op: str  # one of >=, >, <=, <, =
    version: SemVer

    def matches(self, v: SemVer) -> bool:
        match self.op:
            case ">=":
                return v >= self.version
            case ">":
                return v > self.version
            case "<=":
                return v <= self.version
            case "<":
                return v < self.version
            case _:
                return v == self.version


def _partial(text: str) -> tuple[int | None, int | None, int | None]:
    """Parse ``1``, ``1.2``, ``1.x``, ``1.2.3`` into components (``None`` = wildcard/missing)."""
    m = _PARTIAL.match(text)
    if not m:
        raise ValueError(f"invalid version {text!r}")
    parts: list[int | None] = []
    for group in m.groups():
        parts.append(None if group is None or group in "xX*" else int(group))
    # Anything after a wildcard is a wildcard too.
    for i in range(3):
        if parts[i] is None:
            parts[i:] = [None] * (3 - i)
            break
    return parts[0], parts[1], parts[2]


def _expand(op: str, text: str) -> list[_Comparator]:
    """Turn one ``<op><version>`` token into plain comparators."""
    lo: SemVer
    major: int | None
    minor: int | None
    patch: int | None
    if _SEMVER.match(text):
        lo = SemVer.parse(text)
        major, minor, patch = lo.major, lo.minor, lo.patch
    else:
        major, minor, patch = _partial(text)

    def v(a: int, b: int = 0, c: int = 0) -> SemVer:
        return SemVer(a, b, c)

    if major is None:  # "*", "x": any version (">*" / "<*" match nothing)
        return [] if op in ("", "=", ">=", "<=", "^", "~") else [_Comparator("<", v(0))]
    if not _SEMVER.match(text):
        lo = v(major, minor or 0, patch or 0)

    if op == "^":
        if major > 0 or minor is None:
            hi = v(major + 1)
        elif minor > 0 or patch is None:
            hi = v(0, minor + 1)
        else:
            hi = v(0, 0, (patch or 0) + 1)
        return [_Comparator(">=", lo), _Comparator("<", hi)]
    if op == "~":
        hi = v(major + 1) if minor is None else v(major, minor + 1)
        return [_Comparator(">=", lo), _Comparator("<", hi)]
    # Upper bound of a partial version: 1 → <2.0.0, 1.2 → <1.3.0
    if minor is None:
        upper = v(major + 1)
    elif patch is None:
        upper = v(major, minor + 1)
    else:
        upper = None
    match op:
        case "" | "=":
            return (
                [_Comparator("=", lo)]
                if upper is None
                else [
                    _Comparator(">=", lo),
                    _Comparator("<", upper),
                ]
            )
        case ">=":
            return [_Comparator(">=", lo)]
        case "<":
            return [_Comparator("<", lo)]
        case ">":
            return [_Comparator(">", lo) if upper is None else _Comparator(">=", upper)]
        case _:  # "<="
            return [_Comparator("<=", lo) if upper is None else _Comparator("<", upper)]


@dataclass(frozen=True, slots=True)
class SemVerRange:
    """A conjunction of comparators. ``SemVerRange.parse("^1").contains(SemVer.parse("1.4.0"))``."""

    source: str
    comparators: tuple[_Comparator, ...]

    @classmethod
    def parse(cls, text: str) -> "SemVerRange":
        source = text.strip()
        if not source:
            raise ValueError("empty version range")
        comparators: list[_Comparator] = []
        for token in re.split(r"[,\s]+", source):
            if not token:
                continue
            m = _COMPARATOR.match(token)
            if not m:  # pragma: no cover - the pattern accepts any non-empty token
                raise ValueError(f"invalid version range {text!r}")
            try:
                comparators.extend(_expand(m.group(1) or "", m.group(2)))
            except ValueError as exc:
                raise ValueError(f"invalid version range {text!r}: {exc}") from None
        return cls(source, tuple(comparators))

    def contains(self, version: SemVer | str) -> bool:
        v = SemVer.parse(version) if isinstance(version, str) else version
        return all(c.matches(v) for c in self.comparators)

    def __str__(self) -> str:
        return self.source
