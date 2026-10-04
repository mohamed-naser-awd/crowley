"""``${{ ... }}`` inside YAML scalars (docs/SPEC.md §10.1)."""

from dataclasses import dataclass

from crowley.domain.errors import TemplateSemanticError
from crowley.domain.expressions.ast import Expression
from crowley.domain.expressions.parser import parse_expression

OPEN = "${{"
CLOSE = "}}"
ESCAPED_OPEN = "$${{"


@dataclass(frozen=True, slots=True, kw_only=True)
class Text:
    """A plain string with no expressions (escapes already resolved)."""

    value: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Compiled:
    """A scalar that is exactly one ``${{ expr }}``; evaluates to a typed value."""

    expression: Expression


@dataclass(frozen=True, slots=True, kw_only=True)
class Interpolation:
    """Text mixed with expressions; evaluates to a string."""

    parts: tuple[str | Expression, ...]

    @property
    def expressions(self) -> tuple[Expression, ...]:
        return tuple(p for p in self.parts if isinstance(p, Expression))


Scalar = Text | Compiled | Interpolation


def contains_expression(text: str) -> bool:
    return OPEN in text.replace(ESCAPED_OPEN, "")


def parse_scalar(text: str) -> Scalar:
    """Split a string into literal text and compiled expressions.

    Raises ``E321`` for an unterminated ``${{`` or a syntax error inside it.
    """
    parts: list[str | Expression] = []
    buffer: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith(ESCAPED_OPEN, i):
            buffer.append(OPEN)
            i += len(ESCAPED_OPEN)
            continue
        if text.startswith(OPEN, i):
            end = _find_close(text, i + len(OPEN))
            source = text[i + len(OPEN) : end].strip()
            if buffer:
                parts.append("".join(buffer))
                buffer = []
            parts.append(parse_expression(source))
            i = end + len(CLOSE)
            continue
        buffer.append(text[i])
        i += 1
    if buffer:
        parts.append("".join(buffer))

    expressions = [p for p in parts if isinstance(p, Expression)]
    if not expressions:
        return Text(value="".join(p for p in parts if isinstance(p, str)))
    literal_text = "".join(p for p in parts if isinstance(p, str))
    if len(expressions) == 1 and not literal_text.strip():
        return Compiled(expression=expressions[0])
    return Interpolation(parts=tuple(parts))


def _find_close(text: str, start: int) -> int:
    """Index of the ``}}`` that closes an expression, skipping strings and nested braces."""
    depth = 0
    quote: str | None = None
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0 and text.startswith(CLOSE, i):
                return i
            depth -= 1
            if depth < 0:
                break
        i += 1
    raise TemplateSemanticError(
        "E321",
        f"unterminated '${{{{' in {text!r}",
        value=text,
        hint="close the expression with '}}' or write '$${{' for a literal '${{'",
    )
