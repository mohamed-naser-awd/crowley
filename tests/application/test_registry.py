from typing import ClassVar

import pytest

from crowley import BaseAdapter, BaseExtractor, Crowley, FunctionSpec, Registry
from crowley.adapters.http import HttpAdapter
from crowley.application.extractors import extractor_function_specs
from crowley.domain.adapters import TargetKind
from crowley.domain.common import SemVer
from crowley.domain.errors import ConfigurationError
from crowley.domain.expressions import HelperSpec
from crowley.domain.functions import FunctionKind, Requirement
from crowley.extractors.html import HtmlExtractor
from crowley.interface.sdk import BuiltinPlugin
from crowley.stdlib import BUILTIN_FUNCTIONS


def spec(name: str, **kwargs: object) -> FunctionSpec:
    return FunctionSpec(name=name, version=SemVer(1, 2, 0), **kwargs)  # type: ignore[arg-type]


class WsAdapter(BaseAdapter):
    name: ClassVar[str] = "ws"
    version: ClassVar[str] = "1.0.0"
    schemes: ClassVar[tuple[str, ...]] = ("ws", "wss")

    def functions(self) -> list[FunctionSpec]:
        return [spec("ws.send", adapter="ws")]


class PdfExtractor(BaseExtractor):
    name: ClassVar[str] = "pdf"
    version: ClassVar[str] = "2.0.0"
    query_languages: ClassVar[tuple[str, ...]] = ("text",)
    default_language: ClassVar[str] = "text"


def builtin_registry() -> Registry:
    registry = Registry()
    BuiltinPlugin().register(registry)
    return registry


def test_builtins_are_registered() -> None:
    registry = builtin_registry()
    assert set(registry.adapters) == {"http"}
    assert set(registry.extractors) == {"html", "xml", "json", "text"}
    for name in (
        "http.get",
        "http.session",
        "html.extract",
        "json.select_all",
        "paginate.by_cursor",
        "extract.auto",
    ):
        assert registry.function(name) is not None, name
    assert all(s.builtin for s in registry.functions.values())
    assert registry.functions_namespaces() >= {
        "http",
        "html",
        "paginate",
        "transform",
        "control",
        "extract",
    }


def test_builtin_spec_shapes() -> None:
    by_name = {s.name: s for s in BUILTIN_FUNCTIONS}
    by_page = by_name["paginate.by_page"]
    assert by_page.kind is FunctionKind.BLOCK
    assert by_page.body_bindings == ("page",)
    assert by_page.arg_bindings["stop_when"] == ("result", "page")
    assert by_name["transform.map"].lazy_args == {"fields"}
    assert by_name["transform.map"].from_prev_args == {"items"}
    http = {s.name: s for s in HttpAdapter().functions()}
    assert http["http.get"].target_args == {"url"}
    assert "method" not in http["http.get"].properties
    assert http["http.session"].kind is FunctionKind.BLOCK
    generated = {s.name: s for s in extractor_function_specs(HtmlExtractor().spec)}
    assert set(generated) == {"html.parse", "html.select", "html.select_all", "html.extract"}
    assert all(s.pure and s.extractor == "html" for s in generated.values())


def test_function_conflicts_and_reserved_namespaces() -> None:
    registry = Registry()
    registry.add_function(spec("mycorp.decode"))
    with pytest.raises(ConfigurationError) as info:
        registry.add_function(spec("mycorp.decode"))
    assert info.value.code == "E901"
    registry.add_function(spec("mycorp.decode"), replace=True)
    for name in ("http.custom", "local.x", "template.y"):
        with pytest.raises(ConfigurationError) as info:
            registry.add_function(spec(name))
        assert info.value.code == "E901"


def test_adapters_register_their_functions() -> None:
    registry = builtin_registry()
    registry.add_adapter(WsAdapter())
    assert registry.function("ws.send") is not None
    with pytest.raises(ConfigurationError) as info:
        registry.add_adapter(WsAdapter())
    assert info.value.code == "E906"
    with pytest.raises(ConfigurationError):
        registry.add_adapter(WsAdapter(), replace="nope")


def test_adapter_functions_must_stay_in_their_namespace() -> None:
    class Leaky(BaseAdapter):
        name: ClassVar[str] = "leaky"
        version: ClassVar[str] = "1.0.0"
        target_kind: ClassVar[TargetKind] = TargetKind.NONE

        def functions(self) -> list[FunctionSpec]:
            return [spec("other.fn")]

    with pytest.raises(ConfigurationError) as info:
        Registry().add_adapter(Leaky())
    assert info.value.code == "E903"


def test_replacing_builtin_adapter() -> None:
    class CompatibleHttp(HttpAdapter):
        version: ClassVar[str] = "1.4.0"

        def functions(self) -> list[FunctionSpec]:
            return [s for s in super().functions() if s.name != "http.session"]

    class IncompatibleHttp(HttpAdapter):
        version: ClassVar[str] = "2.0.0"

    registry = builtin_registry()
    with pytest.raises(ConfigurationError) as info:
        registry.add_adapter(IncompatibleHttp(), replace="http")
    assert info.value.code == "E906"
    registry.add_adapter(CompatibleHttp(), replace="http")
    assert registry.function("http.session") is None  # the old namespace was replaced
    assert registry.function("http.get") is not None


def test_extractors_and_replacement() -> None:
    registry = builtin_registry()
    registry.add_extractor(PdfExtractor())
    assert registry.function("pdf.extract") is not None
    with pytest.raises(ConfigurationError):
        registry.add_extractor(PdfExtractor())

    class CssOnlyHtml(HtmlExtractor):
        query_languages: ClassVar[tuple[str, ...]] = ("css",)

    with pytest.raises(ConfigurationError) as info:
        registry.add_extractor(CssOnlyHtml(), replace="html")
    assert info.value.code == "E906"

    class FasterHtml(HtmlExtractor):
        version: ClassVar[str] = "1.1.0"

    registry.add_extractor(FasterHtml(), replace="html")
    assert registry.extractor("html") is not None
    with pytest.raises(ConfigurationError):
        registry.add_extractor(PdfExtractor(), replace="unknown")


def test_namespace_clash_between_functions_and_plugins() -> None:
    registry = Registry()
    registry.add_function(spec("ws.helper"))
    with pytest.raises(ConfigurationError):
        registry.add_adapter(WsAdapter())
    registry.add_function(spec("pdf.helper"))
    with pytest.raises(ConfigurationError):
        registry.add_extractor(PdfExtractor())


@pytest.mark.parametrize(
    ("requirement", "problem"),
    [
        ("mycorp.decode@^1", None),
        ("mycorp.decode@^2", "does not satisfy"),
        ("mycorp.*@^1", None),
        ("mycorp.nope", "not registered"),
        ("adapter:ws@^1", None),
        ("adapter:ws@^3", "does not satisfy"),
        ("adapter:soap", "not registered"),
        ("extractor:pdf@^2", None),
        ("extractor:csv", "not registered"),
    ],
)
def test_requirement_problems(requirement: str, problem: str | None) -> None:
    registry = builtin_registry()
    registry.add_function(spec("mycorp.decode"))
    registry.add_adapter(WsAdapter())
    registry.add_extractor(PdfExtractor())
    found = registry.requirement_problem(Requirement.parse(requirement))
    if problem is None:
        assert found is None
    else:
        assert found is not None
        assert problem in found


def test_fingerprint_changes_with_contents() -> None:
    registry = builtin_registry()
    before = registry.fingerprint()
    assert builtin_registry().fingerprint() == before
    registry.add_helper(HelperSpec(name="double", fn=lambda ctx, x: x, min_args=1, max_args=1))
    assert registry.fingerprint() != before


def test_plugin_install_and_facade_registration() -> None:
    class MyPlugin:
        def register(self, registry: Registry) -> None:
            registry.add_function(spec("mycorp.decode"))
            registry.add_adapter(WsAdapter())

    crowley = Crowley(
        plugins=[MyPlugin()],
        functions=[spec("other.fn")],
        extractors=[PdfExtractor()],
        adapters=[HttpAdapter()],
        helpers=[HelperSpec(name="double", fn=lambda ctx, x: x, min_args=1, max_args=1)],
    )
    for name in ("mycorp.decode", "ws.send", "other.fn", "pdf.extract", "http.get"):
        assert crowley.registry.function(name) is not None
    assert crowley.registry.helpers.get("double") is not None
    crowley.register_function(spec("third.fn"))
    assert crowley.registry.function("third.fn") is not None


def test_base_class_defaults() -> None:
    assert BaseExtractor.check_query(PdfExtractor(), "text", "x") is None
    assert WsAdapter().spec.schemes == ("ws", "wss")
    assert BaseAdapter.functions(WsAdapter()) == []
