"""Locations inside a template document, e.g. ``operations.get_people.steps[0].with.url``."""

import re
from dataclasses import dataclass

_SIMPLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

PathPart = str | int


@dataclass(frozen=True, slots=True)
class TemplatePath:
    """An immutable path of mapping keys (``str``) and list indexes (``int``)."""

    parts: tuple[PathPart, ...] = ()

    @classmethod
    def of(cls, *parts: PathPart) -> "TemplatePath":
        return cls(tuple(parts))

    def key(self, key: str) -> "TemplatePath":
        return TemplatePath((*self.parts, key))

    def index(self, index: int) -> "TemplatePath":
        return TemplatePath((*self.parts, index))

    def child(self, part: PathPart) -> "TemplatePath":
        return TemplatePath((*self.parts, part))

    @property
    def parent(self) -> "TemplatePath":
        return TemplatePath(self.parts[:-1])

    @property
    def is_root(self) -> bool:
        return not self.parts

    def __str__(self) -> str:
        out: list[str] = []
        for part in self.parts:
            if isinstance(part, int):
                out.append(f"[{part}]")
            elif _SIMPLE_KEY.match(part):
                out.append(f".{part}" if out else part)
            else:
                escaped = part.replace("\\", "\\\\").replace('"', '\\"')
                out.append(f'["{escaped}"]')
        return "".join(out) or "<root>"
