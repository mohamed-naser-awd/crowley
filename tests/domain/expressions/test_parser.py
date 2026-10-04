import pytest

from crowley.domain.errors import TemplateSemanticError
from crowley.domain.expressions import ast, parse_expression
from crowley.domain.expressions.lexer import MAX_SOURCE_LENGTH, Kind, tokenize
from crowley.domain.expressions.parser import MAX_NESTING


def node(source: str) -> ast.Node:
    return parse_expression(source).node


def test_tokens() -> None:
    kinds = [(t.kind, t.text) for t in tokenize("a?.b[*] // 2 => 'x' >= 1.5e3 not in")]
    assert kinds == [
        (Kind.NAME, "a"),
        (Kind.OP, "?."),
        (Kind.NAME, "b"),
        (Kind.OP, "[*]"),
        (Kind.OP, "//"),
        (Kind.NUMBER, "2"),
        (Kind.OP, "=>"),
        (Kind.STRING, "'x'"),
        (Kind.OP, ">="),
        (Kind.NUMBER, "1.5e3"),
        (Kind.KEYWORD, "not"),
        (Kind.KEYWORD, "in"),
        (Kind.END, ""),
    ]


def test_star_index_allows_spaces() -> None:
    assert [t.text for t in tokenize("xs[ * ]")] == ["xs", "[*]", ""]


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("12", 12),
        ("1.5", 1.5),
        ("2e3", 2000.0),
        ("'a\\'b'", "a'b"),
        ('"tab\\tnew\\nline"', "tab\tnew\nline"),
        ("'\\u00e9'", "é"),
        ("null", None),
        ("true", True),
        ("false", False),
    ],
)
def test_literals(source: str, value: object) -> None:
    parsed = node(source)
    assert isinstance(parsed, ast.Literal)
    assert parsed.value == value
    assert type(parsed.value) is type(value)


def test_precedence() -> None:
    parsed = node("1 + 2 * 3 == 7 and not x or y")
    assert isinstance(parsed, ast.BoolOp)
    assert parsed.op == "or"
    left = parsed.left
    assert isinstance(left, ast.BoolOp)
    assert left.op == "and"
    assert isinstance(left.left, ast.Compare)
    assert isinstance(left.right, ast.Unary)
    cmp_left = left.left.first
    assert isinstance(cmp_left, ast.Binary)
    assert cmp_left.op == "+"
    assert isinstance(cmp_left.right, ast.Binary)
    assert cmp_left.right.op == "*"


def test_conditional_and_chained_comparison() -> None:
    parsed = node("'a' if 1 < x <= 3 else 'b'")
    assert isinstance(parsed, ast.Conditional)
    assert isinstance(parsed.test, ast.Compare)
    assert [op for op, _ in parsed.test.rest] == ["<", "<="]


def test_postfix_chain_and_projection() -> None:
    parsed = node("pages[*].items[*].id")
    assert isinstance(parsed, ast.Projection)
    assert isinstance(parsed.source, ast.Name)
    inner = parsed.body
    assert isinstance(inner, ast.Projection)
    assert isinstance(inner.body, ast.Member)
    assert inner.body.name == "id"


def test_slices() -> None:
    parsed = node("xs[1:-1:2]")
    assert isinstance(parsed, ast.Slice)
    assert parsed.start is not None
    assert parsed.stop is not None
    assert parsed.step is not None
    open_slice = node("xs[:]")
    assert isinstance(open_slice, ast.Slice)
    assert open_slice.start is None
    assert open_slice.stop is None
    assert isinstance(node("xs[::2]"), ast.Slice)


def test_calls_and_lambdas() -> None:
    parsed = node("map(xs, (x, i) => x.id + i)")
    assert isinstance(parsed, ast.Call)
    assert parsed.name == "map"
    lam = parsed.args[1]
    assert isinstance(lam, ast.Lambda)
    assert lam.params == ("x", "i")
    single = node("filter(xs, x => x.ok)")
    assert isinstance(single, ast.Call)
    assert isinstance(single.args[1], ast.Lambda)


def test_literals_of_collections() -> None:
    parsed = node("{a: 1, 'b c': [1, 2], if: null}")
    assert isinstance(parsed, ast.ObjectLiteral)
    assert [k for k, _ in parsed.entries] == ["a", "b c", "if"]
    assert isinstance(node("[]"), ast.ListLiteral)
    assert isinstance(node("{}"), ast.ObjectLiteral)


def test_keyword_member_names() -> None:
    parsed = node("x.in")
    assert isinstance(parsed, ast.Member)
    assert parsed.name == "in"


def test_parenthesised_lambda_lookahead_falls_back_to_grouping() -> None:
    assert isinstance(node("(a)"), ast.Name)
    assert isinstance(node("(a + b)"), ast.Binary)
    with pytest.raises(TemplateSemanticError):
        parse_expression("(a, b)")


@pytest.mark.parametrize(
    "source",
    [
        "",
        "   ",
        "1 +",
        "(1",
        "[1, 2",
        "{a 1}",
        "{a: 1, a: 2}",
        "a.",
        "a.1",
        "1abc",
        "'unterminated",
        "'bad \\q escape'",
        "'bad \\u12'",
        "a ? b",
        "a.b()",
        "x if y",
        "a b",
        "#",
        "map(x, (a, a) => 1)",
        "1 if x => x else 2",
        "é",
        "{1: 2}",
        "xs[]",
    ],
)
def test_syntax_errors_are_e321(source: str) -> None:
    with pytest.raises(TemplateSemanticError) as info:
        parse_expression(source)
    assert info.value.code == "E321"


@pytest.mark.parametrize("source", ["x => x", "[x => x]", "(a, b) => a", "f(y, [x => x])"])
def test_lambda_outside_helper_argument_is_e322(source: str) -> None:
    with pytest.raises(TemplateSemanticError) as info:
        parse_expression(source)
    assert info.value.code == "E322"


def test_source_length_limit() -> None:
    with pytest.raises(TemplateSemanticError) as info:
        parse_expression("1+" * (MAX_SOURCE_LENGTH // 2) + "1")
    assert info.value.code == "E321"


def test_nesting_limit() -> None:
    deep = "(" * (MAX_NESTING + 5) + "1" + ")" * (MAX_NESTING + 5)
    with pytest.raises(TemplateSemanticError) as info:
        parse_expression(deep)
    assert "nested" in info.value.message
    with pytest.raises(TemplateSemanticError):
        parse_expression("-" * 200 + "1")
    with pytest.raises(TemplateSemanticError):
        parse_expression("not " * 200 + "x")


def test_error_reports_column() -> None:
    with pytest.raises(TemplateSemanticError) as info:
        parse_expression("a + * b")
    assert "column 5" in info.value.message


def test_expression_str_is_source() -> None:
    assert str(parse_expression("a+b")) == "a+b"
