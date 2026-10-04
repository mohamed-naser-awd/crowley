import pytest

from crowley.domain.errors import ExecutionError, TemplateSemanticError
from crowley.domain.expressions import (
    Compiled,
    EvalEnv,
    Interpolation,
    Text,
    contains_expression,
    evaluate_scalar,
    parse_scalar,
)


def test_plain_text() -> None:
    scalar = parse_scalar("hello")
    assert scalar == Text(value="hello")
    assert not contains_expression("hello")


def test_single_expression_is_typed() -> None:
    scalar = parse_scalar("  ${{ inputs.pages }}  ")
    assert isinstance(scalar, Compiled)
    assert evaluate_scalar(scalar, {"inputs": {"pages": 3}}) == 3


def test_interpolation_builds_a_string() -> None:
    scalar = parse_scalar("companies/${{ inputs.company }}/people?n=${{ n }}")
    assert isinstance(scalar, Interpolation)
    assert len(scalar.expressions) == 2
    assert (
        evaluate_scalar(scalar, {"inputs": {"company": "acme"}, "n": 5})
        == "companies/acme/people?n=5"
    )


def test_interpolation_of_containers_and_bools() -> None:
    scalar = parse_scalar("v=${{ x }} ok=${{ y }}")
    assert evaluate_scalar(scalar, {"x": [1, {"a": None}], "y": True}) == 'v=[1,{"a":null}] ok=true'


def test_interpolating_null_is_an_error() -> None:
    with pytest.raises(ExecutionError) as info:
        evaluate_scalar(parse_scalar("id=${{ x }}"), {"x": None}, EvalEnv())
    assert info.value.code == "E501"
    assert "${{ x }}" in info.value.message


def test_escape_produces_literal_open() -> None:
    scalar = parse_scalar("use $${{ to write ${{ 'x' }}")
    assert isinstance(scalar, Interpolation)
    assert evaluate_scalar(scalar, {}) == "use ${{ to write x"
    assert parse_scalar("$${{ not an expression }}") == Text(value="${{ not an expression }}")
    assert not contains_expression("$${{ x }}")
    assert contains_expression("a ${{ x }}")


def test_braces_and_strings_inside_expression() -> None:
    scalar = parse_scalar("${{ {'a': {'b': '}}'}} }}")
    assert isinstance(scalar, Compiled)
    assert evaluate_scalar(scalar, {}) == {"a": {"b": "}}"}}
    escaped = parse_scalar('${{ "q\\"}}" }}')
    assert evaluate_scalar(escaped, {}) == 'q"}}'


@pytest.mark.parametrize("text", ["${{ x", "${{ {'a': 1 }", "a ${{ x } b", "${{ x }} ${{ ("])
def test_unterminated_or_invalid(text: str) -> None:
    with pytest.raises(TemplateSemanticError) as info:
        parse_scalar(text)
    assert info.value.code == "E321"
