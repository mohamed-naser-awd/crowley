import math

import pytest

from crowley.domain.common import TemplatePath
from crowley.domain.errors import ExecutionError, ValidationError
from crowley.domain.values import (
    Handle,
    assert_output_safe,
    compare,
    interpolate_text,
    is_number,
    to_bool,
    to_float,
    to_int,
    to_json_text,
    to_str,
    type_of,
    values_equal,
)

NODE = Handle("html.node", object())


@pytest.mark.parametrize(
    ("value", "name"),
    [
        (None, "null"),
        (True, "bool"),
        (0, "int"),
        (1.5, "float"),
        ("x", "string"),
        ([1], "list"),
        ({"a": 1}, "object"),
        (b"x", "bytes"),
        (NODE, "handle"),
    ],
)
def test_type_of(value: object, name: str) -> None:
    assert type_of(value) == name


def test_type_of_rejects_foreign_objects() -> None:
    with pytest.raises(TypeError):
        type_of(object())


def test_is_number_excludes_bool() -> None:
    assert is_number(1)
    assert is_number(1.0)
    assert not is_number(True)
    assert not is_number("1")


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        (1, 1.0, True),
        (True, 1, False),
        (False, 0, False),
        (True, True, True),
        (None, None, True),
        ("a", "a", True),
        ("1", 1, False),
        ([1, 2], [1.0, 2], True),
        ([1, 2], [1, 2, 3], False),
        ([True], [1], False),
        ({"a": 1}, {"a": 1.0}, True),
        ({"a": 1}, {"b": 1}, False),
        ({"a": [1]}, {"a": [2]}, False),
        (b"x", b"x", True),
        (NODE, NODE, True),
        (NODE, Handle("html.node", NODE.payload), False),
    ],
)
def test_values_equal(a: object, b: object, equal: bool) -> None:
    assert values_equal(a, b) is equal
    assert values_equal(b, a) is equal


@pytest.mark.parametrize(
    ("a", "b", "result"),
    [
        (1, 2, -1),
        (2.5, 2, 1),
        (3, 3.0, 0),
        (10**30 + 1, 10**30, 1),
        ("a", "b", -1),
        ("b", "b", 0),
        ([1, 2], [1, 3], -1),
        ([1, 2], [1, 2], 0),
        ([1, 2, 0], [1, 2], 1),
        ([1], [1, 0], -1),
    ],
)
def test_compare(a: object, b: object, result: int) -> None:
    assert compare(a, b) == result


@pytest.mark.parametrize(
    ("a", "b"), [(1, "1"), (True, False), (None, 1), ({"a": 1}, {"a": 1}), ([1], ["a"])]
)
def test_compare_rejects_mixed_types(a: object, b: object) -> None:
    with pytest.raises(ExecutionError) as info:
        compare(a, b)
    assert info.value.code == "E501"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("abc", "abc"),
        (1, "1"),
        (1.5, "1.5"),
        (True, "true"),
        (None, "null"),
        ([1, "é"], '[1,"é"]'),
        ({"a": None}, '{"a":null}'),
    ],
)
def test_to_str(value: object, text: str) -> None:
    assert to_str(value) == text  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [b"x", NODE, [NODE], math.inf, math.nan])
def test_to_str_rejects(value: object) -> None:
    with pytest.raises(ExecutionError):
        to_str(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "result"), [(3, 3), (3.9, 3), (-3.9, -3), ("42", 42), (" -7 ", -7), ("+5", 5)]
)
def test_to_int(value: object, result: int) -> None:
    assert to_int(value) == result  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, None, "1.5", "1,234", "", math.inf, [1]])
def test_to_int_rejects(value: object) -> None:
    with pytest.raises(ExecutionError):
        to_int(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "result"), [(3, 3.0), (2.5, 2.5), ("1e3", 1000.0), (" 0.5 ", 0.5)]
)
def test_to_float(value: object, result: float) -> None:
    assert to_float(value) == result  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, None, "abc", [1.0]])
def test_to_float_rejects(value: object) -> None:
    with pytest.raises(ExecutionError):
        to_float(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "result"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("No", False),
        ("1", True),
        (" yes ", True),
    ],
)
def test_to_bool(value: object, result: bool) -> None:
    assert to_bool(value) is result  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [2, None, "maybe", 1.0, []])
def test_to_bool_rejects(value: object) -> None:
    with pytest.raises(ExecutionError):
        to_bool(value)  # type: ignore[arg-type]


def test_interpolation() -> None:
    assert interpolate_text("x") == "x"
    assert interpolate_text(2) == "2"
    assert interpolate_text(False) == "false"
    assert interpolate_text({"k": [1]}) == '{"k":[1]}'
    with pytest.raises(ExecutionError) as info:
        interpolate_text(None)
    assert info.value.hint is not None
    assert "default" in info.value.hint


def test_to_json_text_is_compact() -> None:
    assert to_json_text([1, {"a": "b"}]) == '[1,{"a":"b"}]'


def test_output_safe_accepts_json_data() -> None:
    assert_output_safe({"a": [1, 2.5, "x", None, True, {"b": []}]})


@pytest.mark.parametrize(
    ("value", "path"),
    [
        ({"items": [1, b"x"]}, "output.items[1]"),
        ([{"node": NODE}], "output[0].node"),
        ({"n": math.nan}, "output.n"),
        ({1: "x"}, "output"),
        ({"t": (1, 2)}, "output.t"),
    ],
)
def test_output_safe_rejects(value: object, path: str) -> None:
    with pytest.raises(ValidationError) as info:
        assert_output_safe(value)
    assert info.value.code == "E405"
    assert info.value.path == path


def test_output_safe_custom_root() -> None:
    with pytest.raises(ValidationError) as info:
        assert_output_safe([b"x"], TemplatePath.of("item"))
    assert info.value.path == "item[0]"


def test_handle_repr() -> None:
    assert repr(NODE) == "<handle html.node>"
