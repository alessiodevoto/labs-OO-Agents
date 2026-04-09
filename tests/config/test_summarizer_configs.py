import pytest
from pydantic import ValidationError

from nemo_oo_agents.config.summarizer_config import MethodSummarizerConfig, TokenBudgetConfig


class TestTokenBudgetConfig:
    def test_defaults(self):
        c = TokenBudgetConfig()
        assert c.max_tokens == 100_000
        assert c.preserve_recent == 10
        assert c.target_chars == 1000

    def test_frozen(self):
        c = TokenBudgetConfig()
        with pytest.raises(ValidationError):
            c.max_tokens = 50_000

    def test_merge_with(self):
        base = TokenBudgetConfig()
        override = TokenBudgetConfig(max_tokens=80_000)
        merged = base.merge_with(override)
        assert merged.max_tokens == 80_000
        assert merged.preserve_recent == 10


class TestMethodSummarizerConfig:
    def test_defaults(self):
        c = MethodSummarizerConfig()
        assert c.min_events == 3
        assert c.exclude_root is True
        assert c.target_chars == 1000

    def test_frozen(self):
        c = MethodSummarizerConfig()
        with pytest.raises(ValidationError):
            c.min_events = 5

    def test_merge_with(self):
        base = MethodSummarizerConfig()
        override = MethodSummarizerConfig(min_events=5)
        merged = base.merge_with(override)
        assert merged.min_events == 5
        assert merged.exclude_root is True


class TestTokenBudgetConfigMergeError:
    def test_merge_with_empty_model_fields_set_raises(self):
        base = TokenBudgetConfig()
        other = TokenBudgetConfig.model_validate({})
        with pytest.raises(
            ValueError, match="merge_with\\(\\) received a config with no model_fields_set"
        ):
            base.merge_with(other)


class TestMethodSummarizerConfigMergeError:
    def test_merge_with_empty_model_fields_set_raises(self):
        base = MethodSummarizerConfig()
        other = MethodSummarizerConfig.model_validate({})
        with pytest.raises(
            ValueError, match="merge_with\\(\\) received a config with no model_fields_set"
        ):
            base.merge_with(other)
