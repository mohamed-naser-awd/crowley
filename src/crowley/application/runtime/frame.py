"""Scopes: frames holding step results, variables, bindings (docs/SPEC.md §8.2)."""

from collections.abc import Iterator, Mapping
from typing import Any

from crowley.domain.values import Value


class Frame:
    """One lexical block scope. Frames chain to their parent; lookups merge outer to inner.

    ``roots`` are the run-wide names (``inputs`` or ``args``, ``secrets``, ``run``). A template
    function body starts a new chain with its own roots, so it can't see the caller's scope.
    """

    __slots__ = ("bindings", "depth", "iteration", "owner", "parent", "roots", "steps", "vars")

    def __init__(
        self,
        *,
        roots: Mapping[str, Value],
        parent: "Frame | None" = None,
        bindings: Mapping[str, Value] | None = None,
        owner: bool = False,
        depth: int = 0,
        iteration: int | None = None,
    ) -> None:
        self.parent = parent
        self.roots = roots
        self.steps: dict[str, Value] = {}
        self.vars: dict[str, Value] = {}
        self.bindings: dict[str, Value] = dict(bindings or {})
        self.owner = owner
        """Block owner (loop iteration, block-function body, template function): ends on return."""
        self.depth = depth
        self.iteration = iteration

    def child(
        self,
        *,
        bindings: Mapping[str, Value] | None = None,
        owner: bool = False,
        iteration: int | None = None,
    ) -> "Frame":
        """A nested block scope (``then``/``else``, loop and function bodies)."""
        return Frame(
            roots=self.roots,
            parent=self,
            bindings=bindings,
            owner=owner,
            depth=self.depth + 1,
            iteration=self.iteration if iteration is None else iteration,
        )

    def _chain(self) -> Iterator["Frame"]:
        frames: list[Frame] = []
        frame: Frame | None = self
        while frame is not None:
            frames.append(frame)
            frame = frame.parent
        return reversed(frames)

    def record(self, step_id: str, value: Value) -> None:
        """Store a step result as ``steps.<id>`` in this block."""
        self.steps[step_id] = value

    def set_var(self, name: str, value: Value) -> None:
        """Assign to the nearest frame where ``name`` exists, else create it here (§8.2)."""
        frame: Frame | None = self
        while frame is not None:
            if name in frame.vars:
                frame.vars[name] = value
                return
            frame = frame.parent
        self.vars[name] = value

    def scope(self, prev: Value, extra: Mapping[str, Value] | None = None) -> dict[str, Any]:
        """The expression scope: roots, bindings (inner shadows outer), steps, vars, prev."""
        scope: dict[str, Any] = dict(self.roots)
        steps: dict[str, Value] = {}
        variables: dict[str, Value] = {}
        for frame in self._chain():
            scope.update(frame.bindings)
            steps.update(frame.steps)
            variables.update(frame.vars)
        scope["steps"] = steps
        scope["vars"] = variables
        scope["prev"] = prev
        if extra:
            scope.update(extra)
        return scope
