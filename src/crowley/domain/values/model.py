"""Runtime value model (docs/SPEC.md §9)."""

from dataclasses import dataclass
from typing import TypeAlias, Union


@dataclass(frozen=True, slots=True, eq=False)
class Handle:
    """Opaque reference created by a function (an HTML node, a session, ...).

    Handles can be passed between functions but are never inspected by expressions and never
    appear in output. Equality is identity.
    """

    type_name: str
    payload: object

    def __repr__(self) -> str:
        return f"<handle {self.type_name}>"


Value: TypeAlias = Union[  # noqa: UP007 - recursive alias needs the string forward references
    bool, int, float, str, bytes, Handle, list["Value"], dict[str, "Value"], None
]
"""Any value a template expression can see."""
