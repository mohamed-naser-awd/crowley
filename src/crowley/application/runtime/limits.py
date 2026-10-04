"""Runtime limit checks (docs/SPEC.md §6). Every violation is E701 and never catchable."""

import time

from crowley.domain.errors import ControlError, LimitError
from crowley.domain.template import Limits


class LimitsGuard:
    """Checks the effective limits at the runtime's choke points."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits.resolved()
        self.started = time.monotonic()
        self.cancel_requested = False

    @property
    def max_retries(self) -> int:
        return self.limits.max_retries_per_step or 0

    def check_step(self, depth: int, path: str) -> None:
        """Called before every step: cancellation, ``max_duration`` and ``max_depth``."""
        if self.cancel_requested:
            raise ControlError("E803", "the run was cancelled", path=path)
        duration = self.limits.max_duration
        if duration is not None and time.monotonic() - self.started > duration.seconds:
            raise LimitError("E701", f"limits.max_duration of {duration} exceeded", path=path)
        max_depth = self.limits.max_depth
        if max_depth is not None and depth > max_depth:
            raise LimitError(
                "E701",
                f"limits.max_depth of {max_depth} exceeded (nesting depth {depth})",
                path=path,
            )

    def check_iteration(self, count: int, path: str) -> None:
        """Called before loop iteration number ``count`` (1-based)."""
        limit = self.limits.max_loop_iterations
        if limit is not None and count > limit:
            raise LimitError("E701", f"limits.max_loop_iterations of {limit} exceeded", path=path)

    def check_items(self, count: int, path: str) -> None:
        """Called before adding emitted item number ``count`` (1-based)."""
        limit = self.limits.max_items
        if limit is not None and count > limit:
            raise LimitError("E701", f"limits.max_items of {limit} exceeded", path=path)
