"""FunctionRegistry: functions bundled in their own module and passed to Crowley."""

import pytest

from crowley import Crowley, FunctionRegistry
from crowley.domain.common import SemVer
from crowley.domain.errors import ConfigurationError
from crowley.domain.functions import FunctionSpec
from tests.application.sample_functions import clean_price, functions

TEMPLATE = """\
crowley: 1
id: me/registry
version: 1.0.0
name: Registry
description: Functions from a FunctionRegistry.
permissions:
  hosts: [example.com]
requires: ["acme.*@^1", "tools.*@^1"]
operations:
  op:
    description: d
    steps:
      - id: price
        use: acme.clean_price
        with: { text: "£ 12.50" }
      - id: loud
        use: acme.shout
        with: { text: hi }
      - for_each: ${{ ['a', 'b'] }}
        do:
          - return: ${{ item }}
      - id: counted
        use: tools.count
    output:
      value: { price: "${{ steps.price }}", loud: "${{ steps.loud }}", n: "${{ steps.counted }}" }
      schema: {}
"""
EXPECTED = {"price": 12.5, "loud": "HI", "n": 2}


def test_registry_contents() -> None:
    assert [f.name for f in functions] == ["acme.clean_price", "acme.shout", "tools.count"]
    assert len(functions) == 3
    assert "acme.shout" in functions
    assert functions["tools.count"].spec.pure  # type: ignore[union-attr]
    assert repr(functions) == "FunctionRegistry('acme', 3 function(s))"
    assert clean_price("£3") == 3.0  # still a normal Python function


@pytest.mark.parametrize(
    "make",
    [
        lambda: Crowley(functions=functions),
        lambda: Crowley(functions=[functions]),
        lambda: Crowley(plugins=[functions]),
    ],
    ids=["functions=registry", "functions=[registry]", "plugins=[registry]"],
)
def test_passing_a_registry(make) -> None:  # type: ignore[no-untyped-def]
    cw = make()
    assert cw.run_sync(cw.load_text(TEMPLATE)).output == EXPECTED


def test_register_a_registry_later() -> None:
    cw = Crowley()
    assert cw.register(functions) is functions
    assert cw.run_sync(cw.load_text(TEMPLATE)).output == EXPECTED


def test_default_namespace_include_and_specs() -> None:
    app = FunctionRegistry()

    @app.function
    def double(n: int) -> int:
        return n * 2

    assert double.name == "app.double"
    app.add(FunctionSpec(name="app.declared", version=SemVer.parse("1.0.0")))
    merged = FunctionRegistry("merged")
    merged.include(app)
    assert [f.name for f in merged] == ["app.double", "app.declared"]


def test_registry_errors() -> None:
    registry = FunctionRegistry("acme")

    @registry.function
    def twice(x: int) -> int:
        return x

    with pytest.raises(ConfigurationError) as info:
        registry.add(twice.handler)
    assert info.value.code == "E901"
    with pytest.raises(ConfigurationError, match="namespace"):
        FunctionRegistry("Not Valid")
    reserved = FunctionRegistry("paginate")
    reserved.add(lambda n=1: n, name="sneaky")
    with pytest.raises(ConfigurationError, match="reserved") as info:
        Crowley(functions=reserved)
    assert info.value.code == "E901"
