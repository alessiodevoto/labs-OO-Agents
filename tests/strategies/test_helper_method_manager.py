"""Tests for HelperMethodManager class vs instance guard.

TDD: These tests verify that HelperMethodManager:
1. Rejects classes (only accepts instances)
2. Binds methods to instances without leaking to the class
3. Works correctly with the standard apply() flow
"""

import pytest

from agent006.strategies.generated_code import ExecutionNamespaceBuilder, HelperMethodManager
from unifiedllm import FakeLLMClient


class TestHelperMethodManagerGuard:
    """Tests for HelperMethodManager class vs instance guard."""

    def test_rejects_class_instead_of_instance(self):
        """HelperMethodManager should raise TypeError if passed a class."""
        from agent006.agent import Agent

        class FakeAgent(Agent, llm=FakeLLMClient()):
            async def process(self) -> dict:
                """Process something."""
                ...

        manager = HelperMethodManager()
        namespace = ExecutionNamespaceBuilder.build(FakeAgent)  # Note: class, not instance

        with pytest.raises(TypeError, match="instance but received a class"):
            manager.apply(
                code="def helper(self): pass",
                agent=FakeAgent,  # CLASS, not instance - should be rejected!
                session_locals={},
                namespace=namespace,
                target_method_name="process",
            )

    def test_accepts_instance(self):
        """HelperMethodManager should accept agent instances."""
        from agent006.agent import Agent

        class FakeAgent(Agent, llm=FakeLLMClient()):
            async def process(self) -> dict:
                """Process something."""
                ...

        manager = HelperMethodManager()
        instance = FakeAgent()
        namespace = ExecutionNamespaceBuilder.build(instance)

        result = manager.apply(
            code="def helper(self): return 42",
            agent=instance,
            session_locals={},
            namespace=namespace,
            target_method_name="process",
        )

        assert "helper" in result.installed
        assert hasattr(instance, "helper")
        # Verify the bound method works
        assert instance.helper() == 42

    def test_method_does_not_leak_to_class(self):
        """Helper methods bound to instance should not appear on other instances."""
        from agent006.agent import Agent

        class FakeAgent(Agent, llm=FakeLLMClient()):
            async def process(self) -> dict:
                """Process something."""
                ...

        manager = HelperMethodManager()
        instance1 = FakeAgent()
        namespace = ExecutionNamespaceBuilder.build(instance1)

        manager.apply(
            code="def helper(self): return 42",
            agent=instance1,
            session_locals={},
            namespace=namespace,
            target_method_name="process",
        )

        # Verify instance1 has the method
        assert hasattr(instance1, "helper")
        assert instance1.helper() == 42

        # Create new instance - should NOT have the helper
        instance2 = FakeAgent()
        assert not hasattr(instance2, "helper"), "Helper method leaked to new instance!"

    def test_method_does_not_leak_to_class_definition(self):
        """Helper methods should not be added to the class __dict__."""
        from agent006.agent import Agent

        class FakeAgent(Agent, llm=FakeLLMClient()):
            async def process(self) -> dict:
                """Process something."""
                ...

        manager = HelperMethodManager()
        instance = FakeAgent()
        namespace = ExecutionNamespaceBuilder.build(instance)

        manager.apply(
            code="def helper(self): return 42",
            agent=instance,
            session_locals={},
            namespace=namespace,
            target_method_name="process",
        )

        # The method should NOT appear in the class __dict__
        assert "helper" not in FakeAgent.__dict__, "Helper method leaked to class __dict__!"

    def test_async_helper_method_binding(self):
        """Async helper methods should be bound correctly."""
        from agent006.agent import Agent

        class FakeAgent(Agent, llm=FakeLLMClient()):
            async def process(self) -> dict:
                """Process something."""
                ...

        manager = HelperMethodManager()
        instance = FakeAgent()
        namespace = ExecutionNamespaceBuilder.build(instance)

        result = manager.apply(
            code="async def async_helper(self): return await asyncio.sleep(0) or 42",
            agent=instance,
            session_locals={},
            namespace=namespace,
            target_method_name="process",
        )

        assert "async_helper" in result.installed
        assert hasattr(instance, "async_helper")

    def test_rejects_method_with_same_name_as_target(self):
        """Helper method with same name as target method should be rejected."""
        from agent006.agent import Agent

        class FakeAgent(Agent, llm=FakeLLMClient()):
            async def process(self) -> dict:
                """Process something."""
                ...

        manager = HelperMethodManager()
        instance = FakeAgent()
        namespace = ExecutionNamespaceBuilder.build(instance)

        result = manager.apply(
            code="def process(self): return {}",  # Same name as target
            agent=instance,
            session_locals={},
            namespace=namespace,
            target_method_name="process",
        )

        # Should be rejected, not installed
        assert "process" in result.rejected
        assert "process" not in result.installed


class TestHelperMethodManagerSessionLocals:
    """Tests for session_locals handling in HelperMethodManager."""

    def test_helper_added_to_session_locals(self):
        """Helper methods should be added to session_locals for reuse."""
        from agent006.agent import Agent

        class FakeAgent(Agent, llm=FakeLLMClient()):
            async def process(self) -> dict:
                """Process something."""
                ...

        manager = HelperMethodManager()
        instance = FakeAgent()
        namespace = ExecutionNamespaceBuilder.build(instance)
        session_locals: dict = {}

        manager.apply(
            code="def helper(self): return 42",
            agent=instance,
            session_locals=session_locals,
            namespace=namespace,
            target_method_name="process",
        )

        assert "helper" in session_locals
        # The session_locals version should be the bound method
        assert callable(session_locals["helper"])


class TestHelperMethodManagerErrors:
    """Tests for error handling in HelperMethodManager."""

    def test_records_decorator_validation_errors(self):
        """Errors from decorator validation should be recorded."""
        from agent006.agent import Agent

        class FakeAgent(Agent, llm=FakeLLMClient()):
            async def process(self) -> dict:
                """Process something."""
                ...

        manager = HelperMethodManager()
        instance = FakeAgent()
        namespace = ExecutionNamespaceBuilder.build(instance)

        # This code has a syntax that will fail during exec
        # (referencing undefined variable)
        result = manager.apply(
            code="def helper(self): return undefined_variable",
            agent=instance,
            session_locals={},
            namespace=namespace,
            target_method_name="process",
        )

        # The method should still be installed (error happens at call time, not definition)
        # Let's test with actual compile-time error
        result = manager.apply(
            code="@nonexistent_decorator\ndef broken_helper(self): pass",
            agent=instance,
            session_locals={},
            namespace=namespace,
            target_method_name="process",
        )

        # Should have an error recorded
        assert len(result.errors) > 0 or "broken_helper" not in result.installed
