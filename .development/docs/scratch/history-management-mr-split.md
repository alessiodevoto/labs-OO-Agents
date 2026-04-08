# Branch Review: feature/history-management-phases-5-7

## Executive Summary

This branch implements history management phases 5-7: history tagging, policies, and token budgeting. Also includes context-blocks event unification, runtime improvements, and unifiedllm registry simplification.

---

## Issues Found

### Critical Issues

| Issue | Location | Status |
|-------|----------|--------|
| **Race condition: turn counter dict mutation** | [actor.py](src/agent006/runtime/actor.py) | ✅ Fixed (copy-on-write) |
| **Bytecode heuristic unreliable** | [decorators.py](src/agent006/decorators.py) | ✅ Fixed (removed, AST-only) |
| **Untracked new files** | `context_vars.py`, `method_wrapper.py` | ⏳ Pending commit |

### Breaking API Changes (this branch)

| Change | Files Affected | Migration |
|--------|---------------|-----------|
| `history.active()` returns `(tag, event)` tuples | History iteration | Must unpack tuples: `for tag, event in history.active()` |
| `token_counter` now required in `HistoryManagementPolicy` | Policy creation | Must provide token counter |
| `ToolResultEvent` removed | Event iteration | Access via `ToolCallEvent.result` |
| `ModelConfig` removed from unifiedllm | External imports | Use dict config directly |

*Note: agentdoc changes (`brief()` removal, `doc()` format change) are already on main*

### Medium Priority

- Duck-typing for RuntimeServices routing is fragile (should use Protocol)
- ~~Registry simplification loses ~80% test coverage~~ ✅ Fixed (12 tests now)
- Lazy imports inside wrapper obscure dependencies

---

## Phase 0: Pre-MR Fixes (in this branch) ✅ COMPLETE

All Phase 0 fixes have been implemented and tested.

### Fix 1: Turn Counter Race Condition ✅

**Location**: [actor.py:559-565](src/agent006/runtime/actor.py#L559-L565), [actor.py:1880-1885](src/agent006/runtime/actor.py#L1880-L1885)

**Problem**: The `_turn_counters_var` dict was mutable and shared across context copies. Parallel tasks could read the same counter value and both increment to the same number.

**Solution Applied**: Copy-on-write pattern in both increment and cleanup locations:
```python
# Increment (line ~562):
turn_counters = _get_turn_counters()
new_counters = {**turn_counters}  # Copy to avoid mutating shared dict
new_counters[current_generation_id] = new_counters.get(current_generation_id, 0) + 1
turn_number = new_counters[current_generation_id]
_turn_counters_var.set(new_counters)  # Set new immutable copy

# Cleanup (line ~1882):
turn_counters = _get_turn_counters()
if generation_id in turn_counters:
    new_counters = {k: v for k, v in turn_counters.items() if k != generation_id}
    _turn_counters_var.set(new_counters)
```

### Fix 2: Remove Bytecode Heuristic ✅

**Location**: [decorators.py](src/agent006/decorators.py)

**Problem**: Bytecode-based ellipsis detection (`len(code.co_code) <= 12`) was unreliable across Python versions and could produce false positives for short implemented functions.

**Solution Applied**: Removed bytecode fallback entirely, now uses AST-only inspection. Added comprehensive integration tests.

### Fix 3: Restore Registry Test Coverage ✅

**Location**: [test_registry.py](packages/unifiedllm/tests/test_registry.py)

**Tests added** (expanded from 4 to 12 tests):
- `TestApiKeyHandling`: API key from env, missing key handling, model-specific env vars
- `TestParameterOverrides`: Basic overrides, precedence, config defaults
- `TestUnknownModel`: Unknown models still work with defaults

### Fix 4: Integration Tests for Ellipsis Detection ✅ (NEW)

**Location**: [test_ellipsis_method_integration.py](tests/integration/test_ellipsis_method_integration.py)

**18 integration tests** using FakeLLM to verify end-to-end ellipsis detection:
- Simple ellipsis methods (one-liner, with docstring, multiline docstring)
- Complex signatures (multiple params, complex return types, optional params)
- Multi-turn generation (method calling another ellipsis method, chained A→B→C)
- Implemented vs ellipsis (implemented not generated, pass not ellipsis, short methods not false positive)
- Edge cases (direct return_result, inline return_result, async with await)

---

## Proposed MR Split (6 MRs)

### MR 1: Architecture Cleanup - Circular Import Resolution ⏳ IN PROGRESS

**MR**: !299 - https://gitlab-master.nvidia.com/interactive-agents/agent006/-/merge_requests/299

**Purpose**: Break circular imports and unify method wrapping logic

**Files**:
- `src/agent006/runtime/context_vars.py` (NEW) - Break circular import for `_parent_agent_var`
- `src/agent006/runtime/method_wrapper.py` (NEW) - Unified wrapper logic
- `src/agent006/agent.py` - Import updates
- `src/agent006/decorators.py` - Use shared wrapper
- `src/agent006/metaclass.py` - Now imports `is_ellipsis_body` from decorators.py (DRY)
- `src/agent006/runtime/actor.py` - Import from context_vars
- `src/agent006/strategies/generated_code.py` - `_exec_with_source_tracking()` for decorated exec'd functions

**Tests**:
- `tests/test_ellipsis_detection_exec.py` (NEW) - 8 tests for exec'd function detection
- All 1643 tests pass

**Additional Fixes in MR 1**:
1. **is_ellipsis_body for exec'd functions**: When decorators run during exec, `_generated_source` is now set before decorator runs
2. **Unified is_ellipsis_body**: Removed duplicate from metaclass.py, now imports from decorators.py
3. **method_wrapper.py routing**: Fixed dead code, added `_in_generation_session.get()` check

**Dependencies**: None (foundational)

---

### MR 2: UnifiedLLM Registry

**Purpose**: Simplified model registry with context window sizes (needed for history policies)

**Files**:
- `packages/unifiedllm/src/unifiedllm/registry.py`
- `packages/unifiedllm/src/unifiedllm/__init__.py`
- `packages/unifiedllm/src/unifiedllm/unifiedllm.py`
- `packages/unifiedllm/scripts/probe_context_windows.py` (NEW)
- `packages/unifiedllm/scripts/verify_context_windows.py` (NEW)
- `packages/unifiedllm/tests/test_registry.py`
- `packages/unifiedllm/tests/test_annotated_return_types.py` (NEW)

**Dependencies**: None (independent infrastructure)

**Note**: Key tests restored in Phase 0

---

### MR 3: Context-Blocks + History Tagging

**Purpose**: Unify tool results, expression rendering, and history tagging (tightly coupled)

**Files** (context-blocks):
- `packages/context-blocks/src/context_blocks/events.py` - Remove ToolResultEvent, add nested ToolResult
- `packages/context-blocks/src/context_blocks/formatter.py` - Expression rendering, tuple format support
- `packages/context-blocks/src/context_blocks/renderer.py` - Tuple format detection
- `packages/context-blocks/tests/test_formatters.py`
- `packages/context-blocks/tests/test_renderer.py`
- `packages/context-blocks/tests/test_events.py`

**Files** (history tagging):
- `src/agent006/runtime/history.py` - Tagging system (`active()` returns `(tag, event)` tuples)
- `tests/test_history_manager.py` - Updated tests

**Key Changes**:
- `ToolResultEvent` removed, replaced with `ToolCallEvent.result`
- Events get stable string tags ("1", "2", etc.)
- `history.active()` returns `list[tuple[str, EventBase]]` (breaking change)
- `history["5"]` access that survives summarization
- Formatter handles tuple format from `history.active()`

**Dependencies**: MR 1

---

### MR 4: Runtime Events - BeforeTurn & Turn Tracking

**Purpose**: Add turn tracking and BeforeTurnEvent emission

**Files**:
- `src/agent006/runtime/actor.py` - BeforeTurnEvent, turn counters
- `src/agent006/strategies/codeact.py` - Turn tracking integration
- `src/agent006/strategies/prefill.py` - Strategy updates
- `tests/runtime/test_span_parent_relationship.py`
- `tests/edge_cases/test_child_agent_edge_cases.py`

**Dependencies**: MR 1, MR 3

**Note**: Race condition will be fixed in Phase 0

---

### MR 5: History Policies

**Purpose**: Implement history management policies (truncation/summarization)

**Files**:
- `src/agent006/runtime/history_policies.py` (NEW)
- `src/agent006/runtime/budget.py` (NEW)
- `src/agent006/util/tokens.py` (NEW)
- `tests/test_history_policies.py` (NEW - 750 lines)
- `tests/test_history_truncation.py`

**Key Changes**:
- `HistoryPolicy` protocol interface
- `HistoryManagementPolicy` with "truncate" or "summarize" modes
- Token budgeting and context window management

**Dependencies**: MR 2 (for context_window sizes), MR 3 (for tagging system), MR 4 (for BeforeTurnEvent)

**Note**: Most experimental piece - may need iteration

---

### MR 6: Examples, CI & Cleanup

**Purpose**: Update examples and CI configuration

**Files**:
- `examples/codeact_event_sequence.py`
- `examples/summarization_demo.py`
- `experiments/capability_eval/strategy.py`
- `.gitlab-ci.yml` - Cache config
- Various test updates for new APIs

**Dependencies**: All previous MRs

---

## Verification Plan

### For Each MR

1. Run full test suite: `pytest`
2. Run type checking: `mypy src/`
3. Verify no regressions in dependent code

### End-to-End Testing

After all MRs merged:
1. Run `examples/codeact_event_sequence.py` - verify event structure
2. Run `examples/summarization_demo.py` - verify history management
3. Verify agentdoc output in agent blocks
4. Test nested agent execution with history policies

---

## Recommended Order

```
MR 1 (Architecture) ──── MR 3 (Context-Blocks + Tagging) ──── MR 4 (Runtime) ────────┐
                                                                                      │
MR 2 (UnifiedLLM) ──────────────────────────────────────────────────────────── MR 5 ─┼── MR 6
                                                                            (Policies)│  (Examples)
```

MRs 1 and 2 can proceed in parallel (foundational).
MRs 3, 4 are sequential after MR 1: context-blocks + tagging → runtime events.
MR 5 (Policies) depends on MR 2 (context_window), MR 3 (tagging), and MR 4 (BeforeTurnEvent).
MR 6 waits for all others.

---

## Decisions Made

1. **Race condition**: Fix in this branch before MRs (Phase 0) ✅
2. **Registry tests**: Restore key tests (Phase 0) ✅
3. **Bytecode heuristic**: Remove fallback, use AST-only (Phase 0) ✅
4. **Integration tests**: Add FakeLLM-based tests for ellipsis detection ✅

---

## Next Steps

1. ~~Implement Phase 0 fixes~~ ✅ Complete
2. ~~Commit untracked files (`context_vars.py`, `method_wrapper.py`)~~ ✅ In MR 1
3. ~~Create MR 1~~ ✅ MR !299 created
4. **Next**: Get MR 1 reviewed and merged
5. Create MR 2 (UnifiedLLM Registry) - can proceed in parallel
6. After MR 1 merges: Create MR 3 → MR 4, then MR 5 (needs 2+3+4), then MR 6

---

## Test Coverage Summary

After Phase 0 fixes + MR 1 additions:
- **18/18** ellipsis integration tests pass ([test_ellipsis_method_integration.py](tests/integration/test_ellipsis_method_integration.py))
- **22/22** ellipsis detection unit tests pass ([test_ellipsis_detection.py](tests/test_ellipsis_detection.py))
- **8/8** exec'd function detection tests pass ([test_ellipsis_detection_exec.py](tests/test_ellipsis_detection_exec.py)) (NEW)
- **2/2** nested structured output tests pass ([test_pure_python_nested_structured_output.py](tests/strategies/test_pure_python_nested_structured_output.py))
- **1643 total tests pass**
- **12/12** registry tests pass ([test_registry.py](packages/unifiedllm/tests/test_registry.py))
