"""Tests for TruncationConfig."""

from nemo_oo_agents.config.truncation_config import (
    DEFAULT_TRUNCATION_CONFIG,
    TruncationConfig,
)


class TestTruncationConfig:
    """Tests for TruncationConfig dataclass."""

    def test_default_values(self):
        """Default config should have expected values."""
        config = TruncationConfig()

        assert config.max_block_chars == 20_000
        assert config.max_stdout_chars == 50_000
        assert config.max_stderr_chars == 20_000
        assert config.max_pprint_elements == 50
        assert config.max_pprint_string == 500
        assert config.max_pprint_depth == 4

    def test_custom_values(self):
        """Custom values should override defaults."""
        config = TruncationConfig(
            max_block_chars=10_000,
            max_stdout_chars=100_000,
            max_stderr_chars=30_000,
            max_pprint_elements=100,
            max_pprint_string=1000,
            max_pprint_depth=5,
        )

        assert config.max_block_chars == 10_000
        assert config.max_stdout_chars == 100_000
        assert config.max_stderr_chars == 30_000
        assert config.max_pprint_elements == 100
        assert config.max_pprint_string == 1000
        assert config.max_pprint_depth == 5

    def test_none_pprint_limits(self):
        """pprint limits can be None for unlimited."""
        config = TruncationConfig(
            max_pprint_elements=None,
            max_pprint_string=None,
            max_pprint_depth=None,
        )

        assert config.max_pprint_elements is None
        assert config.max_pprint_string is None
        assert config.max_pprint_depth is None

    def test_merge_with_none(self):
        """Merging with None should return self."""
        config = TruncationConfig(max_stdout_chars=100_000)
        merged = config.merge_with(None)

        assert merged.max_stdout_chars == 100_000
        assert merged is config  # Returns self when merging with None

    def test_merge_with_overrides(self):
        """Merging should override with explicitly-set values."""
        base = TruncationConfig(
            max_stdout_chars=50_000,
            max_pprint_elements=50,
        )
        override = TruncationConfig(
            max_stdout_chars=100_000,
            max_pprint_elements=100,
        )

        merged = base.merge_with(override)

        assert merged.max_stdout_chars == 100_000
        assert merged.max_pprint_elements == 100

    def test_merge_with_empty_fields_set_raises(self):
        """merge_with(TruncationConfig()) raises since no fields are explicitly set."""
        import pytest

        base = TruncationConfig(max_stdout_chars=100_000, max_stderr_chars=40_000)
        override = TruncationConfig()  # All defaults — model_fields_set is empty

        with pytest.raises(ValueError, match="merge_with"):
            base.merge_with(override)

    def test_merge_creates_new_instance(self):
        """Merge should create a new config instance."""
        config1 = TruncationConfig(max_stdout_chars=100_000)
        config2 = TruncationConfig(max_stderr_chars=30_000)

        merged = config1.merge_with(config2)

        # Should be different object
        assert merged is not config1
        assert merged is not config2

    def test_default_truncation_config_instance(self):
        """DEFAULT_TRUNCATION_CONFIG should exist and have defaults."""
        assert DEFAULT_TRUNCATION_CONFIG.max_block_chars == 20_000
        assert DEFAULT_TRUNCATION_CONFIG.max_stdout_chars == 50_000
        assert DEFAULT_TRUNCATION_CONFIG.max_stderr_chars == 20_000
        assert DEFAULT_TRUNCATION_CONFIG.max_pprint_elements == 50

    def test_all_limits_positive(self):
        """All limits should be positive integers."""
        config = TruncationConfig()

        assert config.max_block_chars > 0
        assert config.max_stdout_chars > 0
        assert config.max_stderr_chars > 0

    def test_equality(self):
        """Two configs with same values should be equal."""
        config1 = TruncationConfig(max_stdout_chars=100_000)
        config2 = TruncationConfig(max_stdout_chars=100_000)

        assert config1 == config2

    def test_inequality(self):
        """Two configs with different values should not be equal."""
        config1 = TruncationConfig(max_stdout_chars=100_000)
        config2 = TruncationConfig(max_stdout_chars=50_000)

        assert config1 != config2


class TestMergeSemantics:
    """Tests for merge_with() merge semantics using model_fields_set."""

    def test_three_way_merge(self):
        """Three-way merge with non-default values."""
        class_level = TruncationConfig(max_stdout_chars=100_000)
        instance_level = TruncationConfig(max_stdout_chars=100_000, max_pprint_elements=100)
        method_level = TruncationConfig(
            max_stdout_chars=100_000, max_pprint_elements=100, max_pprint_depth=10
        )

        merged = class_level.merge_with(instance_level).merge_with(method_level)

        assert merged.max_stdout_chars == 100_000
        assert merged.max_pprint_elements == 100
        assert merged.max_pprint_depth == 10

    def test_later_overrides_earlier(self):
        """Later configs with explicit values override earlier ones."""
        config1 = TruncationConfig(max_stdout_chars=100_000, max_pprint_elements=100)
        config2 = TruncationConfig(max_stdout_chars=200_000, max_pprint_elements=100)

        merged = config1.merge_with(config2)

        assert merged.max_stdout_chars == 200_000
        assert merged.max_pprint_elements == 100

    def test_partial_override(self):
        """Partial override should only change specified fields."""
        base = TruncationConfig(
            max_block_chars=10_000,
            max_stdout_chars=100_000,
            max_stderr_chars=30_000,
            max_pprint_elements=100,
        )
        override = TruncationConfig(max_pprint_elements=200)

        merged = base.merge_with(override)

        assert merged.max_block_chars == 10_000
        assert merged.max_stdout_chars == 100_000
        assert merged.max_stderr_chars == 30_000
        assert merged.max_pprint_elements == 200

    def test_explicit_none_overrides(self):
        """Explicitly passing None overrides the base value."""
        base = TruncationConfig(max_pprint_elements=100)
        override = TruncationConfig(max_pprint_elements=None)

        merged = base.merge_with(override)

        assert merged.max_pprint_elements is None

    def test_unset_fields_dont_override(self):
        """Fields not passed to __init__ don't override base values."""
        base = TruncationConfig(max_pprint_elements=100, max_stdout_chars=80_000)
        override = TruncationConfig(max_stdout_chars=90_000)

        merged = base.merge_with(override)

        assert merged.max_pprint_elements == 100
        assert merged.max_stdout_chars == 90_000

    def test_explicit_default_value_overrides(self):
        """Explicitly setting a field to the default value still overrides."""
        base = TruncationConfig(max_block_chars=10_000)
        override = TruncationConfig(max_block_chars=20_000)

        merged = base.merge_with(override)

        assert merged.max_block_chars == 20_000
