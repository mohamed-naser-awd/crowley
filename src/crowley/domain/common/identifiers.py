"""Identifier rules (docs/SPEC.md §1, §2.1) and template references (§11.5)."""

import re
from dataclasses import dataclass

from crowley.domain.common.semver import SemVerRange

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
"""Operation names, step ids, variable names, template-function names, namespaces."""

FUNCTION_NAME = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
"""Namespaced function names: ``<namespace>.<name>``."""

TEMPLATE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")
"""Template ids: ``owner/name``."""

SECRET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

RESERVED_NAMESPACES = frozenset(
    {
        "crowley",
        "paginate",
        "transform",
        "control",
        "extract",
        "local",
        "template",
        "http",
        "html",
        "xml",
        "json",
        "text",
        "browser",
    }
)

RESERVED_ROOTS = frozenset(
    {
        "prev",
        "inputs",
        "secrets",
        "steps",
        "vars",
        "args",
        "loop",
        "run",
        "page",
        "item",
        "result",
        "error",
    }
)


def is_identifier(text: str) -> bool:
    return bool(IDENTIFIER.match(text))


def is_function_name(text: str) -> bool:
    return bool(FUNCTION_NAME.match(text))


def is_template_id(text: str) -> bool:
    return bool(TEMPLATE_ID.match(text))


def split_function_name(name: str) -> tuple[str, str]:
    """``"http.get"`` → ``("http", "get")``. Raises ``ValueError`` for invalid names."""
    if not is_function_name(name):
        raise ValueError(f"invalid function name {name!r}; expected '<namespace>.<name>'")
    namespace, _, short = name.partition(".")
    return namespace, short


@dataclass(frozen=True, slots=True, kw_only=True)
class TemplateRef:
    """Target of ``use: template:<ref>#<operation>``.

    Exactly one of ``template_id`` (with optional ``version``) or ``path`` is set.
    """

    template_id: str | None = None
    version: SemVerRange | None = None
    path: str | None = None
    operation: str | None = None

    def __str__(self) -> str:
        base = self.path or f"{self.template_id}" + (f"@{self.version}" if self.version else "")
        return f"{base}#{self.operation}" if self.operation else base


def parse_template_ref(text: str) -> TemplateRef:
    """Parse ``owner/name@^1#op`` or ``./login.yml#op`` (the text after ``template:``)."""
    ref, _, operation = text.strip().partition("#")
    if "#" in text and not is_identifier(operation):
        raise ValueError(f"invalid operation name {operation!r} in template reference {text!r}")
    op = operation or None
    if ref.startswith(("./", "../", "/")) or ref.endswith((".yml", ".yaml")):
        return TemplateRef(path=ref, operation=op)
    template_id, _, version = ref.partition("@")
    if not is_template_id(template_id):
        raise ValueError(
            f"invalid template reference {text!r}; expected 'owner/name[@range][#operation]' "
            "or a relative path"
        )
    return TemplateRef(
        template_id=template_id,
        version=SemVerRange.parse(version) if version else None,
        operation=op,
    )
