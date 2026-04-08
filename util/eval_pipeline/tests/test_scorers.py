"""Tests for scorer parameter introspection."""

import pytest

from eval_pipeline.config import get_method_schema

# ============================================================
# Test scorer classes
# ============================================================


class NoParamScorer:
    """Scorer with no constructor params."""

    def score(self, ctx):
        return {"score": 1.0}


class RequiredParamScorer:
    """Scorer with required params."""

    def __init__(self, rubric: str, model: str):
        self.rubric = rubric
        self.model = model

    def score(self, ctx):
        return {"score": 1.0}


class OptionalParamScorer:
    """Scorer with optional params."""

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    def score(self, ctx):
        return {"score": 1.0}


class MixedParamScorer:
    """Scorer with required and optional params."""

    def __init__(self, rubric: str, threshold: float = 0.5):
        self.rubric = rubric
        self.threshold = threshold

    def score(self, ctx):
        return {"score": 1.0}


class VarArgsScorer:
    """Scorer with *args and **kwargs (should be ignored)."""

    def __init__(self, name: str, *args, **kwargs):
        self.name = name

    def score(self, ctx):
        return {"score": 1.0}


# ============================================================
# Tests
# ============================================================


class TestScorerIntrospection:
    """Tests for scorer __init__ introspection."""

    def test_no_param_scorer(self):
        """Scorer with no __init__ params returns empty schema."""
        kwargs, optional = get_method_schema(NoParamScorer, "__init__")
        assert kwargs == {}
        assert optional == set()

    def test_required_param_scorer(self):
        """Required params are detected."""
        kwargs, optional = get_method_schema(RequiredParamScorer, "__init__")
        assert kwargs == {"rubric": "str", "model": "str"}
        assert optional == set()

    def test_optional_param_scorer(self):
        """Optional params are detected."""
        kwargs, optional = get_method_schema(OptionalParamScorer, "__init__")
        assert kwargs == {"threshold": "float"}
        assert optional == {"threshold"}

    def test_mixed_param_scorer(self):
        """Mixed required/optional params are detected."""
        kwargs, optional = get_method_schema(MixedParamScorer, "__init__")
        assert kwargs == {"rubric": "str", "threshold": "float"}
        assert optional == {"threshold"}

    def test_varargs_ignored(self):
        """*args and **kwargs are ignored in schema."""
        kwargs, optional = get_method_schema(VarArgsScorer, "__init__")
        assert kwargs == {"name": "str"}
        assert optional == set()


class TestScorerValidation:
    """Tests for validating scorer configs against schema."""

    def test_all_required_present(self):
        """Config with all required params is valid."""
        kwargs, optional = get_method_schema(RequiredParamScorer, "__init__")
        config = {"rubric": "test", "model": "test-model"}

        errors = []
        for param in kwargs:
            if param not in optional and param not in config:
                errors.append(f"missing '{param}'")

        assert errors == []

    def test_missing_required_detected(self):
        """Missing required param is detected."""
        kwargs, optional = get_method_schema(RequiredParamScorer, "__init__")
        config = {"rubric": "test"}  # missing 'model'

        errors = []
        for param in kwargs:
            if param not in optional and param not in config:
                errors.append(f"missing '{param}'")

        assert errors == ["missing 'model'"]

    def test_optional_can_be_missing(self):
        """Optional param can be missing."""
        kwargs, optional = get_method_schema(MixedParamScorer, "__init__")
        config = {"rubric": "test"}  # missing optional 'threshold'

        errors = []
        for param in kwargs:
            if param not in optional and param not in config:
                errors.append(f"missing '{param}'")

        assert errors == []

    def test_instantiation_with_schema(self):
        """Can instantiate scorer using introspected schema."""
        kwargs, _ = get_method_schema(MixedParamScorer, "__init__")
        config = {"rubric": "test rubric", "threshold": 0.8, "extra": "ignored"}

        # Build init kwargs from config (only params in schema)
        init_kwargs = {k: config[k] for k in kwargs if k in config}

        scorer = MixedParamScorer(**init_kwargs)
        assert scorer.rubric == "test rubric"
        assert scorer.threshold == 0.8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
