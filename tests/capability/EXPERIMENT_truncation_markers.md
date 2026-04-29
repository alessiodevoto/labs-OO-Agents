# Experiment: Truncation Marker Comprehension

**Goal.** Find a marker design and an agent schema that small LLMs can use *without* a system prompt teaching the format. Inform the design of [Truncation 3.0](../../docs/design/truncation-3.0.md).

**Headline outcome.** Two independent decisions matter, in roughly equal measure:

- **Marker shape.** `list(len=N, items=[head, ..., tail])` — lowercase Python typename, function-call shape, total upfront. Generalizes to `dict(len=N, items={...})`, `tuple(len=N, items=(...))`, `set(len=N, items={...})`, and as an inner field of any Pydantic / dataclass instance.
- **Agent schema.** A Pydantic return type bundling `answer` with a `reason` string. Forcing self-justification raises truncation-awareness from 0% to ~60-95% across small models.

Combined, the recommended setup hits **~80-90% across all tested container types and question categories** on a curated 8-model small/mini matrix.

---

## Method

### Agent

Two agents, identical persona, different return types — used as an A/B control:

```python
# tests/capability/agents/truncation_comprehension.py

from typing import Annotated
from pydantic import BaseModel, Field
from nemo_oo_agents import Agent
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.strategies import PredictStrategy


class Answer(BaseModel):
    answer: Annotated[int | None, Field(description="Integer answer, or None if cannot be determined")]
    reason: Annotated[str, Field(description="Why you picked that answer (one or two sentences)")]


class TruncationComprehensionAgent(Agent):
    """You read rendered Python output (lists, dicts, captured streams) and answer
    questions about it.
    """

    @strategy(PredictStrategy())
    async def answer(
        self,
        context: Annotated[str, "The rendered Python output the question is about"],
        question: Annotated[str, "A question to answer."],
    ) -> Answer:
        """
        Based on the `context`, answer the `question`.
        Return an integer if the answer can be determined from the data shown.
        Return None if the answer cannot be determined.
        Include a brief reason string explaining your choice.
        """
        ...


class TruncationComprehensionAgentBare(Agent):
    """You read rendered Python output (lists, dicts, captured streams) and answer
    questions about it.
    """

    @strategy(PredictStrategy())
    async def answer(
        self,
        context: Annotated[str, "The rendered Python output the question is about"],
        question: Annotated[str, "A question to answer."],
    ) -> int | None:
        """
        Based on the `context`, answer the `question`.
        Return an integer if the answer can be determined from the data shown.
        Return None if the answer cannot be determined.
        """
        ...
```

The two agents share the same class docstring (the system-prompt persona), method docstring shape, and parameter descriptions. Only the return type differs — `Answer` vs bare `int | None`. This isolates the schema variable.

### Models tested (8 small/mini, ~20B–~80B)

| Model | Approx. size |
|---|---|
| `claude-haiku` | small/medium |
| `gemini-3-flash-preview` | flash |
| `gemini-2.5-flash-lite` | flash-lite |
| `gpt-5-mini` | mini |
| `gpt-oss-20b` | ~20B |
| `nemotron3-nano-30b` | ~30B |
| `nemotron-super-49b` | ~49B |
| `qwen3-80b` | ~80B (3B-active MoE) |

llama-3.1-8b was tested earlier and removed because its JSON-output instability dominated the signal.

### Question set

A standard 7-question set, parameterized over the same unordered list `[42, 17, 89, 33, 8, …(elided 90)…, 56, 71, 12, 45, 28]`:

| # | Question | Expected | What it tests |
|---|---|---|---|
| 1 | How many items total? | `100` | Marker parsing — read `len=N` |
| 2 | What is the minimum value? | `None` | Awareness — elided items could change the answer |
| 3 | What is the first item? | `42` | Read visible head, position 1 |
| 4 | What is the 50th item? | `None` | Awareness — position 50 is in the elided range |
| 5 | What is the 3rd item? | `89` | Read visible head, position 3 |
| 6 | What is the 9th item? | `None` | Awareness — position 9 is elided; tempting to confuse with "9th visible" |
| 7 | What is the 99th item? | `45` | Read visible tail, position 99 — requires understanding tail occupies positions 96-100 |

The 9th and 99th questions are sharper than the simpler "is the data partial?" questions used in earlier rounds. They require the model to reason about *which positions* are elided vs visible.

### Marker shapes tested (apples-to-apples)

Same data, four wrappers:

```
today_verbose:  [42, 17, 89, 33, 8, ... 90 items not shown ..., 56, 71, 12, 45, 28]
xml:            <list len=100>[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28]</list>
pascal:         List(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])
lower:          list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])
```

### Container types tested (lower style + with-reason agent)

After the apples-to-apples shape comparison settled on `lower` as the recommended shape, container generalization was checked using the same 7-question pattern adapted per type:

- **list** — `list(len=100, items=[…])`
- **dict** — `dict(len=100, items={0: 42, 1: 17, …, 98: 45, 99: 28})`
- **tuple** — `tuple(len=100, items=(…))`
- **pydantic instance** — `Team(name='alpha', members=list(len=100, items=[…]), status='active')`
- **dataclass instance** — `Project(name='alpha', tasks=list(len=100, items=[…]), owner='Bob')`
- **json-shaped dict** — `{"items": list(len=100, items=[…])}`

Plus depth and string-truncation tests:

- **depth** — `dict(len=3, items={'config': {dict: 5 items}, 'data': list(len=100, items=[…]), 'meta': {dict: 4 items}})` — exercises the `{Type: N items}` shallow form.
- **string slicing** — `Job(name='build', exit_code=1, stdout='2024-01-01 INFO startup\n…ERROR connection failed'+8500, runtime_seconds=42)` — exercises rich's `'foo'+N` idiom for max_string truncation.

Sample fixture (`truncation_aware_lower.jsonl`):

```jsonl
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "How many items are in the list total?"}, "expected": 100}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the minimum value across all items in the list?"}, "expected": null}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the first item in the list?"}, "expected": 42}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the value of the 50th item in the list (1-indexed)?"}, "expected": null}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the value of the 3rd item in the list (1-indexed)?"}, "expected": 89}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the value of the 9th item in the list (1-indexed)?"}, "expected": null}
{"args": [], "kwargs": {"context": "list(len=100, items=[42, 17, 89, 33, 8, ..., 56, 71, 12, 45, 28])", "question": "What is the value of the 99th item in the list (1-indexed)?"}, "expected": 45}
```

Each cell = 8 models × 3 runs = 24 samples. Most experiment slices are 168 (1 fixture × 7 questions × 24).

---

## Results

### Headline: marker shape × agent schema

8 models × 7 questions × 3 runs = 168 samples per cell.

| Marker style | with-reason | bare | uplift |
|---|---|---|---|
| today_verbose | 124/168 (74%) | 69/168 (41%) | **+33pp** |
| xml `<list len=N>[…]</list>` | 130/168 (77%) | 83/168 (49%) | **+28pp** |
| pascal `List(len=N, items=[…])` | 133/168 (79%) | 83/168 (49%) | **+30pp** |
| lower `list(len=N, items=[…])` | **141/168 (84%)** | 82/168 (49%) | **+35pp** |

The bare agent caps at ~50% because it cannot return None on awareness questions (model just emits the visible minimum or a guess). The with-reason agent unlocks the awareness path: forcing the model to articulate its logic catches the cases where the visible data doesn't actually justify the answer.

`lower` consistently wins by ~5pp over the others — actual Python typenames, function-call shape, no closing tag.

### Per-question breakdown (lower style + with-reason)

| Question | Pass rate |
|---|---|
| count(100) — parse `len=N` | 24/24 = 100% |
| 1st(42) — visible head, position 1 | 24/24 = 100% |
| 3rd(89) — visible head, position 3 | 24/24 = 100% |
| 50th(None) — elided position | 23/24 = 96% |
| min(None) — awareness, unordered | 16/24 = 67% |
| 9th(None) — elided position, off-by-N tempting | 15/24 = 63% |
| 99th(45) — visible tail, position 99 | 15/24 = 63% |

Marker parsing (count, 1st, 3rd) is solved. Awareness on simple elided positions (50th) is mostly solved. The remaining gap is in three places: min reasoning (the model still defaults to visible-min), 9th-item position arithmetic (model maps "9th" to a visible item), and 99th-item tail-arithmetic (model off-by-ones the position).

### Container generalization (lower style + with-reason)

Same 7-question pattern adapted per container type. Each cell = 168 samples.

| Container | Total | Notes |
|---|---|---|
| list (baseline) | 141/168 (84%) | original test |
| **dict** | **154/168 (92%)** | best — key-value access avoids the "9th visible" confusion |
| tuple | 137/168 (82%) | structurally identical to list |
| pydantic instance | 132/168 (79%) | wrapping a long list field |
| dataclass instance | 132/168 (79%) | identical repr to pydantic |
| json (double-quoted dict) | 137/168 (82%) | dict with JSON-style quotes |

The shape generalizes cleanly. Dict actually scores best because the question phrasing — "value at key 0" — sidesteps the position-arithmetic failures that hurt list/tuple.

### Depth + string truncation

Two additional fixtures exercising the rest of the pprint surface:

```
depth: dict(len=3, items={'config': {dict: 5 items}, 'data': list(len=100, items=[…]), 'meta': {dict: 4 items}})
```

| Question | Pass rate |
|---|---|
| top_count(3) | 24/24 |
| data list len(100) | 24/24 |
| config dict len(5) | 24/24 |
| meta dict len(4) | 24/24 |
| data first(42) | 24/24 |
| data 50th(None) | 19/24 |
| config.host(None) | 24/24 |
| **Total** | **165/168 (98%)** |

`{Type: N items}` is universally understood. Models correctly refuse to answer about depth-truncated content.

```
string slicing: Job(name='build', exit_code=1, stdout='…snippet…'+8500, runtime_seconds=42)
```

| Question | Pass rate |
|---|---|
| exit_code(1) | 24/24 |
| runtime(42) | 24/24 |
| status_code(200) | 24/24 |
| +N suffix=8500 | 24/24 |
| +N suffix=12000 | 22/24 |
| 100th_char(None) | 16/24 |
| 5000th_char(None) | 16/24 |
| **Total** | **148/168 (88%)** |

Sibling field extraction works cleanly. Models can read `+N` as an integer count. Awareness on character-level positions is harder than item-level — small models default to "visible char count is what I have."

---

## Failure-mode analysis

We extracted the model's `reason` text on every wrong answer across cmp18 / cmp19 / cmp20 (315 failures total) and clustered them. Six distinct failure patterns emerged.

### Failure 1 — Min-from-visible (83 cases, 100% of `min` failures)

**What:** Asked "what is the minimum value across all items?" with `len=100`, model returns `8` (the minimum of the visible 10 items).

**Sample reasons:**
- *"The smallest visible is 8. The truncated items are not shown."*
- *"The minimum value in the list is 8, as it is the smallest number shown in the truncated list."*
- *"The minimum value among these is 8. The truncated items are not shown."*

**Pattern:** Model correctly identifies that the data is truncated (often verbatim) but answers from the visible portion anyway.

### Failure 2 — Position-mapping confusion on the 9th item (84 cases)

**What:** Asked for the 9th item in a 100-list with positions 1-5 and 96-100 visible. Model maps "9th" to a visible item via various incorrect schemes.

**Sub-patterns:**
- 44× → `8` (last visible head item, position 5 — model thinks "9th = 5 head + …")
- 12× → `33` (4th head item)
- 8× → `28` (last visible tail item, model maps "9th" to "9th from the start of visible = 9 visible items in")
- 7× → `56` (first tail item)

**Sample reasons:**
- *"The 9th item in the list (1-indexed) is explicitly shown as 8 in the truncated representation."*
- *"The 9th item is 33. The context shows the first few items..."*

**Pattern:** Model treats the visible items as the entire list, indexing into them as if `len=N` doesn't apply.

### Failure 3 — Off-by-one on 99th tail position (53 cases)

**What:** Asked for the 99th item in a 100-list. Visible tail is `[…, 56, 71, 12, 45, 28]` at positions 96-100. Correct answer is 45. Model returns 28.

**Sample reasons:**
- *"The 99th item is the second to last item, which is 28."*
- *"The 99th item is the last shown element, which is 28."*

**Pattern:** Model says "second to last" but reports the last. Off-by-one in reading the tail.

### Failure 4 — Refusal on visible content (34 cases on 99th)

**What:** Asked for the 99th item — which IS visible in the tail — model answers None.

**Sample reasons:**
- *"The 99th item cannot be determined."*
- *"The provided context only shows the first few and last few items… the 99th item cannot be determined."*

**Pattern:** Model is overly conservative; sees `…` and refuses without checking whether the position lands in the visible tail.

### Failure 5 — Count miscalibration (12 cases on count)

**What:** Even with `len=N` upfront, models sometimes answer wrong on count.

**Sub-patterns:**
- 5× → `95` (5 head + 90 elided, forgot the 5 tail)
- 3× → `90` (just the elided count)
- 2× → `None` (refusal)
- 1× → `93` (arithmetic error)

**Sample reasons:**
- *"5 visible items at the start, 5 at the end, 90 not shown… the count is 95"* — reasoning is right but arithmetic is wrong
- *"The total list length is 90"* — confused elided-count with total

**Pattern:** Some models do not propagate `len=N` to the answer when other ways to count are tempting.

### Failure 6 — Off-by-one on head positions (1 case on 3rd)

**What:** Asked for the 3rd item (visible at position 3). Model returns the 2nd item.

**Sample reason:** *"The 3rd item is at index 2 in 0-indexed terms."*

**Pattern:** Confusion between 1-indexed and 0-indexed despite the question stating "(1-indexed)". Rare.

### Net pattern

Five of the six failure modes — Failures 1, 2, 3, 5, 6 — share a common cause: **the model defaults to "answer from the visible portion" and applies its position arithmetic to the visible items**, rather than propagating `len=N` through to the implication that most positions are not visible. Failure 4 is the opposite extreme — refusing even when the answer is visible.

The `<list len=N>` style and the `Answer(answer, reason)` schema together push models from "always answer from visible" (47%) up to "answer from visible OR refuse correctly" (80-90%). Closing the remaining gap requires more than marker / schema design — it's a model-reasoning limitation that needs prompt-level guidance ("propagate len through to position math") or chain-of-thought setups (separate "what positions are elided?" → "is my answer in those?" steps).

---

## Recommendations for Truncation 3.0

### Marker shapes

Use **lowercase Python typenames in function-call form** for length-truncated containers:

```
list(len=N, items=[head, ..., tail])
dict(len=N, items={k1: v1, ..., kN: vN})
tuple(len=N, items=(...))
set(len=N, items={...})
```

Pydantic / dataclass instances render as `Foo(field=value, …)` — their type name is already in the repr; no wrapper needed. When such an instance has a long list/dict field, that field uses the wrapper internally.

### Agent schema

Default to a structured Pydantic return type that bundles the answer with a `reason` field:

```python
class Answer(BaseModel):
    answer: int | None
    reason: str
```

The reason field is the single biggest lever for truncation awareness. Worth ~30pp across all marker styles.

### Other markers (unchanged from earlier rounds)

- `{dict: N items}` / `[list: N items]` / `Type(...)` for depth-truncated containers — universally understood (96/96 in this experiment).
- `'foo'+N` for explicit-only string truncation (rich's idiom).
- `<truncated>...head...tail...</truncated>` for L2 capture overflow.
- `<cycle>`, `<generator>` for meta cases.

### Where this design *doesn't* solve the problem

Truncation awareness on harder reasoning (min, position math, character-level questions) caps around 60-70% even with the recommended shape and schema. Closing that gap is a reasoning problem, not a marker problem. Solutions live in prompt design ("propagate len through to position math"), per-question schema constraints, or chain-of-thought patterns — not in better markers.

---

## Limitations

- **Sample size:** 24 samples per (style, question) cell. Enough to distinguish 0/24 from 24/24 cleanly; finer comparisons (e.g. 14 vs 16 of 24) are within noise.
- **One container size** (100 items) per fixture. Edge cases at very small or very large containers are not exercised here.
- **Question types:** integer-typed answers only (because the agent's `int | None` constraint). String / boolean / structured answers might surface different failure modes.
- **Agent setup:** `PredictStrategy` only. CodeAct, multi-turn, or tool-calling setups may behave differently.
- **Position arithmetic:** the position questions (3rd, 9th, 50th, 99th) are an artificial stress test; many real LLM workloads don't ask "what is the Nth item" in a truncated list. Lower-failure tasks (sibling field extraction, simple counts) hit ≥95% in this matrix.

---

## Related artifacts

- Design doc: [docs/design/truncation-3.0.md](../../docs/design/truncation-3.0.md)
- Test agent: [tests/capability/agents/truncation_comprehension.py](agents/truncation_comprehension.py)
- Test config: [tests/capability/config_truncation.yaml](config_truncation.yaml)
- Test fixtures: `tests/capability/data/truncation_aware_*.jsonl`, `tests/capability/data/truncation_size_v*.jsonl`
- Branch: `test/truncation-comprehension` (MR !147)
