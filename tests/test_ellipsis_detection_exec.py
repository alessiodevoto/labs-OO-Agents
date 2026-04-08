"""Test that has_ellipsis_body works for dynamically exec'd functions.

This tests the critical path where PurePythonStrategy generates code with
@strategy decorators that need to be detected as needing generation.
"""

from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.ellipsis_detection import has_ellipsis_body
from nemo_oo_agents.strategies import PredictStrategy
from nemo_oo_agents.strategies.generated_code import HelperMethodManager


class TestHasEllipsisBodyExec:
    """Test has_ellipsis_body for functions created via exec."""

    def test_raw_exec_function_no_source(self):
        """Raw exec'd functions CAN be detected via bytecode heuristic.

        Even without _generated_source, the bytecode heuristic (checking
        bytecode length ≤12 bytes) successfully detects ellipsis-only functions.
        """
        code = '''
async def my_method(self, x: int) -> str:
    """A method."""
    ...
'''
        namespace = {}
        exec(code, namespace)
        func = namespace["my_method"]

        # Bytecode heuristic detects ellipsis even without _generated_source
        assert has_ellipsis_body(func) is True

    def test_raw_exec_function_implemented(self):
        """Test that exec'd function with implementation is NOT detected as ellipsis."""
        code = '''
async def my_method(self, x: int) -> str:
    """A method."""
    return str(x)
'''
        namespace = {}
        exec(code, namespace)
        func = namespace["my_method"]

        # Should NOT detect as ellipsis
        assert has_ellipsis_body(func) is False

    def test_exec_function_with_generated_source_ellipsis(self):
        """Test that _generated_source attribute enables ellipsis detection."""
        code = '''
async def my_method(self, x: int) -> str:
    """A method."""
    ...
'''
        namespace = {}
        exec(code, namespace)
        func = namespace["my_method"]

        # Set _generated_source (this is what HelperMethodManager does)
        func._generated_source = code

        # Should detect ellipsis body via _generated_source
        assert has_ellipsis_body(func) is True


class TestHelperMethodManagerSourceTracking:
    """Test that HelperMethodManager sets _generated_source correctly."""

    def test_undecorated_method_gets_generated_source(self):
        """HelperMethodManager should set _generated_source on undecorated methods."""
        code = '''
async def _helper(self, x: int) -> str:
    """A helper method."""
    ...
'''

        # Create a mock agent instance
        class MockAgent:
            pass

        agent = MockAgent()
        manager = HelperMethodManager()

        result = manager.apply(
            code,
            agent,
            session_locals={},
            namespace={},
            target_method_name="main_method",  # Not _helper, so it won't be rejected
        )

        assert "_helper" in result.installed
        assert hasattr(agent, "_helper")

        # The underlying function should have _generated_source
        func = agent._helper.__func__
        assert hasattr(func, "_generated_source")
        assert has_ellipsis_body(func) is True

    def test_decorated_method_needs_generation(self):
        """Test that @strategy decorator on method via HelperMethodManager works.

        This is the critical test for the PurePython nested method pattern.
        """
        code = '''
@strategy(PredictStrategy())
async def _summarize(self, doc: str) -> str:
    """Summarize a document."""
    ...
'''

        class MockAgent:
            pass

        agent = MockAgent()
        manager = HelperMethodManager()

        result = manager.apply(
            code,
            agent,
            session_locals={},
            namespace={
                "strategy": strategy,
                "PredictStrategy": PredictStrategy,
            },
            target_method_name="main_method",
        )

        assert "_summarize" in result.installed
        assert hasattr(agent, "_summarize")

        # The decorated function should have _needs_generation=True
        bound_method = agent._summarize
        assert getattr(bound_method, "_needs_generation", None) is True, (
            f"Expected _needs_generation=True but got "
            f"{getattr(bound_method, '_needs_generation', 'MISSING')}"
        )

    def test_decorated_implemented_method_no_generation(self):
        """Test that implemented @strategy method has _needs_generation=False."""
        code = '''
@strategy(PredictStrategy())
async def _helper(self, doc: str) -> str:
    """Process a document."""
    return f"Processed: {doc}"
'''

        class MockAgent:
            pass

        agent = MockAgent()
        manager = HelperMethodManager()

        result = manager.apply(
            code,
            agent,
            session_locals={},
            namespace={
                "strategy": strategy,
                "PredictStrategy": PredictStrategy,
            },
            target_method_name="main_method",
        )

        assert "_helper" in result.installed
        bound_method = agent._helper
        assert getattr(bound_method, "_needs_generation", None) is False


class TestRawExecDecorator:
    """Test raw exec with @strategy decorator (without HelperMethodManager).

    These tests document the expected behavior: raw exec doesn't work
    for decorated functions without _generated_source.
    """

    def test_raw_exec_decorated_detects_via_bytecode(self):
        """Raw exec'd @strategy function detects ellipsis via bytecode heuristic."""
        code = '''
@strategy(PredictStrategy())
async def _helper(self, doc: str) -> str:
    """Summarize a document."""
    ...
'''
        namespace = {
            "strategy": strategy,
            "PredictStrategy": PredictStrategy,
        }
        exec(code, namespace)
        func = namespace["_helper"]

        # Bytecode heuristic allows detection even without _generated_source
        # The @strategy decorator will correctly set _needs_generation=True
        assert getattr(func, "_needs_generation", None) is True

    def test_raw_exec_decorated_implemented(self):
        """Raw exec'd implemented @strategy function correctly has _needs_generation=False."""
        code = '''
@strategy(PredictStrategy())
async def _helper(self, doc: str) -> str:
    """Summarize a document."""
    return f"Summary of: {doc}"
'''
        namespace = {
            "strategy": strategy,
            "PredictStrategy": PredictStrategy,
        }
        exec(code, namespace)
        func = namespace["_helper"]

        # Should NOT need generation since it has implementation
        assert getattr(func, "_needs_generation", None) is False
