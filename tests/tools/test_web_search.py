"""Tests for WebSearchTool config wiring."""

import pytest

from agent006.config.tool_configs import WebSearchConfig
from agent006.tools.web_search_tool import WebSearchTool


def test_web_search_tool_defaults():
    """WebSearchTool initializes with config defaults."""
    tool = WebSearchTool()
    assert tool.config.default_num_results == 5
    assert tool.config.request_timeout == 10.0


def test_web_search_tool_accepts_config():
    """WebSearchTool accepts a WebSearchConfig object."""
    tool = WebSearchTool(config=WebSearchConfig(request_timeout=30.0))
    assert tool.config.request_timeout == 30.0


def test_web_search_tool_uses_config_timeout():
    """Config request_timeout replaces hardcoded 10."""
    tool = WebSearchTool(config=WebSearchConfig(request_timeout=5.0))
    assert tool.config.request_timeout == 5.0


def test_web_search_tool_rejects_flat_kwarg():
    """WebSearchTool raises TypeError on flat kwargs."""
    with pytest.raises(TypeError):
        WebSearchTool(default_num_results=10)


def test_fetch_url_uses_config_timeout_as_default():
    """fetch_url() default timeout should come from config.request_timeout, not hardcoded 10."""
    from unittest.mock import MagicMock, patch

    tool = WebSearchTool(config=WebSearchConfig(request_timeout=42.0))

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"content"
        return mock_response

    with patch("urllib.request.urlopen", fake_urlopen):
        tool.fetch_url("http://example.com")

    assert captured["timeout"] == 42.0, (
        f"Expected fetch_url to use config.request_timeout=42.0 but got {captured['timeout']}"
    )


def test_fetch_url_text_uses_config_timeout_as_default():
    """fetch_url_text() default timeout should come from config.request_timeout."""
    from unittest.mock import MagicMock, patch

    tool = WebSearchTool(config=WebSearchConfig(request_timeout=42.0))

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"<p>hello</p>"
        return mock_response

    with patch("urllib.request.urlopen", fake_urlopen):
        tool.fetch_url_text("http://example.com")

    assert captured["timeout"] == 42.0
