"""The executor: one handler per step kind, all run through the StepPipeline (SPEC §7, §8)."""

from crowley.application.runtime.evaluation import evaluate_expr, evaluate_node
from crowley.application.runtime.frame import Frame
from crowley.application.runtime.pipeline import StepPipeline, step_info
from crowley.application.runtime.signals import (
    BREAK,
    CONTINUE,
    PASSTHROUGH,
    Break,
    Continue,
    HandlerResult,
    Return,
    StepOutcome,
)
from crowley.application.runtime.state import RunState
from crowley.domain.errors import ConfigurationError, ExecutionError, LimitError, ValidationError
from crowley.domain.events import (
    ItemEmit,
    LogEvent,
    LoopEnd,
    LoopIterationAfter,
    LoopIterationBefore,
    LoopStart,
    Skip,
    VariableSet,
)
from crowley.domain.expressions import Compiled
from crowley.domain.functions import Outcome
from crowley.domain.steps import (
    AssertStep,
    Block,
    BreakStep,
    ContinueStep,
    EmitStep,
    FailStep,
    ForEachStep,
    IfStep,
    LogStep,
    ReturnStep,
    SetStep,
    UseStep,
    ValueNode,
    WhileStep,
)
from crowley.domain.values import Value, to_str, type_of

Kwargs = dict[str, Value] | None


class Executor:
    """Walks blocks of steps. Control flow travels as signals returned from the pipeline."""

    def __init__(self, state: RunState) -> None:
        self.state = state
        self.pipeline = StepPipeline(state)
        register = self.pipeline.register
        register("use", self._use)
        register("if", self._if)
        register("for_each", self._for_each)
        register("while", self._while)
        register("set", self._set)
        register("emit", self._emit)
        register("return", self._return)
        register("break", self._break)
        register("continue", self._continue)
        register("fail", self._fail)
        register("assert", self._assert)
        register("log", self._log)

    # ── blocks ─────────────────────────────────────────────────────────────────

    async def run_block(self, block: Block, frame: Frame, prev: Value) -> StepOutcome:
        """Run the steps of ``block`` in order, piping ``prev`` (SPEC §8.4)."""
        for step in block.steps:
            outcome = await self.pipeline.run(step, frame, prev)
            if outcome.signal is not None:
                return outcome
            prev = outcome.prev
        return StepOutcome(prev)

    async def run_owner(self, block: Block, frame: Frame, prev: Value) -> Outcome:
        """Run a block-owner body: its value is the ``return`` value or the final ``prev``."""
        outcome = await self.run_block(block, frame, prev)
        match outcome.signal:
            case Return(value=value):
                return Outcome(value=value)
            case Break():
                return Outcome(has_value=False, is_break=True)
            case Continue():
                return Outcome(has_value=False)
        return Outcome(value=outcome.prev)

    def _eval(self, node: ValueNode, frame: Frame, prev: Value) -> Value:
        return evaluate_node(node, frame.scope(prev), self.state.env)

    # ── step kinds ─────────────────────────────────────────────────────────────

    async def _use(self, step: UseStep, frame: Frame, prev: Value, kwargs: Kwargs) -> HandlerResult:
        raise ConfigurationError(
            "E903", f"function {step.uses!r} can't be called: no function invoker is configured"
        )

    async def _if(self, step: IfStep, frame: Frame, prev: Value, kwargs: Kwargs) -> HandlerResult:
        taken = await self.pipeline.condition(step.condition, step_info(step), frame, prev)
        branch = step.then if taken else step.otherwise
        if branch is None:
            return HandlerResult(value=prev)
        outcome = await self.run_block(branch, frame.child(), prev)
        if isinstance(outcome.signal, Return):
            return HandlerResult(value=outcome.signal.value, signal=outcome.signal)
        return HandlerResult(value=outcome.prev, signal=outcome.signal)

    async def _for_each(
        self, step: ForEachStep, frame: Frame, prev: Value, kwargs: Kwargs
    ) -> HandlerResult:
        state, info, path = self.state, step_info(step), str(step.path)
        items = _require_list(self._eval(step.items, frame, prev), str(step.items.path), "items")
        if step.concurrency is not None:
            _concurrency(self._eval(step.concurrency, frame, prev), str(step.concurrency.path))
        start, _ = await state.publish(
            LoopStart, step=info, frame=frame, scope_prev=prev, items=items
        )
        items = _require_list(start.items, path, "items (after loop.start)")
        results: list[Value] = []
        length = len(items)
        for index, item in enumerate(items):
            state.guard.check_iteration(index + 1, path)
            last = index == length - 1
            loop: dict[str, Value] = {"index": index, "first": index == 0, "last": last}
            loop["length"] = length
            child = frame.child(owner=True, iteration=index, bindings={"loop": loop})
            if step.index_as:
                child.bindings[step.index_as] = index
            before, outcome = await state.publish(
                LoopIterationBefore, step=info, frame=child, scope_prev=prev, binding=item
            )
            if isinstance(outcome.action, Skip):
                continue
            child.bindings[step.as_name] = before.binding
            result = await self.run_owner(step.body, child, before.binding)
            if await self._iteration_after(step, child, result, results):
                break
        end, _ = await state.publish(
            LoopEnd, step=info, frame=frame, scope_prev=prev, result=results
        )
        return HandlerResult(value=end.result)

    async def _while(
        self, step: WhileStep, frame: Frame, prev: Value, kwargs: Kwargs
    ) -> HandlerResult:
        state, info, path = self.state, step_info(step), str(step.path)
        await state.publish(LoopStart, step=info, frame=frame, scope_prev=prev)
        results: list[Value] = []
        loop_prev = prev
        count = 0
        while await self.pipeline.condition(step.condition, info, frame, loop_prev):
            count += 1
            if count > step.max_iterations:
                raise LimitError(
                    "E702",
                    f"while reached max_iterations ({step.max_iterations}) and its condition "
                    "is still true",
                    path=path,
                    location=step.location,
                    hint="raise max_iterations, or use `break` to end the loop early",
                )
            state.guard.check_iteration(count, path)
            index = count - 1
            loop: dict[str, Value] = {"index": index, "first": index == 0}
            child = frame.child(owner=True, iteration=index, bindings={"loop": loop})
            _, outcome = await state.publish(
                LoopIterationBefore, step=info, frame=child, scope_prev=loop_prev
            )
            if isinstance(outcome.action, Skip):
                continue
            result = await self.run_owner(step.body, child, loop_prev)
            stop = await self._iteration_after(step, child, result, results)
            if result.has_value:
                loop_prev = results[-1]
            if stop:
                break
        end, _ = await state.publish(
            LoopEnd, step=info, frame=frame, scope_prev=prev, result=results
        )
        return HandlerResult(value=end.result)

    async def _iteration_after(
        self, step: ForEachStep | WhileStep, child: Frame, result: Outcome, results: list[Value]
    ) -> bool:
        """Fire ``loop.iteration.after``, collect the value; True when the loop must stop."""
        after, _ = await self.state.publish(
            LoopIterationAfter,
            step=step_info(step),
            frame=child,
            scope_prev=result.value,
            value=result.value,
            stop=False,
        )
        if result.has_value:
            results.append(after.value)
        return result.is_break or bool(after.stop)

    async def _set(self, step: SetStep, frame: Frame, prev: Value, kwargs: Kwargs) -> HandlerResult:
        info = step_info(step)
        for name, node in step.assignments:
            value = self._eval(node, frame, prev)
            event, _ = await self.state.publish(
                VariableSet, step=info, frame=frame, scope_prev=prev, variable=name, value=value
            )
            frame.set_var(name, event.value)
        return PASSTHROUGH

    async def _emit(
        self, step: EmitStep, frame: Frame, prev: Value, kwargs: Kwargs
    ) -> HandlerResult:
        state, path = self.state, str(step.path)
        item = self._eval(step.value, frame, prev)
        event, outcome = await state.publish(
            ItemEmit, step=step_info(step), frame=frame, scope_prev=prev, item=item
        )
        if isinstance(outcome.action, Skip):
            return PASSTHROUGH
        if state.validate_item is not None:
            state.validate_item(event.item, path)
        state.guard.check_items(len(state.output) + 1, path)
        state.output.add(event.item)
        state.stats.items += 1
        return PASSTHROUGH

    async def _return(
        self, step: ReturnStep, frame: Frame, prev: Value, kwargs: Kwargs
    ) -> HandlerResult:
        value = self._eval(step.value, frame, prev)
        return HandlerResult(value=value, signal=Return(value))

    async def _break(
        self, step: BreakStep, frame: Frame, prev: Value, kwargs: Kwargs
    ) -> HandlerResult:
        return HandlerResult(signal=BREAK)

    async def _continue(
        self, step: ContinueStep, frame: Frame, prev: Value, kwargs: Kwargs
    ) -> HandlerResult:
        return HandlerResult(signal=CONTINUE)

    async def _fail(
        self, step: FailStep, frame: Frame, prev: Value, kwargs: Kwargs
    ) -> HandlerResult:
        message = to_str(self._eval(step.message, frame, prev))
        raise ExecutionError(
            "E503", message, path=str(step.path), location=step.location, user_code=step.code
        )

    async def _assert(
        self, step: AssertStep, frame: Frame, prev: Value, kwargs: Kwargs
    ) -> HandlerResult:
        value = evaluate_expr(step.condition, frame.scope(prev), self.state.env)
        if not isinstance(value, bool):
            raise ExecutionError(
                "E501",
                f"assert condition must be a bool, got {type_of(value)}",
                path=str(step.condition.path),
                location=step.condition.location,
                value=value,
            )
        if value:
            return PASSTHROUGH
        if step.message is not None:
            message = to_str(self._eval(step.message, frame, prev))
        else:
            scalar = step.condition.scalar
            source = scalar.expression.source if isinstance(scalar, Compiled) else "condition"
            message = f"assertion failed: {source}"
        raise ValidationError("E406", message, path=str(step.path), location=step.location)

    async def _log(self, step: LogStep, frame: Frame, prev: Value, kwargs: Kwargs) -> HandlerResult:
        message = to_str(self._eval(step.message, frame, prev))
        await self.state.publish(
            LogEvent,
            step=step_info(step),
            frame=frame,
            scope_prev=prev,
            level=step.level.value,
            message=message,
        )
        return PASSTHROUGH


def _require_list(value: Value, path: str, what: str) -> list[Value]:
    if not isinstance(value, list):
        raise ExecutionError(
            "E501", f"for_each {what} must be a list, got {type_of(value)}", path=path, value=value
        )
    return value


def _concurrency(value: Value, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExecutionError(
            "E501", f"concurrency must be a positive integer, got {value!r}", path=path
        )
    return value
