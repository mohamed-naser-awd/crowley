import pytest

from crowley.domain.errors import ExecutionError, LimitError, TemplateSemanticError
from crowley.domain.expressions import EvalEnv, HelperSpec, HelperTable, evaluate, parse_expression
from crowley.domain.values import Handle

from .conftest import Run

DATA = {
    "inputs": {"company": "acme", "pages": 2},
    "rows": [{"id": 1, "title": "a", "tags": ["x"]}, {"id": 2, "title": None, "tags": []}],
    "pages": [{"items": [{"id": 1}, {"id": 2}]}, {"items": [{"id": 3}]}],
    "obj": {"a": {"b": 1}, "n": None},
}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("inputs.company", "acme"),
        ("rows[0].title", "a"),
        ("rows[-1].id", 2),
        ("rows[1].title", None),
        ("obj?.missing", None),
        ("obj.n?.deep", None),
        ("obj?.a?.b", 1),
        ("obj['a']['b']", 1),
        ("rows[*].id", [1, 2]),
        ("pages[*].items[*].id", [[1, 2], [3]]),
        ("rows[*].tags[0:1]", [["x"], []]),
        ("rows[0:1][0].id", 1),
        ("'abcdef'[1:-1:2]", "bd"),
        ("'abc'[0]", "a"),
        ("[1, 2, 3][::-1]", [3, 2, 1]),
        ("{a: 1 + 1, b: [inputs.pages]}", {"a": 2, "b": [2]}),
    ],
)
def test_member_index_projection(run: Run, source: str, expected: object) -> None:
    assert run(source, **DATA) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1 + 2", 3),
        ("1 + 2.5", 3.5),
        ("'a' + 'b'", "ab"),
        ("[1] + [2]", [1, 2]),
        ("7 - 10", -3),
        ("3 * 4", 12),
        ("7 / 2", 3.5),
        ("4 / 2", 2.0),
        ("7 // 2", 3),
        ("-7 // 2", -4),
        ("7.5 // 2", 3),
        ("7 % 3", 1),
        ("-x", -5),
        ("--x", 5),
        ("2 + 3 * 4 - 1", 13),
        ("(2 + 3) * 4", 20),
    ],
)
def test_arithmetic(run: Run, source: str, expected: object) -> None:
    result = run(source, x=5)
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1 == 1.0", True),
        ("true == 1", False),
        ("'a' != 'b'", True),
        ("[1, {'a': 2}] == [1.0, {'a': 2}]", True),
        ("1 < 2 < 3", True),
        ("1 < 3 < 2", False),
        ("'b' >= 'a'", True),
        ("2 <= 2", True),
        ("3 > 4", False),
        ("'ell' in 'hello'", True),
        ("2 in [1, 2]", True),
        ("'a' in {a: 1}", True),
        ("'z' not in {a: 1}", True),
        ("3 not in [1, 2]", True),
        ("true and false", False),
        ("false and missing", False),
        ("true or missing", True),
        ("not false", True),
        ("'yes' if 1 > 0 else 'no'", "yes"),
        ("'yes' if 1 < 0 else 'no' if true else 'never'", "no"),
    ],
)
def test_comparison_logic_conditional(run: Run, source: str, expected: object) -> None:
    assert run(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "missing",
        "obj.nope",
        "obj.n.deep",
        "rows[5]",
        "rows['x']",
        "obj[0]",
        "rows[true]",
        "inputs.company.x",
        "5[0]",
        "5[0:1]",
        "[1][0:1:0]",
        "[1]['a':]",
        "obj.n[*]",
        "1 + '1'",
        "'a' - 'b'",
        "1 / 0",
        "1 // 0",
        "1 % 0",
        "-'a'",
        "true + 1",
        "1 < 'a'",
        "1 and true",
        "true and 1",
        "not 1",
        "1 if 'x' else 2",
        "1 in 'abc'",
        "1 in {a: 1}",
        "1 in 5",
        "true < false",
    ],
)
def test_runtime_errors_are_e501(run: Run, source: str) -> None:
    with pytest.raises(ExecutionError) as info:
        run(source, **DATA)
    assert info.value.code == "E501"
    assert source in info.value.message


def test_handles_cannot_be_inspected(run: Run) -> None:
    with pytest.raises(ExecutionError):
        run("h.x", h=Handle("html.node", object()))


def test_missing_key_hint(run: Run) -> None:
    with pytest.raises(ExecutionError) as info:
        run("obj.nope", **DATA)
    assert info.value.hint is not None
    assert "?.nope" in info.value.hint


def test_unknown_helper_and_arity_at_runtime(run: Run) -> None:
    with pytest.raises(TemplateSemanticError) as info:
        run("nope(1)")
    assert info.value.code == "E313"
    with pytest.raises(TemplateSemanticError) as info:
        run("len(1, 2)")
    assert info.value.code == "E314"


def test_lambda_position_rules(run: Run) -> None:
    with pytest.raises(ExecutionError, match="must be a lambda"):
        run("map([1], 'x')")
    with pytest.raises(ExecutionError, match="must not be a lambda"):
        run("len(x => x)")


def test_lambdas_close_over_scope(run: Run) -> None:
    assert run("map(xs, x => x + base)", xs=[1, 2], base=10) == [11, 12]
    assert run("map(xs, (x, i) => [x, i])", xs=["a", "b"]) == [["a", 0], ["b", 1]]
    assert run("map(xs, x => map(x, y => y * k))", xs=[[1], [2, 3]], k=2) == [[2], [4, 6]]


def test_lambda_shadowing_root(run: Run) -> None:
    assert run("map(xs, inputs => inputs * 2)", xs=[1], inputs={"a": 1}) == [2]


def test_step_budget() -> None:
    env = EvalEnv(max_steps=50)
    with pytest.raises(LimitError) as info:
        evaluate(parse_expression("map(range(0, 100), x => x + 1)"), {}, env)
    assert info.value.code == "E703"


def test_custom_helper() -> None:
    table = HelperTable.builtin()
    table.register(HelperSpec(name="double", fn=lambda ctx, x: x * 2, min_args=1, max_args=1))
    assert evaluate(parse_expression("double(21)"), {}, EvalEnv(helpers=table)) == 42
    with pytest.raises(ValueError, match="already registered"):
        table.register(HelperSpec(name="double", fn=lambda ctx, x: x, min_args=1, max_args=1))
    assert "double" in table
    assert "double" not in HelperTable.builtin()
    assert "double" in table.copy()
