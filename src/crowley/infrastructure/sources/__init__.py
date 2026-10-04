"""Template sources: files on disk and in-memory templates (``DirectorySource`` arrives in M4)."""

import asyncio
from collections.abc import Mapping
from pathlib import Path

from crowley.application.ports import Origin, TemplateText
from crowley.domain.errors import TemplateParseError


class FileSource:
    """Resolves file paths; relative paths resolve against the referring template's directory."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else None

    def can_resolve(self, ref: str) -> bool:
        return (
            ref.endswith((".yml", ".yaml"))
            or ref.startswith(("./", "../", "/"))
            or Path(ref).is_absolute()
        )

    def _path(self, ref: str, relative_to: Origin | None) -> Path:
        path = Path(ref)
        if path.is_absolute():
            return path
        if relative_to is not None and relative_to.file:
            return Path(relative_to.file).parent / path
        return (self.base_dir or Path.cwd()) / path

    async def resolve(self, ref: str, *, relative_to: Origin | None = None) -> TemplateText:
        path = self._path(ref, relative_to).resolve()
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            raise TemplateParseError("E104", f"template file not found: {path}") from None
        except (OSError, UnicodeDecodeError) as exc:
            raise TemplateParseError("E104", f"cannot read template {path}: {exc}") from None
        return TemplateText(text=text, origin=Origin(name=str(path), file=str(path)))


class InMemorySource:
    """Templates held in memory, by name. Useful for tests and generated templates."""

    def __init__(self, templates: Mapping[str, str] | None = None) -> None:
        self.templates = dict(templates or {})

    def add(self, name: str, text: str) -> None:
        self.templates[name] = text

    def can_resolve(self, ref: str) -> bool:
        return ref in self.templates

    async def resolve(self, ref: str, *, relative_to: Origin | None = None) -> TemplateText:
        if ref not in self.templates:
            raise TemplateParseError("E104", f"unknown in-memory template {ref!r}")
        return TemplateText(text=self.templates[ref], origin=Origin(name=ref))


__all__ = ["FileSource", "InMemorySource"]
