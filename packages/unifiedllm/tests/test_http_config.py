import pytest
from pydantic import BaseModel, ValidationError

from unifiedllm.http_config import HttpConfig


def test_http_config_is_pydantic_model():
    assert issubclass(HttpConfig, BaseModel)


def test_http_config_defaults():
    c = HttpConfig()
    assert c.max_connections == 100
    assert c.max_keepalive_connections == 0
    assert c.keepalive_expiry == 0.0
    assert c.connect_timeout == 10.0
    assert c.read_timeout == 60.0
    assert c.write_timeout == 10.0
    assert c.pool_timeout == 10.0


def test_http_config_frozen():
    c = HttpConfig()
    with pytest.raises(ValidationError):
        c.connect_timeout = 5.0


def test_merge_with_overrides_only_explicit_fields():
    base = HttpConfig()
    override = HttpConfig(read_timeout=30.0)
    merged = base.merge_with(override)
    assert merged.read_timeout == 30.0
    assert merged.connect_timeout == 10.0  # not overridden


def test_completion_client_accepts_http_config():
    from unifiedllm import CompletionClient, HttpConfig

    # Should not raise — just verifies the constructor signature
    client = CompletionClient("gpt-4o-mini", http_config=HttpConfig(connect_timeout=5.0))
    assert client._http_config.connect_timeout == 5.0
