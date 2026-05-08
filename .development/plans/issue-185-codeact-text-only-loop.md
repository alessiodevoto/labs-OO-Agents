# Issue 185: CodeAct agent infinite loop on text-only responses

## Problem

When the LLM "thinks it's done" by emitting a plain-text summary instead of calling `return_result(...)`, the CodeAct strategy converts that text into a synthetic `execute_python(reasoning(...))` call (see `src/nemo_oo_agents/strategies/codeact.py` lines 747–789). The framework returns `status: complete` for that synthetic call. The LLM, seeing a "complete" tool result, emits another plain-text summary on the next turn — and the loop repeats indefinitely.

`max_iterations` defaults to `None` (unlimited), so there is no implicit cap. In production this has been observed running for 20+ identical turns burning ~5k tokens each (issue 185, evolutionary optimizer trace).

## Root cause

Synthetic text-response conversion silently succeeds. Nothing in the loop distinguishes "real progress" (an actual `execute_python` or `return_result` tool call) from "the model is repeating itself with no tool call." The LLM has no negative signal that text-only is wrong because the synthetic conversion shows up as a completed tool call in the conversation transcript.

## Fix (Option C from the issue: A + B for defense in depth)

### A. Framework stop condition

Track consecutive synthetic text-response conversions. After a configurable threshold (default 3), abort the generation with a clear `GenerationError`. A real tool call in any turn resets the counter.

We deliberately do **not** auto-inject `return_result(None)` — the method's return type may be a non-Optional Pydantic model, so synthesizing a `None` result would just shift the failure mode from infinite loop to validation error. A clear failure with informative diagnostics is more honest.

### B. Prompt reinforcement

Tighten the existing "You MUST call a tool each turn." line in `strategy_instructions()` to make the consequences explicit: plain text will not terminate the session, and the run will fail if it repeats.

## Code changes

### `src/nemo_oo_agents/config/strategy_config.py`

Add a config field on `CodeActConfig`:

```python
max_consecutive_text_only: int = 3
```

Description: Maximum consecutive turns where the LLM returns text without a tool call before the run is aborted. Default 3. The text→synthetic-reasoning conversion counts as text-only. Any real tool call resets the counter. Set to `0` to disable.

**Backward-compat note**: This changes runtime behavior for existing users. Previously a text-only loop would run forever (or until `max_iterations` cut it off, which is `None` by default). With the default `3`, such loops abort with a `GenerationError` after 3 consecutive text-only turns. Users who relied on the old behavior must explicitly set `max_consecutive_text_only=0`. We accept this break because the old behavior is the bug being fixed.

### `src/nemo_oo_agents/strategies/codeact.py`

1. Add a counter field on `CodeActSession`:
   ```python
   consecutive_text_only: int = 0
   ```
2. Add helpers on `CodeActSession` (matching the existing concise style of `record_iteration` / `record_error`):
   ```python
   def record_text_only(self) -> None:
       self.consecutive_text_only += 1

   def reset_text_only(self) -> None:
       self.consecutive_text_only = 0
   ```
   Use **only** these two names everywhere — no `record_text_only_response()` variant.

3. In `execute()`'s main loop:
   - **Tool-calls branch** (around line 718–745): call `session.reset_text_only()` *unconditionally* immediately after the `_process_tool_calls` call returns (i.e. before checking `result.completed`). Any real tool call counts as progress, including ones that complete the task. The `_prepend_reasoning` path (lines 723–731, text + tool calls in the same response) reaches the same `_process_tool_calls` call site, so this single reset covers both subcases.
   - **Text-only branch** (around line 747–789): keep the existing `session.record_iteration()` call (line 757). After appending the synthetic events and calling `get_harness_metrics().text_to_synthetic()`, also call `session.record_text_only()`. Then, if `self.config.max_consecutive_text_only > 0` and `session.consecutive_text_only >= self.config.max_consecutive_text_only`:
       1. Set `turn_state.is_final = True` (the `async with session.turn(...)` context manager at codeact.py:219–222 will record `state.exception` automatically when the raise propagates — do **not** assign `turn_state.exception` manually, do **not** emit a second `AfterTurn`).
       2. Call `get_harness_metrics().text_only_loop_abort()`.
       3. Raise `GenerationError` with a message that embeds a truncated preview of the offending text (use `_truncate_reasoning(text)` so we cap at 500 chars):

          > "CodeAct aborted: LLM returned plain text without a tool call {consecutive_text_only} times in a row (max_consecutive_text_only={M}) for `{method_name}`. The agent likely thinks it is done — it must call `return_result(...)` to finish. Last text: {preview!r}"

      The exception propagates up through the `async with session.turn(...)` context, which records `AfterTurn(is_final=True, success=False, exception_type='GenerationError')` correctly.
   - **Empty-response branch** (line 793 onward): do **not** touch `consecutive_text_only`. Empty responses are a distinct failure mode tracked by `error_count`/`max_retries`; they don't inflate the synthetic-text loop counter and they don't reset it (an alternating `text → empty → text → empty` pattern still trips the consecutive-text guard at the right moment because the empty turns simply leave the counter at its current value while error_count climbs toward `max_retries`).

4. Add a metric in `runtime/harness_metrics.py`:
   ```python
   text_only_loop_aborts_count: int = 0

   def text_only_loop_abort(self) -> None:
       self.text_only_loop_aborts_count += 1
   ```
   Register it next to `text_to_synthetic_count` in the metrics-export block (lines ~510–515) for parity. Increment at the abort site only.

### `strategy_instructions()` prompt update

Replace the existing line:

> Jupyter-like Python session. Parameters pre-loaded as locals; state persists across cells. Use `await` directly, `print`/`pprint` to debug, `doc(obj)` to inspect types. You MUST call a tool each turn.

with a stronger version that directly addresses the failure mode:

> Jupyter-like Python session. Parameters pre-loaded as locals; state persists across cells. Use `await` directly, `print`/`pprint` to debug, `doc(obj)` to inspect types. You MUST call a tool each turn — **plain-text responses do NOT end the session**. To finish, call `return_result(value)`. Repeated text-only responses will abort the run with an error.

## Tests

Add to `tests/strategies/test_codeact_strategy.py` (alongside `test_text_only_response_becomes_synthetic_reasoning_call`):

1. **`test_text_only_loop_aborts_after_threshold`** — feed the LLM 4 consecutive text-only responses; assert `GenerationError` is raised, the message mentions the consecutive-text guard, and at most `max_consecutive_text_only` synthetic calls were created (the 4th turn raises before adding a 4th synthetic event).

2. **`test_text_only_counter_resets_on_real_tool_call`** — script: text → text → execute_python → text → text → return_result. The mid-sequence tool call resets the counter, so the run completes successfully without abort.

3. **`test_text_only_loop_disabled_when_threshold_zero`** — set `max_consecutive_text_only=0`; feed many text-only responses bounded by `max_iterations` instead. Verify abort path is not taken.

4. **`test_text_only_loop_abort_records_after_turn_event`** — assert that the aborting turn emits an `AfterTurn` with `is_final=True` and `exception_type="GenerationError"`.

5. Existing `test_text_only_response_becomes_synthetic_reasoning_call` must still pass (single text-only → synthetic → tool call works fine).

## Edge cases

- **Whitespace-only text**: already routed to the empty-response branch, not the synthetic branch. Counter not touched. ✓
- **Text + tool calls in same response**: hits the `_prepend_reasoning` path, *not* the text-only path. Counter is reset because a real tool call was made. ✓
- **Empty response**: distinct branch with its own `error_count` and `_tool_use_reminder`. Counter not touched. ✓
- **`max_consecutive_text_only=0`**: feature disabled; behaviour unchanged from today. ✓
- **`max_iterations=None`** (default): the new abort is the only safety net. Default of 3 is conservative; user-configurable. ✓
- **Prefill-injected synthetic calls** (from `_run_prefill`): those are real `execute_python` tool calls (computed code), not text→reasoning conversions; counter is reset by `_process_tool_calls` path naturally. They never set `synthetic_type="text_response"`. ✓

## Reproduction (failing test before fix)

`test_text_only_loop_aborts_after_threshold` constructs a session with `max_iterations=10` and a `FakeLLMClient` whose `scripted_responses` is a list of *exactly four* text-only responses (no `return_result`, no `execute_python`). The test asserts that:

1. A `GenerationError` is raised.
2. The error message contains `"max_consecutive_text_only"` and `"plain text"`.
3. There are at most `max_consecutive_text_only` synthetic ToolCallEvents in the event log (i.e. abort fires *before* a 4th synthetic conversion).

Pre-fix behavior: unmodified code converts each of the 4 responses to a synthetic call, then on the 5th turn the `FakeLLMClient` runs out of scripted responses and raises a different error (`IndexError` / mock exhaustion), not a `GenerationError` mentioning `max_consecutive_text_only`. The assertion on the message string fails. (Post-fix: the abort fires on turn 3, before any 4th LLM call.)

If `FakeLLMClient` repeats the last response when scripted responses are exhausted (verify in `tests/strategies/test_codeact_strategy.py` setup), the unmodified code would loop forever — which itself is the bug. We use `max_iterations=4` instead in that case so unmodified code raises a `GenerationError` mentioning `max_iterations`, and the test asserts the *fix-only* message text. Either way, the test fails on unmodified code.

## Verification

```bash
uv run pytest tests/strategies/test_codeact_strategy.py -k text_only -x -v
uv run pytest tests/strategies/test_codeact_strategy.py -x
uv run ruff check src/nemo_oo_agents/strategies/codeact.py \
                  src/nemo_oo_agents/config/strategy_config.py \
                  tests/strategies/test_codeact_strategy.py
```

## Out of scope

- Adapting `PurePythonStrategy` / `CodeActLite` / `Reflexion` — verified by `grep -rn "synthetic_type" src/nemo_oo_agents/strategies/`: only `codeact.py` has the text-to-synthetic conversion path. Other strategies use different completion mechanics (e.g. PurePython parses raw Python output, not tool calls) and cannot exhibit this loop.
- Optimizer-level token caps (separate concern; this fix prevents the runaway at its source).
- Auto-`return_result(None)` behavior. Documented as deliberately not chosen (return-type validation would just move the error).
