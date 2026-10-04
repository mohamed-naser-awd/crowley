"""Property tests: the parser only ever fails with E321/E322, whatever the input."""

from collections.abc import Callable

from hypothesis import given, settings
from hypothesis import strategies as st

from crowley.domain.errors import TemplateSemanticError
from crowley.domain.expressions import parse_expression, parse_scalar

ALLOWED = (None, "E321", "E322")


def _error_code(parse: Callable[[str], object], text: str) -> str | None:
    try:
        parse(text)
    except TemplateSemanticError as exc:
        return exc.code
    return None


_ALPHABET = st.sampled_from(list("abcxyz_019 .,:'\"()[]{}*+-/%<>=!?$#\\ifnotandorelsein=>\n"))
_TOKENS = st.sampled_from(
    [
        "x",
        "y",
        "1",
        "2.5",
        "'s'",
        "null",
        "true",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        ",",
        ":",
        ".",
        "?.",
        "[*]",
        "+",
        "-",
        "*",
        "/",
        "//",
        "%",
        "==",
        "<",
        ">=",
        "and",
        "or",
        "not",
        "in",
        "if",
        "else",
        "=>",
        "map",
        "len",
    ]
)


@settings(max_examples=400, deadline=None)
@given(st.text(_ALPHABET, max_size=60))
def test_parse_expression_never_crashes(source: str) -> None:
    assert _error_code(parse_expression, source) in ALLOWED


@settings(max_examples=400, deadline=None)
@given(st.lists(_TOKENS, max_size=25).map(" ".join))
def test_token_soup_never_crashes(source: str) -> None:
    assert _error_code(parse_expression, source) in ALLOWED


@settings(max_examples=300, deadline=None)
@given(st.text(_ALPHABET, max_size=60))
def test_parse_scalar_never_crashes(text: str) -> None:
    assert _error_code(parse_scalar, text) in ALLOWED
