"""Tokenizer for the expression language (docs/SPEC.md §10.2)."""

from dataclasses import dataclass
from enum import Enum

from crowley.domain.errors import TemplateSemanticError

MAX_SOURCE_LENGTH = 4096

KEYWORDS = frozenset({"null", "true", "false", "and", "or", "not", "in", "if", "else"})

# Longest first, so "//" wins over "/" and "?." over "?".
OPERATORS = (
    "[*]",
    "=>",
    "==",
    "!=",
    "<=",
    ">=",
    "//",
    "?.",
    "<",
    ">",
    "+",
    "-",
    "*",
    "/",
    "%",
    "(",
    ")",
    "[",
    "]",
    "{",
    "}",
    ",",
    ":",
    ".",
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "0": "\0"}


class Kind(Enum):
    NUMBER = "number"
    STRING = "string"
    NAME = "name"
    KEYWORD = "keyword"
    OP = "op"
    END = "end"


@dataclass(frozen=True, slots=True, kw_only=True)
class Token:
    kind: Kind
    text: str
    value: object
    start: int
    end: int


def syntax_error(message: str, source: str, position: int) -> TemplateSemanticError:
    """``E321`` pointing at a 1-based column inside the expression source."""
    return TemplateSemanticError(
        "E321",
        f"{message} at column {position + 1} of expression {source!r}",
        value=source,
    )


def tokenize(source: str) -> list[Token]:
    if len(source) > MAX_SOURCE_LENGTH:
        raise syntax_error(
            f"expression longer than {MAX_SOURCE_LENGTH} characters", source[:40] + "…", 0
        )
    tokens: list[Token] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit():
            i = _number(source, i, tokens)
            continue
        if ch in "'\"":
            i = _string(source, i, tokens)
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j]
            if not word.isascii():
                raise syntax_error(f"invalid name {word!r}", source, i)
            kind = Kind.KEYWORD if word in KEYWORDS else Kind.NAME
            tokens.append(Token(kind=kind, text=word, value=word, start=i, end=j))
            i = j
            continue
        if source.startswith("[", i):
            # "[*]" may contain spaces: "[ * ]"
            j = i + 1
            while j < n and source[j].isspace():
                j += 1
            if j < n and source[j] == "*":
                k = j + 1
                while k < n and source[k].isspace():
                    k += 1
                if k < n and source[k] == "]":
                    tokens.append(Token(kind=Kind.OP, text="[*]", value="[*]", start=i, end=k + 1))
                    i = k + 1
                    continue
        for op in OPERATORS:
            if source.startswith(op, i):
                tokens.append(Token(kind=Kind.OP, text=op, value=op, start=i, end=i + len(op)))
                i += len(op)
                break
        else:
            raise syntax_error(f"unexpected character {ch!r}", source, i)
    tokens.append(Token(kind=Kind.END, text="", value=None, start=n, end=n))
    return tokens


def _number(source: str, i: int, tokens: list[Token]) -> int:
    n, j = len(source), i
    while j < n and source[j].isdigit():
        j += 1
    is_float = False
    if j + 1 < n and source[j] == "." and source[j + 1].isdigit():
        is_float = True
        j += 1
        while j < n and source[j].isdigit():
            j += 1
    if j < n and source[j] in "eE":
        k = j + 1
        if k < n and source[k] in "+-":
            k += 1
        if k < n and source[k].isdigit():
            is_float = True
            j = k
            while j < n and source[j].isdigit():
                j += 1
    text = source[i:j]
    if j < n and (source[j].isalpha() or source[j] == "_"):
        raise syntax_error(f"invalid number {source[i : j + 1]!r}", source, i)
    value: object = float(text) if is_float else int(text)
    tokens.append(Token(kind=Kind.NUMBER, text=text, value=value, start=i, end=j))
    return j


def _string(source: str, i: int, tokens: list[Token]) -> int:
    quote, n = source[i], len(source)
    j = i + 1
    out: list[str] = []
    while j < n:
        ch = source[j]
        if ch == quote:
            text = source[i : j + 1]
            tokens.append(
                Token(kind=Kind.STRING, text=text, value="".join(out), start=i, end=j + 1)
            )
            return j + 1
        if ch == "\\":
            if j + 1 >= n:
                break
            esc = source[j + 1]
            if esc == "u":
                code = source[j + 2 : j + 6]
                if len(code) != 4 or not all(c in "0123456789abcdefABCDEF" for c in code):
                    raise syntax_error("invalid \\u escape", source, j)
                out.append(chr(int(code, 16)))
                j += 6
                continue
            if esc not in _ESCAPES:
                raise syntax_error(f"invalid escape \\{esc}", source, j)
            out.append(_ESCAPES[esc])
            j += 2
            continue
        out.append(ch)
        j += 1
    raise syntax_error("unterminated string", source, i)
