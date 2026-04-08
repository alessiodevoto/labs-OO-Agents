# Remove StructuredOutput Backwards Compatibility

**Date:** 2026-03-23
**Status:** Planned

## Summary

Remove all backwards-compatibility shims for `StructuredOutputStrategy` and `StructuredOutputConfig`.
These were renamed to `PredictStrategy`/`PredictConfig` respectively. The shims emitted `FutureWarning`
and have been in place long enough — now remove them entirely.

## Files to Change

### Source (delete/modify)

| File | Change |
|------|--------|
| `src/nemo_oo_agents/strategies/structured_output.py` | **DELETE** (entire compat shim file) |
| `src/nemo_oo_agents/strategies/__init__.py` | Remove `StructuredOutputStrategy` import + `__all__` entry |
| `src/nemo_oo_agents/__init__.py` | Remove `StructuredOutputStrategy` import + `__all__` entry |
| `src/nemo_oo_agents/config/strategy_config.py` | Remove `StructuredOutputConfig` function |
| `src/nemo_oo_agents/config/__init__.py` | Remove `StructuredOutputConfig` import + `__all__` entry |
| `src/nemo_oo_agents/runtime/actor.py` | Remove `"StructuredOutputStrategy": PredictStrategy` from exec_globals dict |
| `src/nemo_oo_agents/strategies/codeact.py` | Remove `"StructuredOutputStrategy": PredictStrategy` from strategy_extras dict |
| `src/nemo_oo_agents/strategies/pure_python.py` | Remove `"StructuredOutputStrategy": PredictStrategy` from strategy_extras dict |
| `src/nemo_oo_agents/tools/method_writing_lib.py` | Replace `StructuredOutputStrategy()` with `PredictStrategy()` in docstring examples |

### Tests (update)

| File | Change |
|------|--------|
| `tests/config/test_strategy_configs.py` | Remove `StructuredOutputConfig` import + the backwards-compat test |
| `tests/integration/test_codeact_dynamic_structured_output.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` in all code strings + comments |
| `tests/integration/test_codeact_nested_structured_output.py` | Update comments referencing `StructuredOutput` |
| `tests/strategies/test_pure_python_nested_structured_output.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` in code strings + comments |
| `tests/test_ellipsis_detection_exec.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` |
| `tests/test_llm_generated_plan_methods.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` |
| `tests/unit/test_prompts.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` |
| `tests/runtime/test_code_validator.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` in code strings |

### Util (update)

| File | Change |
|------|--------|
| `util/trace-viewer/test_parity.py` | Replace `StructuredOutputStrategy` import + usage with `PredictStrategy` |
| `util/trace-viewer/test_upload.py` | Replace `StructuredOutputStrategy` import + usage with `PredictStrategy` |
| `util/prompt-optimization/test_runner_integration.py` | Update comments referencing `StructuredOutput` to `PredictStrategy` |

### Experiments (update)

| File | Change |
|------|--------|
| `experiments/assistant_python_opt/agents/calculate_batch.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` |
| `experiments/assistant_python_opt/agents/sentiment.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` |
| `experiments/assistant_python_opt/strategy/assistant_python.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` |
| `experiments/evaluation-ablations/agents/cognitive_ci_agent.py` | Replace `StructuredOutputStrategy` with `PredictStrategy` |

## Notes on Test Files

- `test_codeact_dynamic_structured_output.py` was specifically written to test the exec_globals alias.
  Once the alias is removed, the code strings it uses should become `PredictStrategy` (since that IS
  what will be in exec_globals). The test still exercises the same nested-generation pipeline.
- `test_redundant_imports_stripped_silently`: code string uses `from strategy import StructuredOutputStrategy, strategy`.
  Update the code string to use `PredictStrategy` instead (the test still validates the "strip redundant imports" behavior).

## Verification

Run after implementation:
```bash
pytest tests/ -x -q
ruff check src/ tests/ experiments/
```

Confirm no remaining references (excluding `.claude/` history and `.development/docs/` historical design docs):
```bash
grep -r "StructuredOutput" src/ tests/ experiments/ --include="*.py"
```
