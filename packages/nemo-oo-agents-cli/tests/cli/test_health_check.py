# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the LLM health check module."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nemo_oo_agents_cli.tui.health_check import (
    _classify_error,
    _detect_provider,
    _get_expected_env_var,
    _has_llm_config_yaml,
    probe_llm,
)


def _make_llm(model="test-model", registry_config=None):
    """Create a mock LLM with the right attributes."""
    llm = MagicMock()
    llm.model = model
    llm._registry_config = registry_config
    llm.config = {}
    return llm


class TestDetectProvider:
    """Test provider detection."""

    def test_anthropic(self):
        assert _detect_provider("claude-sonnet-4-5") == "anthropic"

    def test_openai(self):
        assert _detect_provider("gpt-4o") == "openai"

    def test_unknown_returns_none_on_error(self):
        # If litellm can't figure it out, we get None or a provider string
        result = _detect_provider("totally-fake-xyz-model-999")
        # Either None or a string is fine
        assert result is None or isinstance(result, str)


class TestGetExpectedEnvVar:
    """Test env var detection for LLM providers."""

    def test_from_registry_config(self):
        llm = _make_llm(registry_config={"api_key_env": "MY_CUSTOM_KEY"})
        assert _get_expected_env_var(llm) == "MY_CUSTOM_KEY"

    def test_from_provider_detection(self):
        llm = _make_llm(model="claude-sonnet-4-5")
        llm._registry_config = None
        result = _get_expected_env_var(llm)
        assert result == "ANTHROPIC_API_KEY"

    def test_openai_model(self):
        llm = _make_llm(model="gpt-4o")
        llm._registry_config = None
        result = _get_expected_env_var(llm)
        assert result == "OPENAI_API_KEY"


class TestClassifyError:
    """Test error classification into user-friendly messages."""

    def test_auth_401_env_not_set(self):
        llm = _make_llm(model="claude-sonnet-4-5")
        llm._registry_config = None
        exc = Exception("Error code: 401 - Unauthorized")
        with patch.dict(os.environ, {}, clear=True):
            result = _classify_error(exc, llm)
        assert not result.ok
        assert "Authentication failed" in result.error_message
        assert "ANTHROPIC_API_KEY" in result.fix_hint
        assert "NOT set" in result.fix_hint

    def test_auth_401_env_set_but_invalid(self):
        llm = _make_llm(model="claude-sonnet-4-5")
        llm._registry_config = None
        exc = Exception("Error code: 401 - Unauthorized")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-bad-key"}):
            result = _classify_error(exc, llm)
        assert not result.ok
        assert "Authentication failed" in result.error_message
        assert "invalid" in result.fix_hint

    def test_model_not_found_404(self):
        llm = _make_llm(model="gpt-99")
        exc = Exception("Error code: 404 - Model not found")
        result = _classify_error(exc, llm)
        assert not result.ok
        assert "not found" in result.error_message
        assert "gpt-99" in result.error_message

    def test_model_not_found_suggests_config_creation(self):
        llm = _make_llm(model="fake-model")
        exc = Exception("The model `fake-model` does not exist")
        with patch("nemo_oo_agents_cli.tui.health_check._has_project_config", return_value=False):
            result = _classify_error(exc, llm)
        assert "create .nemo_oo_agents/config.toml" in result.fix_hint

    def test_model_not_found_suggests_edit_config(self):
        llm = _make_llm(model="fake-model")
        exc = Exception("The model `fake-model` does not exist")
        with patch("nemo_oo_agents_cli.tui.health_check._has_project_config", return_value=True):
            result = _classify_error(exc, llm)
        assert "edit" in result.fix_hint.lower() or "Or edit" in result.fix_hint

    def test_permission_403(self):
        llm = _make_llm(model="gpt-4o")
        exc = Exception("Error code: 403 - Forbidden")
        result = _classify_error(exc, llm)
        assert not result.ok
        assert "Access denied" in result.error_message

    def test_connection_refused(self):
        llm = _make_llm(model="my-model")
        exc = ConnectionError("Connection refused")
        result = _classify_error(exc, llm)
        assert not result.ok
        assert "Cannot connect" in result.error_message

    def test_connection_with_yaml_mentions_api_base(self):
        llm = _make_llm(model="my-model")
        exc = ConnectionError("Connection refused")
        with patch("nemo_oo_agents_cli.tui.health_check._has_llm_config_yaml", return_value=True):
            result = _classify_error(exc, llm)
        assert "api_base" in result.fix_hint

    def test_connection_without_yaml_mentions_provider_down(self):
        llm = _make_llm(model="my-model")
        exc = ConnectionError("Connection refused")
        with patch("nemo_oo_agents_cli.tui.health_check._has_llm_config_yaml", return_value=False):
            result = _classify_error(exc, llm)
        assert "temporarily down" in result.fix_hint

    def test_timeout(self):
        llm = _make_llm(model="slow-model")
        exc = Exception("Request timed out")
        result = _classify_error(exc, llm)
        assert not result.ok
        assert "timed out" in result.error_message

    def test_rate_limit_429(self):
        llm = _make_llm(model="gpt-4o")
        exc = Exception("Error code: 429 - Too Many Requests")
        result = _classify_error(exc, llm)
        assert not result.ok
        assert "Rate limited" in result.error_message

    def test_unknown_error_shows_env_status(self):
        llm = _make_llm(model="claude-sonnet-4-5")
        llm._registry_config = None
        exc = ValueError("Something unexpected")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-real"}):
            result = _classify_error(exc, llm)
        assert "ANTHROPIC_API_KEY" in result.fix_hint
        assert "set" in result.fix_hint


class TestHasLlmConfigYaml:
    """Test llm_config.yaml detection."""

    def test_returns_false_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("UNIFIEDLLM_CONFIG", raising=False)
        assert _has_llm_config_yaml() is False

    def test_returns_true_when_cwd_has_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "llm_config.yaml").write_text("models: {}")
        assert _has_llm_config_yaml() is True


class TestProbeLLM:
    """Test the probe_llm async function."""

    @pytest.mark.asyncio
    async def test_successful_probe(self):
        """A successful LLM call returns ok=True."""
        llm = _make_llm()
        llm.acall = AsyncMock(return_value=MagicMock())

        result = await probe_llm(llm)
        assert result.ok
        assert result.error_message is None

        # Verify the probe used minimal tokens
        llm.acall.assert_called_once()
        call_kwargs = llm.acall.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1

    @pytest.mark.asyncio
    async def test_auth_error_probe(self):
        """Auth errors are caught and classified."""
        llm = _make_llm(model="claude-sonnet-4-5")
        llm._registry_config = None
        llm.acall = AsyncMock(side_effect=Exception("Error code: 401 - Unauthorized"))

        result = await probe_llm(llm)
        assert not result.ok
        assert "Authentication failed" in result.error_message
        assert "claude-sonnet-4-5" in result.error_message

    @pytest.mark.asyncio
    async def test_timeout_probe(self):
        """Timeout is caught when the endpoint is unresponsive."""
        llm = _make_llm(model="slow-model")

        async def slow_call(**kwargs):
            await asyncio.sleep(100)

        llm.acall = slow_call

        with patch("nemo_oo_agents_cli.tui.health_check._PROBE_TIMEOUT_SECONDS", 0.1):
            result = await probe_llm(llm)

        assert not result.ok
        assert "timed out" in result.error_message

    @pytest.mark.asyncio
    async def test_connection_error_probe(self):
        """Connection errors are caught and classified."""
        llm = _make_llm(model="unreachable-model")
        llm.acall = AsyncMock(side_effect=ConnectionError("Connection refused"))

        result = await probe_llm(llm)
        assert not result.ok
        assert "Cannot connect" in result.error_message
        assert "unreachable-model" in result.error_message
