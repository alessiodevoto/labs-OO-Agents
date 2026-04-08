"""Tests for model registry."""

from unifiedllm import MODELS, get_llm_client


class TestGetLlmClientBasics:
    """Basic tests for get_llm_client function."""

    def test_returns_completion_client(self):
        """get_llm_client should return a CompletionClient."""
        from unifiedllm import CompletionClient

        llm = get_llm_client("azure/openai/gpt-5-mini")
        assert isinstance(llm, CompletionClient)

    def test_context_window_accessible(self):
        """get_llm_client result should have context_window property."""
        llm = get_llm_client("azure/openai/gpt-5-mini")
        assert llm.context_window is not None
        assert llm.context_window > 0


class TestModelsRegistry:
    """Tests for the MODELS registry dict."""

    def test_has_entries(self):
        """MODELS should have model configurations."""
        assert len(MODELS) > 0
        assert "azure/openai/gpt-5-mini" in MODELS

    def test_all_models_have_context_window(self):
        """All models should have context_window defined."""
        for model_name, config in MODELS.items():
            assert "context_window" in config, f"{model_name} missing context_window"
            assert config["context_window"] > 0, f"{model_name} has invalid context_window"

    def test_context_windows_reasonable(self):
        """Context windows should be within reasonable bounds."""
        for model_name, config in MODELS.items():
            cw = config["context_window"]
            # Minimum 1K, maximum 2M (for Gemini models)
            assert 1_000 <= cw <= 2_000_000, f"{model_name} has unreasonable context_window: {cw}"


class TestParameterOverrides:
    """Tests for parameter override behavior."""

    def test_basic_overrides(self):
        """get_llm_client should accept parameter overrides."""
        llm = get_llm_client("azure/openai/gpt-5-mini", max_tokens=100, temperature=0.5)
        assert llm.config.get("max_tokens") == 100
        assert llm.config.get("temperature") == 0.5

    def test_override_takes_precedence_over_config(self):
        """User overrides should take precedence over model config defaults."""
        # Nemotron-3-Nano has temperature=0.7 in config
        llm = get_llm_client(
            "nvidia/nvidia/Nemotron-3-Nano-30B-A3B",
            temperature=0.9,
        )
        assert llm.config.get("temperature") == 0.9

    def test_config_defaults_used_when_no_override(self):
        """Model config defaults should be used when no override provided."""
        # Nemotron-3-Nano has temperature=0.7 and top_p=0.7 in config
        llm = get_llm_client("nvidia/nvidia/Nemotron-3-Nano-30B-A3B")
        assert llm.config.get("temperature") == 0.7
        assert llm.config.get("top_p") == 0.7


class TestApiKeyHandling:
    """Tests for API key environment variable handling."""

    def test_api_key_from_environment(self, monkeypatch):
        """API key should be read from environment variable."""
        test_key = "test-api-key-12345"
        monkeypatch.setenv("NVIDIA_INTERNAL_API_KEY", test_key)

        llm = get_llm_client("azure/openai/gpt-5-mini")
        assert llm.config.get("api_key") == test_key

    def test_api_key_missing_gracefully_handled(self, monkeypatch):
        """Missing API key should not crash get_llm_client."""
        # Ensure the env var is not set
        monkeypatch.delenv("NVIDIA_INTERNAL_API_KEY", raising=False)

        # Should not raise
        llm = get_llm_client("azure/openai/gpt-5-mini")
        # api_key should be None or not in config
        assert llm.config.get("api_key") is None

    def test_model_specific_api_key_env(self, monkeypatch):
        """Models can specify different API key environment variables."""
        # Claude reasoning models use the standard NVIDIA_INTERNAL_API_KEY
        test_key = "internal-api-key-xyz"
        monkeypatch.setenv("NVIDIA_INTERNAL_API_KEY", test_key)

        llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1-reasoning-high")
        assert llm.config.get("api_key") == test_key


class TestUnknownModel:
    """Tests for handling unknown models."""

    def test_unknown_model_still_works(self):
        """Unknown model names should still create a client with defaults."""
        # This allows using models not in the registry
        llm = get_llm_client("some/unknown/model")
        # Should use default endpoint
        assert llm.config.get("api_base") is not None
