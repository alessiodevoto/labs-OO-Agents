# NeMo OO Agents Viewer

Trace and evaluation viewer for NeMo OO Agents. Provides a web UI to inspect agent execution traces, LLM calls, code execution, and evaluation results.

## Quick Start

```bash
# Start the viewer (default port 5001)
nemo oo start-dev

# Custom port
nemo oo start-dev --port 5002
```

Then open `http://localhost:5001` in your browser.

## Sending Traces

The viewer accepts OTLP traces on `POST /v1/traces`. The simplest setup:

```python
from openinference_instrumentation_nemo_oo_agents import enable_tracing

# Zero-config: sends traces to localhost:5001
enable_tracing()
```

Traces can also be sent from any OpenTelemetry-compatible exporter:

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:5001/v1/traces"))
)
```

## Exporting Traces

Select one or more traces in the UI and click the export button to download them as OTLP `.jsonl` files.

## Importing and Deleting Traces

Trace files (OTLP `.jsonl`) can be bulk-imported into a running viewer:

```bash
# Import all trace files from a directory
nemo oo import-traces ./traces/

# Import into a viewer running on a different host/port
nemo oo import-traces ./traces/ --endpoint http://host:5001

# Tag the import with a batch ID (auto-generated if omitted)
nemo oo import-traces ./traces/ --batch-id my-experiment-v2
```

Delete all traces from a batch:

```bash
nemo oo delete-traces --batch-id my-experiment-v2
```

## Trace Format

Traces are OTLP JSON. Each span includes a `nemo_oo_agents.viewer.plugin` attribute that tells the viewer which rendering plugin to use. See [docs/trace-plugin-convention.md](docs/trace-plugin-convention.md) for the full specification, including valid plugin values and the attributes each plugin reads.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/traces` | OTLP JSON ingest (accepts `ExportTraceServiceRequest`) |
| `GET` | `/api/traces` | List trace sessions (paginated, filterable by search/experiment/batch) |
| `GET` | `/api/trace?session_id=...` | Get all spans for a session |
| `DELETE` | `/api/traces/{session_id}` | Delete a trace session |
| `DELETE` | `/api/traces?batch_id=...` | Delete all traces in a batch |
