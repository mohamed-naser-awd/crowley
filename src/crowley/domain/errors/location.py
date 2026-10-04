"""Source locations inside template files."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceLocation:
    """A 1-based position in a source file. ``file`` is ``None`` for in-memory templates."""

    file: str | None
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.file or '<memory>'}:{self.line}:{self.column}"
