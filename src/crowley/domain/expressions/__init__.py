"""Expression language: lexer, parser, AST, evaluator, helpers and static analysis."""

from crowley.domain.expressions.analysis import Analysis, HelperCall, Reference, analyze
from crowley.domain.expressions.ast import Expression, Span
from crowley.domain.expressions.embedding import (
    Compiled,
    Interpolation,
    Scalar,
    Text,
    contains_expression,
    parse_scalar,
)
from crowley.domain.expressions.evaluator import MAX_STEPS, EvalEnv, evaluate, evaluate_scalar
from crowley.domain.expressions.helpers import HelperContext, HelperSpec, HelperTable
from crowley.domain.expressions.parser import parse_expression

__all__ = [
    "MAX_STEPS",
    "Analysis",
    "Compiled",
    "EvalEnv",
    "Expression",
    "HelperCall",
    "HelperContext",
    "HelperSpec",
    "HelperTable",
    "Interpolation",
    "Reference",
    "Scalar",
    "Span",
    "Text",
    "analyze",
    "contains_expression",
    "evaluate",
    "evaluate_scalar",
    "parse_expression",
    "parse_scalar",
]
