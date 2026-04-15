# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CodeAct strategy - LLM uses execute_python tool + structured output.

Flow:
1. Build prompt with task + agent state + available methods
2. Call LLM with execute_python tool AND output_model (structured output schema)
3. If LLM calls execute_python tool → execute code, add result to events, continue loop
4. If LLM returns structured output → validate and return result
5. Handle errors and retries

This combines the flexibility of tool-based interaction with structured final outputs.
The LLM can reason in natural language and execute Python code via tool calls
until ready to return a structured result matching the method's return type.

Reference: "Executable Code Actions Elicit Better LLM Agents" (Wang et al.)
"""

import ast
import inspect
import json
import logging
import types
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    get_args,
    get_origin,
)
from uuid import uuid4

from pydantic import BaseModel, PydanticSchemaGenerationError, PydanticUserError, create_model
from pydantic import ValidationError as PydanticValidationError

from agentdoc._structured import format_type as _format_type
from context_blocks import DynamicContext, ResultStatus, ToolCallEvent, ToolResult
from context_blocks.exceptions import BlockSyntaxError
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.errors import GenerationError
from nemo_oo_agents.events import (
    AfterTurn,
    BeforeTurn,
    Error,
    ExecutionSignal,
    PythonOutput,
    Reasoning,
    Task,
)
from nemo_oo_agents.runtime.hooks import call_after_hook, call_before_hook
from nemo_oo_agents.strategies.base import RuntimeServices
from nemo_oo_agents.strategies.codeact_errors import format_validation_error
from nemo_oo_agents.strategies.composite import CompositeStrategy
from nemo_oo_agents.strategies.generated_code import (
    ExecutionNamespaceBuilder,
    GeneratedCodeValidator,
    HelperMethodManager,
)
from nemo_oo_agents.strategies.template import TemplateStrategy
from unifiedllm import Tool, ToolCall

if TYPE_CHECKING:
    from nemo_oo_agents.config.strategy_config import CodeActConfig
    from nemo_oo_agents.errors.formatting import IPythonErrorFormatter
    from nemo_oo_agents.strategies.current_call import CurrentCall

logger = logging.getLogger(__name__)


class _ReturnResultSignal(ExecutionSignal):
    """Signal raised when return_result() is called from within execute_python code.

    This is an internal signal (not an error) that indicates the LLM has computed
    the final result and wants to return it inline, rather than making a separate
    return_result tool call.

    The exception-based approach allows return_result() to work anywhere in the code
    (not just at the end) and immediately stops execution, similar to a return statement.

    Inherits from ExecutionSignal so actor.py can distinguish it from actual errors.
    """

    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__("return_result() called")


# Maximum characters of LLM text to embed in a synthetic reasoning() call.
# Long verbatim text inflates trace storage and is rarely useful beyond a preview.
_MAX_REASONING_TEXT = 500


def _truncate_reasoning(text: str) -> str:
    """Truncate *text* to _MAX_REASONING_TEXT chars for embedding in reasoning()."""
    if len(text) <= _MAX_REASONING_TEXT:
        return text
    return text[:_MAX_REASONING_TEXT] + " [truncated]"


def _prepend_reasoning(tool_calls: list[ToolCall], text: str) -> list[ToolCall]:
    """Return a copy of *tool_calls* with reasoning(text) prepended to the
    first execute_python code block.  Other tool calls are left unchanged.
    """
    result: list[ToolCall] = []
    prepended = False
    preview = _truncate_reasoning(text)
    for tc in tool_calls:
        if not prepended and tc.name == "execute_python":
            try:
                args = json.loads(tc.arguments)
                original_code = args.get("code", "")
                args["code"] = f"reasoning({preview!r})\n{original_code}"
                tc = replace(tc, arguments=json.dumps(args))
                prepended = True
            except json.JSONDecodeError:
                logger.debug(
                    "[CODEACT] _prepend_reasoning: skipping execute_python with unparseable arguments (tool_call_id=%s)",
                    tc.id,
                )
        result.append(tc)
    return result


@dataclass
class _ToolCallsResult:
    """Result of processing tool calls in a single turn."""

    completed: bool = False
    final_value: Any = None


@dataclass
class _TurnState:
    """Mutable state yielded by CodeActSession.turn()."""

    success: bool = False
    is_final: bool = False
    exception: str | None = None


@dataclass
class CodeActSession:
    """Tracks state for a single CodeAct generation session."""

    max_iterations: int
    max_retries: int
    target_method_name: str
    event_manager: Any  # EventManager reference for Out[n] access
    iteration: int = 0
    error_count: int = 0
    session_locals: dict[str, Any] = field(default_factory=dict)
    out_accessor: Any = field(default=None)  # OutAccessor instance, created lazily

    def __post_init__(self):
        """Initialize OutAccessor for Jupyter-style Out[n] access."""
        from nemo_oo_agents.runtime.out_accessor import OutAccessor

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

    @asynccontextmanager
    async def turn(
        self,
        event_manager,
        call_method_name,
        strategy_name,
        generation_id,
        parent_generation_id,
        turn_number,
    ):
        """Emit BeforeTurn/AfterTurn around a turn body, yielding _TurnState."""
        event_manager.add(
            BeforeTurn(
                method_name=call_method_name,
                strategy=strategy_name,
                generation_id=generation_id,
                parent_generation_id=parent_generation_id,
                turn_number=turn_number,
            ),
            record=False,
        )
        state = _TurnState()
        try:
            yield state
        except Exception as exc:
            if state.exception is None:  # Don't overwrite caller-set value
                state.exception = type(exc).__name__
            raise
        finally:
            event_manager.add(
                AfterTurn(
                    method_name=call_method_name,
                    strategy=strategy_name,
                    generation_id=generation_id,
                    parent_generation_id=parent_generation_id,
                    turn_number=turn_number,
                    is_final=state.is_final,
                    success=state.success,
                    exception_type=state.exception,
                ),
                record=False,
            )


def _iter_agent_attrs(agent: Any):
    """Yield non-hidden attribute values from an agent (class then instance)."""
    from agentdoc.visibility import is_hidden_field

    cls = type(agent)
    for name in dir(cls):
        if name.startswith("__"):
            continue
        try:
            val = getattr(cls, name, None)
            if val is None or callable(val):
                continue
            if is_hidden_field(cls, name):
                continue
            yield val
        except Exception:
            pass
    for name, val in getattr(agent, "__dict__", {}).items():
        if val is None or name.startswith("__"):
            continue
        if is_hidden_field(cls, name):
            continue
        yield val


class CodeActStrategy(CompositeStrategy):
    """CodeAct strategy: LLM uses execute_python tool + structured output.

    Combines the flexibility of tool-based interaction with structured final outputs.
    The LLM can reason in natural language and execute Python code via tool calls
    until ready to return a structured result matching the method's return type.

    Key differences from PurePythonStrategy:
    - LLM can reason in natural language between code executions
    - Code is executed via explicit tool calls (not raw output)
    - Final response must be structured output matching return type

    Configuration:
        max_iterations: Maximum number of tool call iterations
        max_retries: Maximum consecutive errors before failure

    Example:
        @strategy(CodeActStrategy())
        def analyze(self, data: str) -> AnalysisResult:
            '''Analyze data and return structured results.'''
            ...

        @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=5)))
        def quick_task(self, x: int) -> dict:
            '''Task with custom iteration limit.'''
            ...
    """

    def __init__(
        self,
        config: "CodeActConfig | None" = None,
        *,
        error_formatter: "IPythonErrorFormatter | None" = None,
    ):
        """Initialize CodeAct strategy.

        Args:
            config: CodeActConfig with iteration limits, timeouts, and sampling params.
                    Defaults to CodeActConfig() with standard defaults.
            error_formatter: Custom error formatter for LLM feedback. Defaults to IPythonErrorFormatter.
                Any object with a `format(error, code) -> str` method works.

        Note:
            Prefill is always enabled and uses InspectInputsPrefill internally.
        """
        from nemo_oo_agents.config.strategy_config import CodeActConfig as _CC

        self.config = config or _CC()
        self.error_formatter = error_formatter

    def _build_sampling_kwargs(self) -> dict:
        """Build sampling kwargs for llm calls, excluding None values."""
        return {
            k: v
            for k, v in {
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "top_p": self.config.top_p,
            }.items()
            if v is not None
        }

    @property
    def name(self) -> str:
        """Strategy name."""
        return "CODEACT"

    def _get_truncation_config(self, runtime: RuntimeServices):
        """Get truncation config from runtime's agent.

        Args:
            runtime: RuntimeServices instance with agent reference

        Returns:
            TruncationConfig from agent, or default if not available
        """
        from nemo_oo_agents.config.truncation_config import DEFAULT_TRUNCATION_CONFIG

        return getattr(runtime.agent, "_truncation", DEFAULT_TRUNCATION_CONFIG)

    def get_block_overrides(self) -> dict[str, "str | DynamicContext | None"]:
        return {
            "strategy_prompt": DynamicContext("strategy.strategy_instructions(runtime)"),
            "execution_context": DynamicContext("strategy.execution_context(runtime)"),
        }

    def get_block_order(self) -> list[str] | None:
        """Place doc(self) after execution_context so the LLM sees instructions first."""
        return [
            "system_prompt",
            "strategy_prompt",
            "execution_context",
            "self",
        ]

    async def execution_context(self, runtime: RuntimeServices) -> str:
        """Generate execution context block showing available imports and types.

        This is a separate context block that appears in the system prompt,
        documenting what symbols are available in execute_python().
        """
        agent_module = inspect.getmodule(type(runtime.agent))
        if not agent_module:
            return """## Execution Context

Standard Python builtins and agent instance (`self`) are available."""

        # Extract context to see what's available
        context = self._extract_module_context(agent_module, agent=runtime.agent)

        # Filter out blocked modules so the LLM doesn't see unavailable symbols
        from nemo_oo_agents.runtime.restrictions import is_from_blocked_module

        blocked = self.config.restrictions.blocked_modules

        # Separate into categories
        modules = []
        types_defined = []
        imported_items = []

        for name, obj in context.items():
            if is_from_blocked_module(obj, blocked):
                continue
            if isinstance(obj, types.ModuleType):
                actual_name = getattr(obj, "__name__", name)
                if actual_name != name:
                    modules.append(f"{actual_name} as {name}")
                else:
                    modules.append(name)
            elif isinstance(obj, type):
                obj_module = getattr(obj, "__module__", None)
                if obj_module == agent_module.__name__:
                    types_defined.append(name)
                else:
                    imported_items.append(name)
            elif callable(obj):
                imported_items.append(name)

        # Build documentation
        parts = ["## Execution Context", ""]
        parts.append("Available in execute_python (no imports needed — everything is pre-loaded):")
        parts.append("")

        if modules:
            parts.append(f"**Imported modules**: {', '.join(sorted(modules))}")

        if types_defined:
            parts.append(
                f"**Available types** (defined in agent module): {', '.join(sorted(types_defined))}"
            )
            parts.append(
                f"  Tip: Use `doc({types_defined[0]})` to inspect fields before constructing"
            )

        if imported_items:
            items = sorted(imported_items)
            parts.append(f"**Imported items**: {', '.join(items)}")

        parts.append(
            "**Task decomposition**: `@strategy(PredictStrategy())` decorator, "
            "`strategy`, `PredictStrategy`, `CodeActStrategy`"
        )
        parts.append("**Stdlib**: `asyncio`, `typing` (Literal, Annotated, etc.)")

        parts.append("")
        parts.append(
            "**Always available**: `self`, `print()`, `pprint()`, `doc()`, `return_result()`, `reasoning()` method parameters"
        )
        parts.append("Do NOT write import statements — all symbols above are already in scope.")

        # Members section — Skill attrs on the agent.
        # Includes both instance attrs and class-level Skill attrs, so that
        # `frontend_design = Skill(path=...)` at class level is visible here.
        # Framework Skills (_-prefixed) are excluded.
        from agentdoc.visibility import is_hidden_field
        from nemo_oo_agents.skill import Skill as _Skill

        skill_attrs_dict = {
            name: val
            for name in dir(runtime.agent)
            if not name.startswith("_")
            and not is_hidden_field(type(runtime.agent), name)
            and isinstance(val := getattr(runtime.agent, name, None), _Skill)
        }
        skill_attrs = list(skill_attrs_dict.items())
        if skill_attrs:
            rows = []
            for name, val in sorted(skill_attrs):
                one_liner = (val.__class__.__doc__ or "").strip().split("\n")[0]
                rows.append(f"| `self.{name}` | {one_liner} |")

            parts.append("")
            parts.append("## Skills")
            parts.append("")
            parts.append(
                "BEFORE starting any task, check if any of these skills applies. "
                "You MUST call `doc(self.<skill>)` before using it — do not assume you know the API."
            )
            parts.append("")
            parts.append("| Skill | Description |")
            parts.append("|-------|-------------|")
            parts.extend(rows)

            # Usage instructions
            parts.append("")
            parts.append("**Usage:**")
            parts.append("- Inspect in REPL: `print(doc(self.<skill>))`")

            # Pin/unpin instructions only when context is visible on the agent
            has_context = not is_hidden_field(type(runtime.agent), "context") and hasattr(
                runtime.agent, "context"
            )
            if has_context:
                parts.append('- Pin to context: `self.context["<skill>"] = doc(self.<skill>)`')
                parts.append('- Unpin: `del self.context["<skill>"]`')

        return "\n".join(parts)

    @strategy(TemplateStrategy())
    async def strategy_instructions(self, runtime: RuntimeServices) -> str:
        """
        ## Strategy

        You are working in an interactive Python session (like a Jupyter notebook).
        Input parameters are pre-loaded as local variables.

        **Your two tools:**
        - `execute_python(code)` — run a code cell (variables persist across cells)
        - `return_result(value)` — submit your final answer

        ## When to use which tool

        **Use `return_result(...)` directly** for simple answers you can determine from the inputs alone (yes/no, extracting one field, a single lookup).

        **Use `execute_python(...)` when:**
        - Input contains lists or batches to process
        - Arithmetic or multi-step computation is needed
        - You need to transform, reshape, or iterate over data

        Always iterate in code for lists/batches — never construct large arrays by hand.

        ## Returning computed results

        After computing in code, call `return_result(variable)` **from within** `execute_python()`:

        <example>
        results = [process(item) for item in items]
        return_result(results)
        </example>

        This passes the variable directly. Do NOT re-type computed values in a separate `return_result` tool call.

        ## No heuristics for language understanding

        Never use keyword matching (`if "x" in text`), regex, or hand-written rules for tasks
        that require understanding language or strings (classification, extraction,
        interpretation, etc.). These tasks need LLM reasoning.
        Either answer directly with `return_result`, or delegate to a
        `@strategy(PredictStrategy())` sub-method (see below).

        BAD:  `if "urgent" in subject: return "high"`
        GOOD: reason about it → `return_result("high")`, or delegate to a sub-method.

        This applies to language tasks only — use regular Python for math, data
        transformation, and other deterministic logic.

        ## Session rules

        - Variables persist across cells (like Jupyter)
        - Use `await` directly — no `asyncio.run()`
        - `print()` / `pprint()` for debugging; `doc(obj)` to inspect types
        - `Out[n]` references result of cell n; `Out[-1]` is the latest
        - You MUST call a tool each turn — no plain-text answers

        ## Restrictions (these will throw errors)

        - `import` (everything is pre-imported; see Execution Context)
        - `eval()`, `exec()`, `compile()`, `__import__()`
        - `input()`, `breakpoint()`
        - `globals()`, `locals()`, `vars()`
        - `asyncio.run()`, `loop.run_until_complete()`
        """
        ...

    @strategy(TemplateStrategy())
    async def _tool_use_reminder(self, runtime: RuntimeServices, reason: str) -> str:
        """{reason} Use `execute_python(code)` to run code, or `return_result(...)` to submit your answer."""
        ...

    @strategy(TemplateStrategy())
    async def _build_task_message(
        self, runtime: RuntimeServices, original_call: "CurrentCall"
    ) -> str:
        """
        ## Task: {original_call.method_name}

        {original_call.docstring}
        """
        ...

    async def execute(self, runtime: RuntimeServices, call: "CurrentCall") -> Any:
        """Execute CodeAct strategy with two-tool approach.

        Uses two tools:
        - execute_python(code): Run Python code for computation
        - return_result(...): Return the final structured answer

        Args:
            runtime: RuntimeServices providing LLM, execution, and event management.
            call: CurrentCall with method details and arguments.

        Returns:
            Validated structured data matching return type.

        Raises:
            GenerationError: If generation fails after max retries/iterations.
        """
        # Return type is pre-resolved by _execute_with_generation (handles PEP 563).
        return_type = call.return_type
        if return_type is None:
            raise GenerationError(
                f"Method `{call.method_name}` has no return type annotation. "
                f"CodeActStrategy requires a return type (Pydantic model or basic type)."
            )

        # Initialize session
        session = CodeActSession(
            max_iterations=self.config.max_iterations,
            max_retries=self.config.max_retries,
            target_method_name=call.method_name,
            event_manager=runtime.event_manager,
        )

        # Build builtins for code execution
        builtins = self._build_builtins(runtime, call)

        # Build both tools
        execute_python_tool = self._build_execute_python_tool()
        return_result_tool = self._build_return_result_tool(return_type, call.method_name)
        tools = [execute_python_tool, return_result_tool]

        # Use the task event's tag as the call ID so the LLM sees a stable reference
        object.__setattr__(call, "id", str(runtime.event_manager._next_tag_num))
        task_content = await self._build_task_message(runtime, original_call=call)
        runtime.event_manager.add(Task(prompt=task_content))

        logger.info(
            f"[CODEACT] Starting session for {call.method_name}: "
            f"max_iterations={self.config.max_iterations}, max_retries={self.config.max_retries}"
        )

        # Run prefill (always enabled - inspects inputs with truncation)
        try:
            await self._run_prefill(runtime, call, builtins, session)
        except Exception as e:
            logger.warning(f"[CODEACT] Prefill error (continuing): {e}")
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

        # CodeAct loop
        while not session.is_exhausted():
            turn_number += 1

            async with session.turn(
                runtime.event_manager,
                call.method_name,
                self.name,
                generation_id,
                parent_generation_id,
                turn_number,
            ) as turn_state:
                # Use "auto" for tool_choice - "required" can cause 500 errors
                # on some models (e.g., Nemotron) when there's conversation events
                tool_choice = "auto"

                logger.debug(
                    f"[CODEACT] Loop iteration: iter={session.iteration}/{session.max_iterations}, "
                    f"err={session.error_count}/{session.max_retries}, tool_choice={tool_choice}"
                )

                response = None
                event_id = None
                try:
                    # generate() rebuilds the conversation from event_manager each call,
                    # so events added in prior iterations change what the LLM sees.
                    response, event_id = await runtime.generate(
                        tools=tools,
                        tool_choice=tool_choice,
                        **self._build_sampling_kwargs(),
                    )
                except BlockSyntaxError as e:
                    self._handle_block_syntax_error(e, session, runtime)
                    continue

                except Exception as e:
                    # LLM API errors (rate limits, connection errors, timeouts, etc.)
                    # Note: unifiedllm already has retry logic with exponential backoff
                    # for 429, 500, 502, 503, 504 errors. This handles cases where
                    # all retries are exhausted or the error is not retryable.
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
                        f"[CODEACT] LLM API error (iter={session.iteration}, err={session.error_count}): "
                        f"{cause_chain}",
                        exc_info=True,
                    )
                    turn_state.exception = error_name
                    if session.is_exhausted():
                        turn_state.is_final = True
                        raise GenerationError(
                            f"LLM API error after {session.max_retries} retries. "
                            f"Original error: {error_name}: {e}"
                        ) from e

                # Skip rest of turn if LLM call failed
                if response is None:
                    continue

                # Tool calls
                if response.finish_reason == "tool_calls" and response.tool_calls:
                    tool_calls = response.tool_calls
                    # If the LLM also emitted message content alongside the tool
                    # call(s), preserve it by prepending reasoning(text) at the
                    # top of the first execute_python code block.
                    if response.content:
                        content = response.content
                        text = (
                            content.model_dump_json()
                            if isinstance(content, BaseModel)
                            else str(content)
                        )
                        if text.strip():
                            tool_calls = _prepend_reasoning(tool_calls, text)
                    result = await self._process_tool_calls(
                        tool_calls,
                        runtime,
                        builtins,
                        session,
                        call,
                        return_type,
                        event_id or "",
                    )
                    if result.completed:
                        turn_state.success = True
                        turn_state.is_final = True
                        return result.final_value
                    continue

                # Text-only response (no tool call) - convert to synthetic reasoning() call
                if response.content:
                    # Normalize content so we can strip-check before committing to synthetic path
                    content = response.content
                    text = (
                        content.model_dump_json()
                        if isinstance(content, BaseModel)
                        else str(content)
                    )
                    if text.strip():
                        session.record_iteration()
                        # Remove the bare LLMOutput — some APIs (e.g., NVIDIA) reject assistant
                        # messages without a tool call. Convert the text to a synthetic
                        # execute_python(reasoning(...)) pair so the content is preserved in
                        # traces and the LLM learns correct interface usage by example.
                        runtime.event_manager.remove(event_id)
                        synthetic_id = f"synthetic_{uuid4().hex[:8]}"
                        runtime.event_manager.add(
                            ToolCallEvent(
                                tool_call_id=synthetic_id,
                                name="execute_python",
                                arguments={"code": f"reasoning({_truncate_reasoning(text)!r})"},
                                result=ToolResult(
                                    tool_call_id=synthetic_id,
                                    content="status: complete",
                                    result_status=ResultStatus.COMPLETE,
                                ),
                                metadata={"synthetic": True, "synthetic_type": "text_response"},
                            )
                        )
                        runtime.event_manager.add(
                            PythonOutput(
                                tool_call_id=synthetic_id,
                                execution_count=session.iteration or 1,
                                execution_status=ResultStatus.COMPLETE,
                                metadata={"synthetic": True, "synthetic_type": "text_response"},
                            )
                        )
                        logger.debug(
                            f"[CODEACT] Text-only response ({len(text)} chars) converted to synthetic reasoning() call."
                        )
                        continue
                    # Whitespace-only content falls through to the empty-response error handler.

                # Empty response - error
                session.record_error()
                # Remove the empty assistant event - APIs reject empty content
                runtime.event_manager.remove(event_id)
                feedback = await self._tool_use_reminder(runtime, reason="Empty response received.")
                runtime.event_manager.add(Error(content=feedback))

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

    def _translate_tool_call_to_code(
        self,
        tool_name: str,
        args: dict[str, Any],
        builtins: dict[str, Any],
        session: CodeActSession,
        runtime: RuntimeServices,
    ) -> str | None:
        """Translate an unknown tool call into execute_python code.

        Weaker models sometimes call agent methods directly as tool calls
        instead of using execute_python(). This translates those into
        equivalent execute_python code, teaching the model the correct
        pattern for subsequent turns.

        Returns Python code string if translatable, None otherwise.
        """
        # Determine how to call the function
        call_target = None
        is_async = False

        # Check if it's a method on the agent (accessible via self.)
        agent = runtime.agent
        if (
            not tool_name.startswith("_")
            and hasattr(agent, tool_name)
            and callable(getattr(agent, tool_name))
        ):
            call_target = f"self.{tool_name}"
            is_async = inspect.iscoroutinefunction(getattr(agent, tool_name))
        # Check if it's a known builtin (module-level function, etc.)
        elif tool_name in builtins and callable(builtins.get(tool_name)):
            call_target = tool_name
            is_async = inspect.iscoroutinefunction(builtins[tool_name])
        # Check session locals (previously defined functions)
        elif tool_name in session.session_locals and callable(
            session.session_locals.get(tool_name)
        ):
            call_target = tool_name
            is_async = inspect.iscoroutinefunction(session.session_locals[tool_name])

        if call_target is None:
            return None

        # Build argument string from the parsed args dict
        arg_parts = []
        for k, v in args.items():
            arg_parts.append(f"{k}={v!r}")
        args_str = ", ".join(arg_parts)

        # Generate code — use await for async methods to pass CodeAct validation
        call_expr = f"await {call_target}({args_str})" if is_async else f"{call_target}({args_str})"
        code = f"result = {call_expr}\nprint(result)"
        return code

    async def _process_tool_calls(
        self,
        tool_calls: list,
        runtime: RuntimeServices,
        builtins: dict[str, Any],
        session: CodeActSession,
        call: "CurrentCall",
        return_type: Any,
        event_id: str,
    ) -> _ToolCallsResult:
        """Process tool calls from a single LLM turn.

        Executes tool calls sequentially, stopping at the first error.
        Returns a _ToolCallsResult indicating whether the task completed.
        """
        # Handle tool calls - process ALL tool calls sequentially
        # Some LLMs return multiple tool calls in one response even when
        # parallel_tool_calls=false. We execute them in order, with each
        # cell's output available to subsequent cells via session_locals.
        session.record_iteration()

        # Remove the empty LLMOutput that runtime.generate() created
        # and replace it with a proper ToolCallEvent that includes tool_calls
        runtime.event_manager.remove(event_id)

        num_tool_calls = len(tool_calls)
        if num_tool_calls > 1:
            logger.debug(f"[CODEACT] Processing {num_tool_calls} tool calls sequentially")

        # Process each tool call in order, stopping at the first error.
        # If one cell fails, subsequent cells likely depend on its output
        # and would cascade into confusing errors.
        for tool_call in tool_calls:
            # Parse arguments
            try:
                args = json.loads(tool_call.arguments)
            except json.JSONDecodeError as e:
                session.record_error()
                runtime.event_manager.add(
                    Error(content=f"Invalid arguments for tool `{tool_call.name}`: {e}")
                )
                # Stop processing remaining tool calls - let LLM fix this first
                return _ToolCallsResult()

            # Add ToolCallEvent to record the tool call (result will be nested later)
            tool_call_event_id = runtime.event_manager.add(
                ToolCallEvent(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    arguments=args,
                    result=None,  # Will be updated after execution
                )
            )

            # Handle based on tool name
            if tool_call.name == "execute_python":
                # Execute Python code
                result = await self._handle_execute_python(
                    runtime,
                    tool_call,
                    args,
                    builtins,
                    session,
                    call.method_name,
                    return_type,
                    tool_call_event_id=tool_call_event_id,
                )
                if result is None or getattr(result, "error", None) is not None:
                    # Error occurred (already handled) or code execution failed -
                    # stop processing remaining tool calls
                    return _ToolCallsResult()

                # Check if return_result() was called inline
                if isinstance(result, tuple) and result[0] == "TASK_COMPLETE":
                    # Task completed via inline return_result()
                    return _ToolCallsResult(completed=True, final_value=result[1])

            elif tool_call.name == "return_result":
                # Return the final result
                validated, error_msg = self._handle_return_result(
                    runtime, tool_call, args, return_type, session, call.method_name
                )
                if error_msg is None:
                    # Success! Update ToolCallEvent with nested result
                    runtime.event_manager.update(
                        tool_call_event_id,
                        result=ToolResult(
                            tool_call_id=tool_call.id,
                            content="Result accepted.",
                            result_status=ResultStatus.COMPLETE,
                        ),
                    )
                    logger.info("[CODEACT] Task completed successfully via return_result")
                    return _ToolCallsResult(completed=True, final_value=validated)
                # Validation error - update with error result
                runtime.event_manager.update(
                    tool_call_event_id,
                    result=ToolResult(
                        tool_call_id=tool_call.id,
                        content=f"Invalid result: {error_msg}\n"
                        f"Please call return_result again with valid arguments. "
                        f"Tip: if you computed the result in execute_python(), you can call "
                        f"return_result(variable) from within the code instead.",
                        result_status=ResultStatus.ERROR,
                    ),
                )
                # Stop processing remaining tool calls
                return _ToolCallsResult()

            else:
                # Unknown tool — attempt to translate to execute_python.
                # Weaker models sometimes call agent methods directly as tool
                # calls instead of wrapping them in execute_python().
                translated_code = (
                    self._translate_tool_call_to_code(
                        tool_call.name, args, builtins, session, runtime
                    )
                    if self.config.translate_tool_calls
                    else None
                )
                if translated_code is not None:
                    logger.debug(
                        f"[CODEACT] Translated tool call '{tool_call.name}' -> execute_python"
                    )
                    # Update ToolCallEvent to reflect the translation
                    runtime.event_manager.update(
                        tool_call_event_id,
                        name="execute_python",
                        arguments={"code": translated_code},
                    )
                    translated_args = {"code": translated_code}
                    result = await self._handle_execute_python(
                        runtime,
                        tool_call,
                        translated_args,
                        builtins,
                        session,
                        call.method_name,
                        return_type,
                        tool_call_event_id=tool_call_event_id,
                    )
                    if result is None or getattr(result, "error", None) is not None:
                        return _ToolCallsResult()
                    if isinstance(result, tuple) and result[0] == "TASK_COMPLETE":
                        return _ToolCallsResult(completed=True, final_value=result[1])
                else:
                    # Truly unknown tool — not translatable
                    session.record_error()
                    runtime.event_manager.update(
                        tool_call_event_id,
                        result=ToolResult(
                            tool_call_id=tool_call.id,
                            content=f"Unknown tool `{tool_call.name}`. "
                            f"Available tools: execute_python, return_result",
                            result_status=ResultStatus.ERROR,
                        ),
                    )
                    # Stop processing remaining tool calls
                    return _ToolCallsResult()

        # All tool calls processed without completion or error-break
        return _ToolCallsResult()

    def _sanitize_code(self, code: str) -> str:
        """Remove markdown code fences that models sometimes include in generated code.

        Some models (especially when they see markdown in prompts) accidentally include
        markdown code fence markers like ``` or ```python in their generated code.
        This strips those artifacts to prevent syntax errors.

        Args:
            code: Raw code string from LLM

        Returns:
            Sanitized code with markdown artifacts removed
        """
        import re

        # Strip leading/trailing whitespace first
        code = code.strip()

        # Remove leading markdown code fence (```python, ```py, ``` etc.)
        code = re.sub(r"^```(?:python|py)?\s*\n?", "", code)

        # Remove trailing markdown code fence
        code = re.sub(r"\n?```\s*$", "", code)

        return code.strip()

    @staticmethod
    def _handle_block_syntax_error(
        e: BlockSyntaxError,
        session: "CodeActSession",
        runtime: RuntimeServices,
    ) -> None:
        """Handle a BlockSyntaxError raised during generate().

        The LLM created a context block with invalid Python syntax.
        This is recoverable — remove the bad block, add error feedback
        so the LLM can fix it, and record an iteration (not an error
        retry, since retrying with the bad block would just fail again).
        """
        logger.warning(f"[CODEACT] Block syntax error in block '{e.key}': {e.original_error}")

        # Remove the bad block so subsequent attempts can proceed
        try:
            _ctx = getattr(runtime, "context", None)
            if _ctx is not None:
                _ctx.remove(e.key)
                logger.debug(f"[CODEACT] Removed bad block '{e.key}' from context")
        except Exception as remove_err:
            logger.debug(f"[CODEACT] Could not remove block '{e.key}': {remove_err}")

        # Add helpful error feedback for the LLM
        _field = getattr(e, "field", "expr")
        error_msg = (
            f"Error: Block '{e.key}' has invalid Python syntax.\n"
            f"The {_field} parameter must be a valid Python expression.\n"
            f"  Invalid {_field}: {e.expr[:100]}{'...' if len(e.expr) > 100 else ''}\n"
            f"  Error: {e.original_error}\n\n"
            f"To fix this, use context.set() with value= for static content:\n"
            f'  context.set("{e.key}", value="your content here")\n'
            f"Or use a valid Python expression:\n"
            f'  context.set("{e.key}", expr="self.my_variable")'
        )
        runtime.event_manager.add(Error(content=error_msg))

        # Record as an iteration (not error) since this is fixable feedback
        session.record_iteration()

    async def _handle_execute_python(
        self,
        runtime: RuntimeServices,
        tool_call: Any,
        args: dict[str, Any],
        builtins: dict[str, Any],
        session: CodeActSession,
        method_name: str,
        return_type: Any,
        tool_call_event_id: str,
    ) -> Any | None:
        """Handle execute_python tool call with deferred output pattern.

        The deferred output pattern ensures tool result is nested in ToolCallEvent
        even when nested agent calls occur during execution:

        1. Update ToolCallEvent.result with "status: executing" immediately
        2. Execute code (nested agent events may be added here)
        3. Update ToolCallEvent.result status to "complete" or "error"
        4. Add PythonOutput with actual output content

        Returns the execution result, a tuple ("TASK_COMPLETE", result) if return_result()
        was called inline, or None if an error occurred.
        """
        code = args.get("code", "")

        # Sanitize code: remove markdown code fences that models sometimes include
        code = self._sanitize_code(code)

        if not code.strip():
            session.record_error()
            # Update ToolCallEvent with error result
            runtime.event_manager.update(
                tool_call_event_id,
                result=ToolResult(
                    tool_call_id=tool_call.id,
                    content="status: error",
                    result_status=ResultStatus.ERROR,
                ),
            )
            runtime.event_manager.add(
                PythonOutput(
                    tool_call_id=tool_call.id,
                    execution_count=session.iteration,
                    stdout="",
                    stderr="Execution error: empty code provided.",
                    value=None,
                    explicit_return=False,
                    execution_status=ResultStatus.ERROR,
                )
            )
            return None

        # Update ToolCallEvent with executing status immediately - BEFORE code execution
        # This ensures result is nested even if nested agents add events
        runtime.event_manager.update(
            tool_call_event_id,
            result=ToolResult(
                tool_call_id=tool_call.id,
                content="status: executing",
                result_status=ResultStatus.COMPLETE,  # Will update to error if needed
            ),
        )

        # Execute the code (pass tool_call.id for trace correlation)
        # Nested agent calls may add their events to the event manager during this execution
        result = await self._execute_code(
            runtime, code, builtins, session, method_name, tool_call_id=tool_call.id
        )

        # Determine final status
        final_status = ResultStatus.ERROR if result.error else ResultStatus.COMPLETE

        # Update ToolCallEvent with final status
        runtime.event_manager.update(
            tool_call_event_id,
            result=ToolResult(
                tool_call_id=tool_call.id,
                content=f"status: {final_status.value}",
                result_status=final_status,
            ),
        )

        # Merge captured locals into session for REPL-style persistence
        if result.captured_locals:
            session.session_locals.update(result.captured_locals)
            logger.debug(f"[CODEACT] Captured locals: {list(result.captured_locals.keys())}")

        # Check if return_result() was called inline (signals task completion)
        if result.signal and isinstance(result.signal, _ReturnResultSignal):
            logger.debug("[CODEACT] Detected inline return_result() call")

            # Validate the return_result (called inline within execute_python, not as separate tool)
            validated, validation_error = self._handle_return_result(
                runtime,
                tool_call,
                result.signal.result,  # Extract the result dict from the signal
                return_type,
                session,
                method_name,
            )

            if validation_error:
                # Update result to reflect validation failure
                runtime.event_manager.update(
                    tool_call_event_id,
                    result=ToolResult(
                        tool_call_id=tool_call.id,
                        content="status: error",
                        result_status=ResultStatus.ERROR,
                    ),
                )

            error_text = ""
            if result.error:
                line_offset = getattr(result, "wrapper_line_offset", 0)
                error_text = self._format_error(result.error, line_offset=line_offset)
            stderr = result.stderr
            if error_text:
                stderr = (
                    f"{stderr}\nExecution error:\n{error_text}"
                    if stderr
                    else f"Execution error:\n{error_text}"
                )
            if validation_error:
                stderr = (
                    f"{stderr}\nreturn_result validation error: {validation_error}"
                    if stderr
                    else f"return_result validation error: {validation_error}"
                )

            runtime.event_manager.add(
                PythonOutput(
                    tool_call_id=tool_call.id,
                    execution_count=session.iteration,
                    stdout=result.stdout,
                    stderr=stderr,
                    value=result.returned_value if result.has_return else None,
                    explicit_return=result.explicit_return,
                    execution_status=ResultStatus.ERROR if validation_error else final_status,
                    images=result.images,
                )
            )

            if validation_error is None:
                # Success! Return special tuple to signal completion
                logger.info("[CODEACT] Task completed successfully via inline return_result()")
                return ("TASK_COMPLETE", validated)

            # Validation failed - error included in PythonOutput
            return None

        # Check if code used an explicit Python `return` statement with a value that matches
        # expected type. This handles the case where the LLM uses `return {...}` instead of
        # `return_result(...)`. We only auto-complete for EXPLICIT returns, not bare expressions.
        if result.explicit_return and result.has_return and not result.error:
            logger.debug(
                "[CODEACT] Detected explicit return statement - attempting auto-completion"
            )
            try:
                success, validated = self._try_validate_return_value(
                    result.returned_value,
                    return_type,
                    method_name,
                )
                if success:
                    # Add execution output event
                    runtime.event_manager.add(
                        PythonOutput(
                            tool_call_id=tool_call.id,
                            execution_count=session.iteration,
                            stdout=result.stdout,
                            stderr=result.stderr,
                            value=result.returned_value,
                            explicit_return=result.explicit_return,
                            execution_status=ResultStatus.COMPLETE,
                            images=result.images,
                        )
                    )
                    logger.info("[CODEACT] Auto-completed task from explicit return statement")
                    return ("TASK_COMPLETE", validated)
                # Validation failed - continue with normal flow
                logger.debug(
                    "[CODEACT] Auto-completion validation failed (type mismatch), continuing loop"
                )
            except Exception as e:
                logger.debug(f"[CODEACT] Auto-completion validation error: {e}, continuing loop")

        # Note: Bare expressions (IPython-style) are shown as "Out[n]:" but do NOT auto-complete.
        # Only explicit `return x` statements can auto-complete the task.

        # Format error if present
        error_text = ""
        if result.error:
            line_offset = getattr(result, "wrapper_line_offset", 0)
            if (
                isinstance(result.error, PydanticValidationError)
                and result.returned_value is not None
            ):
                error_text = format_validation_error(
                    result.error, return_type, result.returned_value, runtime.truncation_config
                )
            else:
                error_text = self._format_error(result.error, line_offset=line_offset)

        # Format captured locals summary for LLM visibility
        captured_summary = ""
        if result.captured_locals:
            items = [
                f"{k} ({type(v).__name__})"
                for k, v in result.captured_locals.items()
                if not k.startswith("_") and k not in ("Out",)
            ]
            if items:
                captured_summary = f"Variables now in scope: {', '.join(items)}"

        # Add PythonOutput with actual output and value
        runtime.event_manager.add(
            PythonOutput(
                tool_call_id=tool_call.id,
                execution_count=session.iteration,
                stdout=result.stdout,
                stderr=result.stderr,
                error=error_text,
                value=result.returned_value if result.has_return and not result.error else None,
                explicit_return=result.explicit_return if result.has_return else False,
                execution_status=final_status,
                captured_locals=captured_summary,
                images=result.images,
            )
        )

        logger.debug(
            f"[CODEACT] execute_python complete. "
            f"stdout_len={len(result.stdout)}, "
            f"has_return={result.has_return}, "
            f"error={result.error is not None}"
        )

        return result

    def _handle_return_result(
        self,
        runtime: RuntimeServices,
        tool_call: Any,
        args: dict[str, Any],
        return_type: Any,
        session: CodeActSession,
        method_name: str,
    ) -> tuple[Any, str | None]:
        """Validate return_result arguments and return the result.

        Pure validation - does NOT update ToolCallEvent.result. Caller is responsible
        for updating the ToolCallEvent with the result when return_result is called
        as a tool (vs inline within execute_python code).

        Args should be: {"result": <value matching return_type>}

        Returns:
            Tuple of (validated_result, error_message):
            - (value, None) on success
            - (None, "error message") on validation failure
        """
        # Generate execution ID and get current generation_id for correlation
        execution_id = str(uuid4())
        current_generation_id = runtime.get_generation_id()

        # Call before hook
        hook_context = call_before_hook(
            "before_tool_execution",
            agent=runtime.agent,
            tool_name="return_result",
            arguments=args,
            execution_id=execution_id,
            generation_id=current_generation_id,
        )

        validated = None
        exception = None
        error_msg = None
        normalized_args: dict[str, Any] = {}

        try:
            # Special case: None return type
            # Accept empty args, result=None, or no result key - all valid for -> None methods
            # Note: `-> None` annotation gives `None` (the value), not `type(None)` (NoneType)
            if return_type is None or return_type is type(None):
                # For None return type, just verify result is None (or missing)
                result_value = args.get("result", None)
                if result_value is not None:
                    error_msg = (
                        f"Method returns None but got result={result_value!r}. "
                        f"Call return_result() with no arguments."
                    )
                    return (None, error_msg)
                return (None, None)  # Success - return None

            # Normalize args to always have "result" key
            # Be flexible: accept both return_result(result=...) and return_result(field1=..., field2=...)
            if "result" not in args and len(args) > 0:
                # LLM passed direct fields (e.g., sum=100, mean=20)
                # Wrap them as the result value
                normalized_args: dict[str, Any] = {"result": args}
            else:
                # Already has "result" key, use as-is
                normalized_args = args

            # Fix GPT-4o-mini double-quoting bug: LLM sometimes wraps string in extra quotes
            # with escaped newlines. Detect and unwrap: "\"code\\n\"" -> "code\n"
            # Safe because legitimate code never starts/ends with " AND contains \\n
            if "result" in normalized_args and isinstance(normalized_args["result"], str):
                result_val = normalized_args["result"]
                if result_val.startswith('"') and result_val.endswith('"') and "\\n" in result_val:
                    # Unwrap the extra quotes and decode escaped characters
                    normalized_args["result"] = result_val[1:-1].encode().decode("unicode_escape")

            # Create wrapper model: class ReturnResultModel(BaseModel): result: T
            # For non-Pydantic types (e.g. pd.DataFrame), falls back to Any in the model
            ReturnResultModel, is_pydantic_validated = self._create_return_model(
                return_type, method_name
            )

            # Handle case where LLM passes a string instead of actual object.
            # Don't transform if the expected return type IS str — the string should stay as-is.
            if (
                "result" in normalized_args
                and isinstance(normalized_args["result"], str)
                and return_type is not str
            ):
                result_str = normalized_args["result"]
                # Variable reference resolution: the LLM sometimes calls return_result
                # as a tool with a variable name (e.g. {"result": "results"}) instead of
                # calling return_result(results) from within execute_python code.
                # Resolve the variable from the session namespace if it exists.
                if result_str.isidentifier() and result_str in session.session_locals:
                    logger.debug(
                        "[CODEACT] Resolved variable reference %r from session locals "
                        "in return_result tool call",
                        result_str,
                    )
                    normalized_args["result"] = session.session_locals[result_str]
                else:
                    normalized_args["result"] = self._maybe_parse_json_string(result_str)

            # Validate using Pydantic
            validated_model = ReturnResultModel(**normalized_args)
            validated = getattr(validated_model, "result")  # noqa: B009

            # For non-Pydantic types, Pydantic accepted Any — do isinstance check.
            # Unwrap Annotated[T, ...] first; isinstance() can't take Annotated as 2nd arg.
            if not is_pydantic_validated and validated is not None:
                base_type, _ = self._extract_annotated_description(return_type)
                if not isinstance(validated, base_type):
                    type_name = getattr(base_type, "__name__", str(base_type))
                    raise TypeError(
                        f"Expected an instance of {type_name}, "
                        f"but got {type(validated).__name__}.\n"
                        f"Hint: Use execute_python() to construct the {type_name} object, "
                        f"then call return_result(variable) from within the code."
                    )

            return (validated, None)

        except (PydanticValidationError, ValueError, TypeError, json.JSONDecodeError) as e:
            exception = e
            session.record_error()
            # Pass actual value for better "Got: {...}" error messages
            actual_value = normalized_args.get("result") if normalized_args else None
            error_msg = format_validation_error(
                e, return_type, actual_value, runtime.truncation_config
            )
            logger.debug(f"[CODEACT] return_result validation failed: {e}")

            if session.is_exhausted():
                raise GenerationError(
                    f"return_result validation failed after {session.max_retries} attempts.\n"
                    f"Last error:\n{error_msg}"
                ) from e

            return (None, error_msg)

        finally:
            # Call after hook
            call_after_hook(
                "after_tool_execution",
                hook_context,
                agent=runtime.agent,
                tool_name="return_result",
                arguments=args,
                result=validated,
                exception=exception,
                execution_id=execution_id,
            )

    def _maybe_parse_json_string(self, value: Any) -> Any:
        """Parse a JSON or Python literal string, otherwise return as-is.

        Some LLMs return structured data as a string instead of actual object values.
        This handles both JSON syntax and Python literal syntax (e.g., lists with
        single quotes like "['a', 'b']").
        """
        if not isinstance(value, str):
            return value

        # Check if it looks like a structured value (starts with { or [)
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            # Try JSON first (more strict)
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass

            # Try Python literal syntax (handles single quotes, etc.)
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                pass  # Not valid Python literal, return as-is

        return value

    def _try_validate_return_value(
        self,
        value: Any,
        return_type: Any,
        method_name: str,
    ) -> tuple[bool, Any]:
        """Try to validate a value against the expected return type.

        Returns:
            Tuple of (success: bool, validated_value: Any)
            - (True, value) if validation succeeded
            - (False, None) if validation failed

        Does NOT add any events to the event manager - used for silent validation checks.
        """
        try:
            # Special case: None return type
            # Note: `-> None` annotation gives `None` (the value), not `type(None)` (NoneType)
            if return_type is None or return_type is type(None):
                if value is None:
                    return (True, None)
                return (False, None)

            # Create wrapper model: class ReturnResultModel(BaseModel): result: T
            # For non-Pydantic types, falls back to Any in the model
            ReturnResultModel, is_pydantic_validated = self._create_return_model(
                return_type, method_name
            )

            # Handle case where value is a JSON string
            # BUT: Don't parse if the expected return type IS str - the string should stay as-is
            if isinstance(value, str) and return_type is not str:
                value = self._maybe_parse_json_string(value)

            # Validate using Pydantic
            validated_model = ReturnResultModel(result=value)
            validated = getattr(validated_model, "result")  # noqa: B009

            # For non-Pydantic types, do isinstance check instead.
            # Unwrap Annotated[T, ...] first; isinstance() can't take Annotated as 2nd arg.
            if not is_pydantic_validated and validated is not None:
                base_type, _ = self._extract_annotated_description(return_type)
                if not isinstance(validated, base_type):
                    return (False, None)

            return (True, validated)

        except (PydanticValidationError, ValueError, TypeError, json.JSONDecodeError):
            return (False, None)

    def _build_execute_python_tool(self) -> Tool:
        """Build the execute_python tool definition."""

        def execute_python(code: str) -> str:
            """Execute Python code in the agent's environment.

            Variables and helper functions persist across calls.
            Access `self` (agent instance), task parameters, and all
            pre-imported modules (see Execution Context).

            Args:
                code: Python code to execute.

            Returns:
                Execution result including stdout, errors, and any returned values.
            """
            return ""

        return Tool(
            name="execute_python",
            description=(
                "Execute Python code in the agent's environment. "
                "Variables persist across calls. "
                "Access `self`, task parameters, and all pre-imported modules "
                "(see Execution Context). "
                "Use `return <value>` to capture results."
            ),
            callable=execute_python,
            parameters_model=None,  # Auto-generate from callable signature
        )

    def _is_pydantic_compatible(self, return_type: Any) -> bool:
        """Check if a type can be used with pydantic.create_model().

        Probes create_model() to test compatibility. No cache — the probe is
        microseconds and avoids id() GC reuse bugs and global mutable state.

        Returns True for Pydantic models, dataclasses, basic types, generics, etc.
        Returns False for types like pd.DataFrame, np.ndarray, custom classes without
        Pydantic support.
        """
        try:
            create_model("_PydanticCompatProbe", result=(return_type, ...))
            return True
        except (PydanticSchemaGenerationError, PydanticUserError, TypeError):
            return False

    def _create_return_model(self, return_type: Any, method_name: str) -> tuple[Any, bool]:
        """Create a Pydantic model for return_result validation.

        Returns:
            Tuple of (model_class, is_pydantic_validated).
            If the return type is Pydantic-compatible, returns the proper model and True.
            If not, returns a model with Any and False (caller should do isinstance check).
        """
        if self._is_pydantic_compatible(return_type):
            model = create_model(
                f"{method_name.title().replace('_', '')}ReturnResult",
                result=(return_type, ...),
            )
            return model, True
        else:
            # Fall back to Any — accept any value, let caller do isinstance check
            model = create_model(
                f"{method_name.title().replace('_', '')}ReturnResult",
                result=(Any, ...),
            )
            return model, False

    def _extract_annotated_description(self, type_hint: Any) -> tuple[Any, str | None]:
        """Extract the base type and first string metadata from Annotated.

        If the type is Annotated[T, "description", ...], extracts T and "description".
        If already has Field, returns (type_hint, None) to avoid double-wrapping.

        Returns:
            (base_type_or_original, description) where description is the first string metadata or None
        """
        from pydantic.fields import FieldInfo

        origin = get_origin(type_hint)

        if origin is Annotated:
            args = get_args(type_hint)
            base_type = args[0]  # First arg is always the actual type
            metadata = args[1:]  # Rest are metadata

            # Check if already has Field/FieldInfo - if so, don't extract
            for item in metadata:
                if isinstance(item, FieldInfo):
                    return type_hint, None

            # Find first string in metadata
            for item in metadata:
                if isinstance(item, str):
                    return base_type, item

            return base_type, None

        # Not annotated
        return type_hint, None

    def _build_return_result_tool(self, return_type: Any, method_name: str) -> Tool:
        """Build the return_result tool with schema matching the return type.

        Always uses a consistent schema: {result: <return_type>}
        Runtime parsing is flexible to accept multiple calling conventions.

        Automatically extracts string descriptions from Annotated[T, "description"]
        and converts them to Pydantic Field descriptions in the tool schema.

        Special case: When return_type is None, the tool accepts no parameters
        (or optional result=None) to indicate task completion.
        """
        from pydantic import Field

        def return_result(result: Any = None) -> Any:
            """Return the final result for the task.

            Call this tool when you have computed the final answer.
            The result must match the expected return type.
            """
            # This callable won't actually be called - we handle it in the execute loop
            return result

        # Handle None return type specially - no required result parameter
        # Note: `-> None` annotation gives `None` (the value), not `type(None)` (NoneType)
        if return_type is None or return_type is type(None):
            # For None return type, result is optional with default None
            ReturnResultModel = create_model(
                f"{method_name.title().replace('_', '')}ReturnResult",
                result=(type(None), None),  # Optional, defaults to None
            )
            description = (
                "Signal task completion. "
                "Call this when you have finished the task. "
                "No parameters required."
            )
        else:
            # Extract description from Annotated if present
            base_type, annotated_desc = self._extract_annotated_description(return_type)

            # Check if the type is Pydantic-compatible; fall back to Any if not
            # (e.g. pd.DataFrame, np.ndarray, custom classes without Pydantic support)
            schema_type = base_type if self._is_pydantic_compatible(base_type) else Any

            # Build the result field with description if available
            if annotated_desc:
                field = Field(..., description=annotated_desc)
                ReturnResultModel = create_model(
                    f"{method_name.title().replace('_', '')}ReturnResult",
                    result=(schema_type, field),
                )
            else:
                ReturnResultModel = create_model(
                    f"{method_name.title().replace('_', '')}ReturnResult",
                    result=(schema_type, ...),
                )

            # Always mention the actual type in the description, even if schema uses Any
            type_name = _format_type(return_type)
            if schema_type is Any and schema_type is not return_type:
                description = (
                    f"Return the final result for the task. "
                    f"Call this ONLY when you have computed the final answer. "
                    f"Expected return type: {type_name}. "
                    f"IMPORTANT: This type cannot be passed directly via this tool. "
                    f"Construct the object in execute_python() and call "
                    f"return_result(variable) from within the code instead."
                )
            else:
                description = (
                    f"Return the final result for the task. "
                    f"Call this ONLY when you have computed the final answer. "
                    f"Expected return type: {type_name}. "
                    f"Tip: prefer calling return_result(variable) from within execute_python() "
                    f"to pass computed results directly."
                )

        return Tool(
            name="return_result",
            description=description,
            callable=return_result,
            parameters_model=ReturnResultModel,
        )

    async def _run_prefill(
        self,
        runtime: RuntimeServices,
        call: "CurrentCall",
        builtins: dict[str, Any],
        session: CodeActSession,
    ) -> None:
        """Run prefill code before the main generation loop.

        Executes prefill code as separate synthetic tool calls. Each prefill type
        runs as its own execution, demonstrating variable persistence across turns.
        This helps the LLM understand that computed variables remain available.

        Runs two types of prefill code (as separate executions):
        1. InspectInputsPrefill: Auto-generated code to inspect input parameters
        2. Pre-ellipsis code: User-defined setup code before the `...` marker
        """
        from nemo_oo_agents.strategies.prefill import InspectInputsPrefill

        prefill_builtins = {**builtins, "_call": call}

        # 1. InspectInputsPrefill (auto-generated parameter inspection)
        # Shows raw inputs first, before any user processing
        prefill = InspectInputsPrefill()
        truncation_config = self._get_truncation_config(runtime)
        inspect_code = prefill.get_code(call, config=truncation_config)

        if inspect_code:
            await self._execute_prefill_step(
                runtime,
                inspect_code,
                prefill_builtins,
                session,
                call.method_name,
                prefill_type="inspect_inputs",
            )

        # 2. Pre-ellipsis code (user-defined setup before ...)
        # Runs as separate execution - LLM sees variables persist from step 1
        if call.pre_ellipsis_code:
            logger.debug(f"[CODEACT] Pre-ellipsis code: {len(call.pre_ellipsis_code)} chars")
            await self._execute_prefill_step(
                runtime,
                call.pre_ellipsis_code,
                prefill_builtins,
                session,
                call.method_name,
                prefill_type="pre_ellipsis",
            )

    async def _execute_prefill_step(
        self,
        runtime: RuntimeServices,
        code: str,
        builtins: dict[str, Any],
        session: CodeActSession,
        method_name: str,
        prefill_type: str,
    ) -> None:
        """Execute a single prefill step as a synthetic tool call.

        Each prefill step appears as a separate code execution in events,
        helping the LLM understand that variables persist across turns.
        """
        logger.debug(f"[CODEACT] Running prefill ({prefill_type}) for {method_name}")

        # Create synthetic tool call
        prefill_id = f"prefill_{uuid4().hex[:8]}"
        prefill_event_id = runtime.event_manager.add(
            ToolCallEvent(
                tool_call_id=prefill_id,
                name="execute_python",
                arguments={"code": code},
                result=None,  # Will be updated after execution
                metadata={"prefill": True, "prefill_type": "inspect_inputs"},
            )
        )

        # Update with executing status immediately (deferred output pattern)
        runtime.event_manager.update(
            prefill_event_id,
            result=ToolResult(
                tool_call_id=prefill_id,
                content="status: executing",
                result_status=ResultStatus.COMPLETE,  # Will update to error if needed
            ),
        )

        # Execute the code
        result = await self._execute_code(
            runtime, code, builtins, session, method_name, tool_call_id=prefill_id
        )

        # Merge captured locals into session (persists for next steps and LLM turns)
        if result.captured_locals:
            session.session_locals.update(result.captured_locals)
            logger.debug(
                f"[CODEACT] Prefill ({prefill_type}) captured locals: "
                f"{list(result.captured_locals.keys())}"
            )

        # Update ToolCallEvent with final status
        final_status = ResultStatus.ERROR if result.error else ResultStatus.COMPLETE
        runtime.event_manager.update(
            prefill_event_id,
            result=ToolResult(
                tool_call_id=prefill_id,
                content=f"status: {final_status.value}",
                result_status=final_status,
            ),
        )

        # Format error if present
        error_text = ""
        if result.error:
            line_offset = getattr(result, "wrapper_line_offset", 0)
            error_text = self._format_error(result.error, line_offset=line_offset)

        # Format captured locals summary for LLM visibility
        captured_summary = ""
        if result.captured_locals:
            # Filter out private/internal variables, format as "name (type)"
            items = [
                f"{k} ({type(v).__name__})"
                for k, v in result.captured_locals.items()
                if not k.startswith("_") and k not in ("Out",)
            ]
            if items:
                captured_summary = f"Variables now in scope: {', '.join(items)}"

        # Add execution output as user message (execution_count=0 since before main loop)
        runtime.event_manager.add(
            PythonOutput(
                tool_call_id=prefill_id,
                execution_count=0,  # Prefill is before main loop
                stdout=result.stdout,
                stderr=result.stderr,
                error=error_text,
                value=result.returned_value if result.has_return else None,
                explicit_return=result.explicit_return if result.has_return else False,
                execution_status=final_status,
                captured_locals=captured_summary,
                images=result.images,
                metadata={"prefill": True, "prefill_type": prefill_type},
            )
        )

        if result.error:
            logger.warning(f"[CODEACT] Prefill ({prefill_type}) execution error: {result.error}")

    async def _execute_code(
        self,
        runtime: RuntimeServices,
        code: str,
        builtins: dict[str, Any],
        session: CodeActSession,
        target_method_name: str,
        tool_call_id: str | None = None,
    ) -> Any:
        """Execute Python code via the runtime."""
        from nemo_oo_agents.events import ExecutionResult

        logger.debug(
            "[CODEACT] Executing code (iter=%s, err=%s, chars=%s)",
            session.iteration,
            session.error_count,
            len(code),
        )

        # Validate REPL policy (no classes, await async methods)
        validator = GeneratedCodeValidator()
        validation_errors = validator.validate(code, runtime.agent)
        if validation_errors:
            error_msg = "Code validation failed:\n" + "\n".join(
                f"• {err}" for err in validation_errors
            )
            logger.warning(f"[CODEACT] Validation errors: {validation_errors}")
            return ExecutionResult(stdout="", error=Exception(error_msg), defined_methods={})

        # Build execution namespace
        strategy_extras: dict[str, Any] = {
            "CodeActStrategy": type(self),
        }
        try:
            from nemo_oo_agents.strategies.predict import PredictStrategy

            strategy_extras["PredictStrategy"] = PredictStrategy
        except ImportError:
            pass

        namespace = ExecutionNamespaceBuilder.build(
            runtime.agent, extra={**builtins, **session.session_locals, **strategy_extras}
        )

        # Extract and bind helper methods before execution
        helper_manager = HelperMethodManager()
        helper_result = helper_manager.apply(
            code,
            runtime.agent,
            session.session_locals,
            namespace=namespace,
            target_method_name=target_method_name,
        )

        if helper_result.rejected:
            error_msg = (
                f"Cannot define method(s) {helper_result.rejected} - "
                f"they would overwrite the target method `{target_method_name}`."
            )
            logger.warning(f"[CODEACT] Rejected helper methods: {helper_result.rejected}")
            return ExecutionResult(stdout="", error=Exception(error_msg), defined_methods={})

        if helper_result.errors:
            error_msg = "Failed to define helper method(s):\n" + "\n".join(
                f"- {e}" for e in helper_result.errors
            )
            logger.warning(f"[CODEACT] Helper method binding errors: {helper_result.errors}")
            return ExecutionResult(stdout="", error=Exception(error_msg), defined_methods={})

        if helper_result.installed:
            logger.debug(f"[CODEACT] Installed helpers: {helper_result.installed}")

        # Execute with session locals
        execution_builtins = {**builtins, **session.session_locals}

        return await runtime.execute_code(
            code,
            builtins=execution_builtins,
            validate=True,
            wrap_in_function=True,
            timeout=self.config.cell_timeout,
            tool_call_id=tool_call_id,
            execution_count=session.iteration,
            restrictions=self.config.restrictions,
        )

    def _format_error(
        self, error: Exception, code: str | None = None, *, line_offset: int = 0
    ) -> str:
        """Format an error for display using the configured formatter."""
        if self.error_formatter is not None:
            # Custom formatters may or may not support line_offset
            try:
                return self.error_formatter.format(error, code, line_offset=line_offset)
            except TypeError:
                # Formatter doesn't accept line_offset
                return self.error_formatter.format(error, code)

        from nemo_oo_agents.errors.formatting import format_error_for_llm

        return format_error_for_llm(error, code, line_offset=line_offset)

    def _extract_module_context(
        self, agent_module: types.ModuleType, agent: Any | None = None
    ) -> dict[str, Any]:
        """Extract relevant items from agent's module for execution context.

        This makes the execution environment behave as if code was written directly
        in the method body, with access to all module-level imports and type definitions.

        Args:
            agent_module: The agent's defining module
            agent: Optional agent instance to check for skill/tool attributes

        Returns:
            Dict of names that should be available during code execution
        """
        context: dict[str, Any] = {}

        from agentdoc.visibility import filter_module_globals

        filtered = filter_module_globals(agent_module)

        # 1. Module-level imports and definitions
        for name, obj in filtered.items():
            # Include imported modules (import os, import json)
            if isinstance(obj, types.ModuleType):
                context[name] = obj
                continue

            # Include imported classes/functions
            # Check if it was imported (not defined in this module)
            obj_module = getattr(obj, "__module__", None)
            if obj_module and obj_module != agent_module.__name__:
                if isinstance(obj, type) or callable(obj):
                    context[name] = obj
                    continue

        # 2. Module-level type definitions (Pydantic models, dataclasses, etc.)
        for name, obj in filtered.items():
            # Include classes defined in this module
            if isinstance(obj, type):
                obj_module = getattr(obj, "__module__", None)
                if obj_module == agent_module.__name__:
                    context[name] = obj

        # 3. Auto-import classes for skill/tool instances found on the agent
        if agent is not None:
            self._import_dynamic_classes(agent, agent_module, context)

        # Note: Return type annotations are automatically included by steps 1-2
        # since they must be known symbols in the module namespace

        return context

    @staticmethod
    def _import_dynamic_classes(
        agent: Any, agent_module: types.ModuleType, context: dict[str, Any]
    ) -> None:
        """Auto-import classes for agent attributes not already in the execution context.

        When agents use dynamically generated tools/skills (e.g. from mcp_nemo_oo_agents or
        skills_nemo_oo_agents), their classes won't be in the module's imports. This discovers
        them from the agent's attributes and makes them available for doc()/isinstance().

        Package-agnostic: works for any class, not just known packages.
        """
        try:
            seen = set(context.keys())

            for attr_value in _iter_agent_attrs(agent):
                cls = type(attr_value)
                name = cls.__name__

                if name in seen or cls.__module__ == "builtins":
                    continue

                context[name] = cls
                agent_module.__dict__[name] = cls
                seen.add(name)
        except Exception:
            pass  # Convenience feature — never break execution

    def _build_builtins(self, runtime: RuntimeServices, call: "CurrentCall") -> dict[str, Any]:
        """Build execution builtins including module context.

        This creates an execution environment that behaves as if the LLM's code
        was written directly in the method body, with access to:
        - Module-level imports
        - Module-level type definitions (Pydantic models, etc.)
        - Strategy builtins (reasoning, return_result)
        - Method parameters
        """

        def reasoning(text: str) -> None:
            """Record reasoning (not shown to user)."""
            runtime.event_manager.add(Reasoning(content=str(text)), record=False)

        def return_result(*args: Any, **kwargs: Any) -> None:
            """Submit the final answer from within execute_python code.

            Args:
                *args: Single positional argument (e.g., return_result(42))
                **kwargs: Named fields matching the return type
                    (e.g., return_result(field1=val1, field2=val2))
            """
            if args:
                if len(args) > 1:
                    raise ValueError("return_result() takes at most 1 positional argument")
                if kwargs:
                    raise ValueError(
                        "Cannot mix positional and keyword arguments in return_result()"
                    )
                # Single positional argument - treat as the 'result' field
                raise _ReturnResultSignal(result={"result": list(args)[0]})
            else:
                # Keyword arguments - pass as-is
                raise _ReturnResultSignal(result=kwargs)

        # Start with module context (imports, type definitions)
        agent_module = inspect.getmodule(type(runtime.agent))
        builtins: dict[str, Any] = {}
        if agent_module:
            builtins.update(self._extract_module_context(agent_module, agent=runtime.agent))

        # Add strategy builtins (these override any module-level names)
        builtins.update(
            {
                "reasoning": reasoning,
                "return_result": return_result,
            }
        )

        # Add method parameters as variables.
        # call.kwargs is already the fully merged positional+keyword mapping
        # (built by _execute_with_generation before the strategy is invoked).
        builtins.update(call.kwargs)

        return builtins
