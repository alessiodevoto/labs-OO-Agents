# History & Context Management System Phase 1 Implementation Plan
#
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
#
**Goal:** Fix invalid history exprs and add `HistoryManager.__getitem__` so renderers can emit valid `self.history[n].content` references.
#
**Architecture:** Add a simple position-based accessor on `HistoryManager` and update formatter/renderer expr templates to use `self.history[n]` with correct `.data` paths. Update tests that assert expr strings to match the new, valid expressions.
#
**Tech Stack:** Python, pytest, `context_blocks` formatters/renderers, `nemo_oo_agents` history runtime.
#
---
#
### Task 1: Add `HistoryManager.__getitem__` and document usage
#
**Files:**
- Modify: `src/nemo_oo_agents/runtime/history.py`
- Test: `tests/test_history_manager.py`
#
**Step 1: Write the failing test**
```python
def test_getitem_returns_event_by_position():
    """HistoryManager supports position-based indexing."""
    hm = HistoryManager()
    task = TaskEvent(content="First")
    hm.add(task)

    assert hm[0].content == "First"
```
#
**Step 2: Run test to verify it fails**
Run: `pytest tests/test_history_manager.py::test_getitem_returns_event_by_position -v`
Expected: FAIL with `TypeError: 'HistoryManager' object is not subscriptable`
#
**Step 3: Write minimal implementation**
```python
def __getitem__(self, position: int) -> EventBase:
    """Access event by position in the active view."""
    return self._events[position]
```
#
**Step 4: Update history docs in-place**
```python
# In __doc_full__ string:
# - add example: self.history[42].content
# - prefer self.history[...] over self.history.events[...]
```
#
**Step 5: Run test to verify it passes**
Run: `pytest tests/test_history_manager.py::test_getitem_returns_event_by_position -v`
Expected: PASS
#
**Step 6: Commit**
```bash
git add tests/test_history_manager.py src/nemo_oo_agents/runtime/history.py
git commit -m "$(cat <<'EOF'
feat: add HistoryManager indexing accessor
Allows position-based event access for valid expr references.
EOF
)"
```
#
---
#
### Task 2: Update formatter expr templates to use `self.history[n].*`
#
**Files:**
- Modify: `packages/context-blocks/src/context_blocks/formatter.py`
- Test: `packages/context-blocks/tests/test_formatters.py`
#
**Step 1: Update tests for expr strings**
```python
assert '<user_message expr="self.history[5].content">' in result
assert '<assistant_message expr="self.history[10].content">' in result
assert '<tool_call expr="self.history[15].arguments"' in result
assert '<tool_result expr="self.history[16].content">' in result
assert '<custom_special expr="self.history[99].content">' in result
```
#
**Step 2: Run test to verify it fails**
Run: `pytest packages/context-blocks/tests/test_formatters.py::TestXMLBlockFormatterEventFormatting -v`
Expected: FAIL with old expr strings
#
**Step 3: Update formatter exprs**
```python
# XMLBlockFormatter.format_event / MarkdownBlockFormatter.format_event
expr = f"self.history[{seq_id}].content"

# XMLBlockFormatter.format_tool_call / MarkdownBlockFormatter.format_tool_call
expr = f"self.history[{seq_id}].arguments"

# XMLBlockFormatter.format_tool_result / MarkdownBlockFormatter.format_tool_result
expr = f"self.history[{seq_id}].content"
```
Also update the example strings in docstrings to match the new expr format.
#
**Step 4: Run test to verify it passes**
Run: `pytest packages/context-blocks/tests/test_formatters.py::TestXMLBlockFormatterEventFormatting -v`
Expected: PASS
#
**Step 5: Commit**
```bash
git add packages/context-blocks/src/context_blocks/formatter.py \
        packages/context-blocks/tests/test_formatters.py
git commit -m "$(cat <<'EOF'
fix: align formatter exprs with history indexing
Use self.history[n].* in XML/Markdown event wrappers.
EOF
)"
```
#
---
#
### Task 3: Update BlockRenderer tests to match new exprs
#
**Files:**
- Modify: `packages/context-blocks/tests/test_renderer.py`
#
**Step 1: Update failing assertions**
```python
assert '<user_message expr="self.history[42].content">' in user_content
assert '<assistant_message expr="self.history[2].content">' in assistant_content
assert '<tool_result expr="self.history[3].content">' in tool_result_msg["content"]
```
#
**Step 2: Run test to verify it fails**
Run: `pytest packages/context-blocks/tests/test_renderer.py::TestBlockRendererEventFormatting -v`
Expected: FAIL with old expr strings
#
**Step 3: Re-run to verify it passes**
Run: `pytest packages/context-blocks/tests/test_renderer.py::TestBlockRendererEventFormatting -v`
Expected: PASS
#
**Step 4: Commit**
```bash
git add packages/context-blocks/tests/test_renderer.py
git commit -m "$(cat <<'EOF'
test: update renderer expr expectations
Align BlockRenderer XML wrappers with history indexing.
EOF
)"
```
#
---
#
### Task 4: Update StructuredOutputStrategy XML wrapper test
#
**Files:**
- Modify: `tests/runtime/test_structured_output_executor.py`
#
**Step 1: Update test XML string**
```python
content = '<assistant_message expr="self.history[1].content">{"name":"Alice","age":28}</assistant_message>'
```
#
**Step 2: Run test to verify it fails**
Run: `pytest tests/runtime/test_structured_output_executor.py::TestStripXMLWrapper::test_strip_xml_with_attributes -v`
Expected: FAIL with old expr string
#
**Step 3: Run test to verify it passes**
Run: `pytest tests/runtime/test_structured_output_executor.py::TestStripXMLWrapper::test_strip_xml_with_attributes -v`
Expected: PASS
#
**Step 4: Commit**
```bash
git add tests/runtime/test_structured_output_executor.py
git commit -m "$(cat <<'EOF'
test: update structured output wrapper example
Use self.history[n].content in XML example.
EOF
)"
```
#
---
#
### Task 5: Verify full phase-1 coverage
#
**Files:**
- No code changes (verification only)
#
**Step 1: Run targeted tests**
Run: `pytest tests/test_history_manager.py::test_getitem_returns_event_by_position \
packages/context-blocks/tests/test_formatters.py::TestXMLBlockFormatterEventFormatting \
packages/context-blocks/tests/test_renderer.py::TestBlockRendererEventFormatting \
tests/runtime/test_structured_output_executor.py::TestStripXMLWrapper::test_strip_xml_with_attributes -v`
Expected: PASS
#
**Step 2: Run broader suite (optional)**
Run: `pytest packages/context-blocks/tests/test_formatters.py packages/context-blocks/tests/test_renderer.py -v`
Expected: PASS
#
---
#
## Execution Handoff
#
**Plan complete and saved to `docs/plans/2026-01-23-history-context-management-system-phase-1.md`. Two execution options:**
#
**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration
#
**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints
#
**Which approach?**
