# TraceExplorer Development Journal

## Test Trace Files

These trace files are used for testing and development of TraceExplorer:

1. **NoteTakingTestWrapper_qwen3-80b** - SUCCESS (5 sessions, 0 errors)
   `/Volumes/dev/dev/fix/results/capability_optimization_20260123_092436/traces/NoteTakingTestWrapper_qwen3-80b_run_test_context_notes_20260123_092436_01_000000_qwen3-80b.006trace.jsonl`

2. **NoteTakingTestWrapper_nemotron3-nano-30b** - FAILED (2 sessions, 2 errors, no error message captured)
   `/Volumes/dev/dev/fix/results/capability_optimization_20260123_092436/traces/NoteTakingTestWrapper_nemotron3-nano-30b_run_test_context_notes_20260123_092436_01_000000_nemotron3-nano-30b.006trace.jsonl`

3. **dabstep_1273_hard** - SUCCESS (4 sessions, 19 turns, full DABStep flow)
   `/Volumes/dev/dev/fix/results/20260123_092336/traces/dabstep_1273_hard_88a3f53a.006trace.jsonl`

4. **OrderTestWrapper_claude-sonnet** - SUCCESS (10 sessions, nested tool calls)
   `/Volumes/dev/dev/fix/results/capability_optimization_20260123_092436/traces/OrderTestWrapper_claude-sonnet_run_conversation_fast_food_order_20260123_092436_01_000001_claude-sonnet.006trace.jsonl`

5. **EmployeeSalaryAgent_qwen3-80b** - SUCCESS (6 sessions, subagent calls)
   `/Volumes/dev/dev/fix/results/capability_optimization_20260123_092436/traces/EmployeeSalaryAgent_qwen3-80b_get_employee_salary_employee_lookup_20260123_092436_01_000000_qwen3-80b.006trace.jsonl`

6. **CalculateBatchAgent_gpt-oss-120b** - SUCCESS (1 session, batch processing)
   `/Volumes/dev/dev/fix/results/capability_optimization_20260123_092436/traces/CalculateBatchAgent_gpt-oss-120b_calculate_calculate_batch_20260123_092436_02_000000_gpt-oss-120b.006trace.jsonl`

7. **SentimentSingleAgent_claude-sonnet** - SUCCESS (1 session, uses return_result tool)
   `/Volumes/dev/dev/fix/results/capability_optimization_20260123_092436/traces/SentimentSingleAgent_claude-sonnet_classify_sentiment_single_20260123_092436_04_000002_claude-sonnet.006trace.jsonl`

8. **SentimentSingleAgent_qwen3-80b** - SUCCESS (1 session, uses structured output)
   `/Volumes/dev/dev/fix/results/capability_optimization_20260123_150958/traces/SentimentSingleAgent_qwen3-80b_classify_sentiment_single_20260123_150958_01_000000_qwen3-80b.006trace.jsonl`

9. **Eval file**
   `/Volumes/dev/dev/fix/results/capability_optimization_20260123_092436/capabilityoptimization_20260123_092436.006eval.jsonl`

## Development Notes

- 2026-01-22: Added error display for failed sessions in `get_overview()`
- 2026-01-22: Fixed status icon to check `result['success']` when available
- 2026-01-23: Updated test trace files to newer runs
- 2026-01-23: Found issue - nemotron trace has status=ERROR but no error message captured (result=None, error_turns=0)
- 2026-01-24: **API cleanup** - reduced from ~30 methods to 9 public methods:
  - Core: `from_file()`, `get_overview()`, `get_session()`, `get_turn()`, `get_errors()`, `get_eval_context()`
  - Utilities: `search()`, `what_went_wrong()`, `diff()`
  - ~20 methods internalized with `_` prefix
- 2026-01-24: Fixed tool_call_id extraction - IDs are on `code_execution` spans, not LLM output messages
- 2026-01-24: Consistent XML formatting: `<tool_call name="..." id="...">` and `<tool_response id="..." status="...">`
- 2026-01-24: Standardized parameter naming: `compact` → `concise` throughout
- 2026-01-26: **Major API improvements based on agent feedback** (see `docs/navigable-trace-feedback.md`):
  - Replaced ✓/✗ with [OK]/[ERR]/[PASS]/[FAIL] labels for clarity
  - Separated runtime errors from eval failures in stats line
  - `concise=True` now shows 10-20x less content than `concise=False`
  - Added context header to `get_session()` showing trace name and parent
  - `get_session(concise=True)` shows turn summaries only (one line per turn)
  - `get_turn()` now includes execution output, removed concise parameter
  - `get_errors()` now shows error chains for cascading failures, removed concise parameter
  - Search results now center match with ellipsis
  - Removed `what_went_wrong()` and `diff()` methods (use get_overview + get_errors instead)
  - Added `help()` method with usage guide for agents
  - Better error message when trying to load .006eval.jsonl files
  - Duration always shows 1 decimal place (ms)
  - All 30 tests passing
- 2026-01-26: **get_turn() LLM context fix** (second round of feedback):
  - `get_turn()` for ExecutionTurns now shows LLM context from preceding OR following turn
  - Handles prefill strategies where execution comes before LLM turn
  - For prefill, shows only system + task messages (not execution results)
  - Updated `help()` with "Truncation Format" section explaining pprint `+N` syntax
  - Updated `help()` get_turn() description to accurately describe behavior for both turn types
  - **Self-documenting headers**: Both turn types now show explanatory headers at the top:
    - LLM turns: `# Turn N: LLM Turn` + `# Shows: Context Window → LLM Output → Execution Result`
    - Exec turns: `# Turn N: Execution Turn` + `# Including LLM context from turn M`
    - Clear section labels: `## LLM Context Window`, `## LLM Context (from turn N)`, `## Execution (turn N)`
  - **help() refactored**: Moved full help text to class docstring; `help()` now returns `inspect.cleandoc(self.__doc__)`
    - Single source of truth for documentation
    - Works with Python's built-in `help(TraceExplorer)` and IDE hover
- 2026-01-26: **Migrated to packages/trace_explorer**:
  - Moved `navigable_trace.py` → `packages/trace_explorer/src/trace_explorer/explorer.py`
  - Renamed class `TraceExplorer` → `TraceExplorer`
  - Added CLI: `python -m trace_explorer trace.006trace.jsonl`
  - Removed old `e2e_optimization.navigable_trace` - all imports now use trace_explorer
  - All 30 tests pass in both new and old locations

## Known Issues

1. **Missing error messages**: Some failed traces (like nemotron) have `status=ERROR` but no captured error message. The `ERR:` line won't show anything useful.

2. **Turns: 0**: Some traces show 0 turns even though there were LLM calls - likely a parsing issue with turn association.
