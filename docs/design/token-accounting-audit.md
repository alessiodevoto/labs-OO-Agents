# Token Accounting Audit

How NeMo OO Agents counts tokens, where those counts are stored, who reads them,
and the bugs found (+ fixed) in this audit. Written against `main` +
the fixes on `fix/summarizer-trigger-stale-last-actual`.

## TL;DR — the four bugs (all token-accounting, compounding)

| # | site | bug | fix |
|---|------|-----|-----|
| 1 | `actor.py:_build_messages` | pre-call estimates were repeatedly reinterpreted as headline truth | !418 follow-up — keep render estimates as fallback only; after a successful call, overwrite headline `total_tokens` with provider `usage.prompt_tokens` |
| 2 | `summarization.py:_should_summarize` | mixed exact API actuals with local estimates, making trigger behavior hard to reason about | !418 simplification — trigger only from per-runtime provider actual `_last_prompt_tokens_actual` |
| 3 | `unifiedllm.py:_update_token_calibration` | `estimated` summed **message text only**, omitting the **tool schemas** the API bills → ratio inflated to ~2.7× → every displayed count ~2.7× reality | !418 `e5261474` — count messages-mode + tool schemas |
| 4 | `actor.py:_archive_on_context_error` | per-actor `ratio_B = actual/total_tokens` collapsed to ~1.0 once `total_tokens` became calibrated (side effect of #1) → archival cap under-shed | !418 `080ef425` — remove the per-actor ratio; cap = `ctx_window*0.70` directly; use the error's reported count on cold-start |

## 1. Sources of token information

There are exactly **two** primitive sources; everything else is derived.

### S1 — `litellm.token_counter` (estimate)
The only tokenizer we call ourselves. Two modes:
- **text-mode**: `token_counter(model, text=str)` — counts a bare string.
- **messages-mode**: `token_counter(model, messages=[...], tools=[...])` — counts a
  full request the way the API does: role/chat-template framing **and** (when
  passed) the tool/function schemas.

Skew: litellm's tokenizer is approximate per model. For the Azure-Anthropic
`claude-opus` gateway it under-counts; the *calibration ratio* corrects for that.

Fallback when an LLM client exposes no counter:
`char_approximate_token_counter(text) = len(text) // 4` (`token_counter.py`).

### S2 — API response `usage` (ground truth)
When a provider returns usage, `usage.prompt_tokens` (or `input_tokens` for
the Responses API / some providers) is the **exact** count the provider billed
for the input — the only true number we get back. Extracted in
`_extract_reasoning_and_usage` (`unifiedllm.py:1424`).

## 2. Stored variables

| variable | where | scale | meaning |
|----------|-------|-------|---------|
| `_token_calibration` (module singleton) | `unifiedllm.py:948` | — | per-model EMA of `actual/estimated`; holds `_ratios[model]` for fallback estimates |
| `_ratios[model]` | inside the singleton | ratio | EMA calibration ratio (S2/S1) |
| `ActorRuntime._last_prompt_tokens_actual` | `actor.py` | real tokens | per-runtime API `prompt_tokens` from this agent's most recent successful call; summarizer trigger source |
| `ContextWindowStats.context_blocks_tokens` | `models.py:234` | calibrated | system-block tokens (S1 × ratio) |
| `ContextWindowStats.events_tokens` | `models.py:240` | calibrated | event-block tokens (S1 × ratio) |
| `ContextWindowStats.total_tokens` | `models.py:244` | real tokens when usage is available; local estimate otherwise | the headline; `generate()` overwrites it with provider `prompt_tokens` after successful calls |
| `ActorRuntime._last_context_stats` | `actor.py:583` | — | the most recent `ContextWindowStats` (published by `_build_messages`) |

## 3. Producers (who SETS the counts)

- **P1 — `render_context` (`renderer.py:202`)**: builds a `ContextWindowStats`
  with `total = context_blocks_tokens + events_tokens`, both via
  `count_fn = llm.count_tokens` (already calibrated). Invariant holds here.
- **P2 — `ActorRuntime._build_messages` / `generate()` (`actor.py`)**: `_build_messages`
  publishes the render-context estimate as a fallback; after a successful API
  call, `generate()` overwrites `total_tokens` with provider `usage.prompt_tokens`,
  the exact count for the request that was just sent.
- **`_update_token_calibration` (`unifiedllm.py:951`)**: the only writer of the
  module calibration singleton. Called after every successful completion at 4
  sites (sync/async × completions/responses). The #3 fix made its `estimated`
  count messages-mode + tool schemas so the ratio reflects tokenizer skew, not a
  coverage gap.

## 4. Consumers (who READS the counts)

- **C1 — TUI ctx% / `ContextWindowStats.format()` (`models.py:306`)**: pure
  `total_tokens / max_total` display. After a successful call this is provider
  actual; before one exists it is only the render fallback estimate.
- **C2 — `TokenBudgetSummarizer._should_summarize` (`summarization.py:597`)**:
  fires only when this runtime's provider-reported `_last_prompt_tokens_actual` is over `config.max_tokens`.
  Local estimates do not trigger summarization.
- **C3 — `ActorRuntime._archive_on_context_error` (`actor.py:603`)**: post-400 event
  shedding. After #4: cap = `ctx_window*0.70` (real scale, no ratio division);
  uses the error's reported `prompt_tokens` as the real size on cold-start.
- **C4 — summarizer input cap (`summarization.py:_input_token_budget`)**: bounds
  the summarizer's *own* render to ~70% of its model window (the !414 backstop).
- Not consumers: `trace_routes.py:646 usage.total_tokens` is the LLM-response
  usage field (unrelated to `ContextWindowStats`); `harness_metrics` has no ref.

## 5. Why the numbers disagreed (the user's "no way 300k/turn")

For a long session: ~1.0M chars of event text ≈ **~253k real tokens** (chars/4).
litellm raw ≈ 139k. The UI showed ~377k events / ~765k total — **~2.7× inflated**,
because the calibration *ratio* itself was bogus (#3): it was learned from
`(API actual incl. tool schemas) / (our estimate excl. tool schemas)`, so it
folded the fixed per-call tool-schema cost into a multiplier and then applied
that multiplier to every event. Not "300k/turn" — ~270 tokens/event × 950
events, inflated 2.7× by a miscalibrated ratio.

## 6. Can we get *actual* token counts from providers?

**After a call: yes, when usage is returned** — `usage.prompt_tokens` (S2) is the exact input
count when the provider returns it. We store it per runtime for display/triggering; calibration uses it immediately to update
the EMA ratio and does not retain a separate global actual cache.

**Before a call (a true pre-flight count): partially.**
- **Anthropic** has a dedicated `POST /v1/messages/count_tokens` endpoint —
  exact input tokens for a given messages+tools payload, no generation. litellm
  exposes `litellm.token_counter` / `litellm.acount_tokens`; for Anthropic models
  litellm can route to the real count-tokens endpoint (vs its local estimator).
- **OpenAI / Azure-OpenAI**: no public pre-call count endpoint — you estimate
  locally (tiktoken) and learn the true count from `usage` after the call.
- **Most gateways (our Azure-Anthropic `claude-opus-4-8`)**: same — `usage`
  after the call is the only guaranteed truth.

**Recommendation:** the calibration approach (estimate locally, correct by the
EMA of `usage/estimate`) is the right general design — it works for every
provider and needs no extra round-trip. The #3 fix makes the *estimate* count
the same payload the API bills (messages + tools), so the ratio converges to the
model's true tokenizer skew (~1.0–1.3×) instead of a tool-coverage artifact.
A *further* optional improvement: for Anthropic-family models, call the real
`count_tokens` endpoint to seed/replace the local estimate (exact, but adds a
network call and only helps that provider) — worth it only if the post-#3 ratio
still drifts in practice.
