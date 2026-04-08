# Nested Agent History Ordering Bug

## Summary

When an outer agent's `execute_python` code calls a nested agent method, the tool result for `execute_python` is added to history AFTER the nested agent's events. This violates OpenAI/Gemini's requirement that tool results reference tool_call_ids from the immediately preceding assistant message.

## Root Cause

In `_handle_execute_python()` ([codeact.py:540](../src/nemo_oo_agents/strategies/codeact.py#L540)):

1. LLM returns `execute_python` tool call with `tool_call_id=A`
2. Code execution starts via `_execute_code()`
3. During execution, `await self.nested_method()` triggers a nested generation
4. The nested method uses the **same shared history** (`self.agent.history_manager`)
5. Nested agent adds its events: task → assistant (tool_call_id=B) → tool result (B)
6. After nested returns, `_handle_execute_python()` adds tool result for A

**Final history order:**
```text
[1] assistant: execute_python (tool_call_id=A)
[2] user: nested task
[3] assistant: return_result (tool_call_id=B)  ← Most recent assistant with tool calls
[4] tool: result for B
[5] tool: result for A  ← References A, but most recent is B!
```

When the next LLM call is made, message [5] fails validation because it references `tool_call_id=A` but the most recent assistant message with tool calls is [3] (with `tool_call_id=B`).

## Error Message

```text
Missing corresponding tool call for tool response message.
Received - message={'role': 'tool', 'tool_call_id': 'call_d3bf542bf8c5437da9e3115aba8f', ...}
last_message_with_tool_calls={'role': 'assistant', 'tool_calls': [{'id': 'call_d544aeaf25f64bde88e5c7edf3de', ...}]}
```

## Why History is Shared

Both outer and inner agent calls use the same `self.agent.history_manager`:

```python
# actor.py:268-270
@property
def history(self) -> Any:
    """History manager for conversation management."""
    return self.agent.history_manager
```

When nested method calls happen on the same agent (`self`), they share the same history.

## Why This is Baked Into the Architecture

The nemo_oo_agents framework **actively encourages** nested agent calls as a core design pattern:

### 1. Strategy Method Composition

The `@strategy` decorator allows any method to become an LLM-powered generation. This naturally leads to composition:

```python
class MyAgent(Agent):
    @strategy(CodeActStrategy())
    async def analyze(self, data: str) -> Analysis:
        """Analyze data - will call self.summarize()"""
        ...

    @strategy(CodeActStrategy())
    async def summarize(self, text: str) -> str:
        """Summarize text"""
        ...
```

The LLM is explicitly instructed it can call `self.summarize()` from within `analyze()`.

### 2. Self-Documentation Exposes Methods

The system prompt includes `doc(self)` which shows all available methods:

```text
class MyAgent:
    async def analyze(data: str) -> Analysis
    async def summarize(text: str) -> str  # ← LLM sees this and can call it
```

### 3. execute_python Enables Arbitrary Calls

The `execute_python` tool allows the LLM to run any Python code, including:

```python
result = await self.other_method(params)  # Nested agent call
```

This is the exact pattern that triggers the bug - the tool result for `execute_python` is added AFTER the nested method's events.

### 4. No Warning or Guard

There is currently no mechanism to:
- Detect that a nested agent call is about to happen
- Warn developers about the shared history issue
- Prevent the problematic pattern

## Reproduction

See test: `tests/integration/test_nested_agent_history_bug.py`

Minimal case:
1. Agent with `outer_method()` that is prompted to call `await self.inner_method()`
2. `inner_method()` returns a result
3. After inner method completes, outer method tries to continue → ERROR

## Affected Providers

### eval_pipeline Results

Tested with `tests/integration/nested_bug/config.yaml`:

| Provider | Model | Result | Error |
|----------|-------|--------|-------|
| Azure/OpenAI | gpt-5.1 | **FAIL** | `tool_calls must be followed by tool messages` |
| Azure/OpenAI | o4-mini | **FAIL** | `tool_calls must be followed by tool messages` |
| AWS/Bedrock | claude-haiku-4-5 | **FAIL** | `tool_use ids without tool_result blocks` |
| GCP/Google | gemini-2.5-flash-lite | PASS | Lenient validation |
| NVIDIA | gpt-oss-120b | PASS | Lenient validation |
| NVIDIA | Nemotron-3-Nano | Mixed | Some timeouts |

### Message Ordering Constraint Tests

Tested hand-built message streams to isolate the constraint (`tests/integration/nested_bug/test_message_ordering.py`):

| Stream Pattern | gpt-5 (Azure) | claude (Bedrock) | gemini (GCP) | gpt-oss (NVIDIA) |
|----------------|---------------|------------------|--------------|------------------|
| **Valid** (A→response A) | ✓ PASS | ✓ PASS | ✓ PASS | ✓ PASS |
| **Nested bug** (A→B→response B→response A) | ✗ FAIL | ✗ FAIL | ✗ FAIL | ✓ PASS |
| **Duplicate response** (A→response A→B→response B→response A) | ✗ FAIL | ✗ FAIL | ✗ FAIL | ✓ PASS |
| **Missing response** (A→user message) | ✗ FAIL | ✗ FAIL | ✓ PASS | ✓ PASS |

**Key findings:**
- **Azure/OpenAI & Bedrock/Claude**: Strict - tool responses must immediately follow their tool calls
- **GCP/Gemini**: Partial - allows missing responses but not out-of-order
- **NVIDIA models**: Lenient - accepts all message orderings

### Error Messages by Provider

**Azure/OpenAI:**
```text
An assistant message with 'tool_calls' must be followed by tool messages
responding to each 'tool_call_id'. The following tool_call_ids did not
have response messages: call_nYuwHLFBGIOy3x6eteH0FJ0D
```

**AWS/Bedrock (Claude):**
```text
messages.2: `tool_use` ids were found without `tool_result` blocks
immediately after: tooluse_w6kHbaKYRRCaGi7MpXNh4g. Each `tool_use` block
must have a corresponding `tool_result` block in the next message.
```

## Evidence

- Reproduction config: `tests/integration/nested_bug/config.yaml`
- Message ordering tests: `tests/integration/nested_bug/test_message_ordering.py`
- Agent definition: `tests/integration/nested_bug/agents.py`
- Trace files: `results/nested_bug/*/traces/`

## Potential Fixes

1. **Add placeholder before execution**: Add tool result with placeholder content before `_execute_code()`, then update with actual content after execution completes.

2. **Insert at correct position**: Track history length before execution, insert tool result at that position after execution.

3. **Isolated nested history**: Nested agents use a separate history that gets merged back in correct order.

4. **Synchronous tool result addition**: Restructure so tool result is added immediately after assistant message, before any code execution.
