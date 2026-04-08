# Completing OpenTelemetry Separation - Phase 4

**Date:** 2025-12-09
**Breaking Change:** Yes
**Status:** ✅ Complete

Completes Phase 4 of [phase-1-opentelemetry.md](methodic006/phase-1-opentelemetry.md). Core library now has **zero OpenTelemetry code**. All instrumentation lives in separate package using standard patterns.

## Files Changed

**Created:** `packages/openinference-instrumentation-agent006/` (726 lines)
**Deleted:** `src/agent006/runtime/otel_hooks.py` (958 lines)
**Modified:** 13 files (core, examples, utils, docs)
**Net:** -232 lines

**Dependencies added:**
- `openinference-instrumentation-litellm` - Handles all litellm tracing
- `openinference-instrumentation`
- `openinference-semantic-conventions`

## Key Technical Details

**Context propagation (critical for multi-instrumentor cooperation):**
```python
token = context.attach(trace.set_span_in_context(span))  # Activates span
# Without this: LiteLLM instrumentor can't find parent → orphaned spans
context.detach(token)  # Cleanup when done
```

**Multi-destination tracing:**
```python
provider.add_span_processor(BatchSpanProcessor(JSONLSpanExporter("./traces")))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(...)))  # Phoenix
# Traces go to both simultaneously
```

## Breaking Changes

**Import change (all tracing code):**
```python
- from agent006.runtime import enable_tracing
+ from openinference_instrumentation_agent006 import enable_tracing
```

**Access current exporter:**
```python
- from agent006.runtime.otel_hooks import _exporter
+ from openinference_instrumentation_agent006 import get_current_exporter
+ exporter = get_current_exporter()
```

## Usage

**Quick start (most common):**
```python
from openinference_instrumentation_agent006 import enable_tracing

exporter = enable_tracing()  # Auto-instruments agent006 + litellm
```

**Manual setup (multi-destination):**
```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(JSONLSpanExporter("./traces")))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(...)))
trace.set_tracer_provider(provider)

Agent006Instrumentor().instrument(tracer_provider=provider)
LiteLLMInstrumentor().instrument(tracer_provider=provider)
```

## Result

- Core: Zero OTel code (-958 lines)
- Instrumentation: Proper package (+726 lines)
- LLM tracing: Standard `openinference-instrumentation-litellm` (no custom code)
- Multi-destination: Standard OTel pattern
- Trace viewer: Updated for OpenInference format

**Net:** -232 lines, cleaner architecture, standard patterns, ecosystem compatible.
