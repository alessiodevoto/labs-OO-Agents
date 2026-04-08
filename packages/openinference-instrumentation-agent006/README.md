# OpenInference Instrumentation for Agent006

OpenTelemetry instrumentation for [Agent006](https://github.com/agent006), following OpenInference semantic conventions.

## Installation

```bash
pip install openinference-instrumentation-agent006
```

## Quick Start

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry import trace
from openinference.instrumentation.agent006 import Agent006Instrumentor
from openinference.instrumentation.litellm import LiteLLMInstrumentor

# Setup tracer provider
provider = TracerProvider()
trace.set_tracer_provider(provider)

# Instrument agent006 and litellm
Agent006Instrumentor().instrument()
LiteLLMInstrumentor().instrument()

# Now all agent operations and LLM calls are traced
from agent006 import Agent

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
from openinference.instrumentation.agent006 import Agent006Instrumentor, JSONLSpanExporter

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
Agent006Instrumentor().instrument()
LiteLLMInstrumentor().instrument()
```

## Convenience API

For quick setup with JSONL traces:

```python
from openinference.instrumentation.agent006 import enable_tracing

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

## Attributes

Following [OpenInference semantic conventions](https://github.com/Arize-ai/openinference):

- `openinference.span.kind` - Span type (AGENT, LLM, TOOL)
- `agent.name` - Agent class name
- `agent.method` - Method being executed
- `generation.strategy` - Strategy used (PURE_PYTHON, STRUCTURED_OUTPUT, etc.)
- `code` - Generated code
- `execution.error` - Error message if failed
- And many more...
