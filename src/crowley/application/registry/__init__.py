"""The registry: functions, adapters, extractors and expression helpers (docs/SPEC.md §11.6)."""

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from crowley.application.adapters import BaseAdapter
from crowley.application.extractors import BaseExtractor, extractor_function_specs
from crowley.application.registry.functions import (
    Handler,
    HandlerBinding,
    RegisteredFunction,
    function,
)
from crowley.domain.common import RESERVED_NAMESPACES
from crowley.domain.errors import ConfigurationError
from crowley.domain.expressions import HelperSpec, HelperTable
from crowley.domain.functions import FunctionSpec, Requirement


def adapter_replace_problem(old: BaseAdapter, new: BaseAdapter) -> str | None:
    """Why ``new`` can't stand in for ``old`` (SPEC §13.2), or ``None`` if it can."""
    a, b = old.spec, new.spec
    if a.name != b.name:
        return f"adapter {b.name!r} cannot replace {a.name!r}"
    if a.version.major != b.version.major:
        return f"adapter {b.name!r} {b.version} is not compatible with {a.version}"
    if a.request_schema != b.request_schema or a.response_schema != b.response_schema:
        return f"adapter {b.name!r} changes the request or response schema"
    return None


def extractor_replace_problem(old: BaseExtractor, new: BaseExtractor) -> str | None:
    """Why ``new`` can't stand in for ``old`` (SPEC §13.8), or ``None`` if it can."""
    a, b = old.spec, new.spec
    if a.name != b.name:
        return f"extractor {b.name!r} cannot replace {a.name!r}"
    if not set(a.query_languages) <= set(b.query_languages):
        return (
            f"extractor {b.name!r} must support the query languages "
            f"{', '.join(a.query_languages)} to replace the existing one"
        )
    return None


class Plugin(Protocol):
    """Anything with ``register(registry)``: bundles functions, adapters, extractors and helpers."""

    def register(self, registry: "Registry") -> None: ...


@dataclass(slots=True)
class Registry:
    functions: dict[str, FunctionSpec] = field(default_factory=dict)
    adapters: dict[str, BaseAdapter] = field(default_factory=dict)
    extractors: dict[str, BaseExtractor] = field(default_factory=dict)
    helpers: HelperTable = field(default_factory=HelperTable.builtin)
    handlers: dict[str, RegisteredFunction] = field(default_factory=dict)
    """Functions with a Python handler. Spec-only functions validate but can't be called."""

    # ── registration ──────────────────────────────────────────────────────────
    def add_function(
        self, function: FunctionSpec | RegisteredFunction, *, replace: bool = False
    ) -> None:
        self._put_function(function, replace=replace, owned=False)

    def _put_function(
        self, function: FunctionSpec | RegisteredFunction, *, replace: bool, owned: bool
    ) -> None:
        """``owned``: registered by the adapter/extractor that owns the namespace."""
        spec = function.spec if isinstance(function, RegisteredFunction) else function
        namespace = spec.namespace
        if namespace in RESERVED_NAMESPACES and not (spec.builtin or owned):
            raise ConfigurationError(
                "E901",
                f"cannot register {spec.name!r}: namespace {namespace!r} is reserved",
                hint="use your own namespace, e.g. 'mycorp.decode'",
            )
        if namespace in ("local", "template"):
            raise ConfigurationError("E901", f"namespace {namespace!r} is reserved for templates")
        if spec.name in self.functions and not replace:
            raise ConfigurationError("E901", f"function {spec.name!r} is already registered")
        self.functions[spec.name] = spec
        if isinstance(function, RegisteredFunction):
            self.handlers[spec.name] = function
        else:
            self.handlers.pop(spec.name, None)

    def add_adapter(self, adapter: BaseAdapter, *, replace: str | None = None) -> None:
        spec = adapter.spec
        existing = self.adapters.get(spec.name)
        if existing is not None:
            if replace != spec.name:
                raise ConfigurationError(
                    "E906",
                    f"adapter {spec.name!r} is already registered",
                    hint=f"pass replace={spec.name!r} to swap it",
                )
            problem = adapter_replace_problem(existing, adapter)
            if problem is not None:
                raise ConfigurationError(
                    "E906",
                    problem,
                    hint="a replacement must keep the major version and request/response schemas",
                )
        elif replace is not None:
            raise ConfigurationError("E906", f"cannot replace unknown adapter {replace!r}")
        if spec.name in self.functions_namespaces() and existing is None:
            raise ConfigurationError(
                "E906", f"namespace {spec.name!r} is already used by functions"
            )
        if existing is not None:
            self._drop_namespace(spec.name)
        self.adapters[spec.name] = adapter
        for fn in adapter.functions():
            fn_spec = fn.spec if isinstance(fn, RegisteredFunction) else fn
            if fn_spec.namespace != spec.name:
                raise ConfigurationError(
                    "E903",
                    f"adapter {spec.name!r} cannot register {fn_spec.name!r} outside its namespace",
                )
            self._put_function(fn, replace=True, owned=True)

    def add_extractor(self, extractor: BaseExtractor, *, replace: str | None = None) -> None:
        spec = extractor.spec
        existing = self.extractors.get(spec.name)
        if existing is not None:
            if replace != spec.name:
                raise ConfigurationError(
                    "E906",
                    f"extractor {spec.name!r} is already registered",
                    hint=f"pass replace={spec.name!r} to swap it",
                )
            problem = extractor_replace_problem(existing, extractor)
            if problem is not None:
                raise ConfigurationError("E906", problem)
            self._drop_namespace(spec.name)
        elif replace is not None:
            raise ConfigurationError("E906", f"cannot replace unknown extractor {replace!r}")
        elif spec.name in self.functions_namespaces():
            raise ConfigurationError(
                "E906", f"namespace {spec.name!r} is already used by functions"
            )
        self.extractors[spec.name] = extractor
        for fn in extractor_function_specs(spec):
            self._put_function(fn, replace=True, owned=True)

    def add_helper(self, spec: HelperSpec, *, replace: bool = False) -> None:
        self.helpers.register(spec, replace=replace)

    def install(self, plugin: Plugin) -> None:
        plugin.register(self)

    # ── lookup ────────────────────────────────────────────────────────────────
    def function(self, name: str) -> FunctionSpec | None:
        return self.functions.get(name)

    def handler(self, name: str) -> RegisteredFunction | None:
        return self.handlers.get(name)

    def adapter(self, name: str) -> BaseAdapter | None:
        return self.adapters.get(name)

    def extractor(self, name: str) -> BaseExtractor | None:
        return self.extractors.get(name)

    def functions_namespaces(self) -> set[str]:
        return {spec.namespace for spec in self.functions.values()}

    def requirement_problem(self, requirement: Requirement) -> str | None:
        """Why ``requirement`` is not satisfied, or ``None`` if it is."""
        if requirement.kind == "adapter":
            adapter = self.adapters.get(requirement.name)
            found = [(requirement.name, adapter.spec.version)] if adapter else []
        elif requirement.kind == "extractor":
            extractor = self.extractors.get(requirement.name)
            found = [(requirement.name, extractor.spec.version)] if extractor else []
        else:
            found = [
                (s.name, s.version)
                for s in self.functions.values()
                if requirement.matches_name(s.name)
            ]
        if not found:
            return f"{requirement.kind} {requirement.name!r} is not registered"
        if requirement.version is not None:
            bad = [
                f"{name} {version}"
                for name, version in found
                if not requirement.version.contains(version)
            ]
            if bad:
                return f"{', '.join(bad)} does not satisfy {requirement.version}"
        return None

    def fingerprint(self) -> str:
        """Changes whenever the set of registered functions/adapters/extractors/helpers changes."""
        parts = sorted(
            [f"f:{s.name}@{s.version}" for s in self.functions.values()]
            + [f"a:{a.spec.name}@{a.spec.version}" for a in self.adapters.values()]
            + [f"e:{e.spec.name}@{e.spec.version}" for e in self.extractors.values()]
            + [f"h:{name}" for name in self.helpers.helpers]
        )
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    def _drop_namespace(self, namespace: str) -> None:
        for name in [n for n, s in self.functions.items() if s.namespace == namespace]:
            del self.functions[name]
            self.handlers.pop(name, None)


__all__ = [
    "Handler",
    "HandlerBinding",
    "Plugin",
    "RegisteredFunction",
    "Registry",
    "adapter_replace_problem",
    "extractor_replace_problem",
    "function",
]
