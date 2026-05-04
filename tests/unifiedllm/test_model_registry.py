# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for YAML-based model registry."""

import textwrap

import pytest

from nemo_oo_agents.unifiedllm import MODELS, CompletionClient, get_llm_client, reload_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the registry cache before and after each test."""
    reload_registry()
    yield
    reload_registry()


class TestEmptyDefaultRegistry:
    """The registry ships empty; public models rely on litellm's built-in routing."""

    def test_registry_empty_by_default(self):
        """Without UNIFIEDLLM_CONFIG or CWD config, the registry is empty."""
        assert MODELS == {}


class TestGetLlmClient:
    """Tests for get_llm_client() function."""

    def test_returns_completion_client(self):
        """get_llm_client should return a CompletionClient."""
        llm = get_llm_client("gpt-4o-mini")
        assert isinstance(llm, CompletionClient)

    def test_unknown_model_passes_through(self):
        """Unknown model names should pass through to CompletionClient directly."""
        llm = get_llm_client("some-unknown-model-xyz")
        assert llm.model == "some-unknown-model-xyz"

    def test_registry_model_uses_model_name(self, tmp_path, monkeypatch):
        """Registry model should use the model_name from config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              my-alias:
                model_name: openai/my-org/my-model
        """)
        )
        reload_registry()
        llm = get_llm_client("my-alias")
        assert llm.model == "openai/my-org/my-model"

    def test_overrides_take_precedence(self):
        """User overrides should take precedence over registry defaults."""
        llm = get_llm_client("gpt-4o-mini", temperature=0.9, max_tokens=100)
        assert llm.config.get("temperature") == 0.9
        assert llm.config.get("max_tokens") == 100

    def test_drop_params_default_true(self):
        """drop_params should default to True."""
        llm = get_llm_client("gpt-4o-mini")
        assert llm.config.get("drop_params") is True

    def test_registry_hit_logs_info(self, tmp_path, monkeypatch, caplog):
        """Registry hits should be logged at INFO level for user visibility."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              my-alias:
                model_name: openai/my-org/my-model
                api_base: https://example.com/v1
        """)
        )
        reload_registry()

        import logging

        with caplog.at_level(logging.INFO, logger="nemo_oo_agents.unifiedllm.registry"):
            get_llm_client("my-alias")
        assert "registry hit" in caplog.text.lower()


class TestApiKeyHandling:
    """Tests for API key environment variable handling."""

    def test_api_key_from_env(self, tmp_path, monkeypatch):
        """API key should be read from the env var specified in config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              test-keyed-model:
                model_name: test-model
                api_key_env: MY_TEST_KEY
        """)
        )
        monkeypatch.setenv("MY_TEST_KEY", "test-key-abc")
        reload_registry()
        llm = get_llm_client("test-keyed-model")
        assert llm.config.get("api_key") == "test-key-abc"

    def test_missing_api_key_handled_gracefully(self, tmp_path, monkeypatch):
        """Missing API key should not crash get_llm_client."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              test-keyed-model:
                model_name: test-model
                api_key_env: NONEXISTENT_KEY
        """)
        )
        monkeypatch.delenv("NONEXISTENT_KEY", raising=False)
        reload_registry()
        llm = get_llm_client("test-keyed-model")
        assert llm.config.get("api_key") is None

    def test_unknown_model_no_api_key_env(self):
        """Unknown models should not attempt to read an env var."""
        llm = get_llm_client("totally-made-up-model")
        assert llm.config.get("api_key") is None


class TestConfigLayering:
    """Tests for YAML config file layering."""

    def test_cwd_override(self, tmp_path, monkeypatch):
        """llm_config.yaml in CWD should populate the registry."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              my-custom-model:
                model_name: openai/my-custom-model
                api_base: https://my-endpoint.example.com/v1
                api_key_env: MY_API_KEY
                context_window: 128000
        """)
        )

        registry = reload_registry()
        assert "my-custom-model" in registry
        assert registry["my-custom-model"]["api_base"] == "https://my-endpoint.example.com/v1"

    def test_null_removes_model(self, tmp_path, monkeypatch):
        """Setting a model to null in a later layer should remove it."""
        env_config = tmp_path / "env.yaml"
        env_config.write_text(
            textwrap.dedent("""\
            models:
              removable:
                model_name: removable
        """)
        )
        monkeypatch.setenv("UNIFIEDLLM_CONFIG", str(env_config))

        monkeypatch.chdir(tmp_path)
        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              removable: null
        """)
        )

        registry = reload_registry()
        assert "removable" not in registry

    def test_env_var_config(self, tmp_path, monkeypatch):
        """UNIFIEDLLM_CONFIG env var should add extra config files."""
        extra_config = tmp_path / "extra.yaml"
        extra_config.write_text(
            textwrap.dedent("""\
            models:
              extra-model:
                model_name: openai/extra-model
                api_base: https://extra.example.com/v1
                context_window: 64000
        """)
        )
        monkeypatch.setenv("UNIFIEDLLM_CONFIG", str(extra_config))

        registry = reload_registry()
        assert "extra-model" in registry
        assert registry["extra-model"]["context_window"] == 64000

    def test_cwd_wins_over_env(self, tmp_path, monkeypatch):
        """CWD config should take precedence over UNIFIEDLLM_CONFIG."""
        monkeypatch.chdir(tmp_path)

        env_config = tmp_path / "env_config.yaml"
        env_config.write_text(
            textwrap.dedent("""\
            models:
              test-model:
                model_name: from-env
                context_window: 1000
        """)
        )
        monkeypatch.setenv("UNIFIEDLLM_CONFIG", str(env_config))

        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              test-model:
                model_name: from-cwd
                context_window: 2000
        """)
        )

        registry = reload_registry()
        assert registry["test-model"]["model_name"] == "from-cwd"
        assert registry["test-model"]["context_window"] == 2000

    def test_multiple_env_configs(self, tmp_path, monkeypatch):
        """UNIFIEDLLM_CONFIG should support comma-separated paths."""
        config1 = tmp_path / "config1.yaml"
        config1.write_text(
            textwrap.dedent("""\
            models:
              model-a:
                model_name: model-a
        """)
        )

        config2 = tmp_path / "config2.yaml"
        config2.write_text(
            textwrap.dedent("""\
            models:
              model-b:
                model_name: model-b
        """)
        )

        monkeypatch.setenv("UNIFIEDLLM_CONFIG", f"{config1},{config2}")
        registry = reload_registry()
        assert "model-a" in registry
        assert "model-b" in registry

    def test_missing_env_config_warned(self, monkeypatch, caplog):
        """Non-existent paths in UNIFIEDLLM_CONFIG should log a warning."""
        monkeypatch.setenv("UNIFIEDLLM_CONFIG", "/nonexistent/path.yaml")
        import logging

        with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.unifiedllm.registry"):
            reload_registry()
        assert "does not exist" in caplog.text


class TestReloadRegistry:
    """Tests for reload_registry() function."""

    def test_reload_picks_up_changes(self, tmp_path, monkeypatch):
        """reload_registry() should pick up newly created config files."""
        monkeypatch.chdir(tmp_path)

        registry1 = reload_registry()
        assert "dynamic-model" not in registry1

        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              dynamic-model:
                model_name: dynamic-model
        """)
        )

        registry2 = reload_registry()
        assert "dynamic-model" in registry2

    def test_reload_updates_in_place(self, tmp_path, monkeypatch):
        """Callers holding a reference to MODELS should see reloaded contents."""
        monkeypatch.chdir(tmp_path)
        original_ref = MODELS

        (tmp_path / "llm_config.yaml").write_text(
            textwrap.dedent("""\
            models:
              new-model:
                model_name: new-model
        """)
        )
        reload_registry()

        # Same dict object, new contents
        assert MODELS is original_ref
        assert "new-model" in original_ref
