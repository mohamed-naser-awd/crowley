"""LoadTemplate: source → parse → meta-schema → compile (docs/SPEC.md §15, stages 1-2)."""

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from crowley.application.compiler import Compiler
from crowley.application.ports import (
    Origin,
    PositionMap,
    TemplateParser,
    TemplateSource,
    TemplateText,
)
from crowley.domain.errors import CrowleyError, Diagnostic, TemplateParseError, ValidationReport
from crowley.domain.template import Template


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadResult:
    template: Template | None
    report: ValidationReport
    origin: Origin | None = None
    positions: PositionMap | None = None


class LoadTemplate:
    def __init__(
        self, *, sources: Sequence[TemplateSource], parser: TemplateParser, compiler: Compiler
    ) -> None:
        self._sources = tuple(sources)
        self._parser = parser
        self._compiler = compiler

    async def report(self, ref: str, *, relative_to: Origin | None = None) -> LoadResult:
        """Load and compile ``ref``, collecting problems instead of raising them."""
        try:
            text = await self._resolve(ref, relative_to)
        except CrowleyError as exc:
            return LoadResult(
                template=None, report=ValidationReport(diagnostics=(Diagnostic.from_error(exc),))
            )
        return self.compile_text(text)

    def compile_text(self, text: TemplateText) -> LoadResult:
        try:
            document = self._parser.parse(text.text, text.origin)
        except CrowleyError as exc:
            return LoadResult(
                template=None,
                report=ValidationReport(diagnostics=(Diagnostic.from_error(exc),)),
                origin=text.origin,
            )
        content_hash = hashlib.sha256(text.text.encode("utf-8")).hexdigest()
        result = self._compiler.compile(document, content_hash=content_hash)
        return LoadResult(
            template=result.template,
            report=ValidationReport(diagnostics=result.diagnostics),
            origin=text.origin,
            positions=document.positions,
        )

    async def __call__(self, ref: str, *, relative_to: Origin | None = None) -> Template:
        """Load ``ref``; raises the first error (others are attached as ``related``)."""
        result = await self.report(ref, relative_to=relative_to)
        result.report.raise_if_errors()
        assert result.template is not None
        return result.template

    def load_sync(self, ref: str) -> Template:
        return asyncio.run(self(ref))

    async def _resolve(self, ref: str, relative_to: Origin | None) -> TemplateText:
        for source in self._sources:
            if source.can_resolve(ref):
                return await source.resolve(ref, relative_to=relative_to)
        raise TemplateParseError("E104", f"no template source can resolve {ref!r}")
