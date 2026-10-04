"""Recursive-descent parser for the expression grammar (docs/SPEC.md §10.2)."""

from collections.abc import Iterator
from contextlib import contextmanager

from crowley.domain.errors import TemplateSemanticError
from crowley.domain.expressions import ast
from crowley.domain.expressions.lexer import Kind, Token, syntax_error, tokenize

MAX_NESTING = 64

_COMPARISON_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})


def parse_expression(source: str) -> ast.Expression:
    """Parse one expression (the text between ``${{`` and ``}}``).

    Raises ``E321`` for syntax errors and ``E322`` for a lambda outside a helper argument.
    """
    return ast.Expression(source=source, node=_Parser(source).parse())


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = tokenize(source)
        self.pos = 0
        self.depth = 0

    # ── token helpers ─────────────────────────────────────────────────────────
    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset: int = 1) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        token = self.tok
        if token.kind is not Kind.END:
            self.pos += 1
        return token

    def at_end(self) -> bool:
        return self.tok.kind is Kind.END

    def is_op(self, text: str, token: Token | None = None) -> bool:
        t = token or self.tok
        return t.kind is Kind.OP and t.text == text

    def is_kw(self, text: str, token: Token | None = None) -> bool:
        t = token or self.tok
        return t.kind is Kind.KEYWORD and t.text == text

    def expect_op(self, text: str) -> Token:
        if not self.is_op(text):
            raise self.error(f"expected {text!r}")
        return self.advance()

    def error(self, message: str, token: Token | None = None) -> TemplateSemanticError:
        t = token or self.tok
        found = "end of expression" if t.kind is Kind.END else repr(t.text)
        return syntax_error(f"{message}, found {found}", self.source, t.start)

    def span(self, start: int) -> ast.Span:
        end = self.tokens[self.pos - 1].end if self.pos else start
        return ast.Span(start=start, end=max(end, start))

    @contextmanager
    def nested(self) -> Iterator[None]:
        self.depth += 1
        if self.depth > MAX_NESTING:
            raise syntax_error(
                f"expression nested deeper than {MAX_NESTING} levels", self.source, self.tok.start
            )
        try:
            yield
        finally:
            self.depth -= 1

    # ── grammar ───────────────────────────────────────────────────────────────
    def parse(self) -> ast.Node:
        if self.at_end():
            raise syntax_error("empty expression", self.source, 0)
        node = self.expr(allow_lambda=False)
        if not self.at_end():
            raise self.error("unexpected token")
        return node

    def expr(self, *, allow_lambda: bool) -> ast.Node:
        with self.nested():
            if self.at_lambda():
                if not allow_lambda:
                    raise TemplateSemanticError(
                        "E322",
                        f"lambda used outside a helper argument at column {self.tok.start + 1} "
                        f"of expression {self.source!r}",
                        value=self.source,
                        hint="lambdas like 'x => x.id' are only allowed as helper arguments, "
                        "e.g. map(items, x => x.id)",
                    )
                return self.lambda_()
            return self.conditional()

    def at_lambda(self) -> bool:
        if self.tok.kind is Kind.NAME and self.is_op("=>", self.peek()):
            return True
        if not self.is_op("("):
            return False
        i = 1
        while True:
            if self.peek(i).kind is not Kind.NAME:
                return False
            nxt = self.peek(i + 1)
            if self.is_op(")", nxt):
                return self.is_op("=>", self.peek(i + 2))
            if not self.is_op(",", nxt):
                return False
            i += 2

    def lambda_(self) -> ast.Node:
        start = self.tok.start
        params: list[str] = []
        if self.is_op("("):
            self.advance()
            while True:
                params.append(str(self.advance().value))
                if self.is_op(")"):
                    self.advance()
                    break
                self.advance()  # ","
        else:
            params.append(str(self.advance().value))
        if len(set(params)) != len(params):
            raise syntax_error("duplicate lambda parameter", self.source, start)
        self.expect_op("=>")
        body = self.expr(allow_lambda=False)
        return ast.Lambda(params=tuple(params), body=body, span=self.span(start))

    def conditional(self) -> ast.Node:
        start = self.tok.start
        then = self.or_expr()
        if not self.is_kw("if"):
            return then
        self.advance()
        test = self.or_expr()
        if not self.is_kw("else"):
            raise self.error("expected 'else'")
        self.advance()
        otherwise = self.expr(allow_lambda=False)
        return ast.Conditional(test=test, then=then, otherwise=otherwise, span=self.span(start))

    def or_expr(self) -> ast.Node:
        start = self.tok.start
        left = self.and_expr()
        while self.is_kw("or"):
            self.advance()
            right = self.and_expr()
            left = ast.BoolOp(op="or", left=left, right=right, span=self.span(start))
        return left

    def and_expr(self) -> ast.Node:
        start = self.tok.start
        left = self.not_expr()
        while self.is_kw("and"):
            self.advance()
            right = self.not_expr()
            left = ast.BoolOp(op="and", left=left, right=right, span=self.span(start))
        return left

    def not_expr(self) -> ast.Node:
        if self.is_kw("not"):
            start = self.advance().start
            with self.nested():
                operand = self.not_expr()
            return ast.Unary(op="not", operand=operand, span=self.span(start))
        return self.comparison()

    def comparison(self) -> ast.Node:
        start = self.tok.start
        first = self.additive()
        rest: list[tuple[str, ast.Node]] = []
        while True:
            if self.tok.kind is Kind.OP and self.tok.text in _COMPARISON_OPS:
                op = self.advance().text
            elif self.is_kw("in"):
                self.advance()
                op = "in"
            elif self.is_kw("not") and self.is_kw("in", self.peek()):
                self.advance()
                self.advance()
                op = "not in"
            else:
                break
            rest.append((op, self.additive()))
        if not rest:
            return first
        return ast.Compare(first=first, rest=tuple(rest), span=self.span(start))

    def additive(self) -> ast.Node:
        start = self.tok.start
        left = self.term()
        while self.tok.kind is Kind.OP and self.tok.text in ("+", "-"):
            op = self.advance().text
            left = ast.Binary(op=op, left=left, right=self.term(), span=self.span(start))
        return left

    def term(self) -> ast.Node:
        start = self.tok.start
        left = self.unary()
        while self.tok.kind is Kind.OP and self.tok.text in ("*", "/", "//", "%"):
            op = self.advance().text
            left = ast.Binary(op=op, left=left, right=self.unary(), span=self.span(start))
        return left

    def unary(self) -> ast.Node:
        if self.is_op("-"):
            start = self.advance().start
            with self.nested():
                operand = self.unary()
            return ast.Unary(op="-", operand=operand, span=self.span(start))
        return self.postfix_ops(self.primary(), self.tok.start)

    def postfix_ops(self, node: ast.Node, start: int) -> ast.Node:
        while True:
            if self.is_op(".") or self.is_op("?."):
                optional = self.advance().text == "?."
                name_tok = self.advance()
                if name_tok.kind not in (Kind.NAME, Kind.KEYWORD):
                    raise self.error("expected a member name", name_tok)
                node = ast.Member(
                    target=node, name=name_tok.text, optional=optional, span=self.span(start)
                )
            elif self.is_op("[*]"):
                star = self.advance()
                with self.nested():
                    body = self.postfix_ops(
                        ast.Current(span=ast.Span(start=star.start, end=star.end)), star.start
                    )
                return ast.Projection(source=node, body=body, span=self.span(start))
            elif self.is_op("["):
                node = self.subscript(node, start)
            elif self.is_op("("):
                if not isinstance(node, ast.Name):
                    raise self.error("calls are only allowed on helper names")
                node = self.call(node, start)
            else:
                return node

    def subscript(self, target: ast.Node, start: int) -> ast.Node:
        self.expect_op("[")
        lower = None if self.is_op(":") else self.expr(allow_lambda=False)
        if not self.is_op(":"):
            if lower is None:  # pragma: no cover - guarded by the branch above
                raise self.error("expected an index")
            self.expect_op("]")
            return ast.Index(target=target, index=lower, span=self.span(start))
        self.advance()
        upper = None if self.is_op(":") or self.is_op("]") else self.expr(allow_lambda=False)
        step = None
        if self.is_op(":"):
            self.advance()
            step = None if self.is_op("]") else self.expr(allow_lambda=False)
        self.expect_op("]")
        return ast.Slice(target=target, start=lower, stop=upper, step=step, span=self.span(start))

    def call(self, callee: ast.Name, start: int) -> ast.Node:
        self.expect_op("(")
        args: list[ast.Node] = []
        if not self.is_op(")"):
            while True:
                args.append(self.expr(allow_lambda=True))
                if self.is_op(")"):
                    break
                self.expect_op(",")
        self.expect_op(")")
        return ast.Call(name=callee.name, args=tuple(args), span=self.span(start))

    def primary(self) -> ast.Node:
        tok = self.tok
        start = tok.start
        if tok.kind in (Kind.NUMBER, Kind.STRING):
            self.advance()
            return ast.Literal(value=tok.value, span=self.span(start))  # type: ignore[arg-type]
        if tok.kind is Kind.KEYWORD and tok.text in ("null", "true", "false"):
            self.advance()
            value = {"null": None, "true": True, "false": False}[tok.text]
            return ast.Literal(value=value, span=self.span(start))
        if tok.kind is Kind.NAME:
            self.advance()
            return ast.Name(name=tok.text, span=self.span(start))
        if self.is_op("("):
            self.advance()
            inner = self.expr(allow_lambda=False)
            self.expect_op(")")
            return inner
        if self.is_op("["):
            return self.list_literal()
        if self.is_op("{"):
            return self.object_literal()
        raise self.error("expected a value")

    def list_literal(self) -> ast.Node:
        start = self.expect_op("[").start
        items: list[ast.Node] = []
        with self.nested():
            if not self.is_op("]"):
                while True:
                    items.append(self.expr(allow_lambda=False))
                    if self.is_op("]"):
                        break
                    self.expect_op(",")
            self.expect_op("]")
        return ast.ListLiteral(items=tuple(items), span=self.span(start))

    def object_literal(self) -> ast.Node:
        start = self.expect_op("{").start
        entries: list[tuple[str, ast.Node]] = []
        seen: set[str] = set()
        with self.nested():
            if not self.is_op("}"):
                while True:
                    key_tok = self.advance()
                    if key_tok.kind not in (Kind.NAME, Kind.STRING, Kind.KEYWORD):
                        raise self.error("expected an object key", key_tok)
                    key = str(key_tok.value)
                    if key in seen:
                        raise syntax_error(f"duplicate key {key!r}", self.source, key_tok.start)
                    seen.add(key)
                    self.expect_op(":")
                    entries.append((key, self.expr(allow_lambda=False)))
                    if self.is_op("}"):
                        break
                    self.expect_op(",")
            self.expect_op("}")
        return ast.ObjectLiteral(entries=tuple(entries), span=self.span(start))
