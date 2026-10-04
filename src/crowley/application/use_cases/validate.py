"""ValidateTemplate: stages 1-3 of docs/SPEC.md §15, collecting every diagnostic."""

from crowley.application.compiler.validator import StaticValidator
from crowley.application.ports import Origin, TemplateText
from crowley.application.registry import Registry
from crowley.application.use_cases.load import LoadResult, LoadTemplate
from crowley.domain.errors import ValidationReport
from crowley.domain.template import Template


def _sort_key(d: object) -> tuple[str, int, int]:
    location = getattr(d, "location", None)
    if location is None:
        return ("", 0, 0)
    return (location.file or "", location.line, location.column)


class ValidateTemplate:
    """Load, compile and statically validate templates. Results are cached by
    ``(content hash, registry fingerprint)``, so re-validating an unchanged template is free."""

    def __init__(
        self, *, loader: LoadTemplate, validator: StaticValidator, registry: Registry
    ) -> None:
        self._loader = loader
        self._validator = validator
        self._registry = registry
        self._cache: dict[tuple[str, str], LoadResult] = {}

    async def __call__(self, ref: str, *, relative_to: Origin | None = None) -> LoadResult:
        return self._finish(await self._loader.report(ref, relative_to=relative_to))

    def text(self, text: TemplateText) -> LoadResult:
        return self._finish(self._loader.compile_text(text))

    async def load(self, ref: str, *, relative_to: Origin | None = None) -> Template:
        """A fully validated template; raises the first error (the rest attached as ``related``)."""
        result = await self(ref, relative_to=relative_to)
        result.report.raise_if_errors()
        assert result.template is not None
        return result.template

    def _finish(self, result: LoadResult) -> LoadResult:
        template = result.template
        if template is None:
            return result
        key = (template.content_hash, self._registry.fingerprint())
        cached = self._cache.get(key)
        if cached is not None and cached.origin == result.origin:
            return cached
        static = self._validator.validate(template, result.positions)
        diagnostics = sorted((*result.report.diagnostics, *static), key=_sort_key)
        report = ValidationReport(diagnostics=tuple(diagnostics))
        final = LoadResult(
            template=template, report=report, origin=result.origin, positions=result.positions
        )
        self._cache[key] = final
        return final
