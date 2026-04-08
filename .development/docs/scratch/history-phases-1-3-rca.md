# RCA: history-phases-1-3 capability regression

## Summary
MR `history-phases-1-3` introduces prompt-history rendering changes that alter how task and tool-output blocks are referenced in the LLM context (e.g., `self.history[0].prompt` vs `self.history.events[0].content`, and `self.history[3].stdout` vs `self.history.events[3].content`). These shifts correlate with the largest regressions (error_recovery + router_*), where LLMs stop invoking recovery logic or delegate subagents less reliably.

## Regression hotspots (overlap set)
- Largest drops are concentrated in:
  - `error_recovery_001` (14 regressions)
  - `router_validate_002` (7)
  - `router_transform_001/002` (4)
  - `fast_food_order_004` (9)
  - `calculate_batch_001` (10)

## Evidence from traces (trace_explorer)
### Error recovery (gpt-oss-120b)
- **Main**: task block uses `expr="self.history.events[0].content"` and LLM calls `execute_python()` to invoke `retrieve_number_from_alec`, handles a 503, retries, returns `17` (PASS).
- **MR**: task block uses `expr="self.history[0].prompt"` and LLM calls `return_result(42)` directly (FAIL).

### Router validate (qwen3-80b)
- **Main**: task block uses `expr="self.history.events[0].content"` and prefill output uses `self.history.events[3].content`. LLM falls back to validation and calls `ValidatorSubAgent` (PASS).
- **MR**: task block uses `expr="self.history[0].prompt"` and prefill output uses `self.history[3].stdout` / `self.history[2].content`. LLM does not take the fallback path and returns empty `agents_called` (FAIL).

### Fast food order (gemini-2.5-flash-lite)
- **Main**: longer multi-turn chain; agent retrieves menu, retries add_item with menu ID, succeeds (PASS).
- **MR**: shorter chain; less recovery behavior, fails to correct burger ID (FAIL).

## Root cause hypothesis
The history phase changes modify how trace events are rendered into prompt blocks. Specifically, task and tool-result blocks now reference `.prompt` or `.stdout` fields on `self.history[...]` rather than `.content` on `self.history.events[...]`. This reduces or alters the LLM-visible context for prefill outputs and task content, leading to:
- Lower likelihood of calling helper methods (error recovery).
- Weaker tool-call disambiguation and fallback logic (router_*).
- Reduced recovery loops in multi-turn tool use (fast_food_order).

## Likely code locations
- `src/agent006/runtime/history.py` (rendering expressions and indexing semantics)
- `src/agent006/events.py` (`TaskEvent.render_spec()` and field selection)
- `packages/context-blocks/src/context_blocks/formatter.py` (XML block rendering)

## Recommended checks
1. Re-render identical prompts on main vs MR and diff task/tool block XML.
2. Add regression test asserting `task` and `execute_python` blocks use stable `self.history.events[*].content` paths (or a supported equivalent).
3. If the new history API is intended, ensure prompt rendering includes the same content as before (including tool stdout and “Execution successful” wrappers).
