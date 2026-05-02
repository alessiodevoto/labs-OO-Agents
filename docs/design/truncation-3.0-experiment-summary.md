# Truncation 3.0 — Experiment Summary

**Goal.** Pick the marker shape that LLMs (small-class through flagship) understand best when reading partially-rendered Python data. Inputs to the decision: a controlled empirical comparison of four candidate shapes across container types and generation strategies, on a 13-model matrix.

## What we measured

| dimension | values |
|---|---|
| **format** | `today_verbose` (`[a, b, ... 90 items not shown ..., y, z]`) <br> `xml` (`<list len=100>[a, b, ..., y, z]</list>`) <br> `lower` (`list(len=100, items=[a, b, ..., y, z])`) <br> `slice_keys` (`list(len=100, [:5]=[...], [-5:]=[...])`) |
| **type** | list, tuple, dict, set, pydantic, dataclass, json, records-of-dicts |
| **strategy** | `PredictStrategy` (sees only the rendered prefill) <br> `CodeActStrategy` (sees the prefill *and* can run Python against the real data) |
| **models** | 13 (claude-haiku/sonnet, gemini 2.5/3 family, gpt-5/5.2/mini, qwen3-80b/3.5-35b, nemotron3-nano-30b/super-49b/super-v3) |
| **questions** | 7 per fixture: count, min, head, mid (elided), 3rd, 9th (elided), tail |

**Test design.** Each fixture provides real Python data plus a `type_tag` and `fmt`; the agent receives the data via a thin `Wrapped` class whose `__repr__` calls our renderer. The framework's existing prefill rendering (`format_parameters_as_code`) falls through to `repr()` for unknown types, so the LLM sees the chosen marker shape — and `CodeAct`'s `execute_python` can still index, iterate, and aggregate the underlying value (`data[49]`, `min(data)`, etc.) because the wrapper passes container ops through. No framework changes; the wrapping is done in a test-local loader monkey-patch.

**Sample count.** 9,979 results across 13 models × 7 questions × 58 fixtures (4 fmts × 8 types × 2 strategies, with `slice_keys` only applicable to ordered types). Error rate 0.4% (all known buckets — bucket-1 type-mismatch on `Answer`, stringified-Answer returns, generation collapse).

## Results

**Format ranking (combined predict + codeact, all types and models):**

| format | predict | codeact | combined |
|---|---|---|---|
| today_verbose | 79% | 91% | 85% |
| xml | 80% | 92% | 86% |
| lower | 80% | 91% | 85% |
| **slice_keys** | **84%** | **93%** | **89%** |

**Strategy lift.** CodeAct beats Predict by **+12 pp** on the three "infer-positions-from-context" formats (`today_verbose`, `xml`, `lower`) and by **+7 pp** on `slice_keys` (the winner is closer to the ceiling, so less lift). This is the test design landing — earlier matrices that passed pre-rendered strings only saw a 3-5 pp CodeAct lift, because CodeAct was reduced to string parsing.

**Type × strategy headline patterns:**
- `set`: biggest predict→codeact gain (+20–25 pp). Predict can't determine min on unordered data; CodeAct just runs `min(data)`.
- `records`: best codeact at 96–99%. Stable id-based access.
- `pydantic` / `dataclass` / `json`: smallest codeact lift (+5–10 pp) — the wrapper structure trips models up before they get to the inner field.
- `dict`: smallest predict-codeact gap (+3–7 pp) — keys are reliable anchors for predict already.

**Per-model tiers** (combined): top — gemini-3.1-pro-preview, claude-sonnet, gemini-3-flash-preview (98–99%); strong — claude-haiku, gemini-2.5-pro, nemotron-3-super-v3, qwen3.5-35b (91–94%); mid — gpt-5-mini, gpt-5.2, nemotron3-nano-30b (79–83%); lagging — qwen3-80b, nemotron-super-49b, gemini-2.5-flash-lite (67–70%).

## Recommendation

A two-state design:

| condition | shape |
|---|---|
| `len(data) ≤ max_length` | plain Python `repr()` — `[1, 2, 3]`, `{1: 2}`, etc. |
| `len(data) > max_length`, ordered (list, tuple, records, pydantic-field, dataclass-field) | `type(len=N, [:H]=[...], [-T:]=[...])` |
| `len(data) > max_length`, unordered (dict, set) | `type(len=N, items={...})` |

The wrapper is a *truncation marker* — its presence unambiguously signals elision; its absence means the data is complete. One mental model for the LLM: "see `(len=N, ...)` → be careful about what's not shown."

**Why slice_keys for ordered:** explicit positional anchoring (`[:5]=[...]`, `[-5:]=[...]`) removes the model's burden of counting from the visible head/tail to infer where the elided range starts. Wins predict by 3–8 pp across all ordered types vs the next-best format.

**Why lower for unordered:** dict and set don't have meaningful positional slices, and `lower` is valid Python (`dict(len=N, items={...})` parses). Same outer wrapper as `slice_keys`, so the model only learns one shape.

## Bounds

With `max_length` (= H + T), `max_string`, and `max_depth` all set, the renderer guarantees a polynomial bound on render size: **O((H+T)^max_depth × max_string)**. For typical (10, 100, 4) ≈ 1 MB upper bound for arbitrary Python values, including cyclic graphs (`<cycle>`) and generators (`<generator>`). For a hard byte ceiling (e.g., 16 KB per block), compose the renderer with the existing `TruncatingStringIO`.

## Status of the truncation 3.0 design

| layer | status |
|---|---|
| **L1** (agent `pprint(...)`) | needs slice notation in `truncating_pformat` — implementing now |
| **L2** (I/O capture, `<truncated>` wrapper) | landed |
| **L3** (block render — pformat with bounded args, never fail) | covered by L1 + the bounds discussion above |
| **L4** (eviction in context-window assembly) | not addressed by this experiment |

Next step is wiring the slice notation into `truncating_pformat` so the production renderer matches the recommended shape. Test infrastructure (`Wrapped`, `truncation_formats`, `realfmt_*` agents, fixtures, analyzer) stays as a regression suite for future format changes.
