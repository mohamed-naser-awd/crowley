"""Diagnostics collected by ``crowley validate`` (docs/SPEC.md §15, §20)."""

from dataclasses import dataclass, field

from crowley.domain.errors.codes import Severity, lookup
from crowley.domain.errors.errors import CrowleyError
from crowley.domain.errors.location import SourceLocation


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    code: str
    message: str
    path: str | None = None
    location: SourceLocation | None = None
    hint: str | None = None

    @property
    def severity(self) -> Severity:
        return lookup(self.code).severity

    @classmethod
    def from_error(cls, error: CrowleyError) -> "Diagnostic":
        return cls(
            code=error.code,
            message=error.message,
            path=error.path,
            location=error.location,
            hint=error.hint,
        )

    def to_error(self) -> CrowleyError:
        return CrowleyError.from_code(
            self.code, self.message, path=self.path, location=self.location, hint=self.hint
        )

    def __str__(self) -> str:
        return str(self.to_error()) if self.severity is Severity.ERROR else self._warning_text()

    def _warning_text(self) -> str:
        head = self.code
        if self.path:
            head += f" at {self.path}"
        if self.location:
            head += f" ({self.location})"
        return f"{head}: {self.message}" + (f" — {self.hint}" if self.hint else "")


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationReport:
    """All diagnostics for one template. ``ok`` means there are no errors."""

    diagnostics: tuple[Diagnostic, ...] = field(default=())

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        """Raise the first error, with the remaining errors attached as ``related``."""
        errors = self.errors
        if errors:
            first = errors[0].to_error()
            first.related = tuple(d.to_error() for d in errors[1:])
            raise first
