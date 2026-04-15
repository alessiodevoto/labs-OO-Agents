# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenTelemetry hooks implementation following OpenInference conventions."""

import contextlib
import difflib
import inspect
import json
import os
import time
from contextvars import ContextVar
from typing import Any

from agentdoc import safe_pformat
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

# Context variable for per-async-context active span tracking
# This prevents context leakage during concurrent execution (e.g., parallel eval samples)
_context_active_spans: ContextVar[dict[str, Span] | None] = ContextVar("context_active_spans", default=None)


def _get_active_spans() -> dict[str, Span]:
    """Get the active spans dict for the current async context."""
    spans = _context_active_spans.get()
    if spans is None:
        spans = {}
        _context_active_spans.set(spans)
    return spans


_ERROR_MESSAGE_LIMIT = 5_000


def _error_message(exception: BaseException) -> str:
    """Return str(exception) capped at _ERROR_MESSAGE_LIMIT chars.

    Exception messages are unbounded — a custom exception can embed repr()
    of a large object.  Span attributes with MB-sized values blow up OTLP
    payloads and trace storage.
    """
    msg = str(exception)
    if len(msg) <= _ERROR_MESSAGE_LIMIT:
        return msg
    half = _ERROR_MESSAGE_LIMIT // 2
    dropped = len(msg) - _ERROR_MESSAGE_LIMIT
    return f"{msg[:half]}\n... {dropped:,} chars truncated ...\n{msg[-half:]}"


# OpenInference span kinds
class SpanKind:
    """OpenInference span kinds."""

    AGENT = "AGENT"
    LLM = "LLM"
    CHAIN = "CHAIN"
    TOOL = "TOOL"
    RETRIEVER = "RETRIEVER"
    EMBEDDING = "EMBEDDING"
    RERANKER = "RERANKER"
    GUARDRAIL = "GUARDRAIL"
    EVALUATOR = "EVALUATOR"


# Viewer plugin hint attribute — tells the trace viewer which rendering plugin to use.
# The attribute name is an arbitrary convention; see docs/trace-plugin-convention.md.
VIEWER_PLUGIN_ATTR = "nemo_oo_agents.viewer.plugin"


class ViewerPlugin:
    """Valid values for the nemo_oo_agents.viewer.plugin span attribute.

    Each value corresponds to a rendering plugin in the trace viewer.
    When set on a span, the viewer uses this value (instead of the span name)
    to select the plugin that renders the span.
    """

    METHOD = "method"
    GENERATION = "generation"
    CODE_EXECUTION = "code_execution"
    TOOL_EXECUTION = "tool_execution"


class OpenInferenceHooks:
    """OpenTelemetry-based instrumentation hooks with OpenInference conventions.

    Implements the nemo_oo_agents InstrumentationHooks protocol.
    """

    def __init__(self, tracer: trace.Tracer, service_name: str = "nemo_oo_agents"):
        """Initialize OTel hooks.

        Args:
            tracer: OTel tracer to use for creating spans
            service_name: Service name for span metadata
        """
        self.tracer = tracer
        self.service_name = service_name

        # State for context_snapshot diffing (keyed by agent call_id)
        self._prev_system_messages: dict[str, str] = {}
        self._turn_counters: dict[str, int] = {}

        # Note: Active spans are tracked per async context via _get_active_spans()
        # to prevent context leakage during concurrent execution

    def before_agent_call(
        self,
        agent: Any,
        method_name: str,
        args: tuple,
        kwargs: dict,
        call_id: str,
        parent_call_id: str | None,
        **extra_kwargs: Any,
    ) -> Any:
        """Create AGENT span for ellipsis method execution."""
        agent_name = type(agent).__name__

        # DEFENSIVE: Initialize spans dict unconditionally to ensure early context establishment
        # This pattern prevents potential timing issues with ContextVar inheritance in async contexts
        spans_dict = _get_active_spans()
        parent_span = spans_dict.get(parent_call_id) if parent_call_id else None
        context = trace.set_span_in_context(parent_span) if parent_span else None

        # Create span
        span = self.tracer.start_span(
            name=f"method.{method_name}",
            context=context,
            start_time=time.time_ns(),
        )

        # Set OpenInference attributes
        span.set_attribute("openinference.span.kind", SpanKind.AGENT)
        span.set_attribute(VIEWER_PLUGIN_ATTR, ViewerPlugin.METHOD)
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("agent.method", method_name)
        span.set_attribute("agent.call_id", call_id)
        if parent_call_id:
            span.set_attribute("agent.parent_call_id", parent_call_id)

        # Extract method signature, docstring, and file path
        try:
            method = getattr(agent, method_name, None)
            if method:
                # Get signature
                sig = inspect.signature(method)
                span.set_attribute("agent.method_signature", f"{method_name}{sig}")

                # Get first line of docstring
                doc = inspect.getdoc(method)
                if doc:
                    first_line = doc.split("\n")[0].strip()
                    span.set_attribute("agent.docstring", first_line)

                # Get agent file path (relative to cwd)
                try:
                    agent_file = inspect.getfile(agent.__class__)
                    cwd = os.getcwd()
                    rel_path = os.path.relpath(agent_file, cwd)
                    span.set_attribute("agent.file_path", rel_path)
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass

        # Serialize args safely
        try:
            span.set_attribute("agent.args", self._safe_serialize(args))
            span.set_attribute("agent.kwargs", self._safe_serialize(kwargs))
        except Exception:
            pass

        # Automatically add any extra kwargs as span attributes
        for key, value in extra_kwargs.items():
            if value is not None:
                with contextlib.suppress(Exception):
                    span.set_attribute(
                        f"agent.{key}",
                        str(value) if not isinstance(value, (str, int, float, bool)) else value,
                    )

        # Track span
        _get_active_spans()[call_id] = span

        return {
            "span": span,
            "call_id": call_id,
            **extra_kwargs,
            "start_time": time.time(),
        }

    def after_agent_call(
        self,
        agent: Any,
        method_name: str,
        result: Any,
        exception: Exception | None,
        context: Any,
        **kwargs: Any,
    ) -> None:
        """Complete AGENT span."""
        if not context:
            return

        span: Span = context.get("span")
        call_id = context.get("call_id")

        if not span:
            return

        # Set result/error
        if exception:
            span.set_status(Status(StatusCode.ERROR, str(exception)))
            span.set_attribute("error.type", type(exception).__name__)
            span.set_attribute("error.message", _error_message(exception))
        else:
            span.set_status(Status(StatusCode.OK))
            with contextlib.suppress(Exception):
                span.set_attribute("agent.result", self._safe_serialize(result))

        # End span
        span.end(end_time=time.time_ns())

        # Clean up context_snapshot state for this agent call
        self._prev_system_messages.pop(call_id, None)
        self._turn_counters.pop(call_id, None)

        # Remove from tracking
        if call_id and call_id in _get_active_spans():
            del _get_active_spans()[call_id]

    def before_generation(
        self,
        agent: Any,
        method_name: str,
        strategy: str,
        generation_id: str,
        parent_generation_id: str | None,
        agent_call_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create LLM/CHAIN span for generation session."""
        from opentelemetry import context

        agent_name = type(agent).__name__

        # Get parent context: prefer the AGENT span for this specific call so that
        # sub-agent generation spans are correctly parented to the sub-agent's AGENT
        # span rather than to the caller's generation span.
        parent_span = _get_active_spans().get(agent_call_id) if agent_call_id else None
        if not parent_span:
            parent_span = _get_active_spans().get(parent_generation_id) if parent_generation_id else None

        parent_context = trace.set_span_in_context(parent_span) if parent_span else None

        # Create span
        span = self.tracer.start_span(
            name="generation",
            context=parent_context,
            start_time=time.time_ns(),
        )

        # Set OpenInference attributes
        span.set_attribute("openinference.span.kind", SpanKind.LLM)
        span.set_attribute(VIEWER_PLUGIN_ATTR, ViewerPlugin.GENERATION)
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("agent.method", method_name)
        span.set_attribute("generation.strategy", strategy)
        span.set_attribute("generation.id", generation_id)
        if parent_generation_id:
            span.set_attribute("generation.parent_id", parent_generation_id)
        if agent_call_id:
            span.set_attribute("agent.call_id", agent_call_id)

        # Automatically add any extra kwargs as span attributes
        for key, value in kwargs.items():
            if value is not None:
                with contextlib.suppress(Exception):
                    span.set_attribute(
                        f"generation.{key}",
                        str(value) if not isinstance(value, (str, int, float, bool)) else value,
                    )

        # Track span
        _get_active_spans()[generation_id] = span

        # CRITICAL: Attach span to context so litellm instrumentor sees it
        token = context.attach(trace.set_span_in_context(span))

        return {
            "span": span,
            "generation_id": generation_id,
            **kwargs,
            "start_time": time.time(),
            "context_token": token,  # Store token to detach later
        }

    def after_generation(
        self,
        agent: Any,
        method_name: str,
        result: Any,
        exception: Exception | None,
        context: Any,
        generation_id: str,
        **kwargs: Any,
    ) -> None:
        """Complete generation span."""
        from opentelemetry import context as otel_context

        if not context:
            return

        span: Span = context.get("span")
        context_token = context.get("context_token")

        if not span:
            return

        # Set result/error
        if exception:
            span.set_status(Status(StatusCode.ERROR, str(exception)))
            span.set_attribute("error.type", type(exception).__name__)
            span.set_attribute("error.message", _error_message(exception))
        else:
            span.set_status(Status(StatusCode.OK))
            # Capture the generation result
            try:
                span.set_attribute("generation.result", self._safe_serialize(result, max_length=5000))
                span.set_attribute("result.type", type(result).__name__ if result else "None")
            except Exception:
                pass

        # End span
        span.end(end_time=time.time_ns())

        # Detach context
        if context_token:
            otel_context.detach(context_token)

        # Remove from tracking
        if generation_id and generation_id in _get_active_spans():
            del _get_active_spans()[generation_id]

    def before_code_execution(
        self,
        agent: Any,
        code: str,
        execution_id: str,
        generation_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create TOOL span for code execution."""
        agent_name = type(agent).__name__

        # Find parent span - prefer generation span if generation_id provided
        parent_span = None
        if generation_id and generation_id in _get_active_spans():
            parent_span = _get_active_spans()[generation_id]
        else:
            # Fallback: most recent span
            for span in reversed(list(_get_active_spans().values())):
                parent_span = span
                break

        context = trace.set_span_in_context(parent_span) if parent_span else None

        # Create span
        span = self.tracer.start_span(
            name="code_execution",
            context=context,
            start_time=time.time_ns(),
        )

        # Set OpenInference attributes
        span.set_attribute("openinference.span.kind", SpanKind.TOOL)
        span.set_attribute(VIEWER_PLUGIN_ATTR, ViewerPlugin.CODE_EXECUTION)
        span.set_attribute("tool.name", "python_executor")
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("code", code[:10000])  # Limit code length
        span.set_attribute("code.length", len(code))
        span.set_attribute("execution.id", execution_id)

        # Add generation_id for correlation with LLM turns
        if generation_id:
            span.set_attribute("generation.id", generation_id)

        # Automatically add any extra kwargs as span attributes
        for key, value in kwargs.items():
            if value is not None:
                attr_name = f"tool.{key}" if not key.startswith("tool") else key
                with contextlib.suppress(Exception):
                    span.set_attribute(
                        attr_name,
                        str(value) if not isinstance(value, (str, int, float, bool)) else value,
                    )

        # Track span
        _get_active_spans()[execution_id] = span

        return {
            "span": span,
            "execution_id": execution_id,
            "generation_id": generation_id,
            **kwargs,  # Pass through all extra context
            "start_time": time.time(),
        }

    def after_code_execution(
        self,
        agent: Any,
        code: str,
        result: Any,
        exception: Exception | None,
        context: Any,
        execution_id: str,
        **kwargs: Any,
    ) -> None:
        """Complete code execution span."""
        if not context:
            return

        span: Span = context.get("span")

        if not span:
            return

        # Set result/error
        if exception:
            span.set_status(Status(StatusCode.ERROR, str(exception)))
            span.set_attribute("error.type", type(exception).__name__)
            span.set_attribute("error.message", _error_message(exception))
        else:
            span.set_status(Status(StatusCode.OK))
            try:
                span.set_attribute("result", self._safe_serialize_execution_result(result))
                span.set_attribute("result.type", type(result).__name__ if result else "None")
            except Exception:
                pass

        # End span
        span.end(end_time=time.time_ns())

        # Remove from tracking
        if execution_id and execution_id in _get_active_spans():
            del _get_active_spans()[execution_id]

    def before_method_invocation(
        self,
        agent: Any,
        method_name: str,
        args: tuple,
        kwargs: dict,
        invocation_id: str,
        **extra_kwargs: Any,
    ) -> Any:
        """Create TOOL span for generated method invocation."""
        agent_name = type(agent).__name__

        # Find parent span (most recent generation or agent span)
        parent_span = None
        for span in reversed(list(_get_active_spans().values())):
            parent_span = span
            break

        context = trace.set_span_in_context(parent_span) if parent_span else None

        # Create span
        span = self.tracer.start_span(
            name=f"method_call.{method_name}",
            context=context,
            start_time=time.time_ns(),
        )

        # Set OpenInference attributes
        span.set_attribute("openinference.span.kind", SpanKind.TOOL)
        span.set_attribute(VIEWER_PLUGIN_ATTR, ViewerPlugin.METHOD)
        span.set_attribute("tool.name", f"generated_method:{method_name}")
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("method.name", method_name)
        span.set_attribute("invocation.id", invocation_id)

        # Serialize args safely
        try:
            span.set_attribute("method.args", self._safe_serialize(args))
            span.set_attribute("method.kwargs", self._safe_serialize(kwargs))
        except Exception:
            pass

        # Automatically add any extra kwargs as span attributes
        for key, value in extra_kwargs.items():
            if value is not None:
                with contextlib.suppress(Exception):
                    span.set_attribute(
                        f"method.{key}",
                        str(value) if not isinstance(value, (str, int, float, bool)) else value,
                    )

        # Track span
        _get_active_spans()[invocation_id] = span

        return {
            "span": span,
            "invocation_id": invocation_id,
            **extra_kwargs,
            "start_time": time.time(),
        }

    def after_method_invocation(
        self,
        agent: Any,
        method_name: str,
        result: Any,
        exception: Exception | None,
        context: Any,
        invocation_id: str,
        **kwargs: Any,
    ) -> None:
        """Complete method invocation span."""
        if not context:
            return

        span: Span = context.get("span")

        if not span:
            return

        # Set result/error
        if exception:
            span.set_status(Status(StatusCode.ERROR, str(exception)))
            span.set_attribute("error.type", type(exception).__name__)
            span.set_attribute("error.message", _error_message(exception))
        else:
            span.set_status(Status(StatusCode.OK))
            try:
                span.set_attribute("method.result", self._safe_serialize(result))
                span.set_attribute("result.type", type(result).__name__ if result else "None")
            except Exception:
                pass

        # End span
        span.end(end_time=time.time_ns())

        # Remove from tracking
        if invocation_id and invocation_id in _get_active_spans():
            del _get_active_spans()[invocation_id]

    def before_tool_execution(
        self,
        agent: Any,
        tool_name: str,
        arguments: dict,
        execution_id: str,
        generation_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create TOOL span for tool execution (e.g., return_result)."""
        agent_name = type(agent).__name__

        # Find parent span - prefer generation span if generation_id provided
        parent_span = None
        if generation_id and generation_id in _get_active_spans():
            parent_span = _get_active_spans()[generation_id]
        else:
            # Fallback: most recent span
            for span in reversed(list(_get_active_spans().values())):
                parent_span = span
                break

        context = trace.set_span_in_context(parent_span) if parent_span else None

        # Create span with tool-specific name
        span = self.tracer.start_span(
            name=f"tool_execution.{tool_name}",
            context=context,
            start_time=time.time_ns(),
        )

        # Set OpenInference attributes
        span.set_attribute("openinference.span.kind", SpanKind.TOOL)
        span.set_attribute(VIEWER_PLUGIN_ATTR, ViewerPlugin.TOOL_EXECUTION)
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("execution.id", execution_id)

        # Serialize arguments
        with contextlib.suppress(Exception):
            span.set_attribute("tool.arguments", self._safe_serialize(arguments, max_length=5000))

        # Add generation_id for correlation with LLM turns
        if generation_id:
            span.set_attribute("generation.id", generation_id)

        # Automatically add any extra kwargs as span attributes
        for key, value in kwargs.items():
            if value is not None:
                attr_name = f"tool.{key}" if not key.startswith("tool") else key
                with contextlib.suppress(Exception):
                    span.set_attribute(
                        attr_name,
                        str(value) if not isinstance(value, (str, int, float, bool)) else value,
                    )

        # Track span
        _get_active_spans()[execution_id] = span

        return {
            "span": span,
            "execution_id": execution_id,
            "generation_id": generation_id,
            **kwargs,  # Pass through all extra context
            "start_time": time.time(),
        }

    def after_tool_execution(
        self,
        agent: Any,
        tool_name: str,
        arguments: dict,
        result: Any,
        exception: Exception | None,
        context: Any,
        execution_id: str,
        **kwargs: Any,
    ) -> None:
        """Complete tool execution span."""
        if not context:
            return

        span: Span = context.get("span")

        if not span:
            return

        # Set result/error
        if exception:
            span.set_status(Status(StatusCode.ERROR, str(exception)))
            span.set_attribute("error.type", type(exception).__name__)
            span.set_attribute("error.message", _error_message(exception))
        else:
            span.set_status(Status(StatusCode.OK))
            try:
                span.set_attribute("tool.result", self._safe_serialize(result, max_length=5000))
                span.set_attribute("result.type", type(result).__name__ if result else "None")
            except Exception:
                pass

        # End span
        span.end(end_time=time.time_ns())

        # Remove from tracking
        if execution_id and execution_id in _get_active_spans():
            del _get_active_spans()[execution_id]

    def on_messages_built(
        self,
        agent: Any,
        method_name: str,
        messages: list[dict[str, Any]],
        generation_id: str,
        **kwargs: Any,
    ) -> None:
        """Create a context_snapshot span with the system message (full or diff)."""
        # Extract system message
        if not messages or messages[0].get("role") != "system":
            return
        system_content = messages[0].get("content", "")
        if not system_content:
            return

        # Find current agent call_id from active spans
        call_id = self._find_current_call_id()
        if not call_id:
            return

        turn_index = self._turn_counters.get(call_id, 0)
        self._turn_counters[call_id] = turn_index + 1
        prev = self._prev_system_messages.get(call_id)

        if prev is None:
            # First generation in this agent call — full snapshot
            content = system_content
            is_diff = False
        elif prev == system_content:
            # No change — skip span entirely
            self._prev_system_messages[call_id] = system_content
            return
        else:
            # Changed — unified diff
            content = "\n".join(
                difflib.unified_diff(
                    prev.splitlines(),
                    system_content.splitlines(),
                    fromfile="previous",
                    tofile="current",
                    lineterm="",
                )
            )
            is_diff = True

        self._prev_system_messages[call_id] = system_content

        # Create child span under current generation span (or any active span)
        parent_span = _get_active_spans().get(generation_id)
        if not parent_span:
            for span in reversed(list(_get_active_spans().values())):
                parent_span = span
                break

        parent_context = trace.set_span_in_context(parent_span) if parent_span else None

        span = self.tracer.start_span(
            "context_snapshot",
            context=parent_context,
            start_time=time.time_ns(),
        )
        span.set_attribute("openinference.span.kind", SpanKind.CHAIN)
        span.set_attribute("nemo_oo_agents.system_message", content)
        span.set_attribute("nemo_oo_agents.system_message.is_diff", is_diff)
        span.set_attribute("nemo_oo_agents.system_message.turn_index", turn_index)
        span.end(end_time=time.time_ns())

    def _find_current_call_id(self) -> str | None:
        """Find the current agent call_id from active spans."""
        active = _get_active_spans()
        # Check which active span keys have tracking state
        for key in reversed(list(active.keys())):
            if key in self._prev_system_messages or key in self._turn_counters:
                return key
        # Fallback: first active span key (likely the agent call)
        if active:
            return next(iter(active))
        return None

    @staticmethod
    def _safe_serialize_execution_result(result: Any) -> str:
        """Serialize an ExecutionResult for trace spans, bounding returned_value.

        Uses safe_pformat on returned_value so a 100 MB return value
        doesn't blow up OTLP payloads.  Other fields (stdout, stderr)
        are already bounded by TruncatingStringIO.
        """
        if hasattr(result, "model_dump"):
            # Use has_return to check for real return value (exclude _NO_RETURN sentinel).
            has_return = getattr(result, "has_return", False)
            d = result.model_dump(exclude_none=True)
            if not has_return:
                d.pop("returned_value", None)
        elif isinstance(result, dict):
            d = result
        else:
            return str(result)[:50_000]

        # Bound returned_value before JSON serialization so the JSON is always valid.
        rv = d.get("returned_value")
        if rv is not None:
            d["returned_value"] = safe_pformat(rv, max_chars=50_000)

        return json.dumps(d, default=str)

    def _safe_serialize(self, obj: Any, max_length: int | None = None) -> str:
        """Safely serialize an object to string for trace span attributes.

        No truncation — traces must faithfully record what the agent produced.
        Agent-facing truncation is done upstream by safe_pformat / block-level
        limits; by the time a value reaches a trace span it is already the
        bounded representation the agent actually saw.

        The ``max_length`` parameter is accepted for API compatibility but
        ignored.  Pass ``max_length=None`` (the default) in all new call sites.
        """
        _ = max_length
        try:
            if obj is None:
                return "null"
            if isinstance(obj, str | int | float | bool):
                result = str(obj)
            elif isinstance(obj, list | tuple):
                # Recursively serialize Pydantic models in lists/tuples
                serialized_items = []
                for item in obj:
                    if hasattr(item, "model_dump"):
                        try:
                            item_dict = item.model_dump(exclude_none=True)
                            item_dict["__class__"] = item.__class__.__name__
                            serialized_items.append(item_dict)
                        except Exception:
                            serialized_items.append(str(item))
                    else:
                        serialized_items.append(item)
                result = json.dumps(serialized_items, default=str)
            elif isinstance(obj, dict):
                result = json.dumps(obj, default=str)
            else:
                # Check if it's a Pydantic model
                if hasattr(obj, "model_dump"):
                    try:
                        # Convert to dict, excluding non-serializable fields
                        obj_dict = obj.model_dump(exclude_none=True)
                        # Filter out callable values (like bound methods) and
                        # sentinel objects (e.g. _NO_RETURN = object()) that are
                        # not JSON-serializable and leak as "<object object at 0x...>"
                        filtered_dict = {
                            k: (
                                None
                                if type(v) is object  # sentinel object()
                                else f"<function {getattr(v, '__name__', 'unknown')}>"
                                if callable(v)
                                else v
                            )
                            for k, v in obj_dict.items()
                        }
                        # Also handle dict values that contain callables or sentinels
                        for k, v in filtered_dict.items():
                            if isinstance(v, dict):
                                filtered_dict[k] = {
                                    dk: (
                                        None
                                        if type(dv) is object
                                        else f"<function {getattr(dv, '__name__', 'unknown')}>"
                                        if callable(dv)
                                        else dv
                                    )
                                    for dk, dv in v.items()
                                }
                        # Add class name for better UI display
                        filtered_dict["__class__"] = obj.__class__.__name__
                        result = json.dumps(filtered_dict, default=str)
                    except Exception:
                        # Fallback to string if model_dump fails
                        result = str(obj)
                else:
                    result = str(obj)

            return result
        except Exception:
            return "<unserializable>"
