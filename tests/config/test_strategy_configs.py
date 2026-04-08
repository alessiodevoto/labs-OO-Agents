import pytest
from pydantic import BaseModel, ValidationError

from agent006.config import CodeActConfig, PredictConfig, ReflexionConfig


class TestCodeActConfig:
    def test_is_pydantic_model(self):
        assert issubclass(CodeActConfig, BaseModel)

    def test_defaults(self):
        c = CodeActConfig()
        assert c.max_iterations == 50
        assert c.max_retries == 3
        assert c.cell_timeout == 600.0
        assert c.max_tokens is None
        assert c.temperature is None
        assert c.top_p is None
        assert c.max_tool_calls is None

    def test_frozen(self):
        c = CodeActConfig()
        with pytest.raises(ValidationError):
            c.max_iterations = 5

    def test_merge_with(self):
        base = CodeActConfig()
        override = CodeActConfig(max_iterations=5, temperature=0.7)
        merged = base.merge_with(override)
        assert merged.max_iterations == 5
        assert merged.temperature == 0.7
        assert merged.max_retries == 3  # not overridden


class TestPredictConfig:
    def test_defaults(self):
        c = PredictConfig()
        assert c.max_retries == 10
        assert c.max_tokens is None
        assert c.temperature is None
        assert c.top_p is None
        assert c.max_error_chars == 1000

    def test_merge_with(self):
        base = PredictConfig()
        override = PredictConfig(max_retries=5)
        merged = base.merge_with(override)
        assert merged.max_retries == 5
        assert merged.max_error_chars == 1000


class TestReflexionConfig:
    def test_defaults(self):
        c = ReflexionConfig()
        assert c.max_iterations == 3
        assert c.max_tokens is None
        assert c.temperature is None
        assert c.top_p is None

    def test_merge_with(self):
        base = ReflexionConfig()
        override = ReflexionConfig(max_iterations=5)
        merged = base.merge_with(override)
        assert merged.max_iterations == 5


class TestStrategyWiring:
    """Tests that strategies accept config objects."""

    def test_predict_accepts_config(self):
        from agent006.strategies.predict import PredictStrategy

        s = PredictStrategy(config=PredictConfig(max_retries=5))
        assert s.config.max_retries == 5

    def test_predict_rejects_flat_kwargs(self):
        from agent006.strategies.predict import PredictStrategy

        with pytest.raises(TypeError):
            PredictStrategy(max_retries=5)
