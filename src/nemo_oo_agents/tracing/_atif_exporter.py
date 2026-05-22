# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ATIF v1.7 trajectory exporter (OpenTelemetry SpanExporter).

Accumulates openinference-instrumented spans and writes an ATIF v1.7
``trajectory.json`` (the shape codex / claude_code emit) on every flush.
Designed to replace ``nemo_flow.AtifExporter`` for agents already producing
openinference OTLP spans via this package's tracing pipeline.

Usage::

    from nemo_oo_agents.tracing import enable_tracing, exporters

    enable_tracing(exporters=[
        exporters.jsonl("/logs/artifacts/traces"),
        exporters.atif(
            "/logs/agent/trajectory.json",
            session_id=trial_id,
            agent_name="my-agent",
            agent_version="0.1.0",
        ),
    ])

The exporter writes the trajectory incrementally on every ``export()`` call
so a force-killed trial still leaves a usable partial trajectory on disk.
``shutdown()`` does a final write and drops the in-memory buffer.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "ATIF-v1.7"


# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------


def _i(x: Any) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _iso(ns: int | str) -> str:
    """OTLP timestamp (nanoseconds-since-epoch) -> ISO-8601 UTC."""
    try:
        ns_i = int(ns)
    except (TypeError, ValueError):
        return ""
    dt = datetime.fromtimestamp(ns_i / 1e9, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _extract_messages(attrs: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    """Pull ``{prefix}.N.message.*`` keys into an ordered list of message dicts.

    Returns messages in index order; each dict has whichever of role / content /
    tool_call_id / tool_calls were set on the span.
    """
    raw: dict[int, dict[str, Any]] = {}
    for k, v in attrs.items():
        m = re.match(rf"{re.escape(prefix)}\.(\d+)\.message\.(content|role|tool_call_id)$", k)
        if m:
            raw.setdefault(int(m.group(1)), {})[m.group(2)] = v
            continue
        m = re.match(
            rf"{re.escape(prefix)}\.(\d+)\.message\.tool_calls\.(\d+)\.tool_call\."
            r"(id|function\.name|function\.arguments)$",
            k,
        )
        if m:
            msg_idx = int(m.group(1))
            tc_idx = int(m.group(2))
            field = m.group(3).replace("function.", "")
            raw.setdefault(msg_idx, {}).setdefault("tool_calls", {}).setdefault(tc_idx, {})[
                field
            ] = v

    out: list[dict[str, Any]] = []
    for idx in sorted(raw):
        msg = dict(raw[idx])
        tcs_raw = msg.pop("tool_calls", None)
        if tcs_raw:
            msg["tool_calls"] = [
                {
                    "tool_call_id": tcs_raw[i].get("id", ""),
                    "function_name": tcs_raw[i].get("name", ""),
                    "arguments": _parse_args(tcs_raw[i].get("arguments", "")),
                }
                for i in sorted(tcs_raw)
            ]
        out.append(msg)
    return out


def _parse_args(s: Any) -> Any:
    """Tool-call arguments come over the wire as a JSON-string; parse if possible."""
    if isinstance(s, dict):
        return s
    if not isinstance(s, str):
        return s
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


_CONTEXT_BLOCK_RE = re.compile(r"\n*<context>.*?</context>\s*$", re.DOTALL)


def _strip_context_block(content: str) -> str:
    """Strip the trailing ``<context>...</context>`` envelope.

    ``CachedRenderer`` appends a dynamic ``<context>`` block to whichever
    user message is currently last in the conversation (see issue #208).
    The same logical message therefore shows up with and without that block
    in different LLM call snapshots; we want the bare content (the block is
    dynamic state, not user-facing trajectory content).
    """
    if not content:
        return content
    return _CONTEXT_BLOCK_RE.sub("", content)


def _clean_content(content: Any) -> str:
    """Normalize message content into a string (it may already be one)."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


# ---------------------------------------------------------------------------
# Span records
# ---------------------------------------------------------------------------


class SpanRecord:
    """Normalized span record fed into :func:`build_trajectory_from_records`."""

    __slots__ = ("start_ns", "end_ns", "attrs", "name")

    def __init__(self, start_ns: int, end_ns: int, attrs: dict[str, Any], name: str = "") -> None:
        self.start_ns = start_ns
        self.end_ns = end_ns
        self.attrs = attrs
        self.name = name


class LlmCall:
    """A single openinference LLM span, decoded into a structured form."""

    def __init__(self, rec: SpanRecord) -> None:
        self.start_ns = rec.start_ns
        self.end_ns = rec.end_ns
        self.attrs = rec.attrs
        self.system_content = ""
        self.input_msgs = _extract_messages(rec.attrs, "llm.input_messages")
        if self.input_msgs and self.input_msgs[0].get("role") == "system":
            self.system_content = self.input_msgs[0].get("content", "") or ""
        self.output_msgs = _extract_messages(rec.attrs, "llm.output_messages")
        self.model_name = rec.attrs.get("llm.model_name", "")
        self.prompt_tokens = _i(rec.attrs.get("llm.token_count.prompt"))
        self.cached_tokens = _i(rec.attrs.get("llm.token_count.prompt_details.cache_read"))
        self.completion_tokens = _i(rec.attrs.get("llm.token_count.completion"))
        self.reasoning_tokens = _i(rec.attrs.get("llm.token_count.completion_details.reasoning"))
        self.cost = _f(rec.attrs.get("llm.cost.total"))
        self.reasoning_content = rec.attrs.get("llm.reasoning_content", "") or ""

    @property
    def is_summarizer(self) -> bool:
        # TokenBudgetSummarizer sub-agent LLM spans are internal housekeeping;
        # they are not part of the user-facing trajectory.
        return "TokenBudgetSummarizer" in self.system_content


class ToolCall:
    """A single openinference TOOL span — used to enrich observations."""

    def __init__(self, rec: SpanRecord) -> None:
        self.start_ns = rec.start_ns
        self.end_ns = rec.end_ns
        self.name = rec.attrs.get("tool.name", "") or rec.name
        self.tool_call_id = rec.attrs.get("tool_call_id", "")
        self.code = rec.attrs.get("code", "")
        self.code_length = _i(rec.attrs.get("code.length"))
        self.result = rec.attrs.get("result", "")
        self.result_type = rec.attrs.get("result.type", "")
        self.execution_id = rec.attrs.get("execution.id", "")

    @property
    def duration_s(self) -> float:
        return (self.end_ns - self.start_ns) / 1e9 if self.end_ns and self.start_ns else 0.0


def _tool_span_observation_content(tc: ToolCall) -> str:
    """Format a TOOL span's ``result`` attribute as observation content.

    ``after_code_execution`` writes ``result`` as a JSON-encoded
    ``{stdout, stderr, returned_value}`` blob (see ``_hooks_impl.py`` —
    ``_safe_serialize_execution_result``). Parse it and emit a compact,
    human-readable rendering. On parse failure, return the raw string —
    older runs may have the Python-repr serialization.
    """
    raw = tc.result if isinstance(tc.result, str) else _clean_content(tc.result)
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw
    if not isinstance(data, dict):
        return raw
    parts: list[str] = []
    stdout = data.get("stdout")
    stderr = data.get("stderr")
    returned_value = data.get("returned_value")
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    if returned_value is not None and returned_value != "None":
        parts.append(f"returned_value:\n{returned_value}")
    # When parts is empty, the execution produced no observable output —
    # return "" so ``_attach_observation`` skips attaching a result.
    # Falling back to ``raw`` here would emit ``{"stdout": "", ...}`` as
    # observation content, which is JSON metadata, not a tool result.
    return "\n".join(parts)


def _partition_records(records: list[SpanRecord]) -> tuple[list[LlmCall], list[ToolCall]]:
    llm_calls: list[LlmCall] = []
    tool_calls: list[ToolCall] = []
    for rec in records:
        kind = rec.attrs.get("openinference.span.kind", "")
        if kind == "LLM":
            llm_calls.append(LlmCall(rec))
        elif kind == "TOOL":
            tool_calls.append(ToolCall(rec))
    llm_calls.sort(key=lambda c: c.start_ns)
    tool_calls.sort(key=lambda c: c.start_ns)
    return llm_calls, tool_calls


def records_from_readable_spans(spans: Sequence[ReadableSpan]) -> list[SpanRecord]:
    """Convert OpenTelemetry ``ReadableSpan`` instances into :class:`SpanRecord`."""
    out: list[SpanRecord] = []
    for s in spans:
        attrs = dict(s.attributes or {})
        out.append(
            SpanRecord(
                start_ns=int(s.start_time or 0),
                end_ns=int(s.end_time or 0),
                attrs=attrs,
                name=s.name or "",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Trajectory builder
# ---------------------------------------------------------------------------


def build_trajectory_from_records(
    records: list[SpanRecord],
    *,
    session_id: str,
    agent_name: str,
    agent_version: str,
    agent_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ATIF v1.7 trajectory dict from already-loaded :class:`SpanRecord`."""
    llm_calls, tool_calls = _partition_records(records)
    main_calls = [c for c in llm_calls if not c.is_summarizer]

    agent: dict[str, Any] = {"name": agent_name, "version": agent_version}
    if agent_extra:
        agent["extra"] = agent_extra

    if not main_calls:
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "trajectory_id": session_id,
            "agent": agent,
            "steps": [],
            "final_metrics": {"total_steps": 0},
        }

    # Build the canonical message log by unioning the input_messages of every
    # main-agent LLM span (in chronological order), then appending each call's
    # output_messages.  We rely on the openinference snapshot convention: each
    # call's input_messages is the full conversation history up to that call,
    # so appending only ``input_msgs[len(log):]`` avoids both content-based
    # dedup pitfalls (drops legitimately repeated turns) and id()-based
    # timestamp lookups.
    log: list[dict[str, Any]] = []
    log_ts: list[int] = []
    # Parallel list: index of the originating ``LlmCall`` in ``main_calls``,
    # or ``None`` for messages that came from a prior call's ``input_msgs``
    # (pre-existing history, not produced by the current call).  This is
    # how we look up per-step ``metrics``, ``llm_call_count``, and
    # ``reasoning_content`` for each emitted agent step.
    log_call_idx: list[int | None] = []

    for call_idx, call in enumerate(main_calls):
        for m in call.input_msgs[len(log) :]:
            log.append(m)
            log_ts.append(call.start_ns)
            log_call_idx.append(None)
        for m in call.output_msgs:
            log.append(m)
            log_ts.append(call.end_ns)
            log_call_idx.append(call_idx)

    model_name = next((c.model_name for c in main_calls if c.model_name), "")
    if model_name:
        agent["model_name"] = model_name

    tools_by_id: dict[str, ToolCall] = {tc.tool_call_id: tc for tc in tool_calls if tc.tool_call_id}

    # Pre-pass: index the best observation content by tool_call_id.
    # nemo-oo-agents emits two follow-ups per tool call — a role=tool placeholder
    # and a verbose role=user message wrapping the actual tool output as
    # ``<sys tag="N">PythonOutput(...)``.  The verbose body carries the producing
    # tool_call_id so we can pair them up.
    obs_by_tc_id: dict[str, str] = {}
    for m in log:
        if m.get("role") == "tool":
            tc = m.get("tool_call_id") or ""
            c = _strip_context_block(m.get("content", "") or "")
            if tc and c and c not in obs_by_tc_id.get(tc, ""):
                obs_by_tc_id[tc] = c
    tcid_in_body_re = re.compile(r"tool_call_id=['\"]([^'\"]+)['\"]")
    for m in log:
        if m.get("role") != "user":
            continue
        if m.get("tool_call_id"):
            tc = m["tool_call_id"]
            c = _strip_context_block(m.get("content", "") or "")
            if c:
                obs_by_tc_id[tc] = c
            continue
        c = _strip_context_block(m.get("content", "") or "")
        match = tcid_in_body_re.search(c)
        if match:
            obs_by_tc_id[match.group(1)] = c

    # Convert message log -> ATIF steps.
    steps: list[dict[str, Any]] = []
    step_id = 0
    system_emitted = False

    def _attach_observation(step: dict[str, Any], tc_id: str) -> None:
        # Prefer the TOOL span's result attribute — it's the authoritative
        # observation content, written locally by the code_execution hook
        # regardless of how the LLM provider serializes follow-up messages
        # (chat ``role: "tool"`` vs Responses ``function_call_output``).
        # Fall back to the message-log scan for tools that don't have a
        # corresponding TOOL span (non-execute_python flows).
        tspan = tools_by_id.get(tc_id)
        content: str = ""
        if tspan:
            content = _tool_span_observation_content(tspan)
        if not content:
            content = obs_by_tc_id.get(tc_id, "") or ""
        if not content:
            return
        obs = step.setdefault("observation", {"results": []})
        for existing in obs["results"]:
            if existing.get("source_call_id") == tc_id:
                return
        obs["results"].append(
            {
                "source_call_id": tc_id,
                "content": _clean_content(content),
            }
        )
        if tspan:
            extra = step.setdefault("extra", {})
            # Key tool_metadata by tool_call_id so multi-tool agent steps
            # don't collide on a single shared dict (with the first tool's
            # metadata winning and later tools losing theirs).
            tool_meta = extra.setdefault("tool_metadata", {})
            tc_meta = tool_meta.setdefault(tc_id, {})
            tc_meta["duration_seconds"] = round(tspan.duration_s, 3)
            if tspan.result_type:
                tc_meta["result_type"] = tspan.result_type

    for idx, m in enumerate(log):
        ts_ns = log_ts[idx]
        role = m.get("role", "")
        content = _strip_context_block(m.get("content") or "")
        tool_call_id = m.get("tool_call_id", "")

        if role == "system":
            if system_emitted:
                continue
            step_id += 1
            steps.append(
                {
                    "step_id": step_id,
                    "timestamp": _iso(ts_ns),
                    "source": "system",
                    "message": _clean_content(content),
                }
            )
            system_emitted = True
            continue

        if role == "tool":
            # Protocol bureaucracy; observations are attached on the agent
            # step from obs_by_tc_id during assistant handling.
            continue

        if role == "user" and not tool_call_id:
            # F3: drop envelope-only user messages whose content is empty
            # after stripping the trailing ``<context>...</context>``.  These
            # come from CachedBlockFormatter's per-call trailing context
            # message; they carry no user-facing content per ATIF.
            if not content.strip():
                continue
            # If the body references a tool_call_id, it's the verbose follow-up
            # -- already indexed in obs_by_tc_id; skip.
            if tcid_in_body_re.search(content):
                continue
            step_id += 1
            steps.append(
                {
                    "step_id": step_id,
                    "timestamp": _iso(ts_ns),
                    "source": "user",
                    "message": _clean_content(content),
                }
            )
            continue

        if role == "user" and tool_call_id:
            continue

        if role == "assistant":
            step_id += 1
            tcs = m.get("tool_calls") or []
            step: dict[str, Any] = {
                "step_id": step_id,
                "timestamp": _iso(ts_ns),
                "source": "agent",
            }
            if model_name:
                step["model_name"] = model_name
            if tcs:
                # F2: ``message`` is the assistant's actual text — empty
                # string when the inference was purely a tool call. Do NOT
                # fall back to a synthetic ``"Executed {fn} {id}"`` log line;
                # that is not assistant output (per ATIF §StepObject) and
                # poisons SFT extraction.
                step["message"] = _clean_content(content)
                step["tool_calls"] = tcs
                for tc in tcs:
                    if tc.get("tool_call_id"):
                        _attach_observation(step, tc["tool_call_id"])
            else:
                step["message"] = _clean_content(content)
            # F4/F5/F7: per-step metrics and llm_call_count from the
            # originating LlmCall. Only emitted on agent steps that
            # correspond to a real LLM inference (i.e. produced by an
            # output_msgs entry — log_call_idx is not None).
            call_idx = log_call_idx[idx]
            if call_idx is not None:
                call = main_calls[call_idx]
                metrics: dict[str, Any] = {
                    "prompt_tokens": call.prompt_tokens,
                    "completion_tokens": call.completion_tokens,
                    "cached_tokens": call.cached_tokens,
                    "cost_usd": round(call.cost, 6),
                }
                if call.reasoning_tokens:
                    metrics["extra"] = {"reasoning_tokens": call.reasoning_tokens}
                step["metrics"] = metrics
                step["llm_call_count"] = 1
                if call.reasoning_content:
                    step["reasoning_content"] = call.reasoning_content
            steps.append(step)
            continue

        step_id += 1
        steps.append(
            {
                "step_id": step_id,
                "timestamp": _iso(ts_ns),
                "source": role or "system",
                "message": _clean_content(content),
            }
        )

    last = main_calls[-1]
    total_prompt = sum(c.prompt_tokens for c in main_calls)
    total_cached = sum(c.cached_tokens for c in main_calls)
    total_completion = sum(c.completion_tokens for c in main_calls)
    total_reasoning = sum(c.reasoning_tokens for c in main_calls)
    total_cost = sum(c.cost for c in main_calls)
    final_metrics = {
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_cached_tokens": total_cached,
        "total_cost_usd": round(total_cost, 6),
        "total_steps": len(steps),
        "extra": {
            "reasoning_output_tokens": total_reasoning,
            "total_tokens": total_prompt + total_completion,
            "last_token_usage": {
                "input_tokens": last.prompt_tokens,
                "cached_input_tokens": last.cached_tokens,
                "output_tokens": last.completion_tokens,
                "reasoning_output_tokens": last.reasoning_tokens,
                "total_tokens": last.prompt_tokens + last.completion_tokens,
            },
        },
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "trajectory_id": session_id,
        "agent": agent,
        "steps": steps,
        "final_metrics": final_metrics,
    }


# ---------------------------------------------------------------------------
# SpanExporter
# ---------------------------------------------------------------------------


class AtifTrajectoryExporter(SpanExporter):
    """SpanExporter that accumulates openinference spans and writes an ATIF
    v1.7 trajectory JSON file on flush.

    Args:
        path: Full output path including filename (e.g. ``/logs/run42.json``).
            Any filename is accepted; ATIF v1.7 does not mandate
            ``trajectory.json``.  Parent directories are created on demand.
        session_id: Stable trajectory id.  For multi-trial benchmark runs this
            should be a per-trial id (dashboards group submissions by
            session_id; reusing one across trials merges them).
        agent_name: Goes into the top-level ``agent.name`` field.
        agent_version: Goes into ``agent.version``.
        model_name: Optional; if set, top-level ``agent.model_name`` carries
            it.  The builder also derives this from LLM spans if you don't.
        agent_extra: Optional extra metadata for ``agent.extra``.
        write_on_each_export: Default True.  Set False to defer writing until
            ``shutdown()``; saves a few file syscalls on short trials at the
            cost of losing the partial-trajectory safety net.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        session_id: str,
        agent_name: str,
        agent_version: str,
        model_name: str | None = None,
        agent_extra: dict[str, Any] | None = None,
        write_on_each_export: bool = True,
    ) -> None:
        self.path = Path(path)
        self.session_id = session_id
        self.agent_name = agent_name
        self.agent_version = agent_version
        self.model_name = model_name
        self.agent_extra = dict(agent_extra) if agent_extra else None
        self._write_on_each_export = write_on_each_export
        self._span_records: list[SpanRecord] = []
        self._shut_down = False
        # SimpleSpanProcessor invokes export() from span-ending threads, which
        # may race against force_flush()/shutdown() from the agent thread.
        self._lock = threading.RLock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            if self._shut_down:
                return SpanExportResult.FAILURE
            try:
                self._span_records.extend(records_from_readable_spans(spans))
                if self._write_on_each_export:
                    self._write_atomic()
            except Exception:  # noqa: BLE001
                # Tracing must never break the run.
                logger.exception("AtifTrajectoryExporter.export failed")
                return SpanExportResult.FAILURE
            return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        with self._lock:
            if self._shut_down:
                # Don't overwrite the trajectory with one built from the
                # cleared buffer.
                return False
            try:
                self._write_atomic()
                return True
            except Exception:  # noqa: BLE001
                logger.exception("AtifTrajectoryExporter.force_flush failed")
                return False

    def shutdown(self) -> None:
        with self._lock:
            if self._shut_down:
                return
            try:
                self._write_atomic()
            except Exception:  # noqa: BLE001
                logger.exception("AtifTrajectoryExporter.shutdown final write failed")
            finally:
                self._span_records = []
                self._shut_down = True

    def _write_atomic(self) -> None:
        traj = build_trajectory_from_records(
            self._span_records,
            session_id=self.session_id,
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            agent_extra=self.agent_extra,
        )
        if self.model_name and not traj.get("agent", {}).get("model_name"):
            traj.setdefault("agent", {})["model_name"] = self.model_name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(traj, indent=2))
        tmp.replace(self.path)
