# Context Snapshot Hook Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Capture the rendered system prompt / context blocks in a dedicated OTel span on every LLM call, so that `detect_context_diffs` can reliably detect context changes even when the rolling window truncates the system message from the `acompletion` span.

**Architecture:** Add a new `on_llm_messages_built` hook to the nemo_oo_agents hook protocol. Call it from `generate()` in `actor.py` after building messages. In `_hooks_impl.py`, implement it to create a short-lived `context_snapshot` child span with the system content. Update `trace_converter.py` to prefer these spans when detecting context diffs, falling back to the old approach for older traces.

**Background:** LiteLLM's OTel instrumentation faithfully records only the messages that were actually sent to the LLM. When the rolling window is active, the system message (index 0) is not included in the messages array, so it never appears in the `acompletion` span. The OTel SDK's 128-attribute cap compounds this — it silently drops later attributes. The `context_snapshot` span is written by our own hook before the LLM call, so it always has the full system content regardless of windowing.

**Tech Stack:** Python, OpenTelemetry SDK (`opentelemetry-sdk`), nemo_oo_agents hooks protocol, `trace_converter.py` (sft_datagen package)

---

### Task 1: Add `on_llm_messages_built` to the hooks protocol

**Files:**
- Modify: `src/nemo_oo_agents/runtime/hooks.py:185-233`

**Step 1: Write the failing test**

In `src/nemo_oo_agents/runtime/test_hooks_protocol.py` (if it exists) or by checking the protocol is runtime-checkable. Actually, protocol compliance is checked at runtime — no new test needed here. Skip to implementation.

**Step 2: Add the hook method to `InstrumentationHooks`**

In `hooks.py`, after `after_tool_execution` (line ~285), add:

```python
def on_llm_messages_built(
    self,
    agent: Any,
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> None:
    """Called after LLM messages are built, immediately before the LLM API call.

    Note: LiteLLM's OTel instrumentation only records the messages that were
    actually sent to the LLM. When the rolling window is active, the system
    message is excluded from the messages array before the call, so it is
    absent from the ``acompletion`` span. This hook captures the rendered
    context blocks on every call so they are always available in the trace.

    Args:
        agent: Agent instance
        messages: Complete messages list built for this LLM call (may be
            a rolling-window slice that excludes the system message)
        **kwargs: Reserved for future use
    """
    ...
```

Also update `__all__` in `hooks.py` if it lists hook method names (it doesn't currently — no change needed).

**Step 3: Verify nothing breaks**

```bash
cd /Volumes/dev/dev/cleanup
source .venv/bin/activate
python -c "from nemo_oo_agents.runtime.hooks import InstrumentationHooks; print('OK')"
```

Expected: `OK`

**Step 4: Commit**

```bash
git add src/nemo_oo_agents/runtime/hooks.py
git commit -m "feat(hooks): add on_llm_messages_built hook to InstrumentationHooks protocol"
```

---

### Task 2: Call the hook from `generate()` in `actor.py`

**Files:**
- Modify: `src/nemo_oo_agents/runtime/actor.py:533-551`

**Step 1: Locate the call site**

In `generate()` (around line 534), messages are built and immediately passed to `llm_client.acall`:

```python
messages = await self._build_messages(...)

# ... hook call goes here ...

response = await llm_client.acall(messages, ...)
```

**Step 2: Add the hook call**

After `messages = await self._build_messages(...)`, insert:

```python
call_before_hook(
    "on_llm_messages_built",
    agent=self.agent,
    messages=messages,
)
```

`call_before_hook` is already imported at the top of `actor.py` (line 25). It handles exceptions defensively so a hook failure never breaks generation.

**Step 3: Verify no regressions**

```bash
cd /Volumes/dev/dev/cleanup
source .venv/bin/activate
python -m pytest src/ -q --tb=short 2>&1 | tail -10
```

Expected: same pass count as before (hook is a no-op when no hooks are installed).

**Step 4: Commit**

```bash
git add src/nemo_oo_agents/runtime/actor.py
git commit -m "feat(actor): call on_llm_messages_built hook after building LLM messages"
```

---

### Task 3: Implement the hook in `_hooks_impl.py`

**Files:**
- Modify: `packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_hooks_impl.py`

**Step 1: Write the failing test**

In `packages/openinference-instrumentation-nemo-oo-agents/tests/`, add a test for `on_llm_messages_built`. Check the existing test patterns (e.g., `test_tracing_integration.py`) to understand how spans are captured in tests.

The test should verify:
- A `context_snapshot` span is created when messages include a system message
- The span has `nemo_oo_agents.context_blocks` set to the system message content
- No span is created when there is no system message

```python
def test_on_llm_messages_built_creates_context_snapshot_span():
    hooks = OpenInferenceHooks(tracer=mock_tracer)
    messages = [
        {"role": "system", "content": "<info expr=\"x\">\nvalue\n</info>"},
        {"role": "user", "content": "hello"},
    ]
    hooks.on_llm_messages_built(agent=mock_agent, messages=messages)
    # Verify a span named "context_snapshot" was created with the right attribute
    span = captured_spans["context_snapshot"]
    assert span.attributes["nemo_oo_agents.context_blocks"] == messages[0]["content"]

def test_on_llm_messages_built_no_span_without_system_message():
    hooks = OpenInferenceHooks(tracer=mock_tracer)
    messages = [{"role": "user", "content": "hello"}]
    hooks.on_llm_messages_built(agent=mock_agent, messages=messages)
    # No context_snapshot span created
    assert "context_snapshot" not in captured_spans
```

**Step 2: Run test to verify it fails**

```bash
cd /Volumes/dev/dev/cleanup/packages/openinference-instrumentation-nemo-oo-agents
/Volumes/dev/dev/cleanup/.venv/bin/python -m pytest tests/ -k "context_snapshot" -v
```

Expected: FAIL — method doesn't exist yet.

**Step 3: Implement the hook method**

Add to `OpenInferenceHooks` class in `_hooks_impl.py`:

```python
def on_llm_messages_built(
    self,
    agent: Any,
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> None:
    """Create a context_snapshot span capturing the system message content.

    Note: LiteLLM's OTel instrumentation only records the messages that were
    actually sent to the LLM. When the rolling window is active, the system
    message is excluded from the messages array before the call, so it is
    absent from the ``acompletion`` span. This hook captures the rendered
    context blocks on every call so they are always available in the trace.
    """
    system_content = next(
        (m.get("content") for m in messages if m.get("role") == "system"),
        None,
    )
    if not system_content:
        return

    with self.tracer.start_as_current_span("context_snapshot") as span:
        span.set_attribute("nemo_oo_agents.context_blocks", system_content)
```

The `with` block ends immediately, so the span is created and closed in-place. It becomes a child of whatever span is currently active (the agent method span).

**Step 4: Run tests to verify they pass**

```bash
cd /Volumes/dev/dev/cleanup/packages/openinference-instrumentation-nemo-oo-agents
/Volumes/dev/dev/cleanup/.venv/bin/python -m pytest tests/ -q
```

Expected: all pass.

**Step 5: Commit**

```bash
git add packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_hooks_impl.py
git add packages/openinference-instrumentation-nemo-oo-agents/tests/
git commit -m "feat(instrumentation): add context_snapshot span in on_llm_messages_built hook"
```

---

### Task 4: Update `trace_converter.py` to use `context_snapshot` spans

**Files:**
- Modify: `experiments/sft_datagen/sft_datagen/trace_converter.py`
- Modify: `experiments/sft_datagen/tests/test_trace_converter.py`

**Background:** `detect_context_diffs` currently reads the system message from `acompletion` spans via `_get_system_content`. For old traces (no `context_snapshot` spans) we keep the existing fallback. For new traces, use `context_snapshot` spans which always have the content.

**Step 1: Update `_get_system_content` to check `nemo_oo_agents.context_blocks` first**

```python
def _get_system_content(attrs: dict[str, Any]) -> str:
    """Return the system message content from a span.

    Checks nemo_oo_agents.context_blocks first (set by the context_snapshot hook,
    always reliable). Falls back to scanning llm.input_messages for older
    traces or spans that lack the hook attribute.
    """
    # Prefer the dedicated context_blocks attribute (context_snapshot spans
    # and any acompletion spans that were enhanced by the hook)
    if ctx := attrs.get("nemo_oo_agents.context_blocks"):
        return ctx
    # Fallback: find the system message in input_messages (may be absent
    # for rolling-window spans that dropped message index 0)
    start, count = _input_message_range(attrs)
    for i in range(start, start + count):
        if attrs.get(f"llm.input_messages.{i}.message.role") == "system":
            return attrs.get(f"llm.input_messages.{i}.message.content", "")
    return ""
```

**Step 2: Write tests for the new `_get_system_content` behaviour**

```python
def test_get_system_content_prefers_context_blocks_attr():
    attrs = {
        "nemo_oo_agents.context_blocks": "from hook",
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": "from messages",
    }
    assert _get_system_content(attrs) == "from hook"

def test_get_system_content_falls_back_to_messages():
    attrs = {
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": "from messages",
    }
    assert _get_system_content(attrs) == "from messages"

def test_get_system_content_empty_when_rolling_window_and_no_hook():
    # Rolling window span: starts at index 3, no system message, no hook attr
    attrs = {
        "llm.input_messages.3.message.role": "tool",
        "llm.input_messages.3.message.content": "result",
    }
    assert _get_system_content(attrs) == ""
```

**Step 3: Update `extract_conversation` to load and prefer `context_snapshot` spans**

In `extract_conversation`, after loading `agent_spans`, add:

```python
# Load context_snapshot spans (created by on_llm_messages_built hook).
# These are more reliable than reading llm.input_messages.0 from acompletion
# spans, because the rolling window may exclude the system message from the
# actual LLM call. context_snapshot spans are written by our own hook before
# the LLM call, so they always contain the full system content.
context_snapshot_spans = [
    s for s in all_spans if s.get("name") == "context_snapshot"
]

# Use context_snapshot spans for context diff detection if we have one per
# agent span (newer traces). Fall back to agent_spans for older traces.
diff_spans = (
    context_snapshot_spans
    if len(context_snapshot_spans) == len(agent_spans)
    else agent_spans
)
```

Then replace the `detect_context_diffs` call:

```python
context_diffs = detect_context_diffs(diff_spans, message_end_counts)
```

**Step 4: Write integration test**

Add a test that checks context diffs are detected even when `acompletion` spans lack message 0:

```python
def test_detect_context_diffs_uses_context_snapshot_spans():
    """Context diffs detected via context_snapshot spans when rolling window active."""
    # Simulate: two context_snapshot spans with changing content
    snapshot_spans = [
        {"name": "context_snapshot", "attributes": {
            "nemo_oo_agents.context_blocks": (
                "<info expr=\"x\">\nold value\n</info>"
            ),
        }},
        {"name": "context_snapshot", "attributes": {
            "nemo_oo_agents.context_blocks": (
                "<info expr=\"x\">\nnew value\n</info>"
            ),
        }},
    ]
    diffs = detect_context_diffs(snapshot_spans, [5, 10])
    assert len(diffs) == 1
    assert diffs[0]["key"] == "info"
    assert diffs[0]["value"] == "new value"
    assert diffs[0]["after_index"] == 4  # message_counts[0] - 1 = 5 - 1
```

**Step 5: Run all trace_converter tests**

```bash
cd /Volumes/dev/dev/cleanup/experiments/sft_datagen
/Volumes/dev/dev/cleanup/.venv/bin/python -m pytest tests/test_trace_converter.py -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add experiments/sft_datagen/sft_datagen/trace_converter.py
git add experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "feat(trace-converter): use context_snapshot spans for reliable context diff detection"
```

---

## Verification

After all tasks:

```bash
# Full test suite for affected packages
cd /Volumes/dev/dev/cleanup/experiments/sft_datagen
/Volumes/dev/dev/cleanup/.venv/bin/python -m pytest tests/ -v

cd /Volumes/dev/dev/cleanup/packages/openinference-instrumentation-nemo-oo-agents
/Volumes/dev/dev/cleanup/.venv/bin/python -m pytest tests/ -v
```

Both should be green.
