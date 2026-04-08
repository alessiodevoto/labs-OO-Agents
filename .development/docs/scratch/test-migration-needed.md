# Test Migration Status for REPL-Style Refactoring

## Summary

After implementing the REPL-style refactoring, 39 tests are failing because their FakeLLMClient responses still use old-style method definitions instead of REPL-style return statements.

## Pattern to Fix

**OLD (method definition style):**
```python
_resp("async def method_name(self, param: type) -> return_type:\n    return value")
```

**NEW (REPL-style):**
```python
_resp("return value")
```

## Test Files Requiring Updates

### Critical (Type Mismatch Errors) - 15 tests
- `tests/core_runtime/test_code_caching.py` (1 test)
- `tests/core_runtime/test_execution.py` (2 tests)
- `tests/core_runtime/test_implemented_plan.py` (1 test)
- `tests/external/test_notebook_scenarios.py` (7 tests)
- `tests/strategies/test_pure_python_return_validation.py` (9 tests)
- `tests/strategies/test_python_task_strategy.py` (1 test)

### Integration Tests - 13 tests
- `tests/core_runtime/test_task_queuing_edge_cases.py` (3 tests)
- `tests/edge_cases/test_generation_lock_edge_cases.py` (2 tests)
- `tests/edge_cases/test_nested_generation_edge_cases.py` (5 tests)
- `tests/integration/test_nested_generation.py` (4 tests)

### Performance/Other - 11 tests
- `tests/core_runtime/test_llm_client_reuse.py` (2 tests)
- `tests/performance/test_client_creation_overhead.py` (2 tests)

## Already Fixed
✅ `tests/runtime/test_pure_python_executor.py` (11 tests)
✅ `tests/strategies/test_pure_python_strategy.py` (2 tests)
✅ `tests/test_module_imports.py` (1 test)
✅ `tests/test_method_call_permutations.py` (2 tests)

## Recommendation

Priority order:
1. Fix `test_pure_python_return_validation.py` (9 tests) - validates core return type functionality
2. Fix `test_notebook_scenarios.py` (7 tests) - validates end-user scenarios
3. Fix remaining core_runtime tests (6 tests)
4. Fix integration/edge case tests (20 tests) - these are complex multi-file updates

Total: **39 tests** need updating across **10 test files**
