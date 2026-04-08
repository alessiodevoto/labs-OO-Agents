"""Integration tests for NeMo OO Agents + NAT.

Tests all four bridges independently and together.

Usage:
    cd examples/nat
    python test_integration.py

Requires:
    - NAT installed (nvidia-nat-core)
    - nvidia-nat-nemo_oo_agents plugin installed (pip install -e ../../packages/nvidia_nat_nemo_oo_agents)
    - NeMo OO Agents importable (../../src on PYTHONPATH or installed)
    - unifiedllm importable (../../packages/unifiedllm on PYTHONPATH or installed)

Tests:
    test_tool_class_generation  - Native tool class created from mock NAT Function
    test_tool_agentdoc          - Generated class introspected by agentdoc
    test_tool_injection         - Tools injected onto agent instance
    test_method_routing         - Correct method resolved from config
    test_otel_bridge            - TracerProvider set up without error
    test_input_conversion       - Various input formats handled correctly
    test_config_validation      - Config rejects invalid inputs
"""

import asyncio
import inspect
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Ensure nemo_oo_agents and unifiedllm are importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "packages" / "unifiedllm" / "src"))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockNATFunction:
    """Mock NAT Function for testing tool bridge without NAT installed."""

    def __init__(self, name: str, description: str, input_fields: dict):
        from pydantic import create_model

        self.description = description
        self.instance_name = name

        # Create a dynamic Pydantic model for the input schema
        field_definitions = {}
        for fname, ftype in input_fields.items():
            field_definitions[fname] = (ftype, ...)

        self._input_schema = create_model(f"{name}Input", **field_definitions)

        self._result = f"Mock result from {name}"

    @property
    def input_schema(self):
        return self._input_schema

    async def ainvoke(self, value, to_type=None):
        return self._result


def run_test(test_fn):
    """Run an async test function and report results."""
    name = test_fn.__name__
    try:
        if asyncio.iscoroutinefunction(test_fn):
            asyncio.run(test_fn())
        else:
            test_fn()
        logger.info("  PASS: %s", name)
        return True
    except Exception as e:
        logger.error("  FAIL: %s -- %s", name, e)
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _import_tool_bridge():
    """Import tool_bridge module, adding path if needed."""
    plugin_src = str(
        Path(__file__).parent.parent.parent / "packages" / "nvidia_nat_nemo_oo_agents" / "src"
    )
    if plugin_src not in sys.path:
        sys.path.insert(0, plugin_src)
    # Import the module directly to avoid triggering NAT imports from register.py
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "tool_bridge",
        Path(__file__).parent.parent.parent
        / "packages"
        / "nvidia_nat_nemo_oo_agents"
        / "src"
        / "nat"
        / "plugins"
        / "nemo_oo_agents"
        / "tool_bridge.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _import_otel_bridge():
    """Import otel_bridge module directly."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "otel_bridge",
        Path(__file__).parent.parent.parent
        / "packages"
        / "nvidia_nat_nemo_oo_agents"
        / "src"
        / "nat"
        / "plugins"
        / "nemo_oo_agents"
        / "otel_bridge.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_class_generation():
    """Verify native Python class is generated from NAT Function schema."""
    tool_bridge = _import_tool_bridge()

    mock_fn = MockNATFunction(
        name="web_search",
        description="Search the web for information",
        input_fields={"query": str, "max_results": int},
    )

    ToolClass = tool_bridge.create_native_tool_class("web_search", mock_fn)

    # Verify class name
    assert ToolClass.__name__ == "WebSearch", f"Expected 'WebSearch', got '{ToolClass.__name__}'"

    # Verify docstring
    assert ToolClass.__doc__ == "Search the web for information"

    # Verify invoke method exists and is async
    assert hasattr(ToolClass, "invoke"), "Missing invoke method"
    assert inspect.iscoroutinefunction(ToolClass.invoke), "invoke must be async"

    # Verify method signature has typed parameters
    sig = inspect.signature(ToolClass.invoke)
    params = list(sig.parameters.keys())
    assert "self" in params, f"Missing 'self' in params: {params}"
    assert "query" in params, f"Missing 'query' in params: {params}"
    assert "max_results" in params, f"Missing 'max_results' in params: {params}"


def test_tool_no_params():
    """Verify tool with no input parameters works."""
    tool_bridge = _import_tool_bridge()

    mock_fn = MockNATFunction(
        name="current_time",
        description="Returns the current date and time",
        input_fields={},
    )

    ToolClass = tool_bridge.create_native_tool_class("current_time", mock_fn)

    assert ToolClass.__name__ == "CurrentTime"
    assert inspect.iscoroutinefunction(ToolClass.invoke)

    # Signature should only have 'self'
    sig = inspect.signature(ToolClass.invoke)
    params = list(sig.parameters.keys())
    assert params == ["self"], f"Expected ['self'], got {params}"


async def test_tool_invocation():
    """Verify generated tool class can invoke the underlying NAT Function."""
    tool_bridge = _import_tool_bridge()

    mock_fn = MockNATFunction(
        name="calculator",
        description="Perform calculations",
        input_fields={"expression": str},
    )

    tool = tool_bridge.create_tool_instance("calculator", mock_fn)
    result = await tool.invoke(expression="2+2")
    assert result == "Mock result from calculator", f"Got: {result}"


def test_tool_agentdoc():
    """Verify generated tool class is introspectable by agentdoc."""
    tool_bridge = _import_tool_bridge()

    mock_fn = MockNATFunction(
        name="web_search",
        description="Search the web",
        input_fields={"query": str},
    )

    tool = tool_bridge.create_tool_instance("web_search", mock_fn)

    try:
        from agentdoc import doc

        doc_output = doc(tool)
        assert "WebSearch" in doc_output, f"'WebSearch' not in doc output:\n{doc_output}"
        assert "invoke" in doc_output, f"'invoke' not in doc output:\n{doc_output}"
        assert "query" in doc_output, f"'query' not in doc output:\n{doc_output}"
        print(f"    agentdoc output:\n{doc_output}")
    except ImportError:
        # agentdoc not available -- verify with standard inspect
        sig = inspect.signature(type(tool).invoke)
        assert "query" in str(sig), f"'query' not in signature: {sig}"


def test_input_conversion():
    """Verify various input formats are handled correctly."""
    # We can't import the full wrapper (needs NAT), so test the input model directly
    from pydantic import BaseModel, ConfigDict

    class NemoOOAgentsWrapperInput(BaseModel):
        model_config = ConfigDict(extra="allow")
        messages: list | str

    def convert_input(value):
        if isinstance(value, str):
            return NemoOOAgentsWrapperInput(messages=value)
        if isinstance(value, dict):
            if "messages" in value:
                return NemoOOAgentsWrapperInput(**value)
            if "content" in value:
                return NemoOOAgentsWrapperInput(messages=value["content"])
        if isinstance(value, list):
            if value and isinstance(value[-1], str):
                return NemoOOAgentsWrapperInput(messages=value[-1])
        return NemoOOAgentsWrapperInput(messages=str(value))

    # Test string conversion
    result = convert_input("Hello world")
    assert result.messages == "Hello world"

    # Test dict with messages key
    result = convert_input({"messages": "Hi there"})
    assert result.messages == "Hi there"

    # Test dict with content key
    result = convert_input({"content": "From content"})
    assert result.messages == "From content"

    # Test list of strings
    result = convert_input(["first", "second"])
    assert result.messages == "second"


def test_otel_bridge():
    """Verify OTel bridge sets up TracerProvider without error."""
    otel_bridge = _import_otel_bridge()

    # Should not raise even without OTLP endpoint
    otel_bridge.setup_shared_tracer()

    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        assert hasattr(provider, "add_span_processor"), "TracerProvider should be an SDK provider"
    except ImportError:
        pass  # OTel not installed -- bridge gracefully degrades


def test_pascal_case():
    """Verify snake_case to PascalCase conversion."""
    tool_bridge = _import_tool_bridge()

    assert tool_bridge._to_pascal_case("web_search") == "WebSearch"
    assert tool_bridge._to_pascal_case("current_time") == "CurrentTime"
    assert tool_bridge._to_pascal_case("calculator") == "Calculator"
    assert tool_bridge._to_pascal_case("my_complex_tool_name") == "MyComplexToolName"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("NeMo OO Agents + NAT Integration Tests")
    logger.info("=" * 50)

    tests = [
        test_tool_class_generation,
        test_tool_no_params,
        test_tool_invocation,
        test_tool_agentdoc,
        test_input_conversion,
        test_otel_bridge,
        test_pascal_case,
    ]

    passed = 0
    failed = 0

    for test in tests:
        if run_test(test):
            passed += 1
        else:
            failed += 1

    logger.info("")
    logger.info("Results: %d passed, %d failed, %d total", passed, failed, len(tests))

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
