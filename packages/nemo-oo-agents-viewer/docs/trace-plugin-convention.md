# Trace Plugin Convention

The trace viewer renders each OTLP span using a **rendering plugin** selected by the span's event type. This document describes how that selection works and what values are valid.

## The `nemo_oo_agents.viewer.plugin` attribute

Each OTLP span can include a custom string attribute:

```
nemo_oo_agents.viewer.plugin = "<plugin_name>"
```

When present, the viewer uses this value to select the rendering plugin. When absent, the viewer falls back to the OTLP **span name**.

> **Note:** The attribute name `nemo_oo_agents.viewer.plugin` is an arbitrary convention chosen for clarity. It is not part of the OTLP or OpenInference specifications.

## Plugin selection logic

The viewer derives an **event type** string for each span:

```
event_type = "span.<nemo_oo_agents.viewer.plugin || span.name>"
```

This event type is matched against the plugin registry in order:

1. **Exact match** (e.g., `span.generation`)
2. **Prefix match** (e.g., `span.method.classify` matches `span.method`)
3. **Wildcard match** (`span.*` catches anything not matched above)
4. **Default plugin** if nothing matches

## Valid plugin values

| `nemo_oo_agents.viewer.plugin` | Plugin | Renders |
|-------------------------------|--------|---------|
| `method` | MethodPlugin | Agent method invocations (both direct calls and generated method calls) |
| `generation` | GenerationPlugin | LLM generation sessions (strategy, iterations, result) |
| `code_execution` | CodeExecutionPlugin | Python code execution (code, stdout, stderr, return value) |
| `tool_execution` | ToolExecutionPlugin | Tool invocations (arguments, result, including `return_result`) |
| `llm_call` | LLMCallPlugin | Raw LLM API calls (messages, tokens, model info) |
| `eval` | EvalPlugin | Evaluation spans |

The `openinference-instrumentation-nemo-oo-agents` package currently sets the first four. `llm_call` and `eval` are available for client code or external instrumentation.

Any span with an unrecognized plugin value falls through to **SpanPlugin** (via the `span.*` wildcard).

## Attributes read by each plugin

Each plugin reads specific span attributes to render its content. All attributes are optional — plugins degrade gracefully when attributes are missing. Attributes are grouped by what they control in the UI.

### `method`

**Identity:** `agent.method` (method name), `agent.name` (agent class)

**Result:** `agent.result` (serialized return value)

**Call signature:** `agent.args` (positional args JSON), `agent.kwargs` (keyword args JSON)

**Status:** `duration_ns`, `status_code` (`"OK"` / `"ERROR"` / `"UNSET"`), `error.message`

**Metadata (expanded view):** `agent.strategy.name`, `agent.call_id`, `agent.file_path`

### `generation`

**Identity:** `generation.strategy` (e.g., `PURE_PYTHON`, `STRUCTURED_OUTPUT`)

**Context:** `agent.method`, `agent.name`

**Status:** `duration_ns`, `status_code`, `error.type`, `error.message`

**Metadata (expanded view):** `generation.id`

### `code_execution`

**Code:** `code` (the Python source)

**Result:** `result` (JSON with `defined_methods`, `returned_value`, `stdout` fields)

**Status:** `duration_ns`, `status_code`, `error`, `status_description` (fallback error message)

### `tool_execution`

**Identity:** Tool name is extracted from the span name (strips `tool_execution.` prefix), falling back to `tool.name`.

**Result:** `tool.result` (serialized result, parsed as JSON if possible)

**Status:** `duration_ns`, `status_code`, `error.message`

**Metadata (expanded view):** `execution.id`, `generation.id`, `agent.name`, `result.type`

### `llm_call`

These spans are produced by the LiteLLM OpenInference instrumentor, which uses the OpenInference flattened attribute convention.

**Model:** `gen_ai.response.model` or `llm.model_name`

**Input messages:** `llm.input_messages.{i}.message.role`, `llm.input_messages.{i}.message.content`, `llm.input_messages.{i}.message.tool_calls.{j}.tool_call.function.name/arguments/id`, `llm.input_messages.{i}.message.tool_call_id`

**Output:** `output.value` (JSON, parsed for text or content arrays)

**Output tool calls:** `llm.output_messages.0.message.tool_calls.{i}.tool_call.function.name/arguments/id`

**Tokens:** `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`

**Reasoning:** `llm.reasoning_content`

**Status:** `duration_ns`, `status_code`

### `eval`

**Identity:** `eval.test_id`, `eval.model`, `eval.agent_class`, `eval.method`

**Score:** `eval.passed` (boolean), `eval.weighted_score` or `eval.score`

**Per-scorer results:** `eval.scorer.{name}.score`, `eval.scorer.{name}.passed`, `eval.scorer.{name}.reasoning`. Alternatively, `eval.scores` as a JSON object with per-scorer data.

**Output comparison:** `eval.expected_output` or `eval.expected`, `eval.actual_output` or `eval.output`

**Status:** `duration_ns`

**Additional metadata (expanded view):** Any `eval.*` attribute not listed above

## Setting the attribute in client code

If you are writing custom instrumentation (outside of `openinference-instrumentation-nemo-oo-agents`), set the attribute on your span:

```python
from opentelemetry import trace

span = tracer.start_span("my_custom_span")
span.set_attribute("nemo_oo_agents.viewer.plugin", "code_execution")
```

Or import the constants from the instrumentation package:

```python
from openinference_instrumentation_nemo_oo_agents._hooks_impl import (
    VIEWER_PLUGIN_ATTR,
    ViewerPlugin,
)

span.set_attribute(VIEWER_PLUGIN_ATTR, ViewerPlugin.CODE_EXECUTION)
```

## Backward compatibility

Traces produced before this attribute was introduced will continue to work. The viewer falls back to `span.name` when the attribute is absent, preserving the existing behavior.
