# Plain Message Formatter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `PlainBlockFormatter` that renders conversation messages as plain text instead of XML-wrapped pformat reprs, enabling A/B testing of simpler LLM context formatting.

**Architecture:** Defer event serialization from `_phase_events` (context builder) to `render_context` (renderer), so the `BlockFormatter` controls both system-block formatting and event content rendering. Add `format_event(event) -> str` as an abstract method on `BlockFormatter`. `XMLBlockFormatter` preserves current pformat behaviour; new `PlainBlockFormatter` renders events as plain human-readable text with no XML wrapper.

**Tech Stack:** Python, Pydantic, pytest, packages/context-blocks, src/nemo_oo_agents

---

## Background: Current vs Proposed Flow

**Current flow:**
```
_phase_events()  →  content = agentdoc_pformat(event)   ← serialized here
render_context() →  content = "<user_message expr=...>\n{content}\n</user_message>"
provider_fmt()   →  {"role": "user", "content": "<user_message ...>PythonOutput(...)</user_message>"}
```

**Proposed flow:**
```
_phase_events()  →  content = ""  (raw event on block.event, like ToolCallEvent today)
render_context() →  content = block_formatter.format_event(event)   ← serialized here
                 →  content = format_message_content(block, format_type)  ← wrapped here
provider_fmt()   →  {"role": "user", "content": "42\n[captured: result (int)]"}
```

**Key invariant:** Event serialization must happen BEFORE truncation in `render_context()`, so `_truncate_blocks` and `_apply_event_total_limit` see real content sizes.

---

## Task 1: Defer event serialization in `_phase_events`

**Files:**
- Modify: `src/nemo_oo_agents/runtime/context_builder.py` (around line 399–413)
- Modify test: `tests/runtime/test_context_builder.py` (around line 505)

**Step 1: Write updated test for deferred serialization**

In `tests/runtime/test_context_builder.py`, find `test_user_event` and update it. After the change, `_phase_events` will produce `content=""` for non-ToolCall events (the raw event is on `block.event`):

```python
def test_user_event(self):
    from nemo_oo_agents.runtime.context_builder import _phase_events

    event = UserEvent(content="Hello", tag="1")
    em = _make_event_manager([event])
    result = _phase_events([], em)

    assert len(result) == 1
    assert result[0].key == "event_1"
    assert result[0].role == Role.USER
    assert result[0].content == ""          # deferred — serialized at render time
    assert result[0].event is event         # raw event carried through
```

**Step 2: Run the test to confirm it currently FAILS**

```bash
source .venv/bin/activate && python -m pytest tests/runtime/test_context_builder.py::TestPhaseEvents::test_user_event -v
```
Expected: FAIL — `AssertionError: 'Hello' in 'Hello'` (content is NOT empty yet)

**Step 3: Change `_phase_events` to defer non-tool event serialization**

In `src/nemo_oo_agents/runtime/context_builder.py`, find the `else` branch in `_phase_events` (currently calls `agentdoc_pformat`). Replace:

```python
        else:
            content = agentdoc_pformat(
                event, max_string=sys.maxsize, max_length=sys.maxsize, max_depth=sys.maxsize
            )
            new_blocks.append(
                ResolvedBlock(
                    key=f"event_{tag}",
                    content=content,
                    role=event_role,
                    metadata=meta,
                    event=event,
                )
            )
```

With:

```python
        else:
            # Serialization is deferred to render_context() via block_formatter.format_event().
            # Raw event is carried on block.event — same pattern as ToolCallEvent.
            new_blocks.append(
                ResolvedBlock(
                    key=f"event_{tag}",
                    content="",
                    role=event_role,
                    metadata=meta,
                    event=event,
                )
            )
```

Also remove the `agentdoc_pformat` import from the top of `context_builder.py` if it's no longer used elsewhere in that file. Check with grep first:

```bash
grep -n "agentdoc_pformat" src/nemo_oo_agents/runtime/context_builder.py
```

If it only appears in `_phase_events`, remove the import line:
```python
from agentdoc import pformat as agentdoc_pformat
```

**Step 4: Run the updated test — should pass**

```bash
source .venv/bin/activate && python -m pytest tests/runtime/test_context_builder.py::TestPhaseEvents::test_user_event -v
```
Expected: PASS

**Step 5: Run ALL context_builder tests — some render_context integration tests may fail now**

```bash
source .venv/bin/activate && python -m pytest tests/runtime/test_context_builder.py -v
```
Expected: Most pass. Note any failures — they will be fixed in Task 3.

**Step 6: Commit**

```bash
git add src/nemo_oo_agents/runtime/context_builder.py tests/runtime/test_context_builder.py
git commit -m "refactor: defer non-tool event serialization to render time"
```

---

## Task 2: Add `format_event()` to `BlockFormatter` and `XMLBlockFormatter`

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/formatter.py`
- Modify: `packages/context-blocks/tests/test_formatters.py`

**Step 1: Write a failing test for `XMLBlockFormatter.format_event()`**

Add a new test class at the bottom of `packages/context-blocks/tests/test_formatters.py`:

```python
class TestBlockFormatterFormatEvent:
    """Tests for BlockFormatter.format_event() — serializes raw events to content strings."""

    def test_xml_format_event_user_event(self):
        """XMLBlockFormatter.format_event() should call agentdoc_pformat on the event."""
        from context_blocks.events import UserEvent

        formatter = XMLBlockFormatter()
        event = UserEvent(content="Hello world", tag="1")
        result = formatter.format_event(event)

        # Should contain the content field (pformat repr of UserEvent)
        assert "Hello world" in result

    def test_format_event_is_abstract(self):
        """BlockFormatter subclasses must implement format_event()."""
        from context_blocks.formatter import BlockFormatter

        class IncompleteFormatter(BlockFormatter):
            @property
            def format_type(self):
                return "xml"

            def format(self, blocks):
                return ""

        with pytest.raises(TypeError):
            IncompleteFormatter()
```

**Step 2: Run tests to confirm FAIL**

```bash
source .venv/bin/activate && python -m pytest packages/context-blocks/tests/test_formatters.py::TestBlockFormatterFormatEvent -v
```
Expected: FAIL — `AttributeError: 'XMLBlockFormatter' object has no attribute 'format_event'`

**Step 3: Add `format_event` as abstract method on `BlockFormatter`**

In `packages/context-blocks/src/context_blocks/formatter.py`, add the import at the top:

```python
import sys

from context_blocks.events import EventBase, ToolCallEvent
```

Then add the abstract method to the `BlockFormatter` ABC (after the existing `format` method):

```python
    @abstractmethod
    def format_event(self, event: "EventBase") -> str:
        """Serialize a non-tool event into a content string.

        Called by render_context() for each non-ToolCallEvent message block
        before truncation. The result becomes block.content.

        Args:
            event: The raw event to serialize.

        Returns:
            String content to use as the block's message content.
        """
        ...
```

**Step 4: Implement `format_event` on `XMLBlockFormatter`**

Add this method to `XMLBlockFormatter` (preserves current behaviour):

```python
    def format_event(self, event: "EventBase") -> str:
        from agentdoc import pformat as agentdoc_pformat

        return agentdoc_pformat(
            event, max_string=sys.maxsize, max_length=sys.maxsize, max_depth=sys.maxsize
        )
```

**Step 5: Implement `format_event` on `MarkdownBlockFormatter`**

Same implementation — pformat is format-agnostic:

```python
    def format_event(self, event: "EventBase") -> str:
        from agentdoc import pformat as agentdoc_pformat

        return agentdoc_pformat(
            event, max_string=sys.maxsize, max_length=sys.maxsize, max_depth=sys.maxsize
        )
```

**Step 6: Run tests — should pass**

```bash
source .venv/bin/activate && python -m pytest packages/context-blocks/tests/test_formatters.py -v
```
Expected: All pass including new tests.

**Step 7: Commit**

```bash
git add packages/context-blocks/src/context_blocks/formatter.py packages/context-blocks/tests/test_formatters.py
git commit -m "feat: add format_event() abstract method to BlockFormatter"
```

---

## Task 3: Serialize events in `render_context()` — restore and verify behaviour

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/renderer.py`
- Modify: `packages/context-blocks/tests/test_renderer.py`

**Step 1: Write a test that verifies event content appears in final output**

Add to `packages/context-blocks/tests/test_renderer.py`:

```python
class TestRenderContextEventSerialization:
    """Tests for the event serialization step in render_context()."""

    def test_non_tool_event_content_serialized_before_output(self):
        """render_context() must serialize event content via block_formatter.format_event()."""
        from context_blocks.events import UserEvent

        event = UserEvent(content="Hello world", tag="1")
        block = ResolvedBlock(
            key="event_1",
            content="",             # deferred — as produced by _phase_events now
            role=Role.USER,
            metadata=BlockMetadata(expr="self.events['1']", tag="1"),
            event=event,
        )
        result = render_context(
            [block],
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        )

        # Content must appear in the user message
        user_msg = result[1]
        assert user_msg["role"] == "user"
        assert "Hello world" in user_msg["content"]

    def test_event_serialization_happens_before_truncation(self):
        """Truncation must see serialized content, not empty strings."""
        from context_blocks.events import UserEvent

        # Create an event whose serialized form will be > 50 chars
        long_content = "A" * 200
        event = UserEvent(content=long_content, tag="1")
        block = ResolvedBlock(
            key="event_1",
            content="",
            role=Role.USER,
            metadata=BlockMetadata(tag="1"),
            event=event,
        )

        result = render_context(
            [block],
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            block_limit=50,  # per-block limit
        )

        # Content should be truncated (not the original 200+ chars)
        user_msg = result[1]
        assert "truncat" in user_msg["content"].lower()
```

**Step 2: Run tests to confirm FAIL**

```bash
source .venv/bin/activate && python -m pytest packages/context-blocks/tests/test_renderer.py::TestRenderContextEventSerialization -v
```
Expected: FAIL — `content=""` still in output (serialization step not added yet)

**Step 3: Add event serialization step to `render_context()`**

In `packages/context-blocks/src/context_blocks/renderer.py`, find the pre-format message loop (currently around line 221). Replace the existing loop:

```python
    # Pre-format message blocks with metadata wrapping (before provider assembly)
    # Tool-call blocks are skipped — they carry the original event, not string content.
    formatted_messages: list[ResolvedBlock] = []
    for block in message_blocks:
        if not isinstance(block.event, ToolCallEvent) and (
            block.metadata.expr or block.metadata.tag
        ):
            content = format_message_content(block, formatter_type)
            block = block.model_copy(update={"content": content})
        formatted_messages.append(block)
```

With the new two-step version:

```python
    # Pre-format message blocks: first serialize event content, then wrap with metadata.
    # Tool-call blocks are handled by ProviderFormatter directly via block.event.
    formatted_messages: list[ResolvedBlock] = []
    for block in message_blocks:
        # Step 1: Serialize event content (deferred from _phase_events).
        # ToolCallEvent blocks keep content="" — ProviderFormatter reads block.event directly.
        if block.event is not None and not isinstance(block.event, ToolCallEvent):
            content = block_formatter.format_event(block.event)
            block = block.model_copy(update={"content": content})

        # Step 2: Wrap with metadata (XML tag, markdown header, plain/nothing).
        if not isinstance(block.event, ToolCallEvent) and (
            block.metadata.expr or block.metadata.tag
        ):
            content = format_message_content(block, formatter_type)
            block = block.model_copy(update={"content": content})

        formatted_messages.append(block)
```

**Step 4: Run the new tests — should pass**

```bash
source .venv/bin/activate && python -m pytest packages/context-blocks/tests/test_renderer.py -v
```
Expected: All pass.

**Step 5: Run full test suite — everything should be green**

```bash
source .venv/bin/activate && python -m pytest packages/context-blocks/tests/ tests/runtime/test_context_builder.py -v
```
Expected: All pass. This confirms the refactor preserves existing behaviour end-to-end.

**Step 6: Commit**

```bash
git add packages/context-blocks/src/context_blocks/renderer.py packages/context-blocks/tests/test_renderer.py
git commit -m "feat: serialize events in render_context() via block_formatter.format_event()"
```

---

## Task 4: Add `FormatType.PLAIN` and plain branch in `format_message_content()`

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/formatter.py`
- Modify: `packages/context-blocks/src/context_blocks/renderer.py`
- Modify: `packages/context-blocks/tests/test_renderer.py`

**Step 1: Write the failing test**

Add to `packages/context-blocks/tests/test_renderer.py`:

```python
class TestFormatMessageContentPlain:
    """Tests for plain format_type in format_message_content()."""

    def test_plain_returns_content_unchanged(self):
        """Plain format should return content without any wrapper."""
        from context_blocks.renderer import format_message_content

        block = ResolvedBlock(
            key="msg",
            content="Hello world",
            role=Role.USER,
            metadata=BlockMetadata(expr="self.events['1']", tag="1"),
        )
        result = format_message_content(block, "plain")

        assert result == "Hello world"
        assert "<" not in result  # no XML tags
        assert "#" not in result  # no markdown headers

    def test_plain_assistant_message_unchanged(self):
        """Plain format returns assistant content as-is."""
        from context_blocks.renderer import format_message_content

        block = ResolvedBlock(
            key="msg",
            content="result = 42",
            role=Role.ASSISTANT,
            metadata=BlockMetadata(tag="3"),
        )
        result = format_message_content(block, "plain")
        assert result == "result = 42"
```

**Step 2: Run tests to confirm FAIL**

```bash
source .venv/bin/activate && python -m pytest packages/context-blocks/tests/test_renderer.py::TestFormatMessageContentPlain -v
```
Expected: FAIL — no `"plain"` branch exists yet

**Step 3: Add `PLAIN` to `FormatType` enum**

In `packages/context-blocks/src/context_blocks/formatter.py`, extend the `FormatType` enum:

```python
class FormatType(StrEnum):
    XML = "xml"
    MARKDOWN = "markdown"
    PLAIN = "plain"

# Convenience aliases
FORMAT_XML = FormatType.XML
FORMAT_MARKDOWN = FormatType.MARKDOWN
FORMAT_PLAIN = FormatType.PLAIN
```

**Step 4: Add plain branch to `format_message_content()`**

In `packages/context-blocks/src/context_blocks/renderer.py`, extend the function:

```python
def format_message_content(block: ResolvedBlock, format_type: str) -> str:
    if format_type == FORMAT_XML:
        role_label = block.role.value + "_message"
        attr_parts = []
        if block.metadata.expr:
            attr_parts.append(f'expr="{block.metadata.expr}"')
        if block.metadata.tag:
            attr_parts.append(f'tag="{block.metadata.tag}"')
        attrs = (" " + " ".join(attr_parts)) if attr_parts else ""
        return f"<{role_label}{attrs}>\n{block.content}\n</{role_label}>"
    elif format_type == FORMAT_PLAIN:
        # No wrapper — content as-is. Tag-based referencing is explained
        # via doc(self.events) in the system prompt instead.
        return block.content
    else:
        role_label = block.role.value.replace("_", " ").title() + " Message"
        meta_parts = []
        if block.metadata.expr:
            meta_parts.append(f'"expr": "{block.metadata.expr}"')
        if block.metadata.tag:
            meta_parts.append(f'"tag": "{block.metadata.tag}"')
        inline_meta = (" `{" + ", ".join(meta_parts) + "}`") if meta_parts else ""
        return f"### {role_label}{inline_meta}\n\n{block.content}"
```

Note: Also add the import at the top of `renderer.py`:
```python
from context_blocks.formatter import FORMAT_PLAIN, FORMAT_XML, BlockFormatter, ProviderFormatter
```

**Step 5: Run the tests — should pass**

```bash
source .venv/bin/activate && python -m pytest packages/context-blocks/tests/test_renderer.py -v
```
Expected: All pass.

**Step 6: Commit**

```bash
git add packages/context-blocks/src/context_blocks/formatter.py packages/context-blocks/src/context_blocks/renderer.py packages/context-blocks/tests/test_renderer.py
git commit -m "feat: add FormatType.PLAIN and plain branch to format_message_content()"
```

---

## Task 5: Create `PlainBlockFormatter` in nemo_oo_agents

**Files:**
- Create: `src/nemo_oo_agents/plain_formatter.py`
- Create: `tests/test_plain_formatter.py`

This formatter lives in the nemo_oo_agents package (not context-blocks) so it can import nemo_oo_agents-specific event types like `PythonOutput`, `Task`, `Error`.

**Step 1: Write the failing tests first**

Create `tests/test_plain_formatter.py`:

```python
"""Tests for PlainBlockFormatter — plain text event serialization."""

import pytest

from context_blocks.formatter import FormatType
from context_blocks.models import BlockMetadata, ResolvedBlock, Role


class TestPlainBlockFormatterFormatType:
    def test_format_type_is_plain(self):
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter

        assert PlainBlockFormatter().format_type == FormatType.PLAIN


class TestPlainBlockFormatterFormatSystemBlocks:
    """System blocks (format()) are identical to XMLBlockFormatter."""

    def test_format_delegates_to_xml(self):
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter

        formatter = PlainBlockFormatter()
        blocks = [ResolvedBlock(key="persona", content="You are helpful.")]
        result = formatter.format(blocks)

        assert "<persona>" in result
        assert "You are helpful." in result


class TestPlainBlockFormatterFormatEvent:
    """format_event() renders each event type as clean plain text."""

    def test_task_renders_prompt(self):
        from nemo_oo_agents.events import Task
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter

        event = Task(prompt="Analyze the data.")
        result = PlainBlockFormatter().format_event(event)

        assert result == "Analyze the data."
        assert "Task(" not in result  # no pformat repr

    def test_error_renders_content(self):
        from nemo_oo_agents.events import Error
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter

        event = Error(content="NameError: name 'x' is not defined")
        result = PlainBlockFormatter().format_event(event)

        assert result == "NameError: name 'x' is not defined"

    def test_python_output_stdout_only(self):
        from nemo_oo_agents.events import PythonOutput
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter
        from context_blocks.events import ResultStatus

        event = PythonOutput(
            tool_call_id="tc_1",
            status=ResultStatus.COMPLETE,
            execution_count=1,
            stdout="42\n",
        )
        result = PlainBlockFormatter().format_event(event)

        assert "42" in result
        assert "PythonOutput(" not in result

    def test_python_output_with_error(self):
        from nemo_oo_agents.events import PythonOutput
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter
        from context_blocks.events import ResultStatus

        event = PythonOutput(
            tool_call_id="tc_1",
            status=ResultStatus.ERROR,
            execution_count=1,
            stdout="",
            error="NameError: name 'foo' is not defined",
        )
        result = PlainBlockFormatter().format_event(event)

        assert "NameError" in result
        assert "[error]" in result

    def test_python_output_with_captured_locals(self):
        from nemo_oo_agents.events import PythonOutput
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter
        from context_blocks.events import ResultStatus

        event = PythonOutput(
            tool_call_id="tc_1",
            status=ResultStatus.COMPLETE,
            execution_count=1,
            stdout="done\n",
            captured_locals="result (list)",
        )
        result = PlainBlockFormatter().format_event(event)

        assert "done" in result
        assert "[captured: result (list)]" in result

    def test_python_output_no_output(self):
        from nemo_oo_agents.events import PythonOutput
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter
        from context_blocks.events import ResultStatus

        event = PythonOutput(
            tool_call_id="tc_1",
            status=ResultStatus.COMPLETE,
            execution_count=1,
        )
        result = PlainBlockFormatter().format_event(event)

        assert result == "(no output)"

    def test_generic_event_with_content_field(self):
        """Events with a 'content' field fall back to returning content directly."""
        from context_blocks.events import UserEvent
        from nemo_oo_agents.plain_formatter import PlainBlockFormatter

        event = UserEvent(content="Hello there")
        result = PlainBlockFormatter().format_event(event)

        assert result == "Hello there"
        assert "UserEvent(" not in result
```

**Step 2: Run tests to confirm FAIL**

```bash
source .venv/bin/activate && python -m pytest tests/test_plain_formatter.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'nemo_oo_agents.plain_formatter'`

**Step 3: Create `PlainBlockFormatter`**

Create `src/nemo_oo_agents/plain_formatter.py`:

```python
"""PlainBlockFormatter — renders conversation messages as plain text.

An experimental alternative to XMLBlockFormatter that strips XML wrapping
and renders events as clean, human-readable text rather than pformat reprs.

Use by setting _block_formatter on your agent:
    class MyAgent(Agent):
        _block_formatter = PlainBlockFormatter()
"""

from context_blocks.events import EventBase
from context_blocks.formatter import FORMAT_PLAIN, FORMAT_XML, BlockFormatter, FormatType, XMLBlockFormatter
from context_blocks.models import ResolvedBlock


class PlainBlockFormatter(BlockFormatter):
    """Renders system blocks as XML (same as XMLBlockFormatter) but serializes
    conversation events as plain text with no XML wrapper.

    Produces cleaner, more token-efficient messages for the LLM:
        - PythonOutput → stdout/stderr/error as plain text
        - Task/Error/Message/Feedback → just the content string
        - Generic events → content field if present, else pformat fallback
    """

    @property
    def format_type(self) -> FormatType:
        return FORMAT_PLAIN

    def format(self, blocks: list[ResolvedBlock]) -> str:
        """Format system blocks using XML (same as XMLBlockFormatter)."""
        return XMLBlockFormatter().format(blocks)

    def format_event(self, event: EventBase) -> str:
        """Serialize a conversation event as plain text."""
        # PythonOutput: has stdout/stderr/error/captured_locals fields
        if hasattr(event, "stdout"):
            return _format_python_output(event)

        # Events with a simple content field (Task has prompt, others have content)
        if hasattr(event, "prompt"):
            return event.prompt

        if hasattr(event, "content"):
            return event.content

        # Fallback: pformat (handles unknown event types gracefully)
        from agentdoc import pformat as agentdoc_pformat
        import sys
        return agentdoc_pformat(
            event, max_string=sys.maxsize, max_length=sys.maxsize, max_depth=sys.maxsize
        )


def _format_python_output(event: EventBase) -> str:
    """Render a PythonOutput event as plain text output."""
    parts = []

    stdout = getattr(event, "stdout", "")
    stderr = getattr(event, "stderr", "")
    error = getattr(event, "error", "")
    captured = getattr(event, "captured_locals", "")

    if stdout:
        parts.append(stdout.rstrip())
    if stderr:
        parts.append(f"[stderr]\n{stderr.rstrip()}")
    if error:
        parts.append(f"[error]\n{error.rstrip()}")
    if captured:
        parts.append(f"[captured: {captured}]")

    return "\n".join(parts) if parts else "(no output)"
```

**Step 4: Run tests — should pass**

```bash
source .venv/bin/activate && python -m pytest tests/test_plain_formatter.py -v
```
Expected: All pass.

**Step 5: Run full test suite to confirm nothing broken**

```bash
source .venv/bin/activate && python -m pytest packages/context-blocks/tests/ tests/ -v
```
Expected: All pass.

**Step 6: Commit**

```bash
git add src/nemo_oo_agents/plain_formatter.py tests/test_plain_formatter.py
git commit -m "feat: add PlainBlockFormatter for plain-text message rendering"
```

---

## Task 6: Smoke test — use `PlainBlockFormatter` on a real agent

**Files:**
- No permanent changes — just verify it works end-to-end

**Step 1: Quick smoke test in a scratch script**

Create `$TMPDIR/smoke_plain_formatter.py` (temp file, not committed):

```python
"""Smoke test: verify PlainBlockFormatter produces clean output."""
import asyncio
import sys
sys.path.insert(0, "src")

from nemo_oo_agents import Agent
from nemo_oo_agents.plain_formatter import PlainBlockFormatter
from nemo_oo_agents.events import Task, PythonOutput
from context_blocks.events import ResultStatus


class TestAgent(Agent):
    _block_formatter = PlainBlockFormatter()

    async def greet(self) -> str:
        return await self.generate()


async def main():
    agent = TestAgent()

    # Simulate what messages look like by calling _build_messages
    # (This is internal but sufficient for smoke testing)
    from nemo_oo_agents.events import Task
    await agent.events.add(Task(prompt="Say hello."))

    # Check format_event directly
    po = PythonOutput(
        tool_call_id="tc_1",
        status=ResultStatus.COMPLETE,
        execution_count=1,
        stdout="Hello, world!\n",
        captured_locals="greeting (str)",
    )
    result = agent._block_formatter.format_event(po)
    print("PythonOutput renders as:")
    print(repr(result))
    assert "Hello, world!" in result
    assert "PythonOutput(" not in result
    assert "[captured: greeting (str)]" in result
    print("OK")


asyncio.run(main())
```

Run:
```bash
source .venv/bin/activate && python $TMPDIR/smoke_plain_formatter.py
```
Expected: Prints `OK` with no errors.

---

## Usage After Implementation

To use the plain formatter on an agent:

```python
from nemo_oo_agents import Agent
from nemo_oo_agents.plain_formatter import PlainBlockFormatter

class MyAgent(Agent):
    _block_formatter = PlainBlockFormatter()
```

This replaces XML-wrapped pformat reprs:
```
# Before (XML + pformat):
<user_message expr='self.events["5"]' tag="5">
PythonOutput(tool_call_id='call_abc', execution_count=1, stdout='42\n', status='complete', captured_locals='result (int)')
</user_message>

# After (plain):
42
[captured: result (int)]
```
