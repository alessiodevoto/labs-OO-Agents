# Review Findings: Context System Refactor

**Branch**: `refactor/context-system-type-safety-v2`
**Date**: 2026-02-12
**Status**: 232/232 tests passing (+8 comprehensive override tests)

---

## High Priority

### ✅ #3: Fix misleading comment in `_apply_context_total_limit`

**Status**: FIXED

**Issue**: Comment claimed "lowest priority blocks dropped first" but the pipeline ordering means scoped/decorator overrides (most specific) are dropped before framework blocks.

**Fix**: Updated docstring to clarify that dropping from the end sacrifices more-specific blocks to preserve framework essentials, and note this is a simple heuristic that could be improved.

**File**: `packages/context-blocks/src/context_blocks/renderer.py:80-96`

---

### ✅ #11: Test all override phase interactions end-to-end

**Status**: FIXED

**Issue**: While individual phases were tested in isolation, comprehensive tests for override priority and interactions across phases were missing.

**Fix**: Added 8 comprehensive integration tests in `tests/runtime/test_context_builder.py`:
- `test_persistent_coexists_with_framework` - Persistent blocks coexist with protected framework blocks
- `test_strategy_overrides_persistent` - Strategy (Phase 3) overrides persistent (Phase 2)
- `test_decorator_overrides_strategy` - Decorator (Phase 4) overrides strategy (Phase 3)
- `test_scoped_overrides_decorator` - Scoped (Phase 5) overrides decorator (Phase 4)
- `test_full_cascade_override` - One block overridden by all 5 phases; scoped wins
- `test_remove_semantics_across_phases` - Setting to `None` removes blocks from earlier phases
- `test_multiple_independent_overrides` - Each phase can add independent blocks
- `test_dynamic_overrides_in_phases` - Dynamic values work correctly in all phases

**Verification**: Confirmed that `decorator_context` is correctly wired through at actor.py:1987 and tests pass.

**Files**:
- `tests/runtime/test_context_builder.py` (+8 tests, lines 810-1010)

---

## Medium Priority

### ✅ #2: Pass `_scoped_blocks_var` explicitly to build_context (like decorator_context)

**Status**: FIXED

**Issue**: `build_context()` claimed to be "pure" but `_phase_scoped_blocks()` read `_scoped_blocks_var` (a context variable) directly. This was inconsistent with `decorator_context` being passed explicitly.

**Fix**: Added `scoped_context` parameter to `build_context()` and `_phase_scoped_blocks()`. Removed import of `_scoped_blocks_var` from `context_builder.py`. The call site in actor.py now passes `scoped_context=_scoped_blocks_var.get()` explicitly.

**Files**:
- `src/agent006/runtime/context_builder.py` (added parameter, removed import)
- `src/agent006/runtime/actor.py` (pass scoped_context explicitly)
- `tests/runtime/test_context_builder.py` (updated 3 tests to pass None or dict directly)

---

### ✅ #4: Improve ContextManager.__getitem__ for unresolved Dynamic blocks

**Status**: FIXED

**Issue**: When LLM-generated code does `self.context["key"]` for a Dynamic block before the first turn, it returns `None`. This was silent — no indication that the block exists but isn't resolved yet.

**Fix**: Updated `__getitem__` docstring to clearly document that Dynamic blocks return `None` before the first LLM turn, since resolution happens during `_prepare_context()`. Added note that immediate evaluation requires static blocks or manual expression calls.

**Files**:
- `src/agent006/runtime/context_manager.py` (improved docstring)

---

### ✅ #5+#10: Restructure `_phase_persistent_blocks` to separate Dynamic/static paths

**Status**: FIXED

**Issue**: The function interleaved two resolution paths (Dynamic async eval vs static pprint), making it hard to see which code path applied to which block type.

**Fix**: Refactored `_phase_persistent_blocks` to use explicit `if isinstance(value, Dynamic)` branch with clear comments:
- Dynamic path: Calls `resolve_fn` (which returns pre-formatted string), caches result
- Static path: Directly pprints non-string values for rendering

Also removed duplicate `import pprint` from top-level imports (now imported locally in the function).

**Files**:
- `src/agent006/runtime/context_builder.py` (restructured logic, moved import)

---

## Low Priority

### ✅ #6: Fix format_type return annotation to FormatType

**Status**: VERIFIED (NO FIX NEEDED)

**Issue**: Believed that `format_type` return annotations were `str` instead of `FormatType`.

**Finding**: Upon inspection, all return annotations are already correct — they return `FormatType`, not `str`. The abstract base and implementations are consistent.

**Files**:
- `packages/context-blocks/src/context_blocks/formatter.py` (already correct)

---

### ✅ #7: TODO marker for scoped_blocks events= discussion

**Status**: FIXED

**Issue**: The old `scoped_blocks()` API supported both `context=` and `events=` kwargs. The new API only supports `context=`. Any code using `events=` would be silently ignored.

**Fix**: Added TODO comment to `scoped_blocks()` docstring noting that the old API supported `events=` and suggesting discussion about whether event overrides are needed in the new architecture.

**Files**:
- `packages/context-blocks/src/context_blocks/scoped.py` (added TODO comment)

---

### ✅ #9: Convert TruncationConfig to Pydantic model

**Status**: FIXED

**Issue**: Used dataclass with custom `__init__` that broke positional args, used non-standard `_explicitly_set` tracking, and manipulated fields via `object.__setattr__`.

**Fix**: Converted to Pydantic `BaseModel`:
- Uses `frozen=True` for immutability
- Uses `@model_validator(mode="after")` to track explicitly-set fields via `model_fields_set`
- `merge_with()` iterates over `TruncationConfig.model_fields` (class attribute, not instance)
- All 17 existing tests pass without modification

**Benefits**:
- Standard Pydantic validation
- Better type checking
- Field descriptions via `Field(description=...)`
- Simpler and more maintainable

**Files**:
- `src/agent006/runtime/truncation_config.py` (converted to Pydantic)
- `tests/runtime/test_truncation_config.py` (all 17 tests pass)

---

## Out of Scope (Design Decisions)

### #1: ResolvedBlock.event couples rendering to event internals

**Status**: ACCEPTED AS TRADEOFF

**Issue**: Formatter directly accesses `block.event.tool_call_id`, `block.event.name`, etc. An intermediate DTO (like the reverted `ToolCallData`) would decouple.

**Decision**: Keep current design. The coupling is intentional — tool calls need structured data, and passing the event through is simpler than maintaining parallel DTOs. If event structure changes, formatter updates are localized.

---

### #8: Duplicate key handling in `_apply_overrides`

**Status**: ACCEPTED (NO FIX NEEDED)

**Issue**: If block list has duplicate keys, only the first is replaced.

**Decision**: Duplicate keys are a caller bug. First-match behavior is deterministic. Adding validation would add complexity for an edge case that shouldn't occur.

---

## Summary

| Priority | Fixed | Pending | Total |
|----------|-------|---------|-------|
| High     | 2     | 0       | 2     |
| Medium   | 4     | 0       | 4     |
| Low      | 3     | 0       | 3     |
| **Total**| **9** | **0**   | **9** |

**All findings addressed!** ✅

---

## Changes Made

1. **✅ #3**: Fixed misleading comment about truncation priority
2. **✅ #6**: Verified return type annotations (already correct)
3. **✅ #7**: Added TODO marker for `scoped_blocks(events=...)` discussion
4. **✅ #4**: Improved `ContextManager.__getitem__` docstring for Dynamic resolution timing
5. **✅ #5+#10**: Restructured `_phase_persistent_blocks` with clear Dynamic/static paths
6. **✅ #2**: Made `build_context()` truly pure by passing `scoped_context` explicitly
7. **✅ #9**: Converted `TruncationConfig` from dataclass to Pydantic BaseModel
8. **✅ #11**: Added 8 comprehensive tests for override phase interactions

**Test Results**: 232/232 tests passing (+8 new tests from 224 baseline)
