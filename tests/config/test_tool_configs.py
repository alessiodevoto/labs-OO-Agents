import pytest
from pydantic import ValidationError

from nemo_oo_agents.config.tool_configs import BashConfig


class TestBashConfig:
    """Tests for BashConfig defaults, immutability, and merging."""

    def test_defaults(self):
        c = BashConfig()
        assert c.default_timeout == 30.0
        assert c.use_sandbox is False
        assert c.srt_settings is None
        assert c.srt_executable is None

    def test_frozen(self):
        c = BashConfig()
        with pytest.raises(ValidationError):
            c.default_timeout = 60.0

    def test_merge_with(self):
        base = BashConfig()
        override = BashConfig(default_timeout=60.0)
        merged = base.merge_with(override)
        assert merged.default_timeout == 60.0
        assert merged.use_sandbox is False


class TestWebSearchConfig:
    """Tests for WebSearchConfig defaults, immutability, and merging."""

    def test_defaults(self):
        c = WebSearchConfig()
        assert c.default_num_results == 5
        assert c.request_timeout == 10.0

    def test_frozen(self):
        c = WebSearchConfig()
        with pytest.raises(ValidationError):
            c.request_timeout = 30.0

    def test_merge_with(self):
        base = WebSearchConfig()
        override = WebSearchConfig(request_timeout=30.0)
        merged = base.merge_with(override)
        assert merged.request_timeout == 30.0
        assert merged.default_num_results == 5


@pytest.mark.parametrize("config_cls", [BashConfig, WebSearchConfig])
def test_merge_with_empty_model_fields_set_raises(config_cls):
    """merge_with() raises ValueError when the override has no fields explicitly set."""
    base = config_cls()
    other = config_cls.model_validate({})
    with pytest.raises(
        ValueError, match="merge_with\\(\\) received a config with no model_fields_set"
    ):
        base.merge_with(other)
