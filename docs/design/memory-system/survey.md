# Long-Term Memory for LLM Agents — Literature Survey

*Prior-art survey for the nooa memory subsystem. Compiled from a deep
multi-source web search (fan-out search → fetch → 3-vote adversarial verification →
cited synthesis) plus the design-phase research in
[`research-notes.md`](./research-notes.md), and cross-checked against our own
implementation findings.*

> **Method & caveats.** Claims tagged *(verified)* survived adversarial
> verification (≥2/3 independent checks) against primary sources; others are from
> the design-phase research or are widely-documented facts. Public sources only.
> Benchmark "SOTA" claims are contested (see §6) — treat rankings as directional.
> Compiled 2026-06.

---

## 1. Why memory, and what this surveys

A bare LLM is stateless across calls; "agent memory" is the machinery that lets an
agent persist and reuse information across turns, sessions, and tasks. This survey
covers (2) the cognitive foundations the field borrows from, (3) the organizing
taxonomy, (4) the concrete systems, (5) the memory *operations* as research
threads, (6) the benchmarks, and (7) where our design sits relative to all of it.

---

## 2. Cognitive foundations (what the field borrows)

Most agent-memory work re-uses a small set of cognitive-science ideas (full
treatment + citations in [`research-notes.md` Part B](./research-notes.md)):

- **Working vs long-term memory** — Baddeley & Hitch (1974); the working/long-term
  split underlies the "context window vs external store" design.
- **Declarative vs non-declarative; episodic vs semantic** — Squire's taxonomy and
  Tulving's episodic/semantic distinction → the `semantic / episodic / procedural`
  memory types nearly every system adopts.
- **ACT-R activation** (Anderson) — base-level activation (recency + frequency) +
  associative spreading; the direct ancestor of recency/importance retrieval scores.
- **Spreading activation** — Collins & Loftus (1975) → graph/multi-hop retrieval.
- **Consolidation & the Ebbinghaus forgetting curve** — systems consolidation and
  exponential retention decay → "reflection/dreaming" and "forgetting" mechanisms.

These are foundations, not contributions: any agent-memory design that uses
episodic/semantic/procedural types + recency/importance scoring + reflection +
decay is standing on this shared base.

---

## 3. Organizing taxonomy

Two framings dominate:

- **A holistic survey taxonomy** *(verified)* — Zhang et al., "A Survey on the
  Memory Mechanism of Large Language Model based Agents" (Renmin Univ. & Huawei
  Noah's Ark; arXiv **2404.13501**, ACM TOIS 2025) organizes the field on three
  axes: **memory sources** (inside-trial / cross-trial / external knowledge),
  **memory forms** (textual vs parametric), and **memory operations** decomposed
  into **writing**, **management** (merging, reflection, forgetting), and
  **reading**. Its Table 3 maps which systems implement which operations
  (MemoryBank, Voyager, MemGPT, Generative Agents, Reflexion, ReAct, …).
- **CoALA** — Sumers, Yao, Narasimhan & Griffiths, "Cognitive Architectures for
  Language Agents" (arXiv **2309.02427**, TMLR 2024) frames an agent as memory
  modules (working + episodic/semantic/procedural long-term) plus an action space
  split into *internal* actions — **reasoning, retrieval, and learning (writing to
  memory)** — and *external* grounding, run by a decision loop. CoALA is the closest
  *framework* to our design (see §7); it puts agent-decided memory writing in the
  action space as a first-class "learning" action.

---

## 4. The systems

Grouped by mechanism. (How memories are **written / retrieved / consolidated /
forgotten** noted per system.)

### 4.1 Memory stream + reflection
- **Generative Agents** *(verified)* — Park et al., UIST 2023 (arXiv **2304.03442**).
  Stores a complete natural-language **memory stream** of all observations; writes
  are automatic-append; **retrieves** by `recency × importance × relevance`;
  **consolidates** by synthesizing higher-level **reflections** (recursive
  reflection trees). Ablation shows observation, planning, and reflection each
  contribute critically to believability. The origin of the recency/importance/
  relevance score and of "reflection."

### 4.2 Self-editing / OS-tiered context
- **MemGPT / Letta** *(verified)* — Packer et al., 2023 (arXiv **2310.08560**).
  "Virtual context management": OS-inspired tiers (in-context "main" + external
  recall + archival storage) with **paging** between them. Memory writing is
  **entirely self-directed** — the agent autonomously edits its own memory via
  function calls (confirmed by the 2404.13501 survey). Letta is the productized
  system; newer work adds "sleep-time compute" (background consolidation).

### 4.3 Production extraction/consolidation pipelines
- **Mem0 / Mem0ᵍ** *(verified)* — Taranjeet et al., 2025 (arXiv **2504.19413**).
  An LLM-driven pipeline that **extracts** salient facts and applies an explicit
  **ADD / UPDATE / DELETE / NOOP** decision engine (update phase checks new facts
  against existing memory for consistency/redundancy). **Mem0ᵍ** adds a graph
  representation (entities = nodes, relation triplets = edges with conflict
  detection). Headline results are *efficiency*-framed: vs full-context, ~91% lower
  p95 latency and >90% token savings; +26% on the LoCoMo LLM-as-judge metric over
  the OpenAI memory baseline (the +26% figure is contested — see §6).
- **A-MEM** — Xu et al., 2025 (arXiv **2502.12110**; `github.com/WujiangXu/A-mem`).
  "Agentic memory": Zettelkasten-style notes the agent **creates and dynamically
  links**, with memory evolution (links/updates) as new notes arrive.
- **MemoryBank** — Zhong et al., 2023 (arXiv **2305.10250**). Adds an **Ebbinghaus
  forgetting curve** so retention decays over time and is reinforced on access.

### 4.4 Graph / temporal memory
- **Zep / Graphiti** — Rasmussen et al., 2025 (arXiv **2501.13956**;
  `neo4j.com/blog/developer/graphiti-knowledge-graph-memory`). A **bi-temporal
  knowledge graph** memory: entities/relations with validity intervals
  (invalidate-don't-delete), strong on temporal reasoning and knowledge updates.
- **HippoRAG** *(verified)* — Gutiérrez et al., 2024 (arXiv **2405.14831**).
  Hippocampal-indexing-inspired: LLM + KG + **Personalized PageRank** retrieval to
  integrate new experience RAG can't. **HippoRAG 2** *(verified)* (arXiv
  **2502.14802**, 2025) deepens passage integration; outperforms standard RAG
  across factual/sense-making/**associative** memory (~7% associative gain over
  NV-Embed-v2).

### 4.5 Parametric / architectural episodic memory
- **Larimar** *(verified)* — Das et al., 2024 (arXiv **2403.11901**). A
  brain-inspired **distributed episodic memory** module enabling **one-shot
  knowledge updates** without retraining, plus selective **forgetting** and leakage
  prevention — directly a forgetting / knowledge-update mechanism.
- **MemoryLLM / M+** *(verified)* — 2024–2025 (arXiv **2502.00592**). Stores memory
  **parametrically** in hidden states (1B-param memory pool; ~20k-token limit); the
  successor **M+** adds a co-trained retriever, extending retention past **160k
  tokens** at comparable GPU overhead.
- **EM-LLM** *(verified)* — Fountas et al., 2024 (arXiv **2407.09450**). Gives a
  *frozen* LLM episodic memory: segments the token stream into events online via
  **Bayesian surprise** (refined with graph modularity/conductance), retrieves via
  combined **similarity + temporal-contiguity** — no fine-tuning.

### 4.6 Verbal self-improvement & procedural memory
- **Reflexion** — Shinn et al., 2023 (arXiv **2303.11366**). The agent writes
  **verbal self-reflections** after failures into an episodic buffer to improve on
  retries (a consolidation/learning mechanism).
- **Voyager** — Wang et al., 2023 (arXiv **2305.16291**). A **skill library**: the
  agent writes verified executable skills (procedural memory) and reuses/composes
  them — the canonical "consolidation → reusable skill" demonstration.

### 4.7 Products & memory layers
- **LangMem** (LangChain), **MemoryOS**, **Cognee**, and **Mem0/Zep as drop-in
  layers** — productized memory SDKs many harnesses integrate.
- **Vendor memory** — OpenAI/ChatGPT memory, Claude memory files/tool, Gemini —
  mostly fact/preference extraction + instruction files (see the harness comparison,
  [`agentic_memory_in_other_harnesses_comparison.md`](./agentic_memory_in_other_harnesses_comparison.md)).

---

## 5. Memory operations (cross-cutting threads)

| operation | representative work |
|---|---|
| **Writing — agent self-authored** | MemGPT/Letta (self-editing), A-MEM, Voyager, Generative Agents (reflection writes) |
| **Writing — harness/pipeline extraction** | Mem0 (ADD/UPDATE/DELETE), Zep (graph build), most vendor memory |
| **Retrieval / scoring** | Generative Agents (recency×importance×relevance), ACT-R activation, HippoRAG (Personalized PageRank), hybrid dense+sparse RAG |
| **Consolidation / reflection / "dreaming"** | Generative Agents (reflection trees), Reflexion, Voyager (skills), Letta sleep-time compute |
| **Forgetting / decay** | MemoryBank (Ebbinghaus), Larimar (selective forgetting) |
| **Knowledge update / reconsolidation / contradiction** | Mem0 (UPDATE/DELETE), Zep (bi-temporal invalidation), Larimar (one-shot edits) |
| **Graph-augmented** | Zep/Graphiti, HippoRAG/2, Mem0ᵍ |

---

## 6. Benchmarks

- **LoCoMo** *(verified)* — Maharana et al., 2024 (arXiv **2402.17753**;
  `snap-research.github.io/locomo`). Very-long-term conversational memory:
  machine-human multi-session dialogues (~300 turns, ~9k tokens, up to 35 sessions).
  Tasks: QA across five reasoning types (single-hop, multi-hop, temporal,
  commonsense, adversarial), event-graph summarization, multimodal generation.
  Long-context LLMs + RAG improve QA **22–66%** but still **lag humans**.
- **LongMemEval** — Wu et al., 2024 (arXiv **2410.10813**). 500 questions over five
  abilities incl. **multi-session reasoning**, **temporal reasoning**, and
  **knowledge updates** — categories that specifically reward consolidation/
  reconsolidation.
- **MSC (Multi-Session Chat)** — Xu et al., 2022 — the earlier multi-session
  persona-consistency benchmark.
- **SOTA is disputed.** Mem0's LoCoMo numbers are contested: Zep's "Is Mem0 Really
  SOTA in Agent Memory?" re-scores competitors (Zep 75.14% vs the 65.99% Mem0
  reported for it), and a Mem0 GitHub issue (#3944) reports a failed LoCoMo
  reproduction. Different judges/subsets/prompts make cross-paper numbers
  **not directly comparable**.

---

## 7. Where our design sits (honest positioning)

Our subsystem (`src/nooa/memory/`, see its
[README](../../../src/nooa/memory/README.md)) is a **faithful synthesis of
the above, not a research novelty**:

| our element | nearest prior art |
|---|---|
| episodic/semantic/procedural/working + intent/reflection types | Squire/Tulving/Baddeley; CoALA; Generative Agents |
| agent-authored memory via native tools, instructed with a schema | MemGPT/Letta (self-editing), A-MEM, Voyager |
| ACT-R scoring (recency+frequency+importance) + multi-hop spread | ACT-R; Generative Agents; HippoRAG |
| dreaming = merge + abstraction (episodes→reflections) | Generative Agents reflection; Letta sleep-time |
| reconsolidation (keep-latest on contradiction) | Mem0 UPDATE/DELETE; Zep bi-temporal |
| Ebbinghaus decay + prune | MemoryBank; Larimar |
| graph + pluggable vector backends, hybrid dense+sparse | Zep/Graphiti, HippoRAG; standard RAG |

**Not novel; not benchmark-SOTA** (we ran only small, self-judged subsets — see the
examples). What is *mildly distinctive* is the **packaging**: all of {conscious
tools + spontaneous injection + dreaming-with-reconsolidation + forgetting + graph
multi-hop + pluggable backends} as one **additive, toggleable, instructed**
subsystem inside nooa's "methods = tools / docstring = prompt" model, with the
agent explicitly told it **owns and curates** its memory (the closest framing is
CoALA's "learning" actions, which we converged on independently). Our one genuinely
useful empirical contribution is the **controlled precision/abstraction finding**:
consolidation helps synthesis under a retrieval bottleneck (+50%), is neutral when
memories fit the budget (+0%), and *hurts* pinpoint lookup (−20%).

---

## 8. Gaps / open problems (where novelty would live)

1. **Adaptive raw-vs-consolidated routing** — decide per query whether to retrieve
   raw memories or consolidated abstractions (auto-resolving the trade-off we
   measured). No system ships this cleanly.
2. **Rigorous, comparable evaluation** — the SOTA disputes (§6) show the field lacks
   a standardized judge/protocol; full-benchmark, independent-judge results would
   turn "plausible" into "SOTA."
3. **Procedural-memory consolidation in coding agents** — Voyager-style skill
   induction inside a coding harness is underexplored.
4. **Reconsolidation correctness** — keep-latest needs reliable contradiction
   detection + temporal grounding; current approaches (incl. ours) are heuristic.

---

## 9. References

Primary (arXiv unless noted):

- 2404.13501 — Memory Mechanism of LLM-based Agents (survey), Renmin/Huawei, TOIS 2025
- 2309.02427 — CoALA: Cognitive Architectures for Language Agents, TMLR 2024
- 2304.03442 — Generative Agents (Park et al., UIST 2023)
- 2310.08560 — MemGPT / Letta (Packer et al., 2023)
- 2504.19413 — Mem0 / Mem0ᵍ (2025)
- 2502.12110 — A-MEM (Agentic Memory, 2025) · `github.com/WujiangXu/A-mem`
- 2305.10250 — MemoryBank (2023) · `github.com/zhongwanjun/MemoryBank-SiliconFriend`
- 2501.13956 — Zep / Graphiti (2025) · `neo4j.com/blog/developer/graphiti-knowledge-graph-memory`
- 2405.14831 — HippoRAG (2024) · 2502.14802 — HippoRAG 2 (2025) · `github.com/OSU-NLP-Group/HippoRAG`
- 2403.11901 — Larimar (episodic memory editing, 2024)
- 2502.00592 — MemoryLLM / M+ (2024–2025)
- 2407.09450 — EM-LLM (episodic, 2024)
- 2303.11366 — Reflexion (2023) · 2305.16291 — Voyager (2023)
- 2402.17753 — LoCoMo (2024) · `snap-research.github.io/locomo`
- 2410.10813 — LongMemEval (2024)
- Zep, "Is Mem0 Really SOTA in Agent Memory?" — `blog.getzep.com` · Mem0 issue #3944 — `github.com/mem0ai/mem0/issues/3944`

Full cognitive-science + engineering citation list: [`research-notes.md`](./research-notes.md).
