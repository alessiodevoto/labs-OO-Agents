# Issue 209 — Native ATIF v1.7 trajectory SpanExporter

Issue: https://gitlab-master.nvidia.com/interactive-agents/nooa/-/issues/209

## Goal

Add a native OpenTelemetry `SpanExporter` that emits an ATIF v1.7
`trajectory.json` directly from the openinference-instrumented OTLP spans this
package already produces. The exporter is wired into the existing tracing
exporters registry as `exporters.atif(...)` so callers can use it alongside
`exporters.jsonl(...)`, `exporters.otlp(...)`, etc.

Drop-in replacement for `nemo_flow.AtifExporter` (compiled native binary)
which produces trajectories the vanessa-bench dashboard renderer cannot
consume: raw `str(dict)` `message` fields, no structured `tool_calls` on
agent steps, observations on phantom system steps, missing token/cost
metrics, and ~200× file-size bloat.

## Inputs (already in the issue)

The issue ships three Python files plus a reference target trajectory:

1. `_atif_builder.py` (534 lines) — pure-Python ATIF v1.7 builder.
   Operates on a `SpanRecord` list, produces a trajectory dict. Two driver
   helpers: `records_from_readable_spans` (live, for the SpanExporter) and
   `_records_from_jsonl` (offline post-processor).

2. `_atif_exporter.py` (137 lines) — `AtifTrajectoryExporter` SpanExporter.
   Accumulates spans across `export()` calls; atomically rewrites
   `trajectory.json` on each batch (`.tmp + replace`); final write on
   `shutdown()`.

3. `exporters_patch.py` — patch fragment for `exporters.py` adding the
   `atif(path, ...)` factory. Mirrors the existing `jsonl`/`otlp`/
   `langfuse`/`local_otlp`/`journal`/`console` style: deferred import,
   returns a `SpanExporter`.

4. `atif_v17_example.json` — reference output shape, taken from a working
   codex run (the renderer's actual input contract).

All four are already downloaded to `/tmp/issue209/` for the implementer.

## Implementation steps

### Files to add (exact drop-in of the attachments)

- `src/nooa/tracing/_atif_builder.py` — from
  `/tmp/issue209/_atif_builder.py`. Keep the SPDX header (already present).
- `src/nooa/tracing/_atif_exporter.py` — from
  `/tmp/issue209/_atif_exporter.py`. Keep the SPDX header.

### Files to modify

- `src/nooa/tracing/exporters.py` — append the new `atif()`
  factory at the end of the file (after `console()`, before
  `_auto_detect_trace_dir()`). The factory body and docstring come from
  `/tmp/issue209/exporters_patch.py`. Imports needed at the top of
  `exporters.py`:
  - `Path` — already imported.
  - `SpanExporter` from `opentelemetry.sdk.trace.export` — already
    imported.
  - **Add `from typing import Any`** — `exporters.py` does not currently
    import it and the `atif()` signature uses `dict[str, Any]`.

  The `AtifTrajectoryExporter` import stays inside the function body
  (lazy), matching `jsonl()` / `journal()` / `otlp()` style.

- `src/nooa/tracing/__init__.py`:
  - **Add `AtifTrajectoryExporter` to `_LOCAL_EXPORTER_TYPES`** (the
    tuple at lines 117–125). The exporter does pure local file I/O
    (`mkdir` + atomic `tmp.write_text` + `replace`); it must use
    `SimpleSpanProcessor`, not `BatchSpanProcessor`. Without this
    change:
    - The "partial trajectory on every export" guarantee is broken
      because batching can buffer spans before any write happens.
    - `_span_records.extend(...)` and `_write_atomic()` are not
      synchronized; `BatchSpanProcessor`'s background thread plus a
      concurrent `force_flush()` / `shutdown()` from the agent's
      thread could race.
    Import path: deferred import inside the file (or top-of-file with
    the other `from nooa.tracing._...` lines — check current
    convention).
  - **Re-export `AtifTrajectoryExporter` in `__all__`** for symmetry
    with the existing `OtlpJsonFileExporter` / `OtlpJsonHttpExporter`
    re-exports. Add the import next to those two and append to the
    `__all__` list.

### Tests to add

`tests/tracing/test_atif_exporter.py` — covers both the builder (pure
function, hermetic) and the exporter (file write + atomic semantics).
The list below is representative; the canonical set of test names lives
in the test file itself and may grow as reviewer feedback is folded in.

1. **`test_atif_factory_returns_span_exporter`** — `exporters.atif(...)`
   returns a `SpanExporter` subclass; the underlying `AtifTrajectoryExporter`
   gets the path / session_id / agent metadata fields.

2. **`test_atif_builder_minimal_trajectory`** — construct a few synthetic
   `SpanRecord` instances (one system + one user + one assistant with
   tool_calls + one TOOL span) and assert `build_trajectory_from_records`
   returns a dict with:
   - `schema_version == "ATIF-v1.7"`
   - `agent.name`, `agent.version`, `agent.model_name` set
   - Step ordering: system → user → agent
   - The agent step has `tool_calls` (a list of dicts with
     `tool_call_id`, `function_name`, `arguments`)
   - `final_metrics.total_prompt_tokens` /
     `total_completion_tokens` / `total_cached_tokens` sum across LLM
     spans correctly; `extra.total_cost_usd` matches.
   - `final_metrics.total_steps == len(steps)`.

3. **`test_atif_builder_drops_summarizer_calls`** — an LLM span whose
   `system_content` contains `"TokenBudgetSummarizer"` is excluded from
   `final_metrics` and steps (housekeeping, not user-facing).

4. **`test_atif_builder_pairs_verbose_tool_followup`** — feed an
   assistant message with a tool_call followed by both a `role=tool`
   placeholder and a `role=user` verbose message containing
   `tool_call_id='call_xyz'` in its body; assert the agent step gets a
   single `observation.results` entry sourced from the verbose body
   (the placeholder is collapsed).

5. **`test_atif_builder_strips_trailing_context_block`** —
   `<context>...</context>` envelopes at the end of message content are
   stripped before deduping.

6. **`test_atif_exporter_writes_atomic_trajectory`** —
   `AtifTrajectoryExporter.export([mock_span])` writes a parseable JSON
   file at the configured path, and creates the parent directory if it
   does not exist. Verify that a `.tmp` file does not linger after a
   successful export (i.e. `replace()` was used).

7. **`test_atif_exporter_shutdown_idempotent`** — calling `shutdown()`
   twice in a row does not raise; the second call is a no-op.

8. **`test_atif_exporter_force_flush_writes_when_called`** —
   `force_flush()` calls `_write_atomic()` and returns `True` on success.

9. **`test_atif_exporter_empty_when_no_main_calls`** — exporting spans
   that contain only TokenBudgetSummarizer LLM calls (or no LLM calls)
   produces a trajectory dict with `steps == []` and
   `final_metrics == {"total_steps": 0}`. The file must still be valid
   JSON.

10. **`test_atif_exporter_in_local_exporter_types`** — import
    `_LOCAL_EXPORTER_TYPES` from `nooa.tracing` and assert
    `AtifTrajectoryExporter` is a member. Regression guard for the
    `__init__.py` wiring change above.

11. **`test_atif_exporter_end_to_end_through_enable_tracing`** —
    integration smoke: call `enable_tracing(exporters=[exporters.atif(
    tmp_path/"trajectory.json", session_id="s", agent_name="a",
    agent_version="0")])`, emit one synthetic span via the tracer
    (or `flush_traces()` after a no-op), call `shutdown_traces()`,
    assert the file exists and parses as JSON with the expected
    top-level keys. This is the only test that exercises the
    processor-selection wiring from `_LOCAL_EXPORTER_TYPES`.

12. **`test_atif_exporter_export_after_shutdown_returns_failure`** —
    `_atif_exporter.py` lines 87–101 explicitly return
    `SpanExportResult.FAILURE` once `_shut_down` is set. Pin this.

13. **`test_atif_exporter_write_on_each_export_false`** — construct
    `AtifTrajectoryExporter(..., write_on_each_export=False)`, call
    `export()` once and assert the target file does NOT exist; then
    call `force_flush()` and assert the file appears.

14. **`test_atif_builder_model_name_override`** — two sub-cases:
    - When the builder cannot infer `model_name` from LLM spans
      (empty `llm.model_name` attrs), the constructor's `model_name`
      kwarg lands on `agent.model_name`.
    - When the builder *can* infer `model_name`, the constructor's
      `model_name` does NOT overwrite it (see `_write_atomic`
      conditional at line 132 of `_atif_exporter.py`).

15. **`test_atif_builder_tcid_in_body_double_quoted`** — pin both
    single-quoted (`tool_call_id='call_xyz'`) and double-quoted
    (`tool_call_id="call_xyz"`) forms of the `tcid_in_body_re` regex
    so a future serialization change in nemo-oo-agents breaks the
    test loudly instead of silently losing observation pairing.

Synthetic `ReadableSpan` mocks: use `unittest.mock.MagicMock` with the
attributes the builder reads (`start_time`, `end_time`, `attributes`,
`name`). The builder only touches `int(s.start_time or 0)`,
`int(s.end_time or 0)`, `dict(s.attributes or {})`, `s.name or ""` — see
`records_from_readable_spans` in `_atif_builder.py`.

### What we deliberately do NOT do in this MR

- **No change to `enable_tracing()`** — `atif()` is opt-in via the
  `exporters=[...]` arg. The auto-probe path (`_default_exporters()`)
  is unchanged.
- **No change to existing exporters** — backward compatible.
- **No removal of `_strip_context_block` heuristic** — depends on issue
  #208 landing; will become a no-op once dynamic context is emitted as
  its own message. Tracked separately.
- **No `nemo_flow` dependency removal** — this repo does not depend on
  `nemo_flow`; the AtifExporter that this work replaces is imported by
  downstream consumers (e.g. vanessa-bench). They migrate on their own
  schedule by switching from `nemo_flow.AtifExporter` to
  `exporters.atif(...)`.

## Schema-version note

The example file (`atif_v17_example.json`) ships with
`"schema_version": "ATIF-v1.5"`. The builder writes
`"ATIF-v1.7"`. This is intentional per the issue title — v1.7 is what
the downstream renderer accepts and what we want to stamp going forward.
The example file is just shape-reference; the version string is not
load-bearing for the renderer in current practice. Compatibility with
the vanessa-bench renderer's field-presence gating was observed across
301 cybergym-300 trials (see issue body); other consumers of the v1.5
schema are not exercised here and would need to be re-validated
separately if used.

## Edge cases and observations from a critical read of the builder

These are things the implementation review should look at; none are
blockers but they shape the test set:

1. **Snapshot-prefix message union.** For each main-agent LLM call we
   append `input_msgs[len(log):]` plus the call's `output_msgs` to a
   running log, with a parallel `log_ts` list for per-message
   timestamps. This relies on the openinference convention that each
   call's `input_messages` is the full conversation history up to that
   call. The approach correctly preserves legitimately repeated user
   or assistant turns (a content-based dedup set would drop them) and
   avoids the fragility of keying timestamps on Python object identity
   (`id(m)`). If a sub-agent rewrites earlier turns out-of-place the
   log may diverge from the canonical conversation, but
   TokenBudgetSummarizer (the only known such sub-agent) is filtered
   out before this stage.

2. **`is_summarizer` heuristic is substring-on-system-content.** If a
   main-agent system prompt ever contains the literal substring
   `"TokenBudgetSummarizer"`, those spans will be wrongly dropped. The
   risk is low (typical main prompts don't name that internal class),
   and the failure mode is "trajectory is missing user-facing steps"
   which is easy to spot. Leave as-is for parity with the local
   validated builder; revisit if it becomes a problem.

3. **`tcid_in_body_re` is a `\\b` regex on `tool_call_id='...'`.** It
   matches single OR double quotes. A nemo-oo-agents serialization
   change that switches to a different shape (e.g. JSON
   `"tool_call_id": "..."`) would break pairing. Pin this in a test
   so a future change to the serialization triggers a test failure
   rather than silent observation loss.

4. **No-LLM-spans corner case.** `_partition_records` returns empty
   `main_calls`; the early-return path emits a trajectory with
   `steps: []` and `final_metrics: {"total_steps": 0}`. The file is
   still valid JSON. Tested.

5. **Atomic write only for the final file, not for batches.** The
   `.tmp + replace` pattern is robust across kill -9 boundaries on the
   same filesystem; a partial trajectory on disk is always either the
   previous complete state or the new complete state, never half-
   written. Good.

6. **No span-volume bound.** The exporter keeps every `SpanRecord`
   in memory for the life of the run. For long-running agents this
   grows monotonically. For the in-scope use case (one trial =
   bounded steps), this is fine; flag in the docstring already. Not
   addressed here.

7. **`_partition_records` keeps LLM spans regardless of token counts.**
   Earlier drafts filtered spans where `llm.token_count.prompt == 0`,
   which silently emptied trajectories from providers that don't
   instrument usage. The filter was removed; `final_metrics` now
   simply rolls up zeros for those spans. Pinned by
   `test_keeps_llm_spans_with_zero_prompt_tokens`.

8. **Exporter mutations are guarded by `threading.RLock`.**
   `SimpleSpanProcessor` invokes `export()` on span-ending threads,
   which may race against `force_flush()` / `shutdown()` from the
   agent thread. The lock keeps `_span_records` mutations and
   `_write_atomic` atomic. `force_flush()` after `shutdown()` returns
   `False` and does not rewrite the trajectory.

## Verification plan

- Run `uv run pytest tests/tracing/test_atif_exporter.py -v` —
  all new tests pass.
- Run `uv run pytest tests/tracing/ -v` — full tracing suite still
  green; no regressions in `test_exporters.py`,
  `test_tracing_integration.py`.
- Run `uv run ruff check src/nooa/tracing/_atif_builder.py
  src/nooa/tracing/_atif_exporter.py
  src/nooa/tracing/exporters.py` — clean.
- Smoke: import `from nooa.tracing import exporters;
  exporters.atif("/tmp/x.json", session_id="s", agent_name="a",
  agent_version="0")` returns an `AtifTrajectoryExporter` without
  raising.

## Out-of-scope follow-ups (link in MR description)

- Remove `_strip_context_block` once issue #208 lands and dynamic
  context is its own message.
- Offline post-processor CLI wrapping `_records_from_jsonl` +
  `build_trajectory` (e.g. `nooa atif-backfill
  traces/*.jsonl -o trajectory.json`). The builder already supports
  this path — only a thin CLI shim is missing.
- Quickstart example
  `examples/quickstart/06_tracing.py` could demonstrate
  `exporters.atif(...)` alongside `exporters.jsonl(...)` — optional,
  not a blocker.
