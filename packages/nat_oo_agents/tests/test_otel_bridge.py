# SPDX-License-Identifier: Apache-2.0
"""Tests for the OTel bridge, including the opt-in OTLP endpoint path.

The core #344 fix is that ``otlp_endpoint`` can now actually reach the
OTLP exporter branch. These tests verify both the argless path and that a
supplied endpoint is threaded through to ``OTLPSpanExporter``.
"""

from __future__ import annotations

import sys
import types

import pytest
from nat.plugins.nooa import otel_bridge


def test_setup_shared_tracer_no_endpoint_does_not_raise():
    # The default (opt-out) path: no OTLP export, must never raise.
    otel_bridge.setup_shared_tracer()
    otel_bridge.setup_shared_tracer(None)


def _ensure_sdk_provider():
    """Return an SDK TracerProvider that setup_shared_tracer will reuse."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    return provider


def test_otlp_endpoint_reaches_exporter(monkeypatch):
    pytest.importorskip("opentelemetry.sdk.trace")

    provider = _ensure_sdk_provider()

    # The real opentelemetry-exporter-otlp-proto-http package may not be
    # installed; inject a fake module so we can assert the endpoint is
    # threaded into OTLPSpanExporter regardless.
    captured: dict = {}

    class FakeOTLPSpanExporter:
        def __init__(self, endpoint=None):
            captured["endpoint"] = endpoint

    fake_mod = types.ModuleType("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    fake_mod.OTLPSpanExporter = FakeOTLPSpanExporter
    monkeypatch.setitem(
        sys.modules,
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        fake_mod,
    )

    added: list = []
    monkeypatch.setattr(provider, "add_span_processor", lambda p: added.append(p))

    endpoint = "http://localhost:4318/v1/traces"
    otel_bridge.setup_shared_tracer(endpoint)

    assert captured.get("endpoint") == endpoint
    assert len(added) == 1


def test_reuses_existing_sdk_provider(monkeypatch):
    provider = _ensure_sdk_provider()
    added: list = []
    monkeypatch.setattr(provider, "add_span_processor", lambda p: added.append(p))

    # No endpoint -> reuse provider, add no OTLP processor.
    otel_bridge.setup_shared_tracer()
    assert added == []
