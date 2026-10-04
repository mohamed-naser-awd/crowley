"""The ``Crowley`` facade: the composition root (docs/ARCHITECTURE.md §6).

It loads and validates templates, registers plugins, holds the global notifiers and creates
processes (``init``) that run one operation each (docs/SPEC.md §17.3, §20).
"""

from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from crowley.adapters.http import HttpAdapter
from crowley.application.adapters import BaseAdapter
from crowley.application.compiler import Compiler, StaticValidator
from crowley.application.events import NotifierScope, NotifierSet
from crowley.application.extractors import BaseExtractor
from crowley.application.ports import (
    HostResolver,
    Origin,
    SecretsProvider,
    TemplateSource,
    TemplateText,
)
from crowley.application.registry import Plugin, RegisteredFunction, Registry
from crowley.application.use_cases import (
    LoadTemplate,
    Process,
    ProcessNotifiers,
    RunOperation,
    RunResult,
    ValidateTemplate,
    run_sync,
)
from crowley.domain.errors import ConfigurationError, ValidationReport
from crowley.domain.expressions import EvalEnv, HelperSpec
from crowley.domain.functions import FunctionSpec
from crowley.domain.services import Clock, RandomSource
from crowley.domain.template import Limits, Template
from crowley.domain.values import Value
from crowley.extractors.html import HtmlExtractor
from crowley.extractors.json import JsonExtractor
from crowley.extractors.text import TextExtractor
from crowley.extractors.xml import XmlExtractor
from crowley.infrastructure.network import SystemHostResolver
from crowley.infrastructure.regex import RegexLibEngine
from crowley.infrastructure.schema import JsonSchemaValidator
from crowley.infrastructure.secrets import DictSecrets
from crowley.infrastructure.sources import FileSource
from crowley.infrastructure.system import SystemClock, SystemRandom
from crowley.infrastructure.yaml import RuamelTemplateParser
from crowley.stdlib import StdlibPlugin


class BuiltinPlugin:
    """The built-in adapter, extractors and stdlib, registered like any other plugin."""

    def register(self, registry: Registry) -> None:
        registry.add_adapter(HttpAdapter())
        for extractor in (HtmlExtractor(), XmlExtractor(), JsonExtractor(), TextExtractor()):
            registry.add_extractor(extractor)
        StdlibPlugin().register(registry)


class Crowley(NotifierScope):
    """The SDK entry point. Notifiers added here are global: they apply to every process."""

    def __init__(
        self,
        *,
        sources: Iterable[TemplateSource] = (),
        functions: Iterable[FunctionSpec | RegisteredFunction] = (),
        adapters: Iterable[BaseAdapter] = (),
        extractors: Iterable[BaseExtractor] = (),
        plugins: Iterable[Plugin] = (),
        helpers: Iterable[HelperSpec] = (),
        secrets: Mapping[str, str] | SecretsProvider | None = None,
        limits: Limits | None = None,
        strict_observers: bool = False,
        clock: Clock | None = None,
        random: RandomSource | None = None,
        resolver: HostResolver | None = None,
    ) -> None:
        self._notifier_set = NotifierSet("global")
        self._strict_observers = strict_observers
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
        for fn in functions:
            self.register_function(fn)
        for helper in helpers:
            self.registry.add_helper(helper)
        schemas = JsonSchemaValidator()
        self.schemas = schemas
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
        clock = clock or SystemClock()
        random = random or SystemRandom()
        regex = RegexLibEngine()
        provider = DictSecrets(secrets) if isinstance(secrets, Mapping) else secrets
        self._runner = RunOperation(
            registry=self.registry,
            schemas=schemas,
            env_factory=lambda: EvalEnv(
                helpers=self.registry.helpers, clock=clock, random=random, regex=regex
            ),
            clock=clock,
            limits=limits or Limits(),
            secrets=provider,
            resolver=resolver or SystemHostResolver(),
        )

    # ── registration ──────────────────────────────────────────────────────────
    def register_function(
        self, function: FunctionSpec | RegisteredFunction, *, replace: bool = False
    ) -> None:
        """Register a function: a ``@function``-decorated handler, or a bare spec."""
        self.registry.add_function(function, replace=replace)

    register = register_function

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
        return run_sync(self.validate_async(ref))

    def load(self, ref: str) -> Template:
        """A validated template. Raises the first error (others attached as ``related``)."""
        return run_sync(self.load_async(ref))

    def load_text(self, text: str, *, name: str = "<memory>") -> Template:
        """A validated template from text that is not stored anywhere. Raises the first error."""
        result = self._validate.text(TemplateText(text=text, origin=Origin(name=name)))
        result.report.raise_if_errors()
        assert result.template is not None
        return result.template

    def validate_text(self, text: str, *, name: str = "<memory>") -> ValidationReport:
        """Validate template text that is not stored anywhere."""
        return self._validate.text(TemplateText(text=text, origin=Origin(name=name))).report

    # ── processes ─────────────────────────────────────────────────────────────
    def init(
        self,
        template: Template | str,
        operation: str | None = None,
        *,
        inputs: Mapping[str, Value] | None = None,
        secrets: Mapping[str, str] | None = None,
        limits: Limits | None = None,
        notifiers: ProcessNotifiers | None = None,
        adapters: Mapping[str, BaseAdapter] | None = None,
    ) -> Process:
        """Create a process for one operation. Inputs and secrets are validated now.

        ``template`` is a loaded template or a reference (``"file.yml"`` or
        ``"file.yml#operation"``). References are loaded synchronously, so inside an event
        loop load them first with ``await crowley.load_async(...)``.
        """
        if isinstance(template, str):
            ref, operation = _split_ref(template, operation)
            template = self.load(ref)
        prepared = self._runner.prepare(
            template, operation, inputs=inputs, secrets=secrets, limits=limits, adapters=adapters
        )
        return Process(
            runner=self._runner,
            prepared=prepared,
            global_notifiers=self._notifier_set,
            strict_observers=self._strict_observers,
            notifiers=notifiers,
        )

    async def _init_async(
        self, template: Template | str, operation: str | None, **options: Any
    ) -> Process:
        if isinstance(template, str):
            ref, operation = _split_ref(template, operation)
            template = await self.load_async(ref)
        return self.init(template, operation, **options)

    async def run(
        self,
        template: Template | str,
        operation: str | None = None,
        *,
        inputs: Mapping[str, Value] | None = None,
        secrets: Mapping[str, str] | None = None,
        limits: Limits | None = None,
        notifiers: ProcessNotifiers | None = None,
        adapters: Mapping[str, BaseAdapter] | None = None,
    ) -> RunResult:
        """Shortcut for ``init(...)`` followed by ``run()``."""
        process = await self._init_async(
            template,
            operation,
            inputs=inputs,
            secrets=secrets,
            limits=limits,
            notifiers=notifiers,
            adapters=adapters,
        )
        return await process.run()

    def run_sync(
        self,
        template: Template | str,
        operation: str | None = None,
        *,
        inputs: Mapping[str, Value] | None = None,
        secrets: Mapping[str, str] | None = None,
        limits: Limits | None = None,
        notifiers: ProcessNotifiers | None = None,
        adapters: Mapping[str, BaseAdapter] | None = None,
    ) -> RunResult:
        """Synchronous ``run``; not usable inside a running event loop (E902)."""
        return run_sync(
            self.run(
                template,
                operation,
                inputs=inputs,
                secrets=secrets,
                limits=limits,
                notifiers=notifiers,
                adapters=adapters,
            )
        )

    async def stream(
        self,
        template: Template | str,
        operation: str | None = None,
        *,
        inputs: Mapping[str, Value] | None = None,
        secrets: Mapping[str, str] | None = None,
        limits: Limits | None = None,
        notifiers: ProcessNotifiers | None = None,
        adapters: Mapping[str, BaseAdapter] | None = None,
    ) -> AsyncIterator[Value]:
        """Shortcut for ``init(...)`` followed by ``stream()``."""
        process = await self._init_async(
            template,
            operation,
            inputs=inputs,
            secrets=secrets,
            limits=limits,
            notifiers=notifiers,
            adapters=adapters,
        )
        async for item in process.stream():
            yield item


def _split_ref(ref: str, operation: str | None) -> tuple[str, str | None]:
    """``"file.yml#op"`` gives ``("file.yml", "op")``; an explicit ``operation`` must agree."""
    base, sep, fragment = ref.partition("#")
    if not sep:
        return ref, operation
    if operation is not None and operation != fragment:
        raise ConfigurationError(
            "E904", f"operation {operation!r} conflicts with {ref!r}", hint="name it once"
        )
    return base, fragment


__all__ = ["BuiltinPlugin", "Crowley"]
