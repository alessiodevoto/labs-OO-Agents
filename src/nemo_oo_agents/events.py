# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Event types for the event pipeline.

Events flow through EventManager.add() for:
- Recording events (if record=True)
- Notifying subscribers via on()
- Telemetry (future: hooks)

Design: phase-2-strategy-middleware.md

NeMo OO Agents events extend context-blocks EventBase and add specialized types
for the code generation pipeline (task, error, feedback, reasoning).

Rendering: Events are rendered using pformat(event). Fields with repr=False
are excluded. Private fields (_field with PrivateAttr) are also excluded.

Type names follow "Type Names are Prompts" - no redundant "Event" suffix.
"""

from collections.abc import Callable
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, Field

from nemo_oo_agents.context_blocks import EventBase as EventBase
from nemo_oo_agents.context_blocks import ResultStatus as ResultStatus
from nemo_oo_agents.context_blocks.models import Role

# Sentinel value to distinguish "no return" from "return None"
_NO_RETURN = object()


# === Execution Signals ===


class ExecutionSignal(BaseException):
    """Base class for control flow signals (not errors).

    Signals are used for non-error control flow like return_result().
    They should NOT be recorded as errors in traces.

    Inherits from BaseException (not Exception) so signals are not caught
    by 'except Exception:' blocks in LLM-generated code. This follows
    Python's convention for control flow exceptions like KeyboardInterrupt,
    SystemExit, and GeneratorExit.

    Subclasses can carry additional data (e.g., the result value).
    """

    pass


# === NeMo OO Agents-specific events ===


class Task(EventBase):  # type: ignore[misc]
    """Task prompt event - added at start of generation."""

    _role: ClassVar[Role] = Role.USER

    prompt: Annotated[str, Field(description="Task prompt describing what to do")]
    images: list[dict[str, Any]] = Field(
        default_factory=list,
        repr=False,
        description="Multimodal content blocks (images, audio, files) attached to this task",
    )


class Message(EventBase):  # type: ignore[misc]
    """User-facing message from generated code via message()."""

    _role: ClassVar[Role] = Role.ASSISTANT

    content: Annotated[str, Field(description="User-facing message content")]


class Reasoning(EventBase):  # type: ignore[misc]
    """Chain-of-thought from generated code via reasoning()."""

    _role: ClassVar[Role] = Role.ASSISTANT

    content: Annotated[str, Field(description="Chain-of-thought reasoning content")]


class Error(EventBase):  # type: ignore[misc]
    """Error for LLM retry."""

    _role: ClassVar[Role] = Role.USER

    content: Annotated[str, Field(description="Error message for LLM retry")]


class DebugTrace(EventBase):  # type: ignore[misc]
    """Debug info stored in traces but never shown to the LLM.

    Use for capturing raw LLM responses, internal state, or diagnostics
    that are useful for post-hoc debugging but should not influence generation.
    """

    _role: ClassVar[Role] = Role.METADATA

    content: Annotated[str, Field(description="Debug information for trace inspection")]


class Feedback(EventBase):  # type: ignore[misc]
    """Execution feedback when target method not yet defined."""

    _role: ClassVar[Role] = Role.USER

    content: Annotated[str, Field(description="Execution feedback content")]


class LLMOutput(EventBase):  # type: ignore[misc]
    """Raw LLM output - code (PURE_PYTHON), JSON (STRUCTURED_OUTPUT), or tool calls (CODEACT)."""

    _role: ClassVar[Role] = Role.ASSISTANT

    content: Annotated[str, Field(description="LLM response content (code or JSON)")]


class PythonOutput(EventBase):  # type: ignore[misc]
    """Output from execute_python - appears as user message in events.

    This event captures the actual output from code execution, separate from
    the tool result status. It allows nested agent calls without breaking
    message ordering requirements (tool results must immediately follow tool calls).
    """

    _role: ClassVar[Role] = Role.USER

    tool_call_id: Annotated[str, Field(description="ID of the tool call that produced this output")]
    execution_status: Annotated[
        ResultStatus,
        Field(description="Execution status (ResultStatus.COMPLETE or ResultStatus.ERROR)"),
    ]
    execution_count: Annotated[
        int,
        Field(
            repr=False, description="Jupyter-style execution count (1, 2, 3, ...) for Out[n] access"
        ),
    ]
    stdout: str = Field(default="", description="Captured stdout from execution")
    stderr: str = Field(default="", description="Captured stderr from execution")
    error: str = Field(default="", description="Formatted error message if execution failed")
    value: Any = Field(default=None, description="Python object returned (for Out[n] access)")
    explicit_return: bool = Field(
        default=False,
        repr=False,
        description="True if value from explicit `return x`, False if implicit",
    )
    captured_locals: str = Field(
        default="",
        repr=False,
        description="Summary of variables captured from execution (available in subsequent executions)",
    )
    images: list[dict[str, Any]] = Field(
        default_factory=list,
        repr=False,
        description="Image content blocks captured via show() during execution",
    )

    model_config = {"arbitrary_types_allowed": True}


class BeforeTurn(EventBase):  # type: ignore[misc]
    """Event emitted before each LLM generation turn.

    This event is emitted before every call to runtime.generate(), which happens
    in strategy loops (e.g., CodeAct's multi-turn loop). It's used to trigger
    event management policies before each generation turn.

    Uses Role.RUNTIME_EVENT to indicate it's never recorded in conversation events.
    """

    _role: ClassVar[Role] = Role.RUNTIME_EVENT

    method_name: Annotated[str, Field(description="Name of the method being generated")]
    strategy: Annotated[str, Field(description="Strategy name")]
    generation_id: Annotated[str, Field(description="Generation session ID")]
    parent_generation_id: str | None = Field(
        default=None, description="Parent generation ID if nested"
    )
    turn_number: Annotated[
        int, Field(description="Turn number within this generation session (1, 2, 3, ...)")
    ]


class AfterTurn(EventBase):  # type: ignore[misc]
    """Event emitted after each LLM generation turn.

    Symmetric with BeforeTurn. Emitted after every generation turn completes.
    When is_final=True, this is the last turn and the method has completed.

    Uses Role.RUNTIME_EVENT to indicate it's never recorded in conversation events.
    Use this for per-turn cleanup, event management, or post-generation analysis.

    Turn numbering:
    - turn_number=1, is_final=False: First turn completed, more turns expected
    - turn_number=N, is_final=True: Final turn, method complete
    """

    _role: ClassVar[Role] = Role.RUNTIME_EVENT

    method_name: Annotated[str, Field(description="Name of the method being generated")]
    strategy: Annotated[str, Field(description="Strategy name")]
    generation_id: Annotated[str, Field(description="Generation session ID")]
    parent_generation_id: str | None = Field(
        default=None, description="Parent generation ID if nested"
    )
    turn_number: Annotated[
        int, Field(description="Turn number within this generation session (1, 2, 3, ...)")
    ]
    is_final: Annotated[bool, Field(description="True if this is the final turn (method complete)")]
    success: bool | None = Field(
        default=None,
        description="True if method completed successfully (only set when is_final=True)",
    )
    exception_type: str | None = Field(
        default=None, description="Exception type if failed (only set when is_final=True)"
    )


class Notification(EventBase):  # type: ignore[misc]
    """Generic "something happened" signal rendered into the LLM context.

    Not tied to any specific producer. Input queues emit Notifications
    on ``put()`` to tell the LLM that new input arrived, but this event
    is equally usable for long-running job completions, timer ticks,
    external webhook deliveries, or any other asynchronous signal the
    agent should know about.

    - ``source`` identifies the producer — convention is a short
      namespaced string like ``"queue:user_messages"``, ``"job:12345"``,
      ``"timer:daily-cron"``. The outer dispatcher keys off this to
      decide which handler to run next.
    - ``description`` is a free-form string for the LLM; include enough
      to make the notification self-describing in the event stream.
    """

    _role: ClassVar[Role] = Role.USER

    source: Annotated[
        str, Field(description="Origin of the notification (e.g. 'queue:user_messages')")
    ]
    description: Annotated[
        str, Field(description="Human-readable description of what happened")
    ] = ""


class Summary(EventBase):  # type: ignore[misc]
    """Collapsed events - with optional summary text.

    Represents a range of events that have been archived and optionally summarized.
    Supports tree traversal for progressive disclosure via EventManager methods.

    - summary_text="..." → LLM-generated summary of collapsed events
    - summary_text=None  → events collapsed without summary (truncation)

    The children_tags field is hidden from rendering (repr=False) to save tokens.
    Agents can access collapsed events via events[summary.children_tags].

    String tags:
    - Individual events: "1", "2", "3", etc.
    - Summary events: "2..40" (range that was collapsed)
    - events["5"] always works, even after event is archived
    """

    _role: ClassVar[Role] = Role.ASSISTANT  # LLM's own recap of past actions

    # The tag for this summary event (e.g., "2..40") - REQUIRED
    summary_tag: Annotated[
        str,
        Field(description="String tag for this summary (e.g., '2..40')"),
    ]
    # Original range for view ordering (still int-based internally)
    replaced_range: Annotated[
        tuple[int, int],
        Field(description="(start_seq, end_seq) for view ordering"),
    ]
    children_tags: list[str] = Field(
        default_factory=list, repr=False, description="Tags of directly replaced events"
    )
    summary_text: str | None = Field(
        default=None, description="LLM-generated summary, or None for truncation"
    )
    doc: str = Field(default="", description="Usage hint for accessing collapsed events")


# === Union of all core event types ===

Event = (
    Task
    | Message
    | Reasoning
    | Error
    | Feedback
    | LLMOutput
    | PythonOutput
    | Notification
    | Summary
    | BeforeTurn
    | AfterTurn
)


# === ExecutionResult ===


class ExecutionResult(BaseModel):
    """Result of code execution via RuntimeServices.execute_code()."""

    stdout: str = Field(default="", description="Captured stdout from execution")
    stderr: str = Field(default="", description="Captured stderr from execution")
    error: Exception | None = Field(default=None, description="Exception if execution failed")
    signal: ExecutionSignal | None = Field(
        default=None, description="Control flow signal (not an error), e.g. return_result()"
    )
    defined_methods: dict[str, Callable[..., Any]] = Field(
        default_factory=dict, description="Methods defined during execution", exclude=True
    )
    returned_value: Any = Field(default=_NO_RETURN, description="Value returned by code")
    explicit_return: bool = Field(
        default=False, description="True only for explicit `return x` statements"
    )
    captured_locals: dict[str, Any] = Field(
        default_factory=dict,
        description="Local variables captured for REPL-style persistence",
        exclude=True,
    )
    images: list[dict[str, Any]] = Field(
        default_factory=list,
        repr=False,
        description="Image content blocks captured via show() during execution",
    )
    wrapper_line_offset: int = Field(
        default=0,
        description="Number of wrapper lines before user code (for traceback adjustment)",
    )

    model_config = {"arbitrary_types_allowed": True}

    @property
    def success(self) -> bool:
        """True if execution completed without error."""
        return self.error is None

    @property
    def has_return(self) -> bool:
        """True if code produced a return value (explicit or implicit)."""
        return self.returned_value is not _NO_RETURN

    def has_method(self, name: str) -> bool:
        """Check if a specific method was defined."""
        return name in self.defined_methods

    def format_output(self, *, fenced: bool = False) -> str:
        """Format stdout/stderr for LLM feedback.

        Args:
            fenced: If True, wrap output in markdown code fences (```).
                   If False, use plain text format.

        Returns:
            Formatted string with stdout/stderr sections, or empty string if no output.
        """
        parts = []
        if self.stdout:
            if fenced:
                parts.append(f"Stdout:\n```\n{self.stdout}\n```")
            else:
                parts.append(f"Stdout:\n{self.stdout}")
        if self.stderr:
            if fenced:
                parts.append(f"Stderr:\n```\n{self.stderr}\n```")
            else:
                parts.append(f"Stderr:\n{self.stderr}")
        return "\n\n".join(parts)
