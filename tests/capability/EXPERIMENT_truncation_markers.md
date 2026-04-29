# Experiment: Truncation Marker Comprehension

**Goal:** Find a length-truncation marker shape that small LLMs comprehend reliably *without* a system prompt teaching the format. Used to inform the design of [Truncation 3.0](../../docs/design/truncation-3.0.md).

**Outcome:** Three len-upfront wrapper styles (`<list len=N>[...]</list>`, `List(len=N, items=[...])`, `list(len=N, items=[...])`) all hit 100% on parsing tasks (count, find-by-position) — a +30 percentage-point improvement over today's `[a, b, ... 95 items not shown ..., y, z]` form. Truncation-*awareness* (returning null when elided content could change the answer) is a separate reasoning problem that no marker design solves.

---

## Method

### Agent

Single-method agent using `PredictStrategy` for structured output. Strict `int | None` return type so PredictStrategy retries trigger on schema violations.

```python
# tests/capability/agents/truncation_comprehension.py

from typing import Annotated

from nemo_oo_agents import Agent
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.strategies import PredictStrategy


class TruncationComprehensionAgent(Agent):
    """You read rendered Python output (lists, dicts, captured streams) and answer
    questions about it. Some output is partial — you must distinguish what is
    actually shown from what is missing, and not invent missing content.
    """

    @strategy(PredictStrategy())
    async def answer(
        self,
        context: Annotated[str, "The rendered Python output the question is about"],
        question: Annotated[str, "A question whose answer is an integer or 'cannot determine'"],
    ) -> int | None:
        """
        Based on the `context`, answer the `question`.
        Return an integer if the answer can be determined from the data shown.
        Return null if the answer cannot be determined.
        """
        ...
```

The `int | None` return type is intentional: with a `str` return, off-task output like `{"value": "Well done!"}` validates as a string and never triggers retry. With `int | None`, only valid integers or null pass; bad output triggers up-to-10 retries (PredictStrategy default).

### Models tested

8 small/mini-class models spanning roughly ~20B–~80B (excluding llama-3.1-8b after early runs showed dominant JSON-output instability not specific to marker design):

| Model | Approx. size |
|---|---|
| `claude-haiku` | small/medium |
| `gemini-3-flash-preview` | flash-class |
| `gemini-2.5-flash-lite` | flash-lite (smallest Gemini) |
| `gpt-5-mini` | mini-class |
| `gpt-oss-20b` | ~20B |
| `nemotron3-nano-30b` | ~30B |
| `nemotron-super-49b` | ~49B |
| `qwen3-80b` | ~80B (3B-active MoE) |

Each (style, question) cell was run 3× per model = **24 samples per cell**. Final apples-to-apples experiment: 4 styles × 4 questions × 8 models × 3 runs = **384 samples**.

### Test data

Same unordered list, four wrapper styles. Same 4 questions across all styles.

**Context (same data, different wrappers):**

```
today's verbose:  [42, 17, 89, 33, 8, ... 90 items not shown ..., 56, 71, 12, 45, 28]
xml:              <list len=100>[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28]</list>
pascal-pydantic:  List(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])
lower-pydantic:   list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])
```

**Questions (same set across all four styles):**

| # | Question | Expected | What it tests |
|---|---|---|---|
| 1 | How many items are in the list total? | `100` | Marker-parsing for the count |
| 2 | What is the minimum value across all items in the list? | `null` | Truncation awareness (the actual minimum could be in the elided 90 items) |
| 3 | What is the first item in the list? | `42` | Reading the visible head |
| 4 | What is the value of the 50th item in the list (1-indexed)? | `null` | Truncation awareness (the 50th item is in the elided portion) |

The data is *unordered* on purpose: a sorted list would make question 2 trivially answerable from the visible tail. With unordered data, the visible head/tail give no information about the elided values.

Sample fixture file (`truncation_aware_lower.jsonl`):

```jsonl
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "How many items are in the list total?"}, "expected": 100}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the minimum value across all items in the list?"}, "expected": null}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the first item in the list?"}, "expected": 42}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the value of the 50th item in the list (1-indexed)?"}, "expected": null}
```

### Run command

```bash
uv run python -m eval_pipeline \
  --config tests/capability/config_truncation.yaml \
  --test truncation_aware_today_verbose,truncation_aware_xml,truncation_aware_pascal,truncation_aware_lower \
  --runs 3 --parallel 40 --timeout 240 \
  --output-dir results/
```

---

## Results

### Per-question pass rate (across 8 models × 3 runs = 24 samples per cell)

| Style | count | min (null) | first | 50th (null) | total |
|---|---|---|---|---|---|
| today_verbose `... N items not shown ...` | 13/24 | 0/24 | 24/24 | 0/24 | **37/96 (39%)** |
| xml `<list len=N>[...]</list>` | **24/24** | 0/24 | 24/24 | 0/24 | **48/96 (50%)** |
| pascal `List(len=N, items=[...])` | **24/24** | 0/24 | 24/24 | 0/24 | **48/96 (50%)** |
| lower `list(len=N, items=[...])` | **24/24** | 0/24 | 24/24 | 0/24 | **48/96 (50%)** |

### Per-model pass rate (4 styles × 4 questions = 16 samples per model per style; total 12 of 16 expected if awareness-questions all fail)

| Model | today_verbose | xml | pascal | lower |
|---|---|---|---|---|
| claude-haiku | 6/12 | 6/12 | 6/12 | 6/12 |
| gemini-2.5-flash-lite | 3/12 | 6/12 | 6/12 | 6/12 |
| gemini-3-flash-preview | 6/12 | 6/12 | 6/12 | 6/12 |
| gpt-5-mini | 6/12 | 6/12 | 6/12 | 6/12 |
| gpt-oss-20b | 6/12 | 6/12 | 6/12 | 6/12 |
| nemotron-super-49b | 3/12 | 6/12 | 6/12 | 6/12 |
| nemotron3-nano-30b | 4/12 | 6/12 | 6/12 | 6/12 |
| qwen3-80b | 3/12 | 6/12 | 6/12 | 6/12 |

The `6/12` consistency on the three new styles tells the story: every model gets the count and first questions right (24/24 = 100%) and gets the min and 50th questions wrong (0/24).

### Sample model outputs on the min question (`expected: null`)

Output across 8 models on `<list len=100>[...]</list>` style:

```
[haiku    ] {"value": 8}     ← visible-min, ignoring elided
[gemini   ] {"value": 8}
[gpt-5-mini] {"value": 0}    ← guessing 0 as a default
[gpt-oss  ] {"value": 8}
[nemoS    ] {"value": 0}
[nemo30   ] {"value": 8}
[qwen     ] {"value": 8}
```

Every model returns the visible minimum (`8`) or a guess. None return `null`. Same pattern across all 4 wrapper styles.

---

## Findings

### 1. Marker design is solid for *parsing*

All three new shapes (xml / pascal / lower) hit **24/24 = 100%** on parsing tasks (count, first-item) — a clean win over today's `[a, b, ... 95 items not shown ..., y, z]` form (13/24 on count). The bottleneck on today's form is arithmetic: small models can't reliably compute `head + N + tail = total` from the verbose marker. Putting the total upfront (`len=100`) eliminates the arithmetic and the failure.

### 2. The three new shapes tie

`<list len=N>[...]</list>` (XML), `List(len=N, items=[...])` (Pydantic PascalCase), and `list(len=N, items=[...])` (lowercase Python typenames) all score identically: 24/24 on parsing, 0/24 on awareness. Pick whichever fits the rest of the system best. The lowercase form has two pragmatic advantages:

- Uses **actual Python typenames** (`list`, `dict`, `tuple`, `set`) — matches Python's builtin names.
- **Function-call shape** is universally familiar to LLMs from training data.
- **Fewer tokens** than XML (no closing tag).

### 3. Truncation *awareness* is not solved by marker design

The min and 50th-item questions test whether the model recognizes that elided content could change the answer. **Every model fails this universally** — including capable models like claude-haiku and gpt-5-mini — across **every** wrapper style.

This is a **reasoning failure**, not a marker failure:

- The models *do* parse the marker correctly (count question = 24/24).
- They *do* find specific positions in the visible head/tail (first question = 24/24).
- They just don't reason about *what they can't see*. They default to "answer from the visible data" even when told 90 of 100 items are elided.

Better markers can't fix this. Solutions live elsewhere: prompt-level instructions ("if the answer requires elided content, return null"), per-question-type stricter schemas, or chain-of-thought prompting that forces the model to enumerate "what data would I need to answer this?"

### 4. Strict typing prevents format failures, not reasoning failures

Switching the agent from `-> str` to `-> int | None` was important infrastructure: it ensures off-task output (`"Well done!"`, corrupted tokens, empty strings) triggers PredictStrategy retries. But it cannot prevent a model from confidently returning `8` on the min question — that's a valid integer, schema-satisfied, just wrong. Retry can't help when the model produces *valid-but-incorrect* output.

### 5. Wording change ("null" → "None") doesn't help

The original prompt said "Return null"; we re-ran with "Return None" (Python convention vs JSON). Results were **bit-identical** — same 0/24 on awareness questions across all 4 styles, same totals, same per-model splits. The bottleneck is the model defaulting to visible-data reasoning, not vocabulary the model fails to recognize.

---

## Recommendations for Truncation 3.0

- **Adopt `list(len=N, items=[head, ..., tail])`** for length-truncated containers (and the symmetric `dict(len=N, items={...})`, `set(len=N, items={...})`, `tuple(len=N, items=(...))` for other built-in types).
- **Use lowercase Python typenames** to match Python's actual builtins.
- **Pydantic instances and dataclasses** keep their existing repr form (`Foo(a=1, b=2, ... +3)`) — the type name is already in the repr, and a wrapper would be redundant.
- **Truncation-awareness is a separate concern.** It needs prompt guidance, not better markers. Document this expectation in the system prompt that ships with 3.0; do not rely on markers alone to make models reason about elided content.

---

## Limitations & Caveats

- **Sample size:** 24 samples per (style, question) cell is enough to distinguish 0/24 from 24/24 (the actual outcomes) but tight for finer-grained comparisons. The 6/12 vs 3/12 differences on today_verbose for some models are within the noise band.
- **Question types:** Only counting / position / min — not exhaustive. Real LLM context contains many other question patterns.
- **Single-method agent:** Real workloads use richer agent setups (CodeAct, multi-turn). PredictStrategy in isolation may understate or overstate practical performance.
- **One container size (100 items):** Smaller and larger containers might score differently. The pattern matters for any container where head/tail isn't enough; would benefit from spot-checking edge cases.

---

## Related artifacts

- Design doc: [docs/design/truncation-3.0.md](../../docs/design/truncation-3.0.md)
- Test agent: [tests/capability/agents/truncation_comprehension.py](agents/truncation_comprehension.py)
- Test config: [tests/capability/config_truncation.yaml](config_truncation.yaml)
- Test fixtures: `tests/capability/data/truncation_aware_*.jsonl`
- Branch: `test/truncation-comprehension` (MR !147)
