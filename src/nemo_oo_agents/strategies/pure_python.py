# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""PURE_PYTHON strategy - LLM outputs pure Python code, no tool calling.

Flow:
1. Build prompt (task + agent state + available methods)
2. Call LLM with NO tools - just get text output
3. Validate code (REPL policy: no classes, await async methods)
4. Extract and bind helper methods (before execution for same-turn use)
5. Execute output as Python via runtime.execute_code()
6. If code has return statement OR method returns None → validate and complete
7. Otherwise → send feedback (stdout, defined helpers) and continue loop

Strategy Variants:
- PurePythonStrategy: Strategy instructions in system message (default)
- TaskMessagePurePythonStrategy: All instructions in task message (condensed after success)
"""

import ast
import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_type_hints

from pydantic import ValidationError as PydanticValidationError

from context_blocks import DynamicContext, ResultStatus
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.errors import GenerationError, XMLFormatError
from nemo_oo_agents.events import (
    AfterTurn,
    BeforeTurn,
    Error,
    Feedback,
    Message,
    PythonOutput,
    Reasoning,
    Task,
)
from nemo_oo_agents.runtime.harness_metrics import get_harness_metrics
from nemo_oo_agents.strategies.base import RuntimeServices
from nemo_oo_agents.strategies.codeact_errors import format_validation_error
from nemo_oo_agents.strategies.composite import CompositeStrategy
from nemo_oo_agents.strategies.generated_code import (
    ExecutionNamespaceBuilder,
    GeneratedCodeValidator,
    HelperMethodManager,
    ReturnValueValidator,
)
from nemo_oo_agents.strategies.template import TemplateStrategy

# Import httpx timeout exceptions if available (used by litellm)
try:
    import httpx

    _HTTPX_TIMEOUT_EXCEPTIONS = (
        httpx.TimeoutException,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
    )
except ImportError:
    # httpx not available - define a base exception class for timeout detection
    _HTTPX_TIMEOUT_EXCEPTIONS = (TimeoutError,)  # type: ignore[assignment]

if TYPE_CHECKING:
    from nemo_oo_agents.runtime.event_manager import EventManager
    from nemo_oo_agents.strategies.current_call import CurrentCall
    from nemo_oo_agents.strategies.prefill import Prefill

logger = logging.getLogger(__name__)


@dataclass
class GenerationSession:
    """Tracks state for a single code generation session."""

    max_iterations: int
    max_retries: int
    target_method_name: str
    event_manager: "EventManager | None" = None  # Optional event manager for Out[n]
    iteration: int = 0
    error_count: int = 0
    session_locals: dict[str, Any] = field(default_factory=dict)
    task_event_id: str = ""
    out_accessor: Any = field(default=None)  # OutAccessor instance, created lazily

    def __post_init__(self) -> None:
        """Initialize OutAccessor for Jupyter-style Out[n] access."""
        from nemo_oo_agents.runtime.out_accessor import OutAccessor

        # Use event manager-backed mode if provided, otherwise internal storage
        self.out_accessor = OutAccessor(event_manager=self.event_manager)
        # Make Out available in session namespace for LLM code
        self.session_locals["Out"] = self.out_accessor

    def is_exhausted(self) -> bool:
        return self.iteration >= self.max_iterations or self.error_count >= self.max_retries

    def record_iteration(self) -> None:
        self.iteration += 1

    def record_error(self) -> None:
        self.error_count += 1

    def record_output(self, execution_count: int, value: Any) -> None:
        """Record an execution output for Out[n] access."""
        if self.out_accessor is not None:
            self.out_accessor.record(execution_count, value)

    def build_failure_error(self) -> GenerationError:
        if self.error_count >= self.max_retries:
            return GenerationError(
                f"Generation failed after {self.error_count} errors (max_retries={self.max_retries}). "
                f"Unable to generate valid code for `{self.target_method_name}`."
            )
        return GenerationError(
            f"Generation failed after {self.iteration} iterations (max_iterations={self.max_iterations}). "
            f"Unable to complete `{self.target_method_name}`."
        )


class PurePythonStrategy(CompositeStrategy):
    """LLM generates pure Python code in REPL-style interaction.

    The LLM outputs Python code directly (no markdown, no fences), which is
    executed in a persistent session. The LLM can define helper functions,
    use print() for debugging, and must use return to complete the task.

    Example:
        @strategy(PurePythonStrategy())
        def analyze(self, data: str) -> dict:
            '''Analyze data and return results.'''
            ...

        @strategy(PurePythonStrategy(max_iterations=5))
        def quick_task(self, x: int) -> int:
            '''Custom iteration limit.'''
            ...
    """

    def __init__(
        self,
        *,
        max_iterations: int = 10,
        max_retries: int = 3,
        prefill: "Prefill | None" = None,
    ):
        if prefill is None:
            from nemo_oo_agents.strategies.prefill import InspectInputsPrefill

            prefill = InspectInputsPrefill()
        elif not callable(getattr(prefill, "get_code", None)):
            raise ValueError("Prefill plugin must implement get_code(call) -> str | None")
        self.max_iterations = max_iterations
        self.max_retries = max_retries
        self.prefill = prefill

    @property
    def name(self) -> str:
        return "PURE_PYTHON"

    def _get_truncation_config(self, runtime: RuntimeServices):
        """Get truncation config from runtime's agent, same as CodeActStrategy."""
        from nemo_oo_agents.config.truncation_config import DEFAULT_TRUNCATION_CONFIG

        return getattr(runtime.agent, "_truncation", DEFAULT_TRUNCATION_CONFIG)

    def get_block_overrides(self) -> dict[str, str | DynamicContext | None]:
        return {
            "strategy_prompt": DynamicContext("strategy.strategy_instructions(runtime)"),
        }

    @strategy(TemplateStrategy())
    async def strategy_instructions(self, runtime: RuntimeServices) -> str:
        """## PURE_PYTHON Mode
        You are operating in ReACT/CodeAct loop mode. Where your interface to both the user and the system
        is only through Python code.

        **Output**: Valid Python code only. No markdown, no fences.

        **You are an instance of the class described with `self(doc)` (see above). You are performing your tasks in a persistent REPL session**:
        - If you know the answer → `return result` immediately (one turn)
        - If you need sub methods → define `async def helper(self, ...):` and annotate with strategy decorator. These methods are automatically added to the instance of the class and therefore they persist on `self` for next turn
        - If there are methods defined on self (this is you) that seem useful, use them using await self.method(...) (for async methods) or self.method(...) (for sync methods) (but pay attention to not create recursions)
        - Variables and methods persist across turns until you `return`

        **Available**:
        - `reasoning("...")` - Your thinking (not shown to user)
        - `message("...")` - Message to user
        - `print(...)` - Debug output (returned to you next turn)
        - `pprint(obj, ...)` - Pretty-print data structures with formatting
        - `self` - Agent with all attributes/tools
        - Method parameters as local variables

        **Delegating to Sub-Methods (@strategy with `...` body)**:
        This is the core capability of the agent framework you are working in: any method that you are defining with (...) and a strategy decorator
        will delegate performing the task to that sub method using a sophisticated LLM/Agent. In this way you can delegate hard subproblems to LLM powered
        smart methods. This only works when you provide the ellipsis `...` in the body and decorate with @strategy and the appropriate strategy.
        - For complex tasks, define sub-methods with `@strategy()` decorator and `...` body if there are no existing methods defined on `self` that can solve the task.
        - Use `@strategy(PredictStrategy())` for fuzzy/LLM sub-tasks (text analysis, summarization, classification)
        - Use `@strategy(PurePythonStrategy())` for structured breakdown - sub-method MUST solve simpler task than parent to avoid infinite recursion
        - After defining sub-methods use them to complete the task.

        <example>
        # Task: Extract person names and organizations from these documents

        reasoning("Need to extract named entities - this is a fuzzy NER task, best done with PredictStrategy")
        @strategy(PredictStrategy())
        async def extract_from_text(self, text: str) -> dict:
            \"\"\"Extract person names and organizations from text. Return dict with 'persons' and 'orgs' keys.\"\"\"
            ...

        all_persons = []
        all_orgs = []
        for doc in docs:
            result = await self.extract_from_text(doc)
            all_persons.extend(result.get("persons", []))
            all_orgs.extend(result.get("orgs", []))
        return {"persons": all_persons, "orgs": all_orgs}
        </example>

        **Complete**: Use `return <value>` when done.

        **Forbidden**: `import` statements. Use modules shown in "Already Imported"."""
        ...

    @strategy(TemplateStrategy())
    async def error_syntax(self, runtime: RuntimeServices) -> str:
        """**Syntax error** - ensure you output valid Python code."""
        ...

    @strategy(TemplateStrategy())
    async def continuation_prompt(self, runtime: RuntimeServices, method: str) -> str:
        """Code executed successfully. Please continue (if you know the result use `return` to complete the task)"""
        ...

    async def execute(self, runtime: RuntimeServices, call: "CurrentCall") -> Any:
        session = self._initialize_session(call, runtime)
        builtins = self._build_builtins(runtime, call)

        task_content = await self._build_task_message(runtime, original_call=call)
        runtime.event_manager.add(Task(prompt=task_content))

        # Run prefill if configured (errors are non-fatal)
        if self.prefill:
            try:
                await self._run_prefill(runtime, call, builtins, session)
            except Exception as e:
                logger.warning(f"[PURE_PYTHON] Prefill error (continuing): {e}")
                runtime.event_manager.add(Error(content=f"Prefill error: {e}"))

        # Get generation_id for turn events
        generation_id = runtime.get_generation_id()
        if generation_id is None:
            raise RuntimeError(
                "get_generation_id() returned None - strategy execution requires a generation context. "
                "This indicates the runtime was not properly initialized via _in_generation_session()."
            )
        parent_generation_id = runtime.get_parent_generation_id()
        turn_number = 0

        while not session.is_exhausted():
            turn_number += 1
            runtime.event_manager.add(
                BeforeTurn(
                    method_name=call.method_name,
                    strategy=self.name,
                    generation_id=generation_id,
                    parent_generation_id=parent_generation_id,
                    turn_number=turn_number,
                ),
                record=False,
            )

            # Turn state for finally block
            turn_success = False
            turn_final = False
            turn_exception: str | None = None

            try:
                code: str | None = None
                generate_event_id: str | None = None
                try:
                    code, generate_event_id = await self._generate_code(runtime, session)
                except _HTTPX_TIMEOUT_EXCEPTIONS as e:
                    # Catch httpx timeout exceptions and preserve them
                    session.record_error()
                    error_msg = (
                        f"LLM request timed out (attempt {session.error_count}/{session.max_retries}): "
                        f"{type(e).__name__}: {e}"
                    )
                    runtime.event_manager.add(Error(content=error_msg))
                    logger.warning(
                        f"[PURE_PYTHON] LLM timeout (iter={session.iteration}, err={session.error_count}): {e}"
                    )
                    turn_exception = type(e).__name__

                    if session.is_exhausted():
                        turn_final = True
                        turn_exception = "GenerationError"
                        raise GenerationError(
                            f"LLM request timed out after {session.max_retries} retries. "
                            f"Original error: {type(e).__name__}: {e}"
                        ) from e
                except XMLFormatError as e:
                    # XML format error - already logged in _generate_code, just count as error
                    session.record_error()
                    logger.warning(
                        f"[PURE_PYTHON] XML format error (iter={session.iteration}, err={session.error_count})"
                    )
                    turn_exception = type(e).__name__

                    if session.is_exhausted():
                        turn_final = True
                        turn_exception = "GenerationError"
                        raise session.build_failure_error() from None
                except Exception as e:
                    # Catch other LLM API errors (rate limits, connection errors, etc.)
                    session.record_error()
                    error_name = type(e).__name__
                    cause_parts = []
                    exc: BaseException | None = e
                    seen_ids: set[int] = set()
                    while exc is not None and id(exc) not in seen_ids:
                        seen_ids.add(id(exc))
                        cause_parts.append(f"{type(exc).__name__}: {exc}")
                        exc = exc.__cause__ or exc.__context__
                    cause_chain = " <- ".join(cause_parts)
                    error_msg = (
                        f"LLM API error (attempt {session.error_count}/{session.max_retries}): "
                        f"{cause_chain}"
                    )
                    runtime.event_manager.add(Error(content=error_msg))
                    logger.warning(
                        f"[PURE_PYTHON] LLM API error (iter={session.iteration}, err={session.error_count}): "
                        f"{cause_chain}",
                        exc_info=True,
                    )
                    turn_exception = error_name

                    if session.is_exhausted():
                        turn_final = True
                        turn_exception = "GenerationError"
                        raise GenerationError(
                            f"LLM API error after {session.max_retries} retries. "
                            f"Original error: {error_name}: {e}"
                        ) from e

                # Skip rest of turn if code generation failed (caught exception above)
                if code is None:
                    continue

                if not code:
                    session.record_error()
                    # Remove the empty assistant event — some APIs reject empty content
                    if generate_event_id is not None:
                        runtime.event_manager.remove(generate_event_id)
                    await self._send_empty_response_error(runtime, call.method_name)
                    continue

                result = await self._execute_code(
                    runtime, code, builtins, session, call.method_name
                )

                # Merge captured locals into session for REPL-style persistence
                if result.captured_locals:
                    session.session_locals.update(result.captured_locals)
                    logger.debug(
                        f"[PURE_PYTHON] Captured locals: {list(result.captured_locals.keys())}"
                    )

                if result.error:
                    session.record_error()
                    await self._send_execution_error(
                        runtime, result.error, code, result.stdout, result.stderr
                    )
                    turn_exception = type(result.error).__name__
                    continue

                session.record_iteration()

                # Emit PythonOutput for Out[n] access (event-manager-backed mode)
                # This stores the actual Python object for Out[n] access
                runtime.event_manager.add(
                    PythonOutput(
                        tool_call_id="",  # No tool call in PURE_PYTHON mode
                        execution_count=session.iteration,
                        stdout=result.stdout,
                        stderr=result.stderr,
                        value=result.returned_value if result.has_return else None,
                        explicit_return=result.explicit_return,
                        execution_status=ResultStatus.COMPLETE,
                    )
                )

                if self._is_task_complete(result, runtime, call):
                    success, validated = await self._finalize_success(
                        runtime, result, call, session
                    )
                    if success:
                        turn_success = True
                        turn_final = True
                        return validated  # finally runs, then returns
                    # If validation failed, turn_success stays False
                    continue

                await self._send_continuation_feedback(runtime, result, call.method_name)
                turn_success = True
                # Fall through to finally, then continue to next iteration

            finally:
                runtime.event_manager.add(
                    AfterTurn(
                        method_name=call.method_name,
                        strategy=self.name,
                        generation_id=generation_id,
                        parent_generation_id=parent_generation_id,
                        turn_number=turn_number,
                        is_final=turn_final,
                        success=turn_success,
                        exception_type=turn_exception,
                    ),
                    record=False,
                )

        # Loop exhausted without success
        runtime.event_manager.add(
            AfterTurn(
                method_name=call.method_name,
                strategy=self.name,
                generation_id=generation_id,
                parent_generation_id=parent_generation_id,
                turn_number=turn_number,
                is_final=True,
                success=False,
                exception_type="GenerationError",
            ),
            record=False,
        )
        raise session.build_failure_error()

    def _initialize_session(
        self, call: "CurrentCall", runtime: RuntimeServices
    ) -> GenerationSession:
        session = GenerationSession(
            max_iterations=self.max_iterations,
            max_retries=self.max_retries,
            target_method_name=call.method_name,
            event_manager=runtime.event_manager,
        )

        logger.info(
            f"[PURE_PYTHON] Starting session for {call.method_name}: "
            f"max_iterations={self.max_iterations}, max_retries={self.max_retries}"
        )

        return session

    async def _run_prefill(
        self,
        runtime: RuntimeServices,
        call: "CurrentCall",
        builtins: dict[str, Any],
        session: GenerationSession,
    ) -> None:
        """Run prefill code before the main generation loop.

        Executes prefill code as a synthetic first turn through the normal
        execution path. Results persist in session_locals for subsequent turns.
        """
        from nemo_oo_agents.events import LLMOutput

        if not self.prefill:
            return

        truncation_config = self._get_truncation_config(runtime)
        code = self.prefill.get_code(call, config=truncation_config)
        if not code:
            logger.debug("[PURE_PYTHON] Prefill returned no code, skipping")
            return

        logger.debug(f"[PURE_PYTHON] Running prefill for {call.method_name}")

        # Add as assistant message (as if LLM output this code)
        # Mark with metadata so it's identifiable in traces
        runtime.event_manager.add(
            LLMOutput(
                content=code,
                metadata={"prefill": True, "prefill_type": "inspect_inputs"},
            )
        )

        # Add prefill-specific builtins
        prefill_builtins = {**builtins, "_call": call}

        # Execute through normal code path
        result = await self._execute_code(
            runtime, code, prefill_builtins, session, call.method_name
        )

        # Merge captured locals into session
        if result.captured_locals:
            session.session_locals.update(result.captured_locals)
            logger.debug(
                f"[PURE_PYTHON] Prefill captured locals: {list(result.captured_locals.keys())}"
            )

        # Emit PythonOutput for the prefill execution result (mirrors normal turn event sequence)
        if not result.error:
            runtime.event_manager.add(
                PythonOutput(
                    tool_call_id="",
                    execution_count=0,  # prefill runs before iteration 1
                    stdout=result.stdout,
                    stderr=result.stderr,
                    value=result.returned_value if result.has_return else None,
                    explicit_return=result.explicit_return,
                    execution_status=ResultStatus.COMPLETE,
                    metadata={"prefill": True, "prefill_type": "inspect_inputs"},
                )
            )
        else:
            # Prefill error - log but don't fail (LLM can still proceed)
            logger.warning(f"[PURE_PYTHON] Prefill execution error: {result.error}")

    async def _generate_code(
        self, runtime: RuntimeServices, session: GenerationSession
    ) -> tuple[str, str]:
        """Generate code from the LLM.

        Returns:
            (code, event_id): code ready for execution (with reasoning calls, without
            fences/XML), and the event_id of the LLMOutput event so the caller can
            remove it if empty (some APIs reject empty assistant messages).
        """
        logger.debug(
            f"[PURE_PYTHON] Loop iteration: iter={session.iteration}/{session.max_iterations}, "
            f"err={session.error_count}/{session.max_retries}"
        )

        response, event_id = await runtime.generate(tools=[])
        raw_code = (response.content or "").strip()

        # Strip wrapper formats - LLM may use markdown fences, XML tags, or both nested
        # Apply both stripping methods iteratively to handle any nesting order
        try:
            code = self._strip_wrappers(raw_code)
        except XMLFormatError as e:
            # Remove the malformed LLMOutput — some APIs reject empty/malformed content
            runtime.event_manager.remove(event_id)
            runtime.event_manager.add(Error(content=f"**Format Error**: {e}"))
            raise

        # Then strip reasoning calls for event storage
        clean_code = self._strip_reasoning_calls(code)

        # Store clean code in events so LLM learns to output plain Python
        runtime.event_manager.update(event_id, content=clean_code)

        # Debug breadcrumbs: we keep these fairly high-signal so log output stays useful.
        logger.debug(
            "[PURE_PYTHON] Generated code (raw_len=%s, clean_len=%s): %s",
            len(raw_code),
            len(clean_code),
            (clean_code[:200] + ("..." if len(clean_code) > 200 else "")),
        )

        # Return code without fences/XML for execution (but with reasoning calls for processing)
        return code, event_id

    def _extract_function_body_if_wrapped(
        self,
        code: str,
        target_method_name: str,
        runtime: RuntimeServices,
    ) -> tuple[str, bool]:
        """Extract function body if code is wrapped in function definition matching target method.

        Handles two cases:
        1. Single wrapped function definition (target method only)
        2. Multiple nodes where one is wrapped target method + helper methods

        Returns:
            tuple[extracted_code, was_extracted]: The code to execute and whether extraction occurred.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code, False

        # Find the target method function definition
        target_method_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        helper_method_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        other_nodes: list[ast.stmt] = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check if first parameter is 'self'
                if node.args.args and node.args.args[0].arg == "self":
                    if node.name == target_method_name:
                        target_method_node = node
                    else:
                        # This is a helper method
                        helper_method_nodes.append(node)
                else:
                    # Function without self - treat as other node
                    other_nodes.append(node)
            else:
                # Non-function node
                other_nodes.append(node)

        # If no target method found, no extraction needed
        if target_method_node is None:
            return code, False

        # If there are other non-function nodes, don't extract (let normal rejection handle it)
        if other_nodes:
            return code, False

        # Extract target method body
        body_nodes = target_method_node.body
        assert body_nodes, (
            "Function body is always non-empty in valid Python (syntax error otherwise)"
        )

        # Build extracted code: helper method definitions + target method body
        extracted_parts = []

        # Add helper method definitions (keep them as-is)
        for helper_node in helper_method_nodes:
            extracted_parts.append(ast.unparse(helper_node))

        # Add target method body (unwrapped)
        target_body_code = ast.unparse(ast.Module(body=body_nodes, type_ignores=[]))
        extracted_parts.append(target_body_code)

        extracted_code = "\n\n".join(extracted_parts)

        logger.debug(
            f"[PURE_PYTHON] Extracted function body from wrapped definition "
            f"(target={target_method_name}, helpers={len(helper_method_nodes)}, "
            f"body_lines={len(target_body_code.splitlines())})"
        )

        return extracted_code, True

    async def _execute_code(
        self,
        runtime: RuntimeServices,
        code: str,
        builtins: dict[str, Any],
        session: GenerationSession,
        target_method_name: str,
    ) -> Any:
        from nemo_oo_agents.events import ExecutionResult

        logger.debug(
            "[PURE_PYTHON] About to execute code (iter=%s, err=%s, chars=%s)",
            session.iteration,
            session.error_count,
            len(code),
        )

        # 0) Check if code is wrapped in function definition - extract body if so
        extracted_code, was_extracted = self._extract_function_body_if_wrapped(
            code, target_method_name, runtime
        )

        if was_extracted:
            # Update events to show unpacked code so LLM learns from example
            # Find the most recent generated code event and update it
            recent_events = runtime.event_manager.filter(limit=20)
            for event in reversed(recent_events):
                if event.event_type == "LLMOutput":
                    runtime.event_manager.update(event.id, content=extracted_code)
                    logger.debug(f"[PURE_PYTHON] Updated event {event.id} with unpacked code")
                    break

            code = extracted_code

        # 1) Validate REPL policy (no classes, await async methods)
        validator = GeneratedCodeValidator()
        validation_errors = validator.validate(code, runtime.agent)
        if validation_errors:
            session.record_error()
            error_msg = "**Error**: Code validation failed:\n\n" + "\n\n".join(
                f"• {err}" for err in validation_errors
            )
            runtime.event_manager.add(Error(content=error_msg))
            logger.warning(f"[PURE_PYTHON] Validation errors: {validation_errors}")
            return ExecutionResult(stdout="", error=None, defined_methods={})

        # 2) Build execution namespace (consistent with ActorRuntime.execute_code)
        # Include strategy symbols for LLM-generated helper methods
        strategy_extras: dict[str, Any] = {
            "PurePythonStrategy": type(self),
        }
        # Try to import other strategies for helper method decorators
        try:
            from nemo_oo_agents.strategies.predict import PredictStrategy
            from nemo_oo_agents.strategies.reflexion import ReflexionStrategy

            strategy_extras["PredictStrategy"] = PredictStrategy
            strategy_extras["ReflexionStrategy"] = ReflexionStrategy
        except ImportError:
            pass

        namespace = ExecutionNamespaceBuilder.build(
            runtime.agent, extra={**builtins, **session.session_locals, **strategy_extras}
        )

        # 3) Extract and bind helper methods BEFORE execution (same-turn use)
        helper_manager = HelperMethodManager()
        helper_result = helper_manager.apply(
            code,
            runtime.agent,
            session.session_locals,
            namespace=namespace,
            target_method_name=target_method_name,
        )

        if helper_result.rejected:
            session.record_error()
            error_msg = (
                f"**Error**: Cannot define method(s) {helper_result.rejected} - "
                f"they would overwrite the target method `{target_method_name}` that you're implementing.\n"
                f"Choose different names for helper methods."
            )
            runtime.event_manager.add(Error(content=error_msg))
            logger.warning(f"[PURE_PYTHON] Rejected helper methods: {helper_result.rejected}")
            return ExecutionResult(stdout="", error=None, defined_methods={})

        if helper_result.errors:
            session.record_error()
            error_msg = "**Error**: Failed to define helper method(s):\n" + "\n".join(
                f"- {e}" for e in helper_result.errors
            )
            runtime.event_manager.add(Error(content=error_msg))
            logger.warning(f"[PURE_PYTHON] Helper method binding errors: {helper_result.errors}")
            return ExecutionResult(stdout="", error=None, defined_methods={})

        if helper_result.installed:
            logger.debug(f"[PURE_PYTHON] Pre-bound helpers: {helper_result.installed}")

        # 4) Execute with session locals (includes pre-bound methods)
        execution_builtins = {**builtins, **session.session_locals}

        # Use iteration+1 for 1-indexed execution count (matches event emission after record_iteration)
        return await runtime.execute_code(
            code,
            builtins=execution_builtins,
            validate=True,
            wrap_in_function=True,
            execution_count=session.iteration + 1,
        )

    def _is_task_complete(self, result: Any, runtime: RuntimeServices, call: "CurrentCall") -> bool:
        if result.has_return:
            return True

        method = getattr(runtime.agent, call.method_name, None)
        if not method:
            return False

        try:
            hints = get_type_hints(method, include_extras=True)
            return_type = hints.get("return", inspect.Parameter.empty)
        except (NameError, TypeError, AttributeError):
            try:
                sig = inspect.signature(method)
                return_type = sig.return_annotation
            except (ValueError, TypeError):
                return False

        if return_type in (inspect.Signature.empty, type(None), None):
            return True

        return False

    async def _finalize_success(
        self, runtime: RuntimeServices, result: Any, call: "CurrentCall", session: GenerationSession
    ) -> tuple[bool, Any]:
        logger.info("[PURE_PYTHON] Task complete - validating and returning result")

        result_to_validate = result.returned_value if result.has_return else None

        # Auto-await coroutines (LLM might call async methods without await)
        if inspect.iscoroutine(result_to_validate):
            logger.warning(
                "[PURE_PYTHON] Result is a coroutine - auto-awaiting "
                "(LLM probably forgot 'await' keyword)"
            )
            result_to_validate = await result_to_validate

        try:
            return_validator = ReturnValueValidator()
            validated_result = return_validator.validate(
                result_to_validate, runtime, call.method_name
            )
            logger.info("[PURE_PYTHON] Method executed successfully")
            return (True, validated_result)
        except (PydanticValidationError, ValueError, TypeError) as e:
            session.record_error()
            error_msg = format_validation_error(
                e, call.return_type, result_to_validate, runtime.truncation_config
            )
            runtime.event_manager.add(Error(content=f"Return type validation error:\n{error_msg}"))
            return (False, None)

    async def _send_empty_response_error(self, runtime: RuntimeServices, method_name: str) -> None:
        message = f"Empty response. Output Python code. Use `return` to complete {method_name}."
        runtime.event_manager.add(Error(content=message))

    async def _send_execution_error(
        self,
        runtime: RuntimeServices,
        error: Exception,
        code: str | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Send an execution error to the LLM with clean formatting.

        Args:
            runtime: RuntimeServices for event management
            error: The exception that occurred
            code: The code that was executed (for better error context)
            stdout: Partial stdout captured before the error (debugging aid)
            stderr: Partial stderr captured before the error (debugging aid)
        """
        error_msg = self._format_error(error, code)
        guidance = ""

        if isinstance(error, SyntaxError):
            guidance = "\n\n" + await self.error_syntax(runtime)

        # Include partial stdout/stderr if any (debugging aid)
        # Build output section from the raw strings (not from result, since we have separate args)
        output_parts = []
        if stdout:
            output_parts.append(f"Stdout:\n```\n{stdout}\n```")
        if stderr:
            output_parts.append(f"Stderr:\n```\n{stderr}\n```")
        output_section = "\n\n" + "\n\n".join(output_parts) if output_parts else ""

        runtime.event_manager.add(
            Error(
                content=f"Execution error:\n```\n{error_msg}\n```{output_section}{guidance}\nFix and try again."
            )
        )

    async def _send_continuation_feedback(
        self, runtime: RuntimeServices, result: Any, method_name: str
    ) -> None:
        feedback_parts = []

        output_text = result.format_output(fenced=True)
        if output_text:
            feedback_parts.append(output_text)

        if result.defined_methods:
            feedback_parts.append(f"Helper methods defined: {list(result.defined_methods.keys())}")

        feedback_msg = await self.continuation_prompt(runtime, method=method_name)
        feedback_parts.append(feedback_msg)

        runtime.event_manager.add(Feedback(content="\n\n".join(feedback_parts)))

        logger.debug(
            f"[PURE_PYTHON] No return statement yet. "
            f"Defined methods: {list(result.defined_methods.keys())}. "
            f"Continuing loop..."
        )

    @strategy(TemplateStrategy())
    async def _build_task_message(
        self, runtime: RuntimeServices, original_call: "CurrentCall"
    ) -> str:
        """
        # Your task
        {original_call.docstring}

        *Important*:
        - If you are not just returning the result directly (using return <result>), explain using `reasoning()` why you cannot do that and then perform the task (in the same turn).
        - If you don't want to make use of existing methods from self, briefly explain why they are not a good fit using `reasoning()`.
        - Use print(doc(self.sub_agent)) to see the documentation of sub agents or any other class attribute.
        """
        ...

    def _build_builtins(self, runtime: RuntimeServices, call: "CurrentCall") -> dict[str, Any]:
        def reasoning(text: str) -> None:
            runtime.event_manager.add(Reasoning(content=str(text)), record=False)

        def emit_message(text: str) -> None:
            runtime.event_manager.add(Message(content=str(text)), record=False)

        builtins: dict[str, Any] = {
            "reasoning": reasoning,
            "message": emit_message,
        }

        method = getattr(runtime.agent, call.method_name, None)
        if method and callable(method):
            try:
                sig = inspect.signature(method)
                param_names = [p for p in sig.parameters.keys() if p != "self"]
                for i, name in enumerate(param_names):
                    if i < len(call.args):
                        builtins[name] = call.args[i]
            except (ValueError, TypeError):
                pass

        # Always inject call kwargs (CurrentCall.from_method maps positional
        # args to names, so this covers Skill methods and agent methods alike).
        builtins.update(call.kwargs)

        return builtins

    # ── Post-response cleanup (PurePython) ─────────────────────
    # Intercept point: strategy-specific response transforms.
    # Handles nested wrapper stripping and reasoning call removal.
    # Consider making extensible in the future.

    def _strip_wrappers(self, code: str) -> str:
        """Strip all wrapper formats from LLM output.

        Handles any nesting order of:
        - Markdown fences (```python ... ```)
        - XML wrapper tags (<tool_code>...</tool_code>)

        Applies stripping iteratively until no more wrappers are found.

        Raises:
            XMLFormatError: If problematic nested/multiple XML tags are detected.
        """
        result = code
        max_iterations = 5  # Prevent infinite loops on malformed input
        iterations_used = 0

        for _ in range(max_iterations):
            previous = result
            iterations_used += 1

            # Try stripping markdown fences
            result = self._strip_code_fences(result)

            # Try stripping XML wrapper
            result = self._strip_xml_wrapper(result)

            # If nothing changed, we're done
            if result == previous:
                break

        if iterations_used > 1:
            get_harness_metrics().nested_wrapper_iteration(iterations_used)

        return result

    def _strip_code_fences(self, code: str) -> str:
        """Strip markdown code fences from LLM output.

        Delegates to the shared ``strip_code_fences`` function.
        """
        from nemo_oo_agents.runtime.response_cleanup import strip_code_fences

        cleaned, fence_token = strip_code_fences(code)
        if fence_token:
            get_harness_metrics().fence_removal(fence_token)
            return cleaned
        return code

    def _strip_xml_wrapper(self, code: str) -> str:
        """Strip XML/HTML wrapper tags from LLM output (strict mode).

        Delegates core matching to the shared ``strip_xml_wrapper`` function.
        Adds strict error handling for code context: raises XMLFormatError on
        malformed or nested XML (indicates LLM confusion about output format).

        Raises:
            XMLFormatError: If output contains nested/multiple XML tags.
        """
        import re

        from nemo_oo_agents.runtime.response_cleanup import strip_xml_wrapper

        stripped = code.strip()
        if not stripped.startswith("<"):
            return code

        inner_content, tag_name = strip_xml_wrapper(stripped)

        if tag_name is None:
            # Starts with < but doesn't match wrapper pattern — check for XML-like tags
            if re.search(r"<\w+[^>]*>", stripped):
                raise XMLFormatError(
                    "You provided XML/HTML tags. This is wrong. You are only allowed to return Python. "
                    "Return plain Python code only, without any XML or HTML markup."
                )
            return code

        # Check for nested XML wrapper tags in the extracted content
        if inner_content.strip().startswith("<") and inner_content.strip().endswith(">"):
            nested_pattern = r"^\s*<(\w+)(?:\s+[^>]*)?>.*</\1>\s*$"
            if re.match(nested_pattern, inner_content, re.DOTALL):
                raise XMLFormatError(
                    f"Output contains nested XML tags (<{tag_name}> wrapping another tag). "
                    "Return plain Python code only, without any XML or HTML markup."
                )

        get_harness_metrics().xml_wrapper_stripped(tag_name)
        logger.debug(
            f"[PURE_PYTHON] Stripped XML wrapper tag <{tag_name}> from LLM output "
            f"(original={len(stripped)} chars, extracted={len(inner_content)} chars)"
        )

        return inner_content

    def _strip_reasoning_calls(self, code: str) -> str:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code

        new_body = []
        stripped_count = 0
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id == "reasoning":
                    stripped_count += 1
                    continue
            new_body.append(node)

        if stripped_count > 0:
            get_harness_metrics().reasoning_call_stripped(stripped_count)

        tree.body = new_body
        return ast.unparse(tree)

    def _format_error(self, error: Exception, code: str | None = None) -> str:
        """Format an error for LLM feedback.

        Uses the error formatting module to produce high-signal, low-noise
        error messages that hide framework internals.

        Args:
            error: The exception to format
            code: Optional source code for better context

        Returns:
            Formatted error string
        """
        from nemo_oo_agents.errors.formatting import format_error_for_llm

        return format_error_for_llm(error, code)
