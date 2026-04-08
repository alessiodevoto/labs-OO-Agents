"""Tests for CurrentCall dataclass.

TDD: Write these tests first, then implement current_call.py to make them pass.
"""

import pytest


class TestCurrentCallBasic:
    """Basic CurrentCall tests."""

    def test_create_with_required_fields(self):
        """CurrentCall should require id, method_name, decorator."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        call = CurrentCall(
            id="call_123",
            method_name="process",
            decorator="plan",
        )

        assert call.id == "call_123"
        assert call.method_name == "process"
        assert call.decorator == "plan"

    def test_optional_fields_default_to_none_or_empty(self):
        """Optional fields should have sensible defaults."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        call = CurrentCall(
            id="call_123",
            method_name="process",
            decorator="plan",
        )

        assert call.signature is None
        assert call.docstring is None
        assert call.args == ()
        assert call.kwargs == {}
        assert call.parent_id is None

    def test_create_with_all_fields(self):
        """CurrentCall should accept all fields."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        call = CurrentCall(
            id="call_123",
            method_name="analyze",
            decorator="plan",
            signature="(self, data: str, limit: int = 10) -> dict",
            docstring="Analyze the data and return results.",
            args=("test data",),
            kwargs={"limit": 5},
            parent_id="parent_call_456",
        )

        assert call.id == "call_123"
        assert call.method_name == "analyze"
        assert call.decorator == "plan"
        assert call.signature == "(self, data: str, limit: int = 10) -> dict"
        assert call.docstring == "Analyze the data and return results."
        assert call.args == ("test data",)
        assert call.kwargs == {"limit": 5}
        assert call.parent_id == "parent_call_456"


class TestCurrentCallFromMethod:
    """Tests for CurrentCall.from_method() factory."""

    def test_from_method_extracts_signature(self):
        """from_method should extract method signature."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        def example_method(self, data: str, count: int = 10) -> list:
            """Process data and return list."""
            pass

        call = CurrentCall.from_method(
            method=example_method,
            args=("test",),
            kwargs={"count": 5},
        )

        assert call.method_name == "example_method"
        assert call.args == ("test",)
        # After rebase: positional args are merged into kwargs for template expansion
        assert call.kwargs == {"data": "test", "count": 5}
        assert "data: str" in call.signature
        assert "count: int = 10" in call.signature
        assert "-> list" in call.signature

    def test_from_method_extracts_docstring(self):
        """from_method should extract method docstring."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        def documented_method(self):
            """This is the docstring for the method."""
            pass

        call = CurrentCall.from_method(method=documented_method)

        assert call.docstring == "This is the docstring for the method."

    def test_from_method_handles_no_docstring(self):
        """from_method should handle methods without docstrings."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        def no_docs(self):
            pass

        call = CurrentCall.from_method(method=no_docs)

        assert call.docstring is None

    def test_from_method_generates_unique_id(self):
        """from_method should generate unique call ID."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        def test_method(self):
            pass

        call1 = CurrentCall.from_method(method=test_method)
        call2 = CurrentCall.from_method(method=test_method)

        assert call1.id != call2.id
        assert call1.id.startswith("call_")
        assert call2.id.startswith("call_")

    def test_from_method_accepts_decorator_type(self):
        """from_method should accept decorator type."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        def test_method(self):
            pass

        call = CurrentCall.from_method(method=test_method, decorator="agent")

        assert call.decorator == "agent"

    def test_from_method_accepts_parent_id(self):
        """from_method should accept parent_id for nested calls."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        def child_method(self):
            pass

        call = CurrentCall.from_method(
            method=child_method,
            parent_id="parent_call_789",
        )

        assert call.parent_id == "parent_call_789"


class TestCurrentCallEquality:
    """Tests for CurrentCall equality and hashing."""

    def test_equality_by_id(self):
        """Two CurrentCall with same id should be equal."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        call1 = CurrentCall(id="same_id", method_name="test", decorator="plan")
        call2 = CurrentCall(id="same_id", method_name="test", decorator="plan")

        assert call1 == call2

    def test_inequality_by_id(self):
        """Two CurrentCall with different id should not be equal."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        call1 = CurrentCall(id="id_1", method_name="test", decorator="plan")
        call2 = CurrentCall(id="id_2", method_name="test", decorator="plan")

        assert call1 != call2

    def test_hashable(self):
        """CurrentCall should be hashable for use in sets/dicts."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        call = CurrentCall(id="call_123", method_name="test", decorator="plan")

        # Should not raise
        hash(call)

        # Should be usable in set
        call_set = {call}
        assert call in call_set


class TestCurrentCallImmutability:
    """Tests for CurrentCall immutability."""

    def test_fields_are_frozen(self):
        """CurrentCall should be frozen (immutable)."""
        from nemo_oo_agents.strategies.current_call import CurrentCall

        call = CurrentCall(id="call_123", method_name="test", decorator="plan")

        with pytest.raises(AttributeError):
            call.id = "new_id"

        with pytest.raises(AttributeError):
            call.method_name = "new_method"
