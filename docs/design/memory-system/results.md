# Memory System — Consolidated Results

Single home for every benchmark run in this work, so results stop living in
ephemeral `/tmp` logs and commit messages. Reproduce via the scripts in
[`examples/memory_bench/`](../../../examples/memory_bench/).

> **Read this first (caveats).** Real model = **gpt-5.4** (NVIDIA gateway) +
> **text-embedding-3-large**; vector backend **chroma_embedded** unless noted.
> Samples are **small, single-run, stochastic**; locomo/longmemeval are graded by a
> **gpt-5.4 self-judge** (recall_qa/reflecting use deterministic token grading).
> `recall_qa`, `reflecting`, `memory_effect`, `bench` are **synthetic** (hand-authored);
> **`locomo` and `longmemeval` are the real published benchmarks.** Numbers are
> *illustrative of mechanisms*, not leaderboard scores.

## Coverage vs the full benchmarks (we ran small subsets)

These are **pilot subsets**, not the full datasets:

- **LoCoMo** (full: 10 conversations, ~199 QA each ≈ ~1,900 QA). We used **1 of 10
  conversations** (sample 0), **answerable categories only** (single-hop, multi-hop,
  temporal — excluded adversarial + open-domain), and **balanced subsets of 9–24
  questions** per run → **≈1–2% of one conversation, ≪1% of the full benchmark**.
- **LongMemEval** (full: **500** questions, 6 categories; the `_s`/`_m` splits add
  large noisy ~115k-token haystacks). We used the **`oracle` split** (smallest,
  easiest haystacks — evidence sessions only), **2 of 6 categories**
  (knowledge-update, multi-session), and **6–10 questions** → **~1–2% of the
  benchmark, on its easiest setting**.

So the headline numbers are single-run, small-N measurements on easy slices — good
for isolating mechanisms, **not** comparable to published full-benchmark SOTA.

---

## 0. Nemotron-3-Ultra — agentic-read write-op scaling (the rigorous run)

The most careful experiment in this work. Model = **nvidia/nvidia/nemotron-3-ultra**
(+ text-embedding-3-large). Harness: [`locomo_scaling.py`](../../../examples/memory_bench/locomo_scaling.py).
Goal: isolate the agent's **WRITE op** by holding the **READ pipeline identical** across
both arms and varying only what was written to the store:

- **BASE (write)** — the agent authors its own curated memories (`self.remember(...)`).
- **CONTROL (no write)** — the **raw conversation is stored verbatim** (every turn), no curation.
- **READ (both arms, identical)** — **spontaneous recall** auto-injects relevant memories,
  then the agent **agentically** calls `recall`/`search`. The model never sees the full
  conversation directly. Reflection OFF for both.

> An earlier version used an *empty-context* control (no memory at all), which is
> guaranteed 0% and meaningless — it falsely showed "+41% for the agent". The fair
> baseline below (same read op over the raw conversation) is the correct comparison.

Coverage: **all 10 LoCoMo conversations**, answerable categories (single/multi-hop,
temporal). Ramp = nested fraction of the full answerable QA (1,444). Stored once:
**agent = 2,482 memories vs raw = 5,882** (agent curates to **2.4× fewer**).

Full 5/10/20/**100%** ramp completed (two independent runs agree). Headline numbers from
the **completed 100% run** (`results/locomo_scaling/20260623_033428/`):

| subset | N | CONTROL (raw) | BASE (agent) | **write effect** | multi-hop (B−C) | temporal (B−C) | single-hop (B−C) |
|---|---|---|---|---|---|---|---|
| 5% | 78 | 45/78 (58%) | 38/78 (49%) | **−9%** | +3% | −15% | −18% |
| 10% | 150 | 80/150 (53%) | 71/150 (47%) | **−6%** | +6% | −2% | −24% |
| 20% | 294 | 161/294 (55%) | 152/294 (52%) | **−3%** | 0% | +3% | −12% |
| **100%** | **1444** | **809/1444 (56%)** | **735/1444 (51%)** | **−5%** | −1% | **+6%** | **−11%** |

(An earlier partial run, stopped at 20%, agreed: 53/52/54% control vs 49/49/53% base,
write effect −4% → −3% → −1%.)

**Verdict (this answers the experiment's question directly, now at full scale):** with the
read pipeline held equal, **agent-authored writing is NOT an accuracy win over storing the
raw conversation** — at 100% it is **−5%** (raw 56% vs agent 51%). Its real, consistent
advantage is **2.4× less storage** (2,482 vs 5,882 memories). The category split is the
durable, interpretable signal:
- **agent wins temporal (+6% at 100%)** — curated memories record dates/ordering explicitly;
- **multi-hop ≈ tied** (−1%);
- **raw wins single-hop (−11% at 100%)** — verbatim turns beat curated facts at pinpoint recall.

So agent-authored memory trades a little single-hop verbatim accuracy for temporal-reasoning
gains and ~2.4× compression — net slightly behind raw on overall accuracy.

**Cost:** each agentic answer is a multi-round CodeAct loop on a slow reasoning model; the
full run was ~2,888 answers (~11% hit the 8-round cap → scored wrong). Reused the prebuilt
stores (skipping the ~2 h build) at concurrency 32 to make the 100% run tractable (~5 h).

**Caveats:** single run; **Nemotron self-judge**; agentic answers bounded to 8 CodeAct rounds
(a handful hit the cap and were scored wrong); N per subset is modest (78/150/294). **Full
per-question detail** — every memory, reasoning trace, `recall`/`search` call, and answer —
is in the run's (gitignored) `results/locomo_scaling/<timestamp>/` dir (`config.json`,
`memories/`, `stores/`, `trajectories.jsonl`, `reports.txt`, `summary.json`).

---

## 1. Does memory help at all? (memory ON vs OFF)

| benchmark | type | memory ON | OFF | gain |
|---|---|---|---|---|
| recall_qa (8 facts) | synthetic | **6/8 (75%)** | 0/8 (0%) | **+75%** |
| locomo (24 Q, 1 convo) | real | **10/24 (42%)** | 0/24 (0%) | **+42%** |
| longmemeval (10 Q, oracle) | real | **7/10 (70%)** | 0/10 (0%) | **+70%** |

OFF is ~0% **by construction** where the answer is only available from a past
session (clean isolation, not a head-to-head vs other memory systems). **Verdict:
memory is decisive when the information isn't reachable in-context.**

## 2. Does reflection (offline consolidation) help?

| experiment | regime | result |
|---|---|---|
| reflecting.py (synthesis, top_k=3) | retrieval bottleneck | reflect OFF 50% → ON **100%** = **+50%** |
| longmemeval (oracle, top_k=8) | no bottleneck | ON 70% → ON+reflect 70% = **+0%** |
| locomo `--reflect` (pinpoint QA) | precision-critical | ON 60% → ON+reflect 40% = **−20%** |

**Verdict:** consolidation pays off only when **retrieval is the bottleneck** and the
model can't reconcile in-context; it's neutral when memories already fit the budget,
and it **hurts** pinpoint lookup (abstraction blurs the exact fact). Reflection is a
deliberate tool, not a default → off by default for retrieval-QA.

## 3. Agent-authored write vs a deterministic harness rule (the write-op ablation)

Retrieval + answer held **identical**; reflection **off**; only the WRITE op varies.

| benchmark | OFF | **agent** | raw | window | chunk | llm-summary |
|---|---|---|---|---|---|---|
| recall_qa (8) | 0% | 88% · 8 mem | **100%** · 8 | 100% · 8 | 100% · 8 | 100% · 8 |
| locomo (9, real) | 0% | **56%** · 158 mem | 56% · 419 | — | 44% · 130 | — |
| longmemeval (6, real) | 0% | 33% · 17 mem | 33% · 33 | — | 17% · 88 | — |

(`· N mem` = memories stored.) Per-category highlights:
- **longmemeval:** agent wins **knowledge-update (67% vs raw 33%)**; raw wins
  **multi-session (33% vs agent 0%)**.
- **locomo:** agent wins **temporal (33% vs raw 0%)**; raw wins **single-hop
  (100% vs agent 67%)**; multi-hop tied (67%).

**Verdict (answers the experiment's question):**
- **On accuracy, agent-write is NOT better than a dumb `raw` rule.** It *ties* raw
  on both real benchmarks (56%, 33%) and *loses* on clean synthetic facts (−12%,
  paraphrase dropped a token). `chunk` is consistently worst.
- **On storage, agent-write wins consistently** — equal accuracy at **~2–3× fewer
  memories** (158 vs 419; 17 vs 33; vs chunk 130/88).
- **The split is by question type:** curation/reconciliation helps *temporal* and
  *knowledge-update*; verbatim storage helps *single-hop* and *multi-session*
  aggregation (the agent sometimes drops the exact piece).

So the honest value of agent-authored writing here is **compression/curation at
equal accuracy**, plus category-specific gains (currency/temporal) — **not** a
blanket accuracy win over storing everything.

## 4. Memory can be detrimental

`memory_effect.py` (synthetic): when a fact has **changed**, recalled-but-stale
memory misleads the agent. Oracle shows both sides deterministically (recall →
helped, stale → hurt); the real gpt-5.4 run shows the **stale** case where memory
HURTS (agent acts on the outdated value; the no-memory agent inspects current
reality and succeeds). Mirrors the locomo `--reflect` precision finding.

---

## Configurations

| experiment | model | embedder | backend | N | judge | reflection |
|---|---|---|---|---|---|---|
| recall_qa | gpt-5.4 | text-embedding-3-large | chroma | 8 | token substring | off |
| locomo (ON/OFF) | gpt-5.4 | text-embedding-3-large | chroma | 24 | gpt-5.4 | off |
| locomo `--reflect` | gpt-5.4 | text-embedding-3-large | chroma | 15 | gpt-5.4 | on (reasoner+reconciler) |
| longmemeval | gpt-5.4 | text-embedding-3-large | chroma | 10 | gpt-5.4 | on |
| reflecting | gpt-5.4 | text-embedding-3-large | chroma | 2 topics | token substring | on (reasoner) |
| write-op (3 benches) | gpt-5.4 | text-embedding-3-large | chroma | 8/9/6 | per-bench | off |

Verified real: hundreds of `model=openai/openai/gpt-5.4` calls per run (e.g. 852 for
the 10-Q longmemeval run) + real `text-embedding-3-large` (the dim-mismatch bug we
fixed only surfaces from a live embedding call).

## Honest synthesis

1. **Memory ON ≫ OFF** when the answer isn't in-context (the decisive, robust result).
2. **Agent-authored writing ≈ deterministic raw on accuracy** (ties on real data,
   loses on clean facts) but **wins ~2–3× on storage** — its value is curation/
   efficiency, with category-specific accuracy gains (temporal, knowledge-update),
   **not** universal accuracy superiority.
3. **Reflection helps only under a retrieval bottleneck**; neutral or harmful otherwise.
4. **Memory is a double-edged tool** — stale memory and over-abstraction can hurt.

### To make these defensible (not just illustrative)
Larger N + multiple seeds (mean±std); an **independent** judge (not gpt-5.4);
**matched-token-budget** comparisons; and the full (non-oracle) LongMemEval and full
LoCoMo sets. The current numbers are honest single-run, small-N measurements.
