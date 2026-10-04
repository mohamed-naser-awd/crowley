"""The ``Crowley`` facade: the composition root (docs/ARCHITECTURE.md §6).

It loads and validates templates, registers plugins, holds the global notifiers and creates
processes (``init``) that run one operation each (docs/SPEC.md §17.3, §20).
"""

import asyncio
import concurrent.futures
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable, Mapping
from typing import Any, TypeVar

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
from crowley.application.registry import (
    Plugin,
    RegisteredFunction,
    Registry,
    as_registered,
)
from crowley.application.registry import function as declare
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

T = TypeVar("T")


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
        functions: Iterable[FunctionSpec | RegisteredFunction | Callable[..., Any]] = (),
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
        self,
        function: FunctionSpec | RegisteredFunction | Callable[..., Any],
        *,
        replace: bool = False,
    ) -> RegisteredFunction | FunctionSpec:
        """Register a function: a plain Python function (schemas from its type hints, named
        ``app.<name>``), a ``@function``-decorated one, or a bare spec."""
        registered = as_registered(function)
        self.registry.add_function(registered, replace=replace)
        return registered

    register = register_function

    def function(
        self, handler_or_name: Callable[..., Any] | str | None = None, /, **options: Any
    ) -> Any:
        """Decorator that declares and registers a function in one go.

        ``@crowley.function``, ``@crowley.function("acme.slugify")`` or
        ``@crowley.function(name=..., input=..., output=...)``; see :func:`crowley.function`.
        """
        if callable(handler_or_name):
            return self.register_function(declare(handler_or_name, **options))

        def decorate(handler: Callable[..., Any]) -> RegisteredFunction:
            if isinstance(handler_or_name, str):
                options.setdefault("name", handler_or_name)
            registered = declare(**options)(handler)
            self.register_function(registered)
            return registered

        return decorate

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
        return _blocking(self.validate_async(ref))

    def load(self, ref: str) -> Template:
        """A validated template. Raises the first error (others attached as ``related``).

        Works inside a running event loop too (loading is a short, self-contained read).
        """
        return _blocking(self.load_async(ref))

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
        extractors: Mapping[str, BaseExtractor] | None = None,
    ) -> Process:
        """Create a process for one operation. Inputs and secrets are validated now.

        ``template`` is a loaded template or a reference (``"file.yml"`` or
        ``"file.yml#operation"``).
        """
        if isinstance(template, str):
            ref, operation = _split_ref(template, operation)
            template = self.load(ref)
        prepared = self._runner.prepare(
            template,
            operation,
            inputs=inputs,
            secrets=secrets,
            limits=limits,
            adapters=adapters,
            extractors=extractors,
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
        extractors: Mapping[str, BaseExtractor] | None = None,
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
            extractors=extractors,
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
        extractors: Mapping[str, BaseExtractor] | None = None,
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
                extractors=extractors,
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
        extractors: Mapping[str, BaseExtractor] | None = None,
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
            extractors=extractors,
        )
        async for item in process.stream():
            yield item


def _blocking(coroutine: Coroutine[Any, Any, T]) -> T:
    """Run a short coroutine to completion from sync code, even inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


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
