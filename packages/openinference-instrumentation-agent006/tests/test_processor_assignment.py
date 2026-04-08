"""Tests for span processor assignment in _add_exporters().

Pins the contract that:
  - OtlpJsonHttpExporter  → BatchSpanProcessor  (non-blocking, batched)
  - OtlpJsonFileExporter  → SimpleSpanProcessor (in-process I/O, synchronous)
  - ConsoleSpanExporter   → SimpleSpanProcessor (in-process I/O, synchronous)
"""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from openinference_instrumentation_agent006 import _add_exporters
from openinference_instrumentation_agent006._otlp_file_exporter import OtlpJsonFileExporter
from openinference_instrumentation_agent006._otlp_http_exporter import OtlpJsonHttpExporter


def _processors(provider: TracerProvider):
    """Return the list of span processors attached to *provider*."""
    return list(provider._active_span_processor._span_processors)


class TestProcessorAssignment:
    def test_http_exporter_gets_batch_processor(self):
        """OtlpJsonHttpExporter must use BatchSpanProcessor (non-blocking)."""
        provider = TracerProvider()
        exp = OtlpJsonHttpExporter()
        _add_exporters(provider, [exp])

        procs = _processors(provider)
        assert len(procs) == 1
        assert isinstance(procs[0], BatchSpanProcessor), (
            f"OtlpJsonHttpExporter must be wrapped in BatchSpanProcessor, got {type(procs[0]).__name__}"
        )

    def test_file_exporter_gets_simple_processor(self, tmp_path):
        """OtlpJsonFileExporter must use SimpleSpanProcessor (in-process I/O)."""
        provider = TracerProvider()
        exp = OtlpJsonFileExporter(tmp_path)
        _add_exporters(provider, [exp])

        procs = _processors(provider)
        assert len(procs) == 1
        assert isinstance(procs[0], SimpleSpanProcessor)
        assert procs[0].span_exporter is exp

    def test_console_exporter_gets_simple_processor(self):
        """ConsoleSpanExporter must use SimpleSpanProcessor."""
        provider = TracerProvider()
        exp = ConsoleSpanExporter()
        _add_exporters(provider, [exp])

        procs = _processors(provider)
        assert len(procs) == 1
        assert isinstance(procs[0], SimpleSpanProcessor)
        assert procs[0].span_exporter is exp

    def test_mixed_exporters_get_correct_processors(self, tmp_path):
        """A mix of exporter types each gets its appropriate processor."""
        provider = TracerProvider()
        http_exp = OtlpJsonHttpExporter()
        file_exp = OtlpJsonFileExporter(tmp_path)
        _add_exporters(provider, [http_exp, file_exp])

        procs = _processors(provider)
        assert len(procs) == 2

        batch_procs = [p for p in procs if isinstance(p, BatchSpanProcessor)]
        simple_procs = [p for p in procs if isinstance(p, SimpleSpanProcessor)]
        assert len(batch_procs) == 1
        assert len(simple_procs) == 1
        assert simple_procs[0].span_exporter is file_exp
