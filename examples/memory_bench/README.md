# Memory Benchmark — long-horizon agentic CLI tasks

A full-fledged example exercising the opt-in memory subsystem
(`nemo_oo_agents.memory`) end to end: **both vector backends** (sqlite-vec and
Chroma), the **real models** (gpt-5.4 + `text-embedding-3-large` on the NVIDIA
gateway), a **long-horizon benchmark** modelled on
[LongCLI-Bench](https://github.com/finyorko/longcli-bench), and **memory-usage
monitoring** via the framework's existing logging/event systems.

## Research question

> On a long-horizon task split into dependent "sessions" — where short-term
> context is wiped between sessions — does an agent's long-term memory let it
> carry forward the conventions/skills it established earlier, improving
> requirement fulfillment (fail→pass) and regression avoidance (pass→pass)?

## Experiment design

One small project (`KVStore`) is built up over four dependent tasks, one per
LongCLI-Bench category — `from_scratch` → `feature_add` → `bug_fix` → `refactor`
(see [`tasks.py`](./tasks.py)). Each task has a **fail→pass** check (the new
requirement) and a **regression** set (all earlier checks, which must keep
passing) — LongCLI-Bench's dual-set protocol.

Between tasks the agent's **short-term context is cleared** (a fresh session), so
only **long-term memory** carries the conventions forward: the data-file format,
the public API, the bug-fix lessons. With memory **off**, the agent must
rediscover them each session.

Two solvers:

| solver | what it is | needs |
|--------|-----------|-------|
| `oracle` | deterministic; writes the known-good solution and drives the full memory pipeline + monitoring | nothing (offline) |
| `llm` | a real CodeAct agent (gpt-5.4) that writes the code itself, using `self.recall`/`self.remember` | `ARC_LLM_*` env |

The `oracle` solver **verifies the harness, the memory plumbing, and the
monitoring** with no credentials. The real *success-lift* from memory is measured
with the `llm` solver against gpt-5.4. (The oracle always solves, so its ON/OFF
*success* is identical by construction — only the memory-usage counters differ.)

## Key metrics

- **requirement fulfillment (f2p)** — fraction of tasks whose new requirement passes.
- **regression avoidance (p2p)** — fraction of later tasks that keep earlier checks green.
- **steps** — LLM turns per task (1 for the oracle).
- **memory usage** — `MemoryStats`: writes, reinforced (dedup-on-write), recalls,
  recalled_items, injections, reflections, merged, edges_added, pruned, store_size.

## Monitoring & logging

Uses the framework's existing observability (no core changes):

- the `nemo_oo_agents.memory` logger (`--verbose` raises it to DEBUG for per-op traces);
- `MemoryWritten` / `MemoryRecalled` / `MemoryInjected` / `ReflectionCompleted` events
  emitted on the agent's `EventManager` with the `RUNTIME_EVENT` role (visible to
  any event/telemetry subscriber, never shown to the LLM);
- `manager.memory_stats()` / `manager.log_summary()` for a counter snapshot.

## How to run

Offline (no credentials) — verifies harness + monitoring across backends:

```bash
uv run python examples/memory_bench/bench.py --solver oracle --compare --backend numpy
uv run python examples/memory_bench/bench.py --solver oracle --compare --backend sqlite_vec
uv run python examples/memory_bench/bench.py --solver oracle --compare --backend chroma_embedded
```

Real run with gpt-5.4 + text-embedding-3-large (set the env from `llm.py`):

```bash
export ARC_LLM_MODEL=openai/openai/gpt-5.4
export ARC_LLM_BASE_URL=https://inference-api.nvidia.com/v1
export ARC_LLM_API_KEY=...                       # NVIDIA gateway key
export MEM_EMBED_MODEL=openai/azure/openai/text-embedding-3-large
export MEM_EMBED_BASE_URL=https://inference-api.nvidia.com/v1/embeddings
export MEM_EMBED_API_KEY=...
export MEM_EMBED_DIMS=1024

uv run python examples/memory_bench/bench.py --solver llm --compare \
    --backend chroma_embedded --embedder litellm --verbose
```

`sqlite-vec` / `chromadb` are optional: `uv pip install sqlite-vec chromadb`.

> **gpt-5.4 routing note.** Default reasoning + CodeAct works with
> `ARC_LLM_MODEL=openai/openai/gpt-5.4`. Passing `--reasoning-effort high` pushes
> the call onto litellm's Responses route, which strips an extra provider segment
> on this gateway — add one more `openai/` (i.e. `openai/openai/openai/gpt-5.4`)
> for high-reasoning runs. `drop_params=True` (already set) handles the
> unsupported `tool_choice`. See `examples/arc_agi/NEMO_OO_FINDINGS.md` §4b.

## Relationship to LongCLI-Bench

This example reproduces LongCLI-Bench's **structure** (long-horizon dependent
tasks, four categories, fail→pass + regression dual-set, step scoring) in a
self-contained, dependency-free form so it runs anywhere. The upstream benchmark
runs its 20 curated tasks inside per-task **Docker** containers via a
Terminal-Bench harness; to run the memory agent against those, point an adapter
at `longcli-bench/tasks_long_cli/` and drive each container with the same
`MemoryToolsMixin` + `MemoryManager.install(...)` agent used here.

## When does memory actually matter? (eight runnable benchmarks)

The KVStore suite above shows the *system* works but not a memory *lift* — a
strong model (gpt-5.4) solves those self-contained tasks with or without memory.
Five extra scripts isolate where memory makes a real difference.

### `locomo.py` — a published benchmark, with **agent-authored** memory (the differentiator)

[LoCoMo](https://github.com/snap-research/locomo) (Maharana et al., 2024) is the
long-term conversational-memory benchmark memory systems (Mem0, Zep, …) report on:
very long multi-session dialogues (~19 sessions, ~400 turns) whose questions need
earlier turns recalled.

**The key difference from other systems:** they *extract and store memories for the
agent* (a harness pipeline). Here the **agent authors its own memories** — it reads
each session and calls `self.remember(...)` to write schema-structured memories
itself (no raw bulk-store, no harness extraction). At QA time it retrieves them and
answers, vs. a **no-memory** ablation. Because the agent does the authoring, this
benchmark **requires LLM credentials** (no offline/raw fallback); the dataset
auto-downloads (≈2.8 MB, not vendored).

```bash
uv run python examples/memory_bench/locomo.py --backend chroma_embedded --embedder litellm --limit 16
```

**Real gpt-5.4 + text-embedding-3-large (sample 0, 16 questions balanced, LLM-judged).**
The agent distilled the 419 raw turns into **153 self-authored memories**, then:

| category | memory ON | memory OFF |
|----------|-----------|------------|
| single-hop | **3/5 (60%)** | 0/5 (0%) |
| temporal | 2/5 (40%) | 0/5 (0%) |
| multi-hop | 2/6 (33%) | 0/6 (0%) |
| **overall** | **7/16 (44%)** | **0/16 (0%)** |

**→ +44% from memory** — and *as accurate as* bulk-retrieving all 419 raw turns
(42% in an earlier run) from a ~3× smaller, agent-curated store. OFF is 0% by
construction (no access to the conversation), isolating memory's contribution;
accuracy tracks difficulty (single-hop ≫ temporal/multi-hop), matching published
LoCoMo patterns. The agent is *told* it owns its memory and is given the schema
(`MEMORY_SCHEMA_GUIDE`, injected at install) — so it also `update_memory()`s and
`forget()`s as it goes, rather than the framework extracting memories behind it.

**Does reflection help here? No — it hurts.** `--reflect` A/Bs consolidation on the
*same* agent-authored memories (QA before vs after `manager.reflect()`, which merges
duplicates and uses an LLM reasoner to abstract turns into `reflection` memories):

```bash
uv run python examples/memory_bench/locomo.py --backend chroma_embedded --embedder litellm --reflect --limit 15
```

| overall | OFF | ON | ON+reflect |
|---|---|---|---|
| accuracy | 0% | **60%** | 40% |

Reflection was **−20%** (single-hop 40→20, temporal 80→60, multi-hop 60→40). LoCoMo
asks for *pinpoint* facts (a date, a name, one detail); the reflection's abstraction is
**lossy** — it summarises specific turns into broad reflections that then crowd out
the exact memory a question needs. This is the mirror image of `reflecting.py` (+50%),
where *synthesis* questions reward consolidation — and it's exactly why LoCoMo keeps
reflection **off** by default. Memory (and consolidation) is a tool with a
precision/abstraction trade-off, not a free win. (Single run; agent authorship +
abstraction are stochastic, but the direction is consistent.)

### `recall_qa.py` — memory is decisive (clear positive gain)

Cross-session QA: facts are taught one-per-session (short-term context wiped
between sessions), then asked back in fresh sessions. The answers are **unique,
unguessable tokens**, so a no-memory agent cannot derive them — this isolates
memory's contribution.

```bash
uv run python examples/memory_bench/recall_qa.py --solver oracle   # deterministic
uv run python examples/memory_bench/recall_qa.py --solver llm --backend chroma_embedded --embedder litellm
```

**Real gpt-5.4 + text-embedding-3-large:** memory **ON 6/8 (75%)** vs
**OFF 0/8 (0%)** → **+75% gain**. (Oracle, deterministic: ON 100% / OFF 0%.) OFF
is uniformly "I don't know" — exactly as designed, since the tokens can't be guessed.

### `memory_effect.py` — memory useful *and* detrimental

Two paired-session scenarios. `recall`: a stable convention only memory still
holds. `stale`: a convention that **changed** between sessions, so a recalled-but-
outdated memory misleads.

```bash
uv run python examples/memory_bench/memory_effect.py --solver oracle
uv run python examples/memory_bench/memory_effect.py --solver llm --backend chroma_embedded --embedder litellm
```

**Real result:** the `stale` scenario shows **memory HURT** (ON acts on the
outdated fact and fails; OFF inspects current reality and passes) — the
proactive-interference failure the design's reconsolidation/forgetting targets.
The deterministic oracle shows both sides cleanly (recall → helped, stale → hurt).
With a strong model the `recall` *lift* only appears when the fact is genuinely
unavailable in-session — which is what `recall_qa.py` demonstrates.

**The by-reference fix (third arm, `ON+refs`).** The same `stale` scenario, but
the memory stores a *pointer* to the schema doc
(`references=["file:.../SCHEMA.md"]`) instead of a frozen copy of the value. At
recall time the reference resolves LIVE against the current file — the memory
cannot go stale. Oracle result: `stale` ON **FAIL** / OFF PASS / **ON+refs
PASS** → "memory HURT; references FIXED it". This is the direct proof of the
pass-by-reference feature (design plan §4).

### `todo_prospective.py` — prospective memory: do commitments fire later?

Session 0 plants "when X happens, do Y" commitments as `type="todo"` memories
(plus 12 distractor facts); later sessions announce one cue each with context
wiped. Metrics: **surfaced** (the right todo appears in its cue session's
injected block — the memory system's job), **fired** (a minimally-judging
policy then acts — the agent's job), **false fires**, and **closed** (fired
todos marked DONE). Arms = the `inject_open_todos` A/B: `OFF` / `relevant` /
`always`.

```bash
uv run python examples/memory_bench/todo_prospective.py --solver oracle
```

Oracle result: OFF 0/4 by construction; both `relevant` and `always` surface
and fire 4/4 with 0 false fires and full lifecycle closure. The
`relevant`-vs-`always` gap is expected to open on larger stores / LLM arms —
that comparison decides the shipped default (design plan §2.3).

### `shared_memory.py` — owner isolation by default, transfer on request

Two agents, one store. Scout (owner=`scout`) ingests 6 facts; builder
(owner=`builder`) answers questions twice: default own-scope (must find
NOTHING — isolation is a correctness property, leakage exits non-zero) and
`owner="*"` (knowledge transfers).

```bash
uv run python examples/memory_bench/shared_memory.py --solver oracle
```

Oracle result: default scope **0/6 + 0 leaked (ISOLATED)**; `owner="*"`
**6/6 (TRANSFERS)**; the `cross_owner_recalls` stats counter tracks the
explicit widenings.

### `ranger_bench.py` — an EBR-style continual-learning protocol (behavioral)

Epoch AI's EBR-Bench tests whether agents **learn from experience** across
repeated playthroughs where notes are all that persists, scoring only the final
20%. The benchmark itself is closed (no public harness, copyrighted game
content — see the design plan §7.2), so `ranger_bench.py` adopts the *protocol*
with an original deterministic mini campaign game ("Trail Ranger": 5-day
expedition, 4 loadout archetypes, 5 hidden gotchas, 12 objectives). Unlike
LoCoMo/LongMemEval this measures whether memory **changes behavior**, not
whether it recalls facts.

```bash
uv run python examples/memory_bench/ranger_bench.py   # offline, deterministic
```

Result (10 playthroughs, score 0..12): OFF flat at **8** (relearns every run,
never explores loadouts), ON climbs **8 → 11** by playthrough 4 (explores all 4
loadouts, converges on rope_map, **0 repeated mistakes**), GUIDE (pre-seeded
strategy = the ceiling) at **11** from playthrough 1. Memory effect **+3.0** on
the final-2 mean; ON reaches the GUIDE ceiling. The script exits non-zero if
the calibration invariant (GUIDE ≥ ON > OFF) breaks.

### `reflecting.py` — does consolidation (reflection) actually help?

Isolates the **reflection** step. The agent accumulates many scattered single-fact
**episodes**, then answers *synthesis* questions ("tell me everything about X")
under a small retrieval budget (`top_k=3`). Both conditions have memory ON — the
only difference is whether `manager.reflect()` runs, whose generative step uses an
**LLM reasoner** to consolidate the scattered episodes into a few compact
`reflection` memories.

```bash
uv run python examples/memory_bench/reflecting.py --backend chroma_embedded --embedder litellm
```

**Real gpt-5.4 + text-embedding-3-large** (completeness = fraction of a topic's facts present in the answer):

| | reflect OFF (raw episodes) | reflect ON (consolidated) |
|---|---|---|
| synthesis completeness | 50% | **100%** |

**→ +50% from reflection.** With `top_k=3`, plain retrieval over 10 scattered
episodes fetches only a few facts; the reflection's reasoner folded them into **3
`reflection` memories** (and added 20 graph edges), so one consolidated memory now
packs the whole synthesis within the retrieval budget. This is the consolidation
payoff the design's abstraction step is for. (The deterministic reflection ops —
merge / edge-formation / re-score / prune — are unit-tested in
`tests/memory/test_memory_reflection.py`; the LLM reasoner in
`tests/memory/test_memory_reflection_reasoner.py`.)

### `longmemeval.py` — reflection + reconsolidation on a published benchmark

[LongMemEval](https://github.com/xiaowu0162/LongMemEval) (Wu et al., 2025); its
`knowledge-update` and `multi-session` categories reward consolidation. The agent
authors its own memories from each question's sessions; we compare OFF vs ON vs
ON+reflect, where **reflect = an LLM `reasoner`** (consolidate scattered facts) **+ an
LLM `reconciler`** (keep-latest on contradicted/updated facts — the reconsolidation
step added to `ReflectionEngine`). Dataset auto-downloads (HF `longmemeval-cleaned`, oracle split).

```bash
uv run python examples/memory_bench/longmemeval.py --backend chroma_embedded --embedder litellm --per-cat 5
```

**Real gpt-5.4 + text-embedding-3-large (oracle split, 10 Qs):**

| category | OFF | ON | ON+reflect |
|---|---|---|---|
| knowledge-update | 0% | 60% | 60% |
| multi-session | 0% | 80% | 80% |
| **overall** | **0%** | **70%** | **70%** |

**Memory is decisive (+70%); reflection was neutral here (+0%) — and did NOT hurt**
(contrast LoCoMo −20%). Why neutral, not positive? The *oracle* haystacks are small
and `top_k=8` already retrieves every relevant memory, so there's no recall
*bottleneck* for consolidation to relieve; and gpt-5.4 reconciles old-vs-new values
in-context when both are retrieved, making reflect-time reconsolidation redundant. The
reconciler *kept* the current value rather than blurring it — which is why reflection held
its successes here instead of dropping like on LoCoMo. To make reflection *win* on
LongMemEval, run the full (non-oracle) noisy haystack: many distractor sessions
create the retrieval bottleneck consolidation is for.

### When does reflection help? (summary of the three reflection experiments)

| benchmark | the question needs | retrieval bottleneck? | reflect effect |
|---|---|---|---|
| `reflecting.py` | synthesis of many scattered facts | **yes** (`top_k=3` ≪ #facts) | **+50%** |
| `longmemeval.py` (oracle) | aggregation / latest value | no (small store, `top_k=8`) | **+0%** (neutral) |
| `locomo.py` | one pinpoint fact | no | **−20%** (abstraction blurs it) |

**Principle:** consolidation pays off when **retrieval is the bottleneck** (large/noisy
store under a tight budget) *and* the model can't reconcile in-context; it's neutral
when the relevant memories already fit the budget, and it hurts pinpoint lookups by
trading precision for abstraction. Reflection is a tool to apply deliberately, not a
default.

### Write-op ablation — is agent-authored writing better than a deterministic rule?

`--write {agent,raw,window,chunk,llm-summary}` (in all three QA benchmarks) holds
**retrieval + answer fixed** and reflection **off**, varying only *how memories are
written*, to test whether the agent's self-authoring beats a dumb harness rule.

```bash
uv run python examples/memory_bench/recall_qa.py   --solver llm --backend chroma_embedded --embedder litellm --write agent raw window chunk llm-summary
uv run python examples/memory_bench/locomo.py      --backend chroma_embedded --embedder litellm --write agent raw chunk --limit 9
uv run python examples/memory_bench/longmemeval.py --backend chroma_embedded --embedder litellm --write agent raw chunk --per-cat 3
```

**Real gpt-5.4 + text-embedding-3-large:**

| benchmark | agent | raw | chunk | stored (agent vs raw) |
|---|---|---|---|---|
| recall_qa (synthetic, clean facts) | 88% | **100%** | 100% | 8 vs 8 |
| locomo (real, 9 Q) | 56% | 56% | 44% | **158 vs 419** |
| longmemeval (real, 6 Q) | 33% | 33% | 17% | **17 vs 33** |

**Agent-write is *not* an accuracy win over storing everything** — it ties `raw` on
both real benchmarks and *loses* on clean synthetic facts (paraphrase dropped a
token) — but it reaches that accuracy at **~2–3× less storage**, and wins
category-by-category where currency matters (agent > raw on temporal +
knowledge-update; raw > agent on single-hop + multi-session aggregation). So the
honest value of agent-authored writing is **curation/efficiency, not raw accuracy**.
Full tables, configs, and caveats: [`../../docs/design/memory-system/results.md`](../../docs/design/memory-system/results.md).

## Results summary

Offline (`oracle`, all three backends): harness + memory plumbing + monitoring
verified — 4/4 tasks, f2p 100% / regression 100%, and the expected memory-usage
counters (e.g. `writes=5 recalls=4 recalled_items=8 reflections=4 store_size=5`).
Backends are interchangeable (identical retrieval ranking; see
`tests/memory/test_memory_vector_backends.py`).

Real `llm` run — **gpt-5.4 + text-embedding-3-large on the NVIDIA gateway, verified**:

- **chroma_embedded**, full ON-vs-OFF compare (4 tasks each): both conditions
  **4/4 — f2p 100%, regression 100%**. Memory ON drove the real pipeline:
  `writes=8 recalls=4 recalled_items=11 injections=3 injected_chars=1185 reflections=4 edges_added=2 store_size=8`.
- **sqlite_vec**, memory ON (real 1024-d embeddings): f2p 100%,
  `writes=4 recalls=2 recalled_items=2 injections=1 reflections=2 store_size=4`.

Both backends behave identically against the live models. **Caveat (honest):**
gpt-5.4 solves these self-contained tasks with *or* without memory, so this suite
verifies the **system end to end** but does not by itself show a success *lift*
from memory — that needs harder/underspecified tasks whose instructions omit the
conventions an agent must recall (the upstream LongCLI-Bench Docker tasks are a
good target). The live run also caught and fixed a real bug: the litellm embedder
must size vectors from `dimensions` (1024), not the hashing default (256) — see
`tests/memory/test_memory_embeddings.py::test_litellm_embedder_dim_uses_dimensions_not_hashing_dim`.
