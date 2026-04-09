# OpenInference Instrumentation for NeMo OO Agents

OpenTelemetry instrumentation for [NeMo OO Agents](https://github.com/nemo_oo_agents), following OpenInference semantic conventions.

## Installation

```bash
pip install openinference-instrumentation-nemo-oo-agents
```

## Quick Start

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry import trace
from openinference.instrumentation.nemo_oo_agents import NemoOOAgentsInstrumentor
from openinference.instrumentation.litellm import LiteLLMInstrumentor

# Setup tracer provider
provider = TracerProvider()
trace.set_tracer_provider(provider)

# Instrument nemo_oo_agents and litellm
NemoOOAgentsInstrumentor().instrument()
LiteLLMInstrumentor().instrument()

# Now all agent operations and LLM calls are traced
from nemo_oo_agents import Agent

class MyAgent(Agent, llm=my_llm):
    async def my_method(self):
        """Do something..."""
        ...

agent = MyAgent()
await agent.my_method()  # Fully traced!
```

## Multi-Destination Tracing

Send traces to multiple destinations simultaneously:

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from openinference.instrumentation.nemo_oo_agents import NemoOOAgentsInstrumentor, JSONLSpanExporter

provider = TracerProvider()

# Send to Arize Phoenix for visualization
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:6006/v1/traces"))
)

# Save to JSONL files for trace-viewer
provider.add_span_processor(
    BatchSpanProcessor(JSONLSpanExporter("./traces"))
)

trace.set_tracer_provider(provider)

# Instrument everything
NemoOOAgentsInstrumentor().instrument()
LiteLLMInstrumentor().instrument()
```

## Convenience API

For quick setup with JSONL traces:

```python
from openinference.instrumentation.nemo_oo_agents import enable_tracing

# Auto-configures tracer provider with JSONL export
exporter = enable_tracing()  # Uses util/trace-viewer/traces/ if available
print(f"Traces: {exporter.trace_file}")
```

## Span Types

The instrumentor creates spans with OpenInference semantic conventions:

- **AGENT** spans - Ellipsis method calls
- **LLM** spans - Code generation sessions
- **TOOL** spans - Code execution and method calls
- **LLM** spans - LiteLLM calls (via LiteLLMInstrumentor)

## Viewer Plugin Hint

Each span includes a `nemo_oo_agents.viewer.plugin` attribute that tells the [trace viewer](../nemo-oo-agents-viewer/) which rendering plugin to use. Valid values:

| Value | Span Type | Viewer Plugin |
|-------|-----------|---------------|
| `method` | Agent method calls | MethodPlugin |
| `generation` | LLM generation sessions | GenerationPlugin |
| `code_execution` | Code execution | CodeExecutionPlugin |
| `tool_execution` | Tool invocations | ToolExecutionPlugin |
| `llm_call` | Raw LLM API calls | LLMCallPlugin |
| `eval` | Evaluation spans | EvalPlugin |

If you write custom instrumentation, set this attribute so the viewer renders your spans correctly. See [`nemo-oo-agents-viewer/docs/trace-plugin-convention.md`](../nemo-oo-agents-viewer/docs/trace-plugin-convention.md) for the full specification.

## Attributes

Following [OpenInference semantic conventions](https://github.com/Arize-ai/openinference):

- `openinference.span.kind` - Span type (AGENT, LLM, TOOL)
- `nemo_oo_agents.viewer.plugin` - Viewer rendering plugin hint (see above)
- `agent.name` - Agent class name
- `agent.method` - Method being executed
- `generation.strategy` - Strategy used (PURE_PYTHON, STRUCTURED_OUTPUT, etc.)
- `code` - Generated code
- `execution.error` - Error message if failed
- And many more...
