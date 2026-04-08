# Fix Method Spans Missing Parent-Child Relationships

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the tracing bug where method spans have `parent_span_id: None` instead of properly referencing their parent method's span.

**Architecture:** The `_agent_call_stack` is currently only managed (push/pop) when `is_nested=True`, but `is_nested` is based on `_in_generation_session` context var which starts False for the first method. This means the root method never pushes its call_id to the stack, so child methods get `parent_call_id=None`. The fix is to always push/pop the call_id regardless of `is_nested`.

**Tech Stack:** Python, pytest, nemo_oo_agents runtime

**GitLab Issue:** https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/81

---

## Summary of Changes

The fix is in `src/nemo_oo_agents/runtime/actor.py`:
- Line ~1574: Remove `if is_nested:` condition around push
- Line ~1703: Remove `if is_nested:` condition around pop

The `is_nested` flag should only control inline execution (deadlock avoidance), not call tracking.

---

### Task 1: Write Failing Test for Parent-Child Span Relationship

**Files:**
- Create: `tests/runtime/test_span_parent_relationship.py`

**Step 1: Write the failing test**

```python
"""Test that method spans have correct parent-child relationships."""

import asyncio
import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from nemo_oo_agents.agent import Agent
from nemo_oo_agents.runtime.hooks import set_hooks, get_hooks
from unifiedllm import FakeLLMClient


class SpanTrackingHooks:
    """Mock hooks that track span parent relationships."""

    def __init__(self):
        self.calls = []  # List of (method_name, call_id, parent_call_id)

    def before_agent_call(self, agent, method_name, args, kwargs, call_id, parent_call_id, **extra):
        self.calls.append({
            "method_name": method_name,
            "call_id": call_id,
            "parent_call_id": parent_call_id,
        })
        return {"call_id": call_id}

    def after_agent_call(self, agent, method_name, result, exception, context):
        pass

    def before_generation(self, agent, method_name, strategy, generation_id, parent_generation_id, **extra):
        return {}

    def after_generation(self, agent, method_name, result, exception, context, generation_id):
        pass

    def before_code_execution(self, agent, code, execution_id, **extra):
        return {}

    def after_code_execution(self, agent, code, result, exception, context, execution_id):
        pass

    def before_method_invocation(self, agent, method_name, args, kwargs, invocation_id, **extra):
        return {}

    def after_method_invocation(self, agent, method_name, result, exception, context, invocation_id):
        pass

    def before_tool_execution(self, agent, tool_name, arguments, execution_id, **extra):
        return {}

    def after_tool_execution(self, agent, tool_name, arguments, result, exception, context, execution_id):
        pass


@pytest.fixture
def span_hooks():
    """Fixture that installs span tracking hooks and cleans up."""
    hooks = SpanTrackingHooks()
    old_hooks = get_hooks()
    set_hooks(hooks)
    yield hooks
    set_hooks(old_hooks)


class TestMethodSpanParentRelationship:
    """Test that nested method calls have correct parent-child span relationships."""

    @pytest.mark.asyncio
    async def test_nested_method_has_parent_call_id(self, span_hooks):
        """When a method calls another method, the child should have parent_call_id set.

        This is the core bug: solve_task calling phase_1 should result in phase_1
        having parent_call_id pointing to solve_task's call_id.
        """
        _TEST_LLM = FakeLLMClient()

        class NestedAgent(Agent, llm=_TEST_LLM):
            """Agent with nested method calls."""

            async def outer_method(self) -> str:
                """Root method that calls inner method."""
                result = await self.inner_method()
                return f"outer({result})"

            async def inner_method(self) -> str:
                """Child method called by outer."""
                return "inner"

        agent = NestedAgent()
        result = await agent.outer_method()

        assert result == "outer(inner)"

        # Verify we captured both method calls
        assert len(span_hooks.calls) == 2

        outer_call = next(c for c in span_hooks.calls if c["method_name"] == "outer_method")
        inner_call = next(c for c in span_hooks.calls if c["method_name"] == "inner_method")

        # Root method should have no parent
        assert outer_call["parent_call_id"] is None, (
            f"Root method outer_method should have no parent, got {outer_call['parent_call_id']}"
        )

        # Child method should have parent pointing to outer's call_id
        # THIS IS THE BUG: inner_method has parent_call_id=None instead of outer's call_id
        assert inner_call["parent_call_id"] == outer_call["call_id"], (
            f"inner_method should have parent_call_id={outer_call['call_id']}, "
            f"but got {inner_call['parent_call_id']}"
        )


    @pytest.mark.asyncio
    async def test_deeply_nested_methods_chain_correctly(self, span_hooks):
        """Test three levels of nesting: A -> B -> C."""
        _TEST_LLM = FakeLLMClient()

        class DeeplyNestedAgent(Agent, llm=_TEST_LLM):
            async def level_a(self) -> str:
                result = await self.level_b()
                return f"A({result})"

            async def level_b(self) -> str:
                result = await self.level_c()
                return f"B({result})"

            async def level_c(self) -> str:
                return "C"

        agent = DeeplyNestedAgent()
        result = await agent.level_a()

        assert result == "A(B(C))"
        assert len(span_hooks.calls) == 3

        call_a = next(c for c in span_hooks.calls if c["method_name"] == "level_a")
        call_b = next(c for c in span_hooks.calls if c["method_name"] == "level_b")
        call_c = next(c for c in span_hooks.calls if c["method_name"] == "level_c")

        # Verify chain: A has no parent, B's parent is A, C's parent is B
        assert call_a["parent_call_id"] is None
        assert call_b["parent_call_id"] == call_a["call_id"], (
            f"level_b should have parent={call_a['call_id']}, got {call_b['parent_call_id']}"
        )
        assert call_c["parent_call_id"] == call_b["call_id"], (
            f"level_c should have parent={call_b['call_id']}, got {call_c['parent_call_id']}"
        )
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/runtime/test_span_parent_relationship.py -v`

Expected: FAIL with assertion error showing `inner_method` has `parent_call_id=None` instead of `outer_method`'s call_id.

**Step 3: Commit test**

```bash
git add tests/runtime/test_span_parent_relationship.py
git commit -m "test: add failing test for method span parent-child relationships

Verifies nested method calls should have correct parent_call_id.
Currently fails due to bug in actor.py stack management.

Refs: gitlab#81

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2: Fix the Bug in actor.py

**Files:**
- Modify: `src/nemo_oo_agents/runtime/actor.py:1573-1576` (push)
- Modify: `src/nemo_oo_agents/runtime/actor.py:1702-1705` (pop)

**Step 1: Fix the push - always push call_id to stack**

Change lines 1573-1576 from:

```python
            # Push agent_call_id for nested calls
            if is_nested:
                new_agent_call_id = str(uuid4())
                self._agent_call_stack.append(new_agent_call_id)
```

To:

```python
            # Push agent_call_id for ALL method calls (not just nested)
            # This ensures parent_call_id is available for child method spans
            new_agent_call_id = str(uuid4())
            self._agent_call_stack.append(new_agent_call_id)
```

**Step 2: Fix the pop - always pop call_id from stack**

Change lines 1702-1705 from:

```python
            # Pop agent_call_id from stack if we pushed it (nested call cleanup)
            if is_nested:
                if self._agent_call_stack:
                    self._agent_call_stack.pop()
```

To:

```python
            # Pop agent_call_id from stack (we always push, so always pop)
            if self._agent_call_stack:
                self._agent_call_stack.pop()
```

**Step 3: Run test to verify it passes**

Run: `pytest tests/runtime/test_span_parent_relationship.py -v`

Expected: PASS - both tests should pass now.

**Step 4: Run full test suite to check for regressions**

Run: `pytest tests/ -v --tb=short`

Expected: All tests pass.

**Step 5: Commit fix**

```bash
git add src/nemo_oo_agents/runtime/actor.py
git commit -m "fix: always push/pop agent_call_id for correct span parent relationships

Remove the is_nested condition from agent_call_stack management.
Previously, only nested calls pushed their call_id, but the first/root
method never pushed, causing all child methods to have parent_call_id=None.

Now all method calls push their call_id, ensuring child methods can
correctly reference their parent's call_id for trace visualization.

Fixes: gitlab#81

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Verify with Stack Isolation Tests

**Files:**
- Read: `src/nemo_oo_agents/runtime/tests/test_stack_isolation.py`

**Step 1: Run existing stack isolation tests**

Run: `pytest src/nemo_oo_agents/runtime/tests/test_stack_isolation.py -v`

Expected: PASS - existing isolation tests should still pass since we're still using ContextVars for isolation.

**Step 2: Run the full runtime test suite**

Run: `pytest tests/runtime/ -v --tb=short`

Expected: All runtime tests pass.

**Step 3: Commit verification (if any changes needed)**

If tests reveal issues, fix them. Otherwise, no commit needed for this task.

---

### Task 4: Manual Verification with Trace Output

**Step 1: Create a minimal reproduction script**

Create `scripts/verify_span_fix.py` (temporary, don't commit):

```python
"""Verify the span parent relationship fix."""

import asyncio
import json
from nemo_oo_agents.agent import Agent
from nemo_oo_agents.runtime.hooks import set_hooks, get_hooks
from unifiedllm import FakeLLMClient


class DebugHooks:
    def __init__(self):
        self.spans = []

    def before_agent_call(self, agent, method_name, args, kwargs, call_id, parent_call_id, **extra):
        self.spans.append({
            "name": f"method.{method_name}",
            "call_id": call_id[:8],
            "parent_call_id": parent_call_id[:8] if parent_call_id else None,
        })
        return {}

    def after_agent_call(self, *args, **kwargs): pass
    def before_generation(self, *args, **kwargs): return {}
    def after_generation(self, *args, **kwargs): pass
    def before_code_execution(self, *args, **kwargs): return {}
    def after_code_execution(self, *args, **kwargs): pass
    def before_method_invocation(self, *args, **kwargs): return {}
    def after_method_invocation(self, *args, **kwargs): pass
    def before_tool_execution(self, *args, **kwargs): return {}
    def after_tool_execution(self, *args, **kwargs): pass


async def main():
    hooks = DebugHooks()
    set_hooks(hooks)

    class TestAgent(Agent, llm=FakeLLMClient()):
        async def solve_task(self) -> str:
            await self.phase_1_understand()
            await self.phase_2_discover()
            return "done"

        async def phase_1_understand(self) -> str:
            return "understood"

        async def phase_2_discover(self) -> str:
            return "discovered"

    agent = TestAgent()
    await agent.solve_task()

    print("\nSpan Hierarchy:")
    for span in hooks.spans:
        print(f"  {span['name']:30} call_id={span['call_id']} parent={span['parent_call_id']}")

    # Verify
    solve = next(s for s in hooks.spans if "solve_task" in s["name"])
    phase1 = next(s for s in hooks.spans if "phase_1" in s["name"])
    phase2 = next(s for s in hooks.spans if "phase_2" in s["name"])

    print("\nVerification:")
    print(f"  solve_task parent is None: {solve['parent_call_id'] is None}")
    print(f"  phase_1 parent is solve_task: {phase1['parent_call_id'] == solve['call_id']}")
    print(f"  phase_2 parent is solve_task: {phase2['parent_call_id'] == solve['call_id']}")

    set_hooks(None)


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Run verification script**

Run: `python scripts/verify_span_fix.py`

Expected output:
```
Span Hierarchy:
  method.solve_task              call_id=abc12345 parent=None
  method.phase_1_understand      call_id=def67890 parent=abc12345
  method.phase_2_discover        call_id=ghi11111 parent=abc12345

Verification:
  solve_task parent is None: True
  phase_1 parent is solve_task: True
  phase_2 parent is solve_task: True
```

**Step 3: Clean up**

Run: `rm scripts/verify_span_fix.py` (don't commit the verification script)

---

### Task 5: Final Verification and Cleanup

**Step 1: Run full test suite**

Run: `pytest tests/ src/nemo_oo_agents/runtime/tests/ -v --tb=short`

Expected: All tests pass.

**Step 2: Check for type errors (if mypy configured)**

Run: `mypy src/nemo_oo_agents/runtime/actor.py --ignore-missing-imports` (optional)

**Step 3: Update issue**

Comment on GitLab issue #81 with:
- Summary of the fix
- Test results
- Any notes about the change

---

## Verification Checklist

- [ ] `test_nested_method_has_parent_call_id` passes
- [ ] `test_deeply_nested_methods_chain_correctly` passes
- [ ] All existing `test_stack_isolation.py` tests pass
- [ ] All `tests/runtime/` tests pass
- [ ] No regressions in other test suites
- [ ] Manual verification shows correct parent-child relationships
