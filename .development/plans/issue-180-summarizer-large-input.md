# Issue 180 — TokenBudgetSummarizer blocked by `max_param_chars`

Source: https://gitlab-master.nvidia.com/interactive-agents/nooa/-/issues/180

## Problem

`TokenBudgetSummarizer.summarize()` is configured with `@strategy(PredictStrategy())` —
i.e. the default `PredictConfig`. The default has `max_param_chars=200_000`. When a
parent agent's history grows past that threshold, the summarizer correctly fires,
but `PredictStrategy._assert_param_sizes` raises `ValueError` against the
`history_markdown` parameter, the summarizer's outer `try/except` logs a warning and
discards the result, and history stays uncompressed → next turn overflows the LLM
context window.

This is a catch-22: the *only* component whose job is to compress arbitrarily-large
input is itself blocked by an arbitrary-input safety guard.

## Root cause — four bounds on the input pipeline

`PredictStrategy.execute()` and the downstream rendering pipeline enforce
**four** independent safety bounds when handling the `history_markdown`
parameter, none of which make sense for the summarizer:

1. **`PredictConfig.max_param_chars` = 200_000** — `_assert_param_sizes()` raises
   `ValueError` if `len(value) > limit` for any string parameter
   (`predict.py:329`).
   *(This is the one in the bug report — it fires first and shuts everything
   down.)*
2. **`TruncationConfig.max_block_chars` = 20_000** — `execute()` raises
   `ValueError` if `len(task_prompt) > tc.max_block_chars` (`predict.py:184`).
3. **`TruncationConfig.prefill_format.max_string` = 500** — `_build_task_message`
   calls `format_parameters_as_code(tc=tc)` which uses
   `pformat(v, max_string=500, …)`, so any string parameter is silently
   truncated to 500 chars when rendered into the task prompt
   (`current_call.py:138`).
4. **`TruncationConfig.event_format.max_string` = 10_000** — after `Task(...)`
   is added to events, `render_context` materializes it via
   `block_formatter.format_event(event, max_chars=…, event_format=event_format)`
   which uses `truncating_pformat(event, max_string=10_000, …)`. So even if the
   prompt builder produced the full text, the LLM-message-building step would
   cut `Task.prompt` down to ~10 K chars before the call to the LLM
   (`formatter.py:108-111`, `truncation_config.py:141`).

The bug report only mentions (1) because that's where the WARNING currently
fires. But (1) is just the first guard; lifting only (1) would make the
summarizer silently truncate `history_markdown` first to 500 chars in the
prompt body and then the whole Task event to 10 000 chars at message build —
the LLM would see almost nothing and produce a useless summary.

`SummarizationAgent.summarize()` is structurally a special case: its single
purpose is to receive arbitrarily-large input and compress it. The framework's
ordinary safety guards (which exist to catch accidental oversize inputs to
**other** PredictStrategy methods) are wrong for this method.

`history_markdown` is always a `str` by construction — `_render_range_to_markdown`
returns the joined markdown (`summarization.py:425`) — and `target_chars: int` is
trivially small (its `repr` is a few bytes), so neither needs special handling
beyond the four overrides below.

## Fix

Override the four relevant bounds on the summarizer's `summarize` method via
the existing `@strategy(...)` decorator API. No new public config knobs, no
public API change. Only `SummarizationAgent.summarize()` is affected — every
other `PredictStrategy` method in the framework keeps its default safety
guards. `MethodSummarizer` inherits the override automatically.

In `src/nooa/agents/summarization.py`:

```python
from nooa.config.strategy_config import PredictConfig
from nooa.config.truncation_config import FormatConfig, TruncationConfig

# Effectively unbounded — the summarizer's contract is "accept arbitrarily-large
# input and compress it." Real upper bound is the LLM's context window, not a
# framework safety guard. 10_000_000 chars is well above any realistic LLM
# context (Claude/Gemini today max ~1M tokens ≈ ~4M chars).
_SUMMARIZER_MAX_CHARS = 10_000_000

class SummarizationAgent(Agent):
    ...

    @strategy(
        PredictStrategy(PredictConfig(max_param_chars=_SUMMARIZER_MAX_CHARS)),
        truncation=TruncationConfig(
            max_block_chars=_SUMMARIZER_MAX_CHARS,
            prefill_format=FormatConfig(
                max_string=None, max_length=None, max_depth=None
            ),
            event_format=FormatConfig(
                max_string=None, max_length=None, max_depth=None
            ),
        ),
    )
    async def summarize(self, history_markdown: str, target_chars: int) -> str:
        """..."""
        ...
```

Why each piece is required:

- `PredictConfig(max_param_chars=_SUMMARIZER_MAX_CHARS)` lifts guard (1) —
  `_assert_param_sizes` no longer rejects large `history_markdown`.
- `TruncationConfig.max_block_chars` lifts guard (2) — the prompt-size guard
  no longer rejects the rendered task prompt.
- `prefill_format=FormatConfig(max_string=None, …)` lifts guard (3) — the
  `history_markdown` parameter is rendered in full into the prompt rather
  than pformat-truncated to 500 chars per string.
- `event_format=FormatConfig(max_string=None, …)` lifts guard (4) — the
  `Task` event survives the `render_context` step at full size when the
  LLM message is built.

`max_length=None` and `max_depth=None` are also set on both `FormatConfig`s as
a defensive measure — `history_markdown` is a flat string today, but if a
future caller passes structured data, the summarize prompt should still
render it fully rather than collapsing inner containers.

The existing `_render_range_to_markdown` path on the summarizer (which uses
`self._truncation.max_block_chars` per parent event, see
`summarization.py:411-416`) is intentionally left alone. That path bounds the
size of *each parent event* before joining — agent-level config — and is a
separate concern from the per-method overrides for the LLM call.

## Why not other approaches

- **Make `max_param_chars` configurable on `TokenBudgetConfig`.** Adds a knob
  the user must remember to tune; doesn't address (2)/(3)/(4); leaves the
  catch-22 in place by default. Worse UX for the same outcome.
- **Chunk the input inside `summarize`.** Possible but invasive: needs a
  chunked summarization protocol, multiple LLM calls per summarize, more
  complex error handling. Out of scope for a bugfix.
- **Lift the guards globally.** Defeats their purpose for ordinary
  PredictStrategy methods that *do* want to fail loudly on oversize input.
  This fix is per-method (decorator-scoped) precisely so the global behavior
  for user-defined predict methods is unchanged.
- **`Annotated[str, spec(max_string=N)]` on `history_markdown`.** Solves (3)
  per-parameter but doesn't help (1), (2), or (4). Would still need the
  other three overrides anyway, so we'd be choosing a less-uniform API for
  no benefit.

## Scope

- Single source file changed: `src/nooa/agents/summarization.py` —
  decorator on `SummarizationAgent.summarize()` plus a constant and
  module-level imports.
- New test file: `tests/agents/test_summarization_large_input.py`.
- No public API change. No config breaking change.
- `MethodSummarizer` automatically benefits — it inherits `summarize()`
  from the base.

## Tests

Add `tests/agents/test_summarization_large_input.py` with:

1. **`test_summarize_strategy_override_present`** — introspect via
   `SummarizationAgent.summarize.__wrapped__._strategy_override` (the
   `@strategy` decorator stores `_strategy_override` on the inner `func`,
   not the wrapper — `decorators.py:83`; the wrapper has `__wrapped__`
   via `functools.wraps`, see `runtime/method_wrapper.py`). Assert it is
   a `PredictStrategy` instance and
   `instance.config.max_param_chars >= 10_000_000`.

2. **`test_summarize_truncation_override_present`** — read
   `SummarizationAgent.summarize._strategy_truncation` (this attribute
   *is* set on the wrapper at `decorators.py:128`, unlike
   `_strategy_override`). Assert
   `max_block_chars >= 10_000_000`,
   `prefill_format.max_string is None`, and
   `event_format.max_string is None`.

3. **`test_assert_param_sizes_passes_for_large_history`** — construct a
   `PredictStrategy` with the same `PredictConfig` the decorator uses,
   build a `CurrentCall` with a 1 M-char `history_markdown` string, and
   call `_assert_param_sizes` directly — must not raise. Mirrors the
   guard tests in `src/nooa/strategies/tests/test_predict_param_guard.py`.

4. **`test_method_summarizer_inherits_overrides`** — same introspection as
   tests (1)+(2) but on `MethodSummarizer.summarize` instead of
   `SummarizationAgent.summarize`. Locks the inheritance contract.

5. **`test_summarize_does_not_raise_at_predict_size_guards_with_500k`**
   (lighter-weight regression): build `CurrentCall` with `history_markdown`
   sized at 500 K chars (well past the 200 K default but well under the new
   limit) and confirm the `_assert_param_sizes` step is a no-op for the
   summarizer's effective config.

We don't need an end-to-end LLM integration test — the unit-level assertions
on the strategy/truncation overrides are enough to lock the contract in place,
and end-to-end summarization is already exercised in
`tests/agents/test_summarization_agents.py`.

## Verification

- `uv run pytest tests/agents/test_summarization_agents.py
  tests/agents/test_summarization_large_input.py
  src/nooa/strategies/tests/test_predict_param_guard.py -x`
- `uv run pytest -x -k "summariz or predict"` for a wider sweep.
- `uv run ruff check src/nooa/agents/summarization.py
  tests/agents/test_summarization_large_input.py`
- `uv run ruff format src/nooa/agents/summarization.py
  tests/agents/test_summarization_large_input.py`

## Out of scope

- The original "history grew to 473 K chars before summarization triggered"
  behavior in `EvolutionaryOptimizer`. The summarizer should now handle that
  gracefully (which is what this fix ensures), but the *trigger threshold*
  question — `TokenBudgetConfig.max_tokens` vs. real prompt growth between
  AfterTurn events — is a separate concern not reported as part of issue 180.
- Configurability: not adding a `max_input_chars` knob on `TokenBudgetConfig`.
  If a user wants tighter bounds on what the summarizer accepts, they can
  wrap the call themselves.

## Round 2 — MR review (post-merge feedback)

The MR reviewer (issue author) noted that the magic constant
`_SUMMARIZER_MAX_CHARS = 10_000_000` was arbitrary, and asked whether
`PredictConfig.max_param_chars` and `TruncationConfig.max_block_chars`
should accept `None` to mean "unconstrained" — matching `pformat`'s
`max_string=None` semantics.

This was the right call. The constant disappears; both fields now accept
`None`, the two guards (`_assert_param_sizes` and the prompt-size check
in `predict.py:execute()`) early-return when the limit is `None`, and the
summarizer's decorator becomes:

```python
@strategy(
    PredictStrategy(PredictConfig(max_param_chars=None)),
    truncation=TruncationConfig(
        max_block_chars=None,
        prefill_format=FormatConfig(max_string=None, max_length=None, max_depth=None),
        event_format=FormatConfig(max_string=None, max_length=None, max_depth=None),
    ),
)
```

Type-annotation knock-ons:
- `PredictConfig.max_param_chars: int | None = 200_000`
- `TruncationConfig.max_block_chars: int | None = 20_000` (validator updated
  to allow `None`)
- `codeact_lite.plain_event_content(max_chars: int | None = ...)` and
  `PlainCodeActBlockFormatter(max_chars: int | None = ...)` — these forward
  to `truncating_pformat(max_chars: int | None)` which already accepts
  `None`, so this is a typing-only fix.

Test updates:
- Assert `... is None` instead of `>= _SUMMARIZER_MAX_CHARS` on every
  override (more precise).
- New `test_assert_param_sizes_still_fires_when_limit_is_set` — guards the
  global default behavior so the per-method override doesn't accidentally
  weaken safety for ordinary callers.
- `_assert_format_unconstrained()` helper added to assert
  `max_string`/`max_length`/`max_depth` are all `None` (Greptile P2 — the
  defensive `max_length=None` and `max_depth=None` were not asserted in
  round 1).
