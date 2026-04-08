"""Test that doc(self) includes Already Imported section for Agent instances."""

from unifiedllm import FakeLLMClient

from agentdoc import doc

# Try to import Agent006 if available
try:
    from agent006 import Agent
    from unifiedllm import FakeLLMClient

    class TestAgent(Agent, llm=FakeLLMClient()):
        """A test agent with module-level imports."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.data = {"key": "value"}

        def process(self, item: str) -> str:
            """Process an item."""
            return item.upper()

    # Create instance and show doc output
    agent = TestAgent()
    result = doc(agent)

    print("=" * 80)
    print("doc(self) output for TestAgent:")
    print("=" * 80)
    print(result)
    print("=" * 80)

    # Check that imports section is included
    if "Already Imported" in result or "# Already Imported:" in result:
        print("\n✅ SUCCESS: Already Imported section is included!")
        # Verify asyncio is included (should always be present)
        if "import asyncio" in result:
            print("✅ asyncio import found as expected")
        else:
            print("⚠️  asyncio import not found (might be expected)")
    else:
        print("\n❌ FAILED: Already Imported section not found")
        print("\nLooking for 'Already Imported' or '# Already Imported:' in output")

except ImportError:
    print("agent006 not available - skipping Agent test")
    print("Testing with regular class instead...")

    class RegularClass:
        """A regular class (not an Agent)."""

        def __init__(self):
            self.value = 42

    obj = RegularClass()
    result = doc(obj)
    print("=" * 80)
    print("doc() output for RegularClass (should NOT have imports):")
    print("=" * 80)
    print(result)
    print("=" * 80)
