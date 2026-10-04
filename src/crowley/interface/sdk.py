"""The ``Crowley`` facade: the composition root (docs/ARCHITECTURE.md §6).

M1 scope: loading and validating templates, and registering plugins. Running operations
(``init``/``run``/``stream``, notifiers) arrives with the runtime in M2.
"""

import asyncio
from collections.abc import Coroutine, Iterable
from typing import Any, TypeVar

from crowley.adapters.http import HttpAdapter
from crowley.application.adapters import BaseAdapter
from crowley.application.compiler import Compiler, StaticValidator
from crowley.application.extractors import BaseExtractor
from crowley.application.ports import Origin, TemplateSource, TemplateText
from crowley.application.registry import Plugin, Registry
from crowley.application.use_cases import LoadTemplate, ValidateTemplate
from crowley.domain.errors import ConfigurationError, ValidationReport
from crowley.domain.expressions import HelperSpec
from crowley.domain.functions import FunctionSpec
from crowley.domain.template import Template
from crowley.extractors.html import HtmlExtractor
from crowley.extractors.json import JsonExtractor
from crowley.extractors.text import TextExtractor
from crowley.extractors.xml import XmlExtractor
from crowley.infrastructure.schema import JsonSchemaValidator
from crowley.infrastructure.sources import FileSource
from crowley.infrastructure.yaml import RuamelTemplateParser
from crowley.stdlib import StdlibPlugin

T = TypeVar("T")


class BuiltinPlugin:
    """The built-in adapter, extractors and stdlib, registered like any other plugin."""

    def register(self, registry: Registry) -> None:
        registry.add_adapter(HttpAdapter())
        for extractor in (HtmlExtractor(), XmlExtractor(), JsonExtractor(), TextExtractor()):
            registry.add_extractor(extractor)
        StdlibPlugin().register(registry)


class Crowley:
    def __init__(
        self,
        *,
        sources: Iterable[TemplateSource] = (),
        functions: Iterable[FunctionSpec] = (),
        adapters: Iterable[BaseAdapter] = (),
        extractors: Iterable[BaseExtractor] = (),
        plugins: Iterable[Plugin] = (),
        helpers: Iterable[HelperSpec] = (),
    ) -> None:
        self.registry = Registry()
        BuiltinPlugin().register(self.registry)
        for adapter in adapters:
            self.register_adapter(
                adapter, replace=adapter.name if adapter.name in self.registry.adapters else None
            )
        for extractor in extractors:
            self.register_extractor(
                extractor,
                replace=extractor.name if extractor.name in self.registry.extractors else None,
            )
        for plugin in plugins:
            self.registry.install(plugin)
        for spec in functions:
            self.register_function(spec)
        for helper in helpers:
            self.registry.add_helper(helper)
        schemas = JsonSchemaValidator()
        self._loader = LoadTemplate(
            sources=[*sources, FileSource()],
            parser=RuamelTemplateParser(),
            compiler=Compiler(schemas),
        )
        self._validate = ValidateTemplate(
            loader=self._loader,
            validator=StaticValidator(self.registry, schemas),
            registry=self.registry,
        )

    # ── registration ──────────────────────────────────────────────────────────
    def register_function(self, spec: FunctionSpec) -> None:
        self.registry.add_function(spec)

    def register_adapter(self, adapter: BaseAdapter, *, replace: str | None = None) -> None:
        self.registry.add_adapter(adapter, replace=replace)

    def register_extractor(self, extractor: BaseExtractor, *, replace: str | None = None) -> None:
        self.registry.add_extractor(extractor, replace=replace)

    # ── templates ─────────────────────────────────────────────────────────────
    async def validate_async(self, ref: str) -> ValidationReport:
        return (await self._validate(ref)).report

    async def load_async(self, ref: str) -> Template:
        return await self._validate.load(ref)

    def validate(self, ref: str) -> ValidationReport:
        """Stages 1-3: every diagnostic for ``ref`` (a file path or a source reference)."""
        return _run_sync(self.validate_async(ref))

    def load(self, ref: str) -> Template:
        """A validated template. Raises the first error (others attached as ``related``)."""
        return _run_sync(self.load_async(ref))

    def validate_text(self, text: str, *, name: str = "<memory>") -> ValidationReport:
        """Validate template text that is not stored anywhere."""
        return self._validate.text(TemplateText(text=text, origin=Origin(name=name))).report


def _run_sync(coroutine: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    coroutine.close()
    raise ConfigurationError(
        "E902",
        "the synchronous API cannot be used inside a running event loop",
        hint="use 'await crowley.load_async(...)' / 'validate_async(...)' instead",
    )


__all__ = ["BuiltinPlugin", "Crowley"]
