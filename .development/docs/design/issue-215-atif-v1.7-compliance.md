# Issue 215 — ATIF v1.7 compliance fixes in trajectory exporter

Issue: https://gitlab-master.nvidia.com/interactive-agents/nooa/-/issues/215
RFC:   `0001-trajectory-format-2.md` (ATIF v1.7, April 2026)

## Goal

The `_atif_exporter.py` writes `schema_version: "ATIF-v1.7"` but deviates from
the spec in three load-bearing ways. Downstream consumers (Vanessa Bench
dashboard) end up with nearly-empty timelines because tool execution results
are missing and agent steps carry framework log lines instead of model output.

Bring the exporter into compliance with the three critical RFC violations and
opportunistically populate optional v1.7 fields that the framework already
tracks (per-step `metrics`, `llm_call_count`, top-level `total_cost_usd`,
`reasoning_content`).

## Root cause analysis (per fix)

### 1. Observation pairing — root cause

The current builder constructs an `obs_by_tc_id` index by walking the
reconstructed message log, looking for:

1. `role: "tool"` messages with `tool_call_id` set;
2. `role: "user"` messages with `tool_call_id` set;
3. `role: "user"` messages where the body contains `tool_call_id='…'` (the
   verbose `<sys tag="N">PythonOutput(...)</sys>` follow-ups).

In the actual nemo-oo-agents codeact flow:

- The `role: "tool"` message content is the **literal string `"status: complete"`**
  (or `"status: error"`), set by `codeact.py:1330–1338` —
  `ToolResult.content = f"status: {final_status.value}"`. The actual stdout is
  emitted via a separate `PythonOutput` event.
- The real tool output rides in the next `PythonOutput` event, which the
  formatter renders as a `role: "user"` message wrapping
  `pformat(PythonOutput(...))`. `pformat` produces single-quoted Python repr
  syntax (`tool_call_id='call_xyz'`).
- When the agent uses the OpenAI **Responses** API (not Chat Completions), the
  wire format swaps `role: "tool"` for `type: "function_call_output"`. This is
  what unifiedllm transforms to in `unifiedllm.py:2120–2208`. Depending on the
  openinference instrumentation path, these messages may not appear as
  `role: "tool"` in span attributes at all — leaving the index empty.

The current code **never falls back to the TOOL span**, even though the
framework already captures the real tool result there (`code_execution` span;
`_hooks_impl.py:473` sets `span.set_attribute("result", …)`). `ToolCall.result`
is loaded into `tools_by_id` (`_atif_exporter.py:304`) but used only for
duration / type metadata — never as the observation content itself.

That is the actual fix: **use the TOOL span's `result` attribute as the
authoritative observation content**, and treat the message-log scan as an
optional enrichment for non-`execute_python` flows where TOOL spans may not
exist.

### 2. Agent message — root cause

`_atif_exporter.py:419` falls back to `f"Executed {fn_name} {first_id}"` when
the assistant's output content is empty/whitespace. This is wrong per RFC §
StepObject: *"For agent steps, this is the assistant's response."* It must be
the actual model text (or `""` when the inference was purely a tool call).

The synthetic log line is reconstructable from `tool_calls[0]` if a viewer
wants it; storing it in `message` blocks SFT extraction and is unreadable.

### 3. Phantom user steps — root cause

The `CachedBlockFormatter` (`src/nooa/context_blocks/renderers/cached.py:127–145`)
always appends a
trailing `RenderedMessage(role=USER, content="<context>…</context>")` to
isolate dynamic context blocks for prompt caching (issue #208). The exporter
applies `_strip_context_block`, which removes the entire trailing
`<context>…</context>` envelope, leaving an **empty string** as content.

`_atif_exporter.py:386–400` then emits that as a `source: "user"` step with
empty `message`. With one such trailing message per LLM call, a 65-call trial
produces ~33 phantom user steps (only ~half emit because the prefix-union
keeps only new messages — and that ratio is what the issue reports).

RFC § StepObject says `source: "user"` is *"for user messages."* These are not
user messages; they should not exist.

## Proposed fixes

### Critical (issue #215 — strict compliance)

**F1 — Observation pairing.** In `_attach_observation`:

1. Prefer the TOOL span's `result` attribute from `tools_by_id[tc_id]` if
   present and non-empty. The span's `result` attribute is currently a
   `truncating_pformat` (Python repr) of the `ExecutionResult(...)` object,
   set via the generic `_safe_serialize` at `_hooks_impl.py:473`. It is NOT
   JSON — the JSON-emitting `_safe_serialize_execution_result`
   (`_hooks_impl.py:773–800`) exists but is dead code (never called).
2. **Sub-fix F1a: wire the JSON serializer.** Switch
   `after_code_execution` (`_hooks_impl.py:466–479`) to call
   `_safe_serialize_execution_result(result)` instead of the generic
   `_safe_serialize(result)`. This produces a JSON-encoded
   `{stdout, stderr, returned_value}` blob, which the exporter can
   `json.loads()` and re-format as a human-readable observation. This is
   the existing code's clear intent (the function is named for it).
3. Fall back to the existing `obs_by_tc_id` lookup for tools that don't have
   a corresponding TOOL span (e.g. non-`execute_python` tools rendered via
   chat-format tool messages).
4. If both are absent, emit no `observation` field — leaving the spec-required
   contract (`observation.results[*].source_call_id == tool_call_id`) intact.
5. Always set `source_call_id = tc_id` on the observation result. Always
   coerce content to a string per `_clean_content`.

**Dependency note for F1**: `tools_by_id[tool_call_id]` works only because
the `tool_call_id` kwarg threaded through `actor.py:1014` → hook → the
generic kwargs-auto-attribute loop at `_hooks_impl.py:425–433` keeps the
attribute name `tool_call_id` (the loop prefixes with `tool.` only when
the kwarg name doesn't already start with `tool`). A future refactor of
`before_code_execution` that drops this implicit conversion silently
breaks observation pairing. Add a regression test pinning that
`code_execution` TOOL spans carry a `tool_call_id` attribute.

**F2 — Agent message content.** Replace:

```python
step["message"] = msg_summary or f"Executed {fn_name} {first_id}".strip()
```

with:

```python
step["message"] = msg_summary  # the assistant's actual text, "" if no text
```

The framework log line ("Executed execute_python call_…") is not preserved
anywhere — viewers that want it can reconstruct from `tool_calls[0]`. (Per
issue #215's "Fix #2 alternative", we could stash it in `step.extra` for
debug, but there is no consumer that needs it, so we drop it.)

**F3 — Drop phantom user steps.** In the `role == "user" and not tool_call_id`
branch (line 386), after `content = _strip_context_block(...)`, add
`if not content.strip(): continue` immediately above the `tcid_in_body_re`
short-circuit at line 389. The user-message branch emits only
`{step_id, timestamp, source, message}` so there is no other field whose
absence we need to defend against — empty content alone is sufficient to
drop the step.

### Optional (broader v1.7 compliance — same scope of code, low risk)

These are populated from data we already have in `LlmCall` records:

**F4 — Per-step `metrics` on agent (assistant) steps only.** Per RFC §
MetricsSchema: `prompt_tokens`, `completion_tokens`, `cached_tokens`,
`cost_usd`, `extra.reasoning_tokens`. We have one `LlmCall` per assistant
inference already.

Pairing approach: each emitted agent step corresponds to an entry of
`call.output_msgs` from a specific `LlmCall`. The current message-log union
loses that linkage (we only retain `log_ts`). Reintroduce per-step
linkage by also tracking the originating `LlmCall` index alongside `log_ts`
(`log_call_idx: list[int | None]`); `None` for messages that came from
`input_msgs` (those are pre-existing history rendered into system/user
steps, NOT produced by the current call). At emit time, look up
`main_calls[log_call_idx[idx]]` for the agent step's metrics.

**Scoping**: `metrics` is attached **only on `source: "agent"` steps**
(per RFC § MetricsSchema: "Only applicable when source is 'agent'"). Steps
with `log_call_idx[idx] is None` (system / initial user) get no `metrics`
field — which is the desired behaviour.

**F5 — `llm_call_count = 1` on assistant steps only.** Per RFC § StepObject
(v1.7): consumers use this to distinguish deterministic dispatch
(`llm_call_count: 0`) from real inference. Without it, SFT pipelines have
to guess. Set it ONLY on `source: "agent"` steps that have an associated
`LlmCall` (i.e. `log_call_idx[idx] is not None`). User and system steps do
not get this field.

**F6 — `total_cost_usd` at top level of `final_metrics`.** RFC § FinalMetrics
defines `total_cost_usd` as a top-level field. We currently emit it under
`extra.total_cost_usd`. Promote it (and keep `extra.last_token_usage` /
`extra.total_tokens` / `extra.reasoning_output_tokens` as-is — those are not
spec fields).

**F7 — `reasoning_content` on agent steps when present.** The litellm patch
already captures `llm.reasoning_content` on the LLM span
(`_litellm_patch.py:99–101`). Surface it on the matching agent step (uses the
same F4 linkage). Extend `LlmCall.__init__` to also pull
`rec.attrs.get("llm.reasoning_content", "")`.

**F8 — Root `trajectory_id`.** Set `trajectory_id = session_id` at the root
of every emitted trajectory. RFC v1.7 makes `trajectory_id` recommended on
standalone trajectories (and required on embedded subagents — not in scope
for this MR). One-line change in both branches of the trajectory dict
construction (empty-call branch at line 273 and the regular branch at line
464). Using `session_id` as the value is acceptable: the RFC explicitly
allows `trajectory_id` to be any unique identifier; reusing `session_id`
keeps producers single-sourced and downstream consumers can dedup on
either field.

### Deliberately out of scope (documented as a gap, not implemented)

These are also non-compliant or under-utilized, but each is its own
non-trivial change; addressing them all here would balloon the MR. Listed
here so the gap is visible to readers of the design doc and so follow-up
issues can be filed:

- **`agent.tool_definitions`.** Optional v1.5+; would be useful for SFT
  pipelines but the openinference TOOL spans don't carry full schemas, only
  call shapes. Filing as a follow-up requires routing the tool registry
  through `enable_tracing`.
- **`reasoning_effort` on agent steps.** Optional; nemo-oo-agents does not
  currently set a reasoning-effort hint.
- **`subagent_trajectories` / `SubagentTrajectoryRef`.** v1.7 single-file
  subagent embedding. Not used today because nemo-oo-agents subagents are
  filtered out by `is_summarizer` (only TokenBudgetSummarizer is recognized).
  Real subagent support would need a separate plan.
- **`extra` on `ToolCallSchema` and `ObservationResultSchema`.** Already
  partially populated (`extra.tool_metadata`). Not aligning with the v1.7
  schema-defined `extra` semantics, but functionally equivalent.
- **`is_copied_context`.** v1.7 normative for cross-trajectory copies (e.g.
  after summarization). nemo-oo-agents does not currently copy steps across
  trajectories, so this would only matter if subagent embedding lands.
- **`context_management` convention (v1.7 § VII).** System steps that
  transform context would carry `extra.context_management.{type,boundary}`.
  Not currently emitted; relevant only when summarization compaction is
  surfaced as a step (today the summarizer is filtered out entirely).
- **Multimodal content (v1.6+).** `message` and `content` would need to
  accept `ContentPart[]`. nemo-oo-agents image support exists at the event
  layer (`event.images`) but is currently dropped by the exporter.
- **Token IDs and logprobs (v1.3, v1.4).** RL-grade fields. The framework
  does not currently capture these.

A follow-up issue should be filed to track these. The current MR keeps scope
to the three issue-#215 violations plus the four cheap data-already-available
populations (F4–F7).

## Implementation

### Files to modify

- `src/nooa/tracing/_atif_exporter.py` — primary exporter
  changes:
  - Drop the `f"Executed {fn_name} {first_id}"` fallback at line 419 (F2).
  - Add an empty-content skip after `_strip_context_block` at line 386 (F3).
  - Rewrite `_attach_observation` to consult `tools_by_id` first (F1).
  - Track originating-`LlmCall` index per log message; emit `metrics`,
    `llm_call_count`, and `reasoning_content` on agent steps (F4, F5, F7).
  - Promote `total_cost_usd` from `extra` to top-level (F6).
  - Parse the TOOL span's serialized `result` (JSON with stdout/stderr/
    returned_value) for human-readable content. Concretely: try
    `json.loads(rec.attrs["result"])` and re-format as
    `"stdout:\n…\nstderr:\n…\nreturned_value:\n…"` (skipping empty parts).
    Fall back to the raw `result` string on JSON failure.

- `src/nooa/tracing/_hooks_impl.py` — wire the previously-dead
  `_safe_serialize_execution_result(...)` into
  `after_code_execution` so the TOOL span's `result` attribute carries
  parseable JSON instead of the generic repr serializer's output (F1a —
  pre-condition for F1).

- `tests/tracing/test_atif_exporter.py` — add regression tests (see "Tests"
  below).

### Tests (regressions first, then optional)

Bug-fix discipline: write each failing regression FIRST and run it against
the unmodified production code to confirm the failure, then apply the fix
and confirm green.

1. **`test_observation_attached_from_tool_span_when_message_log_lacks_content`**
   (F1 regression). Build records where the assistant emits a tool_call, the
   `role: "tool"` follow-up only carries `"status: complete"` (no
   `tool_call_id` matchable verbose user message), and there IS a TOOL span
   with `result` set to a JSON-encoded `{"stdout":"…","stderr":"","returned_value":"…"}`
   blob (post-F1a). Assert:
   - The step has `observation.results[0].source_call_id == tool_call_id`.
   - Crucially, `observation.results[0].content` contains the TOOL span's
     `stdout` text (e.g. `"hello world"`) — NOT the placeholder
     `"status: complete"` that would come from the role=tool message
     fallback.
   - Asserting on content **inequality** (`!= "status: complete"`) is what
     proves the fix; unmodified code emits the placeholder.

1a. **`test_code_execution_span_carries_tool_call_id_attribute`** (F1
    dependency pin). Run a synthetic `before_code_execution` /
    `after_code_execution` call pair with `tool_call_id="call_abc"`,
    inspect the resulting span's attributes, and assert
    `span.attributes["tool_call_id"] == "call_abc"`. Guards against a
    refactor of the kwargs-auto-attribute loop in `_hooks_impl.py`
    silently breaking F1.

2. **`test_agent_message_is_assistant_content_not_executed_log`**
   (F2 regression). Assistant output message has empty `content` and a
   `tool_call`. Current code emits `"Executed shell call_…"`. Assert that the
   fixed code emits `""` (the literal empty string) and the `tool_calls`
   field is still populated.

3. **`test_empty_user_message_after_context_strip_is_dropped`**
   (F3 regression). Build a record where one of `call.input_msgs` is a
   USER message with content `"<context>\n…\n</context>"` (no other body).
   After `_strip_context_block`, content is empty. Assert no user step is
   emitted for this message. Pair with a sibling user message containing
   real content to verify only the envelope-only one is dropped.

4. **`test_per_step_metrics_on_agent_steps`** (F4). Two consecutive
   `LlmCall`s with different token counts and costs. Assert each emitted
   agent step has `metrics.prompt_tokens` / `completion_tokens` /
   `cached_tokens` / `cost_usd` matching its originating LLM call, and that
   the running totals in `final_metrics` still sum correctly.

5. **`test_agent_step_carries_llm_call_count_one`** (F5). Each emitted
   agent step has `llm_call_count == 1`.

6. **`test_final_metrics_promotes_total_cost_usd_to_top_level`** (F6).
   `traj["final_metrics"]["total_cost_usd"] == sum(costs)`, and
   `traj["final_metrics"]["extra"]` does NOT contain `total_cost_usd`.

7. **`test_reasoning_content_attached_to_agent_step_when_present`** (F7).
   Set `llm.reasoning_content` on a `_llm_record` span fixture; assert the
   emitted agent step has `reasoning_content` matching.

8. **`test_observation_falls_back_to_message_log_when_no_tool_span`**
   (F1 fallback). The TOOL-span-first preference must NOT regress the
   existing chat-message scan path. Construct a synthetic record where no
   TOOL span exists for a tool_call but a verbose `<sys
   tag="N">PythonOutput(tool_call_id='…')</sys>` user message does. Assert
   observation still attached from the message log.

9. **`test_trajectory_carries_root_trajectory_id`** (F8). After building a
   trajectory with `session_id="s-123"`, assert
   `traj["trajectory_id"] == "s-123"` (or whatever derivation is chosen).
   Also pin the empty-call branch: build with no records and assert
   `trajectory_id` is still set on the empty trajectory dict.

All existing tests in `test_atif_exporter.py` must continue to pass.

### Edge cases and observations

These should be reviewed during implementation but don't change the plan:

1. **OpenAI Responses-API path may bypass `role: "tool"`.** When
   nemo-oo-agents talks to a Responses-API provider, `unifiedllm.py:2120–
   2208` transforms `role: "tool"` messages into `type: "function_call_output"`
   items. The openinference instrumentation captures the *chat-format*
   messages as input/output snapshots but its handling of native Responses
   items varies by version. F1's TOOL-span-first ordering means the
   exporter doesn't depend on this path working — the result content comes
   from the local code-execution span, which is recorded regardless of
   provider format.

2. **`log_call_idx` is order-sensitive.** The current `input_msgs[len(log):]`
   prefix-union assumes each call's input_msgs is the strict prefix of
   conversation history at that call. The parallel `log_call_idx` list
   must follow the same indexing — append `None` per input-msg fill,
   then the current call's index per output-msg fill — and the indexer
   list MUST stay length-aligned with `log`. Pin in F4's test by
   asserting `len(log_call_idx) == len(log)` indirectly (every agent step
   has metrics; every non-agent step does not).

3. **TOOL span result parsing is best-effort.** F1a switches the
   serializer to emit JSON, but a pre-F1a trajectory replayed offline
   still has Python-repr `ExecutionResult(...)` strings. The exporter
   should try `json.loads`, and on `JSONDecodeError` fall back to the
   raw string (which is still meaningful, just denser). This keeps the
   exporter robust against running against old span data.

### Verification

- `uv run pytest tests/tracing/test_atif_exporter.py -v` — all (new + old)
  green.
- `uv run pytest tests/tracing/ -v` — full tracing suite green.
- `uv run ruff check src/nooa/tracing/_atif_exporter.py tests/tracing/test_atif_exporter.py`
  — clean.
- Manual smoke: run `examples/quickstart/14_atif_trajectory.py` (already
  exists), open the resulting `trajectory.json`, verify a tool-using agent
  step now carries `observation.results[*]` and the agent `message` is the
  actual model output.

## Summary of v1.7 compliance after this MR

| Area                                | Status     | Notes |
|-------------------------------------|------------|-------|
| `schema_version`                    | OK         | "ATIF-v1.7" |
| `session_id`                        | OK         | Always set |
| `trajectory_id` (root)              | OK after F8| derived from session_id |
| `agent.name` / `version`            | OK         | |
| `agent.model_name`                  | OK         | Inferred or set |
| `agent.tool_definitions`            | Gap        | Optional v1.5+ |
| `agent.extra`                       | OK         | Pass-through |
| `steps`                             | OK         | |
| `final_metrics.*`                   | After F6   | top-level `total_cost_usd` |
| `final_metrics.extra`               | OK         | non-spec aggregates retained |
| `subagent_trajectories`             | Gap        | v1.7 feature; not used |
| `continued_trajectory_ref`          | Gap        | |
| Multimodal (v1.6+)                  | Gap        | event.images dropped |
| **Step `source: "user/agent/system"`** | OK after F3 | phantom user steps dropped |
| **Step `message` is assistant text**| OK after F2 | no "Executed …" log line |
| **Step `observation` paired**       | OK after F1 | TOOL span as primary source |
| Step `metrics`                      | OK after F4 | from LlmCall |
| Step `reasoning_content`            | OK after F7 | when litellm patch captures |
| Step `llm_call_count`               | OK after F5 | set to 1 on LLM steps |
| Step `reasoning_effort`             | Gap        | Not tracked |
| Step `is_copied_context`            | N/A        | No cross-trajectory copy |
| `ToolCall.extra`                    | Gap        | partial via step.extra.tool_metadata |
| `Observation.results[].extra`       | Gap        | |
| `Observation.results[].subagent_trajectory_ref` | Gap | |
| Context Management convention       | Gap        | summarizer filtered out anyway |
| `prompt_token_ids` / `logprobs`     | N/A        | Not captured by framework |

The Gaps row above is the list of follow-up issues to file after this MR
lands. None of them are necessary for the Vanessa Bench dashboard or for SFT
extraction from cybergym-oo trajectories, which is the immediate consumer
need.
