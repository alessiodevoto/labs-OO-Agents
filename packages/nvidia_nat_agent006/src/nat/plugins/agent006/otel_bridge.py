# SPDX-License-Identifier: Apache-2.0
"""OTel bridge: shared TracerProvider for dual OTLP + JSONL export.

Sets up a global OpenTelemetry TracerProvider before Agent006 agent
instantiation. When Agent006's enable_tracing() is called later, it
detects the existing provider and adds its JSONL processor alongside
any OTLP exporters already configured.

Result: Agent006 spans flow to both OTLP (NAT's Phoenix/Jaeger)
and JSONL (Agent006's trace viewer).
"""

import logging

logger = logging.getLogger(__name__)


def setup_shared_tracer(otlp_endpoint: str | None = None) -> None:
    """Set up a shared global TracerProvider.

    If an OTLP endpoint is provided, configures an OTLP exporter.
    If a TracerProvider already exists (e.g., from NAT), reuses it.

    Agent006's enable_tracing() will detect this provider and add its
    JSONL processor alongside, enabling dual export.

    Args:
        otlp_endpoint: Optional OTLP HTTP endpoint (e.g., "http://localhost:4318/v1/traces")
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        existing_provider = trace.get_tracer_provider()

        # Check if an SDK TracerProvider already exists
        if hasattr(existing_provider, "add_span_processor"):
            logger.info("Existing OTel TracerProvider found -- Agent006 will piggyback on it")
            provider = existing_provider
        else:
            # Create a new TracerProvider
            provider = TracerProvider()
            trace.set_tracer_provider(provider)
            logger.info("Created new OTel TracerProvider for Agent006 bridge")

        # Add OTLP exporter if endpoint is provided
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
                from opentelemetry.sdk.trace.export import SimpleSpanProcessor

                otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(SimpleSpanProcessor(otlp_exporter))
                logger.info("Added OTLP exporter to TracerProvider: %s", otlp_endpoint)
            except ImportError:
                logger.warning(
                    "opentelemetry-exporter-otlp-proto-http not installed. OTLP export disabled."
                )

    except ImportError:
        logger.debug("OpenTelemetry SDK not installed. OTel bridge disabled.")
