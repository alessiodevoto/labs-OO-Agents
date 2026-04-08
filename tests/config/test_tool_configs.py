import pytest
from pydantic import ValidationError

from nemo_oo_agents.config.tool_configs import BashConfig, WebSearchConfig


class TestBashConfig:
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
