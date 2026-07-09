# OpenInference trace conformance

## Problem

nooa emits OpenTelemetry spans for every agent run. For those traces to
work in any OpenInference-compatible backend (Phoenix, Arize, …) without surprises,
the spans must follow the [OpenInference semantic conventions](https://arize-ai.github.io/openinference/spec/semantic_conventions.html):
the correct span **kind** per operation and the spec's attribute **names/types** for
I/O, tool calls, and LLM calls.

The framework historically emitted its own attribute names (`agent.args`,
`agent.result`, `code`, `tool.arguments`, `generation.result`, …) and mis-kinded the
per-turn orchestration span as `LLM`. This document describes the conformant model
the framework now emits and how it is enforced.

## Spans and kinds

Two sources produce spans:

- **Framework spans** — emitted by `OpenInferenceHooks`
  (`src/nooa/tracing/_hooks_impl.py`).
- **LLM spans** — emitted by `openinference-instrumentation-litellm` for each
  `litellm.acompletion` call (patched in `_litellm_patch.py` to also capture
  `tool_call.id` and reasoning content). These carry the `llm.*` attributes.

| Span (name) | Represents | `openinference.span.kind` |
|---|---|---|
| `method.<m>` | an agent method (ellipsis) run | `AGENT` |
| `generation` | one strategy turn (orchestration step) | `CHAIN` |
| `acompletion` (litellm) | the actual provider LLM call | `LLM` |
| `code_execution` | the python-executor tool | `TOOL` |
| `method_call.<m>` | a generated-method invocation | `TOOL` |
| `tool_execution.<t>` | a tool call (e.g. `return_result`) | `TOOL` |
| `context_snapshot` | the per-turn system-prompt snapshot/diff | `CHAIN` |

Key decisions:

- **`generation` is `CHAIN`, not `LLM`.** It is an orchestration step with no
  `llm.*` attributes; the real LLM call is the nested `acompletion` span. Marking it
  `LLM` would make backends expect `llm.model_name` / `llm.input_messages` on it.
- All emitted kinds are members of the OpenInference kind enum. Span **names** are
  free-form (OpenInference classifies on the kind attribute, not the name), so the
  framework's finer-grained concepts collapse onto the nearest kind (e.g. both
  `generation` and `context_snapshot` are `CHAIN`).
- Attribute names and kind values are emitted from the
  `openinference-semantic-conventions` constants (`SpanAttributes.*`,
  `OpenInferenceSpanKindValues.*`) so they track the spec rather than hardcoded
  strings.

## I/O is OpenInference-only (single representation)

Span inputs/outputs are represented **once**, using the OpenInference attributes —
the legacy native I/O attributes are no longer emitted.

| Span | `input.value` | `input.mime_type` | `output.value` | `output.mime_type` |
|---|---|---|---|---|
| `method.<m>` (AGENT) | JSON `{"args":…, "kwargs":…}` | `application/json` | serialized result | `text/plain` |
| `generation` (CHAIN) | — | — | serialized result | `text/plain` |
| `code_execution` (TOOL) | JSON `{"code":…}` | `application/json` | serialized result | `application/json` for an execution result (JSON of stdout/stderr/returned_value), else `text/plain` |
| `method_call.<m>` (TOOL) | JSON `{"args":…, "kwargs":…}` | `application/json` | serialized result | `text/plain` |
| `tool_execution.<t>` (TOOL) | JSON arguments | `application/json` | serialized result | `text/plain` |
| `context_snapshot` (CHAIN) | system message (full or diff) | `text/plain` | — | — |

Serialized values are bounded (truncating serializer, ≤50 KB; code capped at 10 KB).
`input.value` is always valid JSON via a serializer that falls back to a JSON string
literal of the value's repr when the value is not natively JSON-serializable.

### Tool-call identity on TOOL spans

Each TOOL span (`code_execution`, `method_call`, `tool_execution`) also carries the
flat tool-call attributes so backends render it as a call:

- `tool.name`, `tool.id`
- `tool_call.function.name`, `tool_call.function.arguments` (valid JSON)
- `tool_call.id` — the model-provided id when present, otherwise the framework
  execution/invocation id (so inline framework tools always have a stable id).

### LLM spans

`llm.model_name`, `llm.invocation_parameters`, `llm.token_count.*`,
`llm.input_messages.*` / `llm.output_messages.*` (roles, content, tool calls with
`tool_call.function.name`/`arguments`/`id`), and `input.value`/`output.value` are
produced by the litellm instrumentor (+ the patch for `tool_call.id`).

**Cost** (`llm.cost.total`, and `llm.cost.prompt`/`llm.cost.completion` when
derivable) is stamped by the patch — the instrumentor omits it. Two sources: the
LiteLLM gateway's `x-litellm-response-cost` response header (preferred; requires
`litellm.return_response_headers = True`, which the patch sets — and the only source
for models with no local pricing), falling back to litellm's locally computed
`response_cost` for direct API calls. Cost is only stamped when positive, so
unknown-pricing models stay unset rather than reporting a misleading $0.

### Framework metadata retained

Attributes with no OpenInference equivalent are kept (backends ignore unknown
attributes): `agent.name`/`method`/`call_id`/`parent_call_id`/`method_signature`/
`docstring`/`file_path`, `generation.id`/`strategy`/`parent_id`, `code.length`,
`execution.id`, `invocation.id`, `result.type`, `nooa.viewer.plugin`,
and `session.id`.

`context_snapshot` additionally keeps `nooa.system_message` (+
`system_message.is_diff` / `system_message.turn_index`): it is not a plain duplicate
of `input.value` — it carries diff semantics and drives the viewer's diff renderer.

## Reader backwards-compatibility

Already-saved traces use the old native attribute names, so the readers accept both:
they read the OpenInference name first and fall back to the native name.

- **Trace explorer** (`trace_explorer/explorer.py`): the `_io_value` /
  `_io_json_field` helpers back every I/O read (agent args/kwargs/result, generation
  result, code-exec code/result, tool arguments/result).
- **Trace viewer** (React plugins `Method`, `ToolExecution`, `CodeExecution`,
  `Span`): OpenInference-first with native fallback. `LLMCall` is already
  OpenInference-native.
- **Benchmark trace analyzer** reads code OpenInference-first.

The explorer's turn parsing keys on span **names** (`generation`, `acompletion`,
`code_execution`), not on the kind — so the `generation` kind change is transparent
to it. The viewer's journal augmentation targets the litellm `LLM` span, never the
`generation` span.

**Wire strip is LLM-only.** When traces are sent to a viewer, the journal exporter
strips `input.value`/`output.value` from the wire because the viewer reconstructs
them for `LLM` spans from the message journal. That strip is applied **only to
`LLM`-kind spans** (`span_to_otlp`'s `exclude_attr_prefixes_llm_only`): framework
spans are not in the journal, so their `input.value`/`output.value` — now the sole
carrier of their I/O — must survive the wire, or consumers reading via the viewer
(`TraceExplorer.from_viewer`, e.g. the capability scorers) would see empty I/O.

## Conformance tests

`tests/tracing/test_openinference_conformance.py` validates emitted spans against the
`openinference.semconv.trace` constants (so the suite fails if a name/type/required
field drifts from the spec). Two fixtures:

- **Framework spans** — a small CodeAct `FakeLLM` agent run through the real hooks
  (no network). Asserts span kinds are valid enum members; AGENT/TOOL/CHAIN I/O
  attributes and tool-call identity attributes are present and correctly typed; the
  `generation` span is `CHAIN` and no `LLM` span exists; the native I/O attributes
  are absent; span names are stable.
- **LLM span** — a `litellm.acompletion(mock_response=…)` call. Asserts the `LLM`
  span name/kind, `llm.model_name`, integer token counts, input/output messages, and
  the tool-call attributes (including `tool_call.id` from the patch).

`tests/trace_explorer/test_oi_only_backcompat.py` builds synthetic
OpenInference-only spans (no native I/O attributes) and asserts the explorer still
recovers code, stdout, returned value, agent args/kwargs, results, and the
`return_result` preview — plus a native-only span as the backwards-compat guard.

## Out of scope

- `tool.description` / `tool.parameters` / `tool.json_schema` on framework TOOL spans
  (optional; the LLM span already carries offered tools as `llm.tools.*`).
- `RETRIEVER` / `EMBEDDING` / `RERANKER` / `GUARDRAIL` / `EVALUATOR` span kinds — the
  framework produces no such operations.
