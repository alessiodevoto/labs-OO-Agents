# SPDX-License-Identifier: Apache-2.0
"""Tests for the NeMo OO Agents NAT wrapper config and tracing wiring.

These require the NAT runtime (nvidia-nat-core); they are skipped if it is
not importable. The key #344 assertion is that ``config.otlp_endpoint`` is
threaded through ``register()`` into ``setup_shared_tracer(...)``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nat.builder.function")

from nat.plugins.nooa.nooa_wrapper import (  # noqa: E402
    NemoOOAgentsWrapperConfig,
    register,
)

# ---------------------------------------------------------------------------
# Config field
# ---------------------------------------------------------------------------


def test_config_declares_otlp_endpoint_field():
    assert "otlp_endpoint" in NemoOOAgentsWrapperConfig.model_fields


def test_config_otlp_endpoint_defaults_to_none():
    cfg = NemoOOAgentsWrapperConfig(agent="mod.py:Agent", method="chat")
    assert cfg.otlp_endpoint is None


def test_config_accepts_otlp_endpoint():
    cfg = NemoOOAgentsWrapperConfig(
        agent="mod.py:Agent",
        method="chat",
        otlp_endpoint="http://collector:4318/v1/traces",
    )
    assert cfg.otlp_endpoint == "http://collector:4318/v1/traces"


def test_config_still_forbids_unknown_keys():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        NemoOOAgentsWrapperConfig(agent="mod.py:Agent", method="chat", bogus="x")


# ---------------------------------------------------------------------------
# register() threads otlp_endpoint into setup_shared_tracer
# ---------------------------------------------------------------------------


def _write_agent_module(tmp_path):
    mod = tmp_path / "agent_mod.py"
    mod.write_text(
        "class MyAgent:\n"
        "    def __init__(self, **kwargs):\n"
        "        pass\n"
        "    async def chat(self, message):\n"
        "        return 'ok'\n"
    )
    return mod


class _DummyBuilder:
    """Unused when the config declares no llm_name and no tools."""


@pytest.mark.asyncio
async def test_register_threads_otlp_endpoint_to_setup_shared_tracer(tmp_path, monkeypatch):
    mod = _write_agent_module(tmp_path)

    # Avoid real tracing side effects.
    import nooa.tracing as tracing

    monkeypatch.setattr(tracing, "enable_tracing", lambda **kwargs: None)

    calls: list = []
    import nat.plugins.nooa.otel_bridge as ob

    monkeypatch.setattr(ob, "setup_shared_tracer", lambda endpoint=None: calls.append(endpoint))

    cfg = NemoOOAgentsWrapperConfig(
        agent=f"{mod}:MyAgent",
        method="chat",
        otlp_endpoint="http://collector:4318/v1/traces",
        enable_tracing=True,
    )

    async with register(cfg, _DummyBuilder()) as wrapper_fn:
        assert wrapper_fn is not None

    assert calls == ["http://collector:4318/v1/traces"]


@pytest.mark.asyncio
async def test_register_passes_none_endpoint_by_default(tmp_path, monkeypatch):
    mod = _write_agent_module(tmp_path)

    import nooa.tracing as tracing

    monkeypatch.setattr(tracing, "enable_tracing", lambda **kwargs: None)

    calls: list = []
    import nat.plugins.nooa.otel_bridge as ob

    monkeypatch.setattr(ob, "setup_shared_tracer", lambda endpoint=None: calls.append(endpoint))

    cfg = NemoOOAgentsWrapperConfig(
        agent=f"{mod}:MyAgent",
        method="chat",
        enable_tracing=True,
    )

    async with register(cfg, _DummyBuilder()) as wrapper_fn:
        assert wrapper_fn is not None

    assert calls == [None]
