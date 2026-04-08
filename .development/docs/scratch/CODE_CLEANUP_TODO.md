# Code Cleanup TODO

Tracking document for code cleanup MRs. Each section is scoped for a single MR.

---

## Future Considerations (Not Urgent)

### Simplify HistoryManager API
The dual low-level (`add_user_message`) and high-level (`record_llm_response`) APIs could be confusing. Consider deprecating low-level API for external use.

### State Diff Capture
`_capture_agent_state()` in executor captures everything as strings. Consider making this opt-in or more targeted.

---

## Skipped

### MR 3: Remove Unused Agent Attributes
**Status:** Skipped - attributes ARE used in `runtime/prompts.py`

### MR 8: Simplify Prompt Loader (Remove Speculative Versioning)
**Status:** Skipped - not urgent, versioning may be needed later

### MR 11: Reduce Context Variable Exposure
**Status:** Skipped - low value, underscore prefix already signals internal use

---

## Completed

### MR 1: Remove Dead Error Classes
**MR:** https://gitlab-master.nvidia.com/interactive-agents/agent006/-/merge_requests/3

- `src/agent006/errors/__init__.py` - Removed `SignalError`, `TaskQueueError`, `InvalidDecoratorError`

### MR 2: Remove Unused RUNTIME_TOOL_SCHEMAS
**MR:** https://gitlab-master.nvidia.com/interactive-agents/agent006/-/merge_requests/2

- `src/agent006/types.py` - Removed `RuntimeToolType` enum and `RUNTIME_TOOL_SCHEMAS` dict (~100 lines)
- `src/agent006/__init__.py` - Removed `RuntimeToolType` from exports
- `tests/test_types.py` - Removed `test_runtime_tool_schemas` test and unused `json` import

### MR 4: Remove Unused agent_utilities.py
**MR:** https://gitlab-master.nvidia.com/interactive-agents/agent006/-/merge_requests/4

- Deleted `src/agent006/agent_utilities.py` entirely (108 lines)
- Updated `src/agent006/agent.py` docstring to remove reference

### MR 5: Remove Empty Decorator Validation Loop
**MR:** https://gitlab-master.nvidia.com/interactive-agents/agent006/-/merge_requests/5

- `src/agent006/decorators.py` - Removed empty validation loop and `FRAMEWORK_METHODS` (27 lines)

### MR 6: Consolidate Utility Registration
**Status:** N/A - resolved by MR 4 (deleting agent_utilities.py)

### MR 7: Replace LocalNamespace with SimpleNamespace

- `src/agent006/runtime/executors/base.py` - Removed `LocalNamespace` class (25 lines), replaced with `types.SimpleNamespace`

### MR 9: Consolidate Task Tracking

- `src/agent006/agent.py` - Removed `_current_task` attribute
- `src/agent006/runtime/actor.py` - Changed `_current_task` to store `TaskWrapper` instead of `Task`
- `src/agent006/util/task.py` - Updated to access current task via `runtime._current_task`

### MR 10: Simplify Method Body Extraction

- `src/agent006/runtime/actor.py` - Simplified `_extract_method_body()` by adding `textwrap.dedent()` before AST parsing and removing fragile string-parsing fallback (~32 lines removed)

### MR 12: Remove Dead Wrapper Methods

- `src/agent006/runtime/executors/base.py` - Removed 261 lines of dead code:
  - `_strip_leading_docstring()` - only called from `_wrap_body_in_method`
  - `_wrap_body_in_method()` - never called
  - `_extract_method_from_wrapper()` - only called from `_select_return_code`
  - `_select_return_code()` - never called
