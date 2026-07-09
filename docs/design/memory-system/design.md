# Memory System for NeMo OO Agents — Design

> **Status:** v1 IMPLEMENTED. The schema, algorithms, and hook map below are backed
> by a codebase analysis + cognitive-science research pass and are now realised in
> code (`src/nooa/memory/`), tested (`tests/memory/`, 69 tests), and
> demonstrated (`examples/quickstart/11_memory.py`). See **§5.1 Implementation status**.
>
> **Companion file:** [`research-notes.md`](./research-notes.md) (1918 lines) — full
> codebase analysis (`file:line` refs), cognitive-science + prior-art research (with
> citations), and the complete synthesis. This doc is the digestible decision record;
> the appendix is the evidence.

---

## 0. Goal in one paragraph

An **opt-in, additive** long-term memory subsystem for nooa agents — toggled on/off
per agent, tuned by hyperparameters, **zero impact when off**. It gives an agent durable
memory across **long-horizon autonomous tasks** and **tasks accreted over time**, modeled
on how the conscious brain uses memory: **spontaneous association** (similarity retrieval
injected each turn), **deliberate recall** (query/term search tools), **encoding** (writing
memories on important events), **consolidation / reflection** (offline refinement after a
task ends), and **forgetting** (continuous decay + offline pruning). Memories form a
**directed causal graph** with a **loose schema** and live in a **SQLite-centric** store
with vectors embedded via an **NVIDIA-served embedding model**.

---

## 1. Rationale

**Problem.** A nooa agent is effectively amnesic between method calls and sessions.
Long-horizon autonomous runs rediscover the same facts, re-derive the same procedures, and
repeat the same mistakes; recurring tasks given over days/weeks start cold every time. The
framework already invests in *short-term* context management (truncation/eviction), but
there is **no durable store** that survives a run and accumulates skills and facts.

**Why brain-inspired.** The four+one operations map cleanly onto well-studied memory
processes, which gives us a principled (not ad-hoc) design and a vocabulary for the API:

| Operation | Cognitive basis (see `research-notes.md` Part B) |
|-----------|--------------------------------------------------|
| Spontaneous association | Spreading activation (Collins–Loftus), ACT-R activation, priming |
| Deliberate recall | Cue-dependent retrieval, encoding-specificity |
| Encoding (write) | Salience/poignancy tagging at encoding |
| Consolidation (reflection) | Systems consolidation, hippocampal–neocortical replay, NREM→REM |
| Forgetting | Ebbinghaus decay, synaptic homeostasis (SHY), retrieval-induced forgetting |

**Why additive / opt-in.** The framework already has the exact precedent — the
summarization subsystem installs onto an agent via a classmethod and per-instance event
subscriptions, touching no core code (`agents/summarization.py:85`). We mirror that: an
agent that doesn't `install` memory pays nothing.

**Non-goals.**
- Not a replacement for in-context working memory / truncation (that's `scratch` at most).
- Not a general RAG-over-arbitrary-corpus tool; this is the *agent's own* memory.
- Not a distributed/multi-tenant memory service in v1 (single project dir; see §4.5).

## 2. Definitions

- **Memory (record):** atomic stored unit; schema in §4.1.
- **Memory type:** `info`, `skill`, `episode`, `intent`, `reflection`, `scratch` (§4.1b).
- **Descriptor:** subjective tags on a memory — `importance`, `salience`, `confidence`,
  `mood`, `strength` (§4.1).
- **Spontaneous association:** automatic, similarity-driven retrieval injected into context
  each turn via a dynamic context block. No explicit agent action.
- **Deliberate recall:** agent-initiated query/term search (a conscious tool).
- **Encoding (write):** creating a memory — via a conscious tool or an automatic
  event-driven trigger; both pass dedup-on-write.
- **Consolidation / reflection:** offline refinement of the store after a task ends
  (merge, abstract, re-score, form edges, prune).
- **Forgetting:** active degradation/removal — *online* activation decay (time + fetch;
  Ebbinghaus; interference) and *offline* pruning/archival during reflection. First-class,
  distinct from reflection refinement though partly enacted within it.
- **Activation:** per-memory scalar (recency + access frequency, ACT-R base-level) that
  drives both retrieval ranking and forgetting; raised by retrieval, decays over time.
- **Causal/memory graph:** directed, typed graph; edges record which memories gave rise to
  others (`derived_from`, `created_by`, `causes`) plus associative relations.
- **Hop:** one edge traversal in graph-augmented retrieval (0/1/2+ configurable).
- **Memory store:** SQLite-centric — metadata + graph in SQLite; vectors via SQLite (`sqlite-vec`)
  or an embedded Chroma over SQLite (§4.5, decision pending).

## 3. Analysis of the current codebase

The add-on needs five integration points; **all exist today** (no core edits required).
Full detail with every `file:line` in [`research-notes.md` Part A & D](./research-notes.md).
The hooks below were **spot-verified against the source** this pass.

### 3.1 Install / lifecycle precedent — *the template to copy*
`agents/summarization.py` is a working, shipped example of an opt-in subsystem:
- `install(cls, agent, **kwargs)` classmethod (`summarization.py:85`) — constructs the
  manager, stores it on the agent to tie lifetime, registers subscriptions.
- per-instance event subs `event_manager.on("AfterTurn", handler)` (`:160-161`), returned
  as unsubscribe closures; `_uninstall()` (`:165`) drops them all.
- background work via `asyncio.create_task` with pending-task tracking (`:313`).
- root-call detection via `turn_number == 1 and is_final` (`:667-671`).

→ **Decision:** our `MemoryManager.install(agent, config=...)` mirrors this exactly.

### 3.2 Spontaneous-injection channel — dynamic context block
- `self.context[k] = v` (`ContextApi.__setitem__`, `context.py:63`) and
  `self.context.set_dynamic(k, "expr")` (`context.py:70`) both route to the **dynamic**
  partition; `ContextManager.set_dynamic` (`context_manager.py:102`) re-evaluates the
  expression **every LLM turn**. This is the channel for per-turn memory injection.
- The expression can call an async agent method (auto-awaited), e.g.
  `set_dynamic("recalled_memories", "agent._memory.recall_for_context()")`.
- **Eviction nuance:** plain dynamic blocks are evicted *first* under context pressure —
  which silently drops memory in long tasks. But `set_dynamic_protected`
  (`context_manager.py:308`) exists and is **not** first-evicted → use it so injected
  memory survives. *(This refines open-question P2-#12.)*

### 3.3 Lifecycle hooks — events + middleware
`runtime/event_manager.py` exposes a multi-subscriber bus:
- `on(event_type, handler)` (`:184`) — handlers are sync + exception-swallowing (never
  block the agent); enqueue real work as a task.
- `intercept("agent_call", mw)` (`:262`, run by `run_middleware` `:309`) — wraps a whole
  method call; `AgentCallContext.result` holds the return value → the **post-task reflection**
  hook.
- `register_event_type(cls)` (`:165`) + `add(ev, record=...)` (`:120`) — persist custom
  `MemoryWritten` / `ReflectionCompleted` events without injecting them into LLM context.
- Useful events (`events.py`): `Task` (`:59`, fires once at task start → "prime memory"),
  `Notification` (`:301`, purpose-built external signal → write trigger), `AfterTurn`
  (`:218`) with `is_final` (`:243`) + `parent_generation_id` (`:210/237`) → strategy-agnostic
  run-completion signal (gate on `is_final and parent_generation_id is None`).

### 3.4 Conscious-tool exposure — methods, not Tool objects
CodeAct keeps a fixed two-tool envelope (`execute_python` / `return_result`); agent
**methods** become callable inside `execute_python` and are documented to the LLM via
`doc(self)`. So memory tools are just public methods on a **mixin** (`recall/search/
remember/associate`) that delegate to the manager. Their return type (`Memory`) must be a
**module-level** type to land in `exec_globals`.

### 3.5 Storage subsystem — reuse SQLite, add a memory store
The repo already has a robust SQLite layer (`storage/sqlite.py`) with serialization
helpers and an allowlist-secured payload codec (`storage/serialization.py`). Per the
analysis we should **not** overload the agent's `EventBackend`/`StorageManager` (it takes
an exclusive per-session flock — `sqlite.py:600-637`) for cross-session memory. Instead add
a dedicated **`MemoryStore`** (own SQLite file, tables `memories` + `memory_edges`, own
`schema_version`) reusing those helpers. This is the metadata + graph layer; vectors are
handled per §4.5.

### 3.6 Config + embeddings integration
- Config convention: frozen Pydantic config objects with `merge_with()`
  (`config/summarizer_config.py`), re-exported from `config/__init__.py`. We mirror this
  with `MemoryConfig` (§4.6).
- Embeddings: the unified LLM registry resolves api_base/api_key from a model alias; an
  embeddings call can reuse `litellm.aembedding` over the **existing NVIDIA gateway** — no
  new key/env var, just a model alias (e.g. an `nv-embed*` / `text-embedding-3-large`-class
  model).
- Project dir: `get_project_dir("memory")` honors `NEMO_OO_PROJECT_DIR` → natural home for
  the SQLite file(s).

### 3.7 Prior art + dependencies
- No existing memory/RAG/vector subsystem in the repo to reuse or collide with.
- `chromadb` / `sqlite-vec` / `sentence-transformers` are **not** in `pyproject.toml` — the
  vector dependency is a net-new choice (informs the §4.5 decision toward SQLite-first).

## 4. Suggested solution

### 4.1 Memory record schema (loose / non-strict)

All fields except `id`, `type`, `content`, `created_at` are optional with sane defaults —
a memory can be a one-line fact or a fully-annotated, graph-linked episode. Full field
table + citations in [`research-notes.md` Part C(a)](./research-notes.md).

**(a) Field groups**
- **Identity:** `id` (UUID), `type`, `title?`, `content`.
- **Structural:** `size_chars`, `token_len`, `sentence_count` (atomicity / chunk signals).
- **Descriptors:** `importance` (1–10, LLM poignancy at write), `salience` (0–1,
  outcome/surprise/novelty tag — over-replay failures), `confidence` (0–1, gates
  reconsolidation overwrite), `mood?`, `strength` (spaced-repetition counter, +1 on recall),
  `reinforcement_count`.
- **Metadata:** `created_at`, `last_accessed_at`, `access_log[]` (ACT-R base-level input),
  `access_count`, `source_task_ref?`, `related_files[]`, `chat_turn_ref?`,
  `valid_from?/valid_to?` (bi-temporal: invalidate-don't-delete), `trigger?` (intents).
- **Context cue:** `context = {entities[], tags[], place_or_task, mood}` for encoding-
  specificity cue overlap.
- **Embedding:** `embedding_ref`, `embedding_dims` (guards dim drift). Embedding text =
  `concat(title, content, tags, entities)`; embed `passage` on write, `query` on read.
- **Graph:** `edges = [{target_id, type, weight, created_at}]`.

**(b) Memory type taxonomy** (extends your `skill` + `info`; basis in R1)

| Type | What it stores | Human basis | Persistence |
|------|----------------|-------------|-------------|
| `info` | facts, prefs, domain rules, conventions | semantic | long-term |
| `skill` | reusable **verified** procedures + applicability conditions | procedural | long-term |
| `episode` | a specific task run: goal/actions/observations/outcome | episodic | long-term |
| `intent` | future intention/reminder/TODO with a `trigger` | prospective | until fired |
| `reflection` | insight distilled from episodes, cites evidence | schema/gist abstraction | long-term |
| `scratch` | transient per-task workspace | working memory | transient (never durably written) |

`episode` is the highest-value addition — the raw material reflection consolidates into
`info`/`skill`. `intent` is the only prospective category. `scratch` makes the
working-vs-long-term axis explicit to prevent store bloat.

**(c) Edge types:** `derived_from`, `created_by` (causal provenance — always attached on
write); `causes`, `precedes`, `part_of`, `refines`, `supports`, `contradicts`, `related`,
`triggers`. Causal/`refines` edges get higher base weight than `related`.

### 4.2 The core operations

- **4.2.1 Spontaneous association** — derive a query from current state → hybrid retrieve
  (dense ∪ BM25 → RRF) → score (§4.3) → inject top-k as a *protected* dynamic context block.
- **4.2.2 Deliberate recall** — conscious `recall(query, k)` / `search(query, k)` tools the
  LLM calls inside `execute_python`.
- **4.2.3 Encoding (write)** — conscious `remember(...)` + automatic event-driven writes;
  both pass the **dedup-on-write decision engine** (retrieve top-k similar → one LLM call →
  `ADD | UPDATE | DELETE | NOOP`). Every write attaches a causal `created_by`/`derived_from`
  edge.
- **4.2.4 Consolidation (reflection)** — pure-Python orchestrator after a task, ordered
  ops mirroring biology: **gate write → replay-select → NREM (dedup/merge, reconsolidate,
  schema-route, transfer) → REM (abstract episodes→skills, mine causal edges,
  counterfactual failure rollouts) → renormalize (re-score, global down-scale) → form edges
  → prune**. Ordering is load-bearing: clean before abstract before forget. Each LLM step is
  one generation method; deterministic steps are plain helpers.
- **4.2.5 Forgetting & decay** — first-class, two timescales:
  - *Online (no LLM):* per-memory **activation** decays with time (ACT-R base-level / Ebbinghaus
    `R=e^(−t/S)`), boosted on each fetch (`strength += 1`); retrieval-induced
    strengthening of cued memories + suppression of competitors.
  - *Offline (in reflection):* memories below activation/importance threshold are **pruned or
    archived** (soft-delete tombstone), redundant ones merged; SHY-style global down-scaling
    preserves relative ordering.
  - Tunable: decay half-life, fetch-boost, prune threshold, hard-delete vs archive,
    **protected types** (e.g. high-importance `skill` never auto-forgotten).

### 4.3 Retrieval model

**Per-node score** = relevance + recency + importance + associative spread (each min-max
normalized across candidates). Defaults from R3/R4:
```
rel(m,q)   = λ·cos(e_m,e_q) + (1−λ)·ctxOverlap(m,q)        λ=0.7
rec(m)     = σ( ln Σ_k Δ_k^−d )                            d=0.5   (ACT-R base-level)
imp(m)     = importance/10
S_base(m)  = α_rel·rel̂ + α_rec·reĉ + α_imp·imp̂            1.0 / 0.5 / 0.5
Act(n)     = S_base(n) + γ·Σ_{m∈cues} A(m)·w_mn·(S_max − ln fan_m)_+   γ=0.5
```
First-stage candidate recall is hybrid (dense top-50 ∪ BM25 top-50 → RRF → top-20) before
scoring. On retrieval, append `t_now` to `access_log`, `strength += 1` (hot paths
self-strengthen). The associative-spread term is what surfaces a memory not directly similar
to the query because it's linked to one that is — *spontaneous association*.

**Multi-hop traversal** (for `hops ≥ 1`): bounded BFS / personalized-PageRank-style spread
from seed cues, activation decayed per hop:
```
A^(h)(n) = A^(h−1)(n) + δ^h · Σ_{m∈pred(n)} A^(h−1)(m)·w_mn·(S_max − ln fan_m)_+
defaults: δ=0.6 per-hop decay, K=3 max hops, b=5 beam/node, θ=0.05 floor, M=12 returned
```
Route simple lookups to plain hybrid retrieval; use traversal only for genuinely
multi-hop/causal queries to avoid graph latency.

#### 4.3.1 Query strategies (pluggable)

The per-turn "spontaneous association" query is **not** a fixed choice — it's a
`QueryStrategy` protocol with config-selected built-ins, because the right cue source
differs by agent kind (chat vs. coding vs. autonomous). Several can be enabled at once
(multi-query → union of candidates before scoring).

```python
class QueryStrategy(Protocol):
    def derive(self, agent, events) -> list[str]: ...   # 0+ query strings for this turn
```

| Strategy | Cue source | Cost | Best for |
|----------|-----------|------|----------|
| `last_message` (default) | last user `Message`/`Task` text | 1 embed | chat / clear-intent tasks |
| `recent_events(n)` | last-N events concatenated | 1 embed (bigger) | multi-step tasks; noisier |
| `distilled` | tiny LLM call → "what am I doing now" | +1 LLM call | long-horizon, ambiguous intent |
| `working_state` | recent REPL vars / tool outputs | 1 embed | coding agents |

Config: `retrieval.query_strategies: tuple[str, ...]` (one or more) + per-strategy params.
Default `("last_message",)`. Multi-query results are merged (dedup by id, keep max score).

### 4.4 Integration architecture (additive, opt-in)

Two surfaces, both wired in `install()`; **every hook maps to a verified extension point.**

**Surface A — conscious tools** (mixin → visible via `doc(self)`):
```python
class MemoryToolsMixin:
    def remember(self, text, *, type="info", **descriptors) -> str: ...   # → self._memory.remember
    def recall(self, query, k=5) -> list[Memory]: ...
    def search(self, query, k=5) -> list[Memory]: ...
    def associate(self, a_id, b_id, relation="related") -> None: ...

class MyAgent(MemoryToolsMixin, Agent, llm=llm): ...
```
Each tool is individually enable/disable-able (`config.tools`); disabled → `@hidden` so it
never appears in `doc(self)`. If memory isn't installed, tools raise a clear error (harmless
when off).

**Surface B — wrapper/hook** via `MemoryManager.install(agent, config)`:
1. registers the **protected dynamic context block** for pre-turn spontaneous injection
   (`set_dynamic_protected` → `_memory.recall_for_context()`). The query comes from the
   configured `QueryStrategy`(s) (§4.3.1); the re-query **cadence is configurable**
   (`inject_cadence`): `self_gated` (re-query only when a state-hash changes — default),
   `per_task` (query once at `on("Task")`), or `every_turn` (uncached).
2. registers **write-on-event** subscriptions (`on("Notification"|"Error"|"Message"|"Task")`).
3. registers **post-task reflection** — **[decided]** `intercept("agent_call")` gated to the
   top-level entrypoint (`only_top_level=True`): nested subagent calls do **not** each reflect,
   and `AgentCallContext.result` gives the reflection the full episode + return value.
4. `register_event_type(MemoryWritten/ReflectionCompleted)`; returns `uninstall()`.

**Why `install()` + mixin** (not a wrapper class or class decorator): a wrapper breaks the
`class MyAgent(Agent, llm=llm)` metaclass path; a class decorator can't register
*per-instance* event subs (those need the live `event_manager`). `install()` matches the two
existing precedents and the per-instance reality. Manager fields are `Annotated[T, hidden]`
so nothing leaks into `doc(self)`.

### 4.5 Storage & embeddings — pluggable `VectorBackend`, decision deferred

**Invariant (all options):** metadata + the association graph always live in **SQLite**
(`MemoryStore`, reusing `storage/sqlite.py` + `serialization.py`). Only the **vector index**
backend varies, abstracted behind a small protocol so the choice is **reversible** and never
blocks the rest of the design:

```python
class VectorBackend(Protocol):
    def upsert(self, ids, embeddings, metadatas) -> None: ...
    def query(self, embedding, k, where=None) -> list[tuple[str, float]]: ...   # (id, score)
    def delete(self, ids) -> None: ...
```

Default impl = `sqlite-vec` (keeps everything in one file); alternates swap in by config
(`chroma.backend`). Embeddings always via `litellm.aembedding` over the existing NVIDIA
gateway (normalized, batched). Memory DB is a **separate file/connection** from the agent's
session snapshot DB (avoids the exclusive flock); WAL mode for read-during-reflect.

#### Trade-off matrix (informs the *default*, not a hard commitment)

| Dimension | **SQLite + `sqlite-vec`** | **Chroma embedded** (PersistentClient) | **Chroma server** (HTTP) |
|-----------|---------------------------|----------------------------------------|--------------------------|
| New deps | `sqlite-vec` (tiny extension) | `chromadb` (+ transitive) | `chromadb` + a running server |
| Process model | in-process, 1 file | in-process, own file(s) | separate server to run/manage |
| ANN index | brute-force KNN (early versions) → O(n) scan | HNSW → sub-linear | HNSW → sub-linear |
| Practical scale | ✅ ~10²–10⁵ vectors | ✅ 10⁵–10⁶+ | ✅ 10⁵–10⁶+ |
| Consistency | ✅ **one DB, one transaction** (vectors+meta+graph atomic) | ⚠️ two stores to sync (Chroma + our SQLite graph) → possible dangling refs | ⚠️ two stores + network |
| Hybrid sparse+dense | ✅ FTS5 (BM25) **in the same DB** | ⚠️ build sparse separately | ⚠️ build sparse separately |
| Metadata filtering | SQL `WHERE` alongside KNN | rich `where`/`where_document` | rich `where`/`where_document` |
| Ops / CI / headless | ✅ trivial (copy a file) | ✅ file-based, no server | ❌ must run+health-check a server |
| Backup / snapshot / versioning | ✅ single file | ⚠️ Chroma's own dirs + schema versioning | ⚠️ server-managed |
| Concurrency | ⚠️ SQLite single-writer (WAL needed for reflect-vs-task) | handles its own | ✅ server-side |
| Maturity / docs | ⚠️ younger | ✅ popular, well-documented | ✅ popular |
| Cross-process / shared memory | ❌ single process | ❌ single process | ✅ shared service |
| Graph traversal | our SQLite graph (native) | our SQLite graph (Chroma has none) | our SQLite graph |

**Reading of the matrix.** For v1 — single project dir, one agent, expected 10²–10⁵
memories — `sqlite-vec` wins on simplicity, consistency (one transactional DB), hybrid
search (FTS5 in-DB), and ops. Its only real risk is brute-force KNN at scale; if memory
counts grow past ~10⁵ or cross-process sharing is needed, swap the `VectorBackend` to
Chroma — cheap because of the protocol. **Recommendation: default `sqlite-vec`, ship the
Chroma-embedded impl as the escape hatch.** Final call to be made when we have a target
memory-count estimate (open Q1).

### 4.6 Configuration / hyperparameters

Frozen `MemoryConfig` (nested sub-configs) — full surface in
[`research-notes.md` Part D §3](./research-notes.md). Headline knobs:

| Group | Knobs |
|-------|-------|
| Master | `enabled`, `tools`, `inject_context` |
| Chunking | `chunk_size`, `chunk_overlap` |
| Spontaneous | `query_strategies` (tuple, e.g. `("last_message",)`), `inject_cadence` (`self_gated`/`per_task`/`every_turn`), `context_char_budget` |
| Retrieval | `top_k`, `hops` (0/1/2+), `per_hop_decay`, `per_hop_fanout`, `min_similarity`, `weights` |
| Embedding | `model`, `endpoint`, `batch_size`, `dim` |
| Store | `backend` (sqlite-vec / chroma-embedded / chroma-http), `path`, `collection` |
| Write | `on_events`, `salience_min`, `dedup_window`, `write_episodic` |
| Reflection | `enabled`, `trigger`, `only_top_level`, `entrypoint_methods`, budgets (`max_*`, `token_budget`), `background` |
| Forget | `decay_half_life`, `fetch_boost`, `prune_threshold`, `archive_vs_delete`, `protected_types` |

## 5. Implementation plan

### 5.1 Implementation status (v1 — implemented)

Shipped as a fully **additive** module — no existing files changed. Verified with
`uv run pytest tests/memory/` (**69 tests, all passing**) and a runnable demo
(`examples/quickstart/11_memory.py`). Full regression: 6816 passing (the only
failures are pre-existing, unrelated to memory: a missing optional `mcp` dep + an
uninstalled nvidia entry-point package).

| File | What it implements |
|------|--------------------|
| `memory/schema.py` | `Memory`, `MemoryType` (info/skill/episode/intent/reflection/scratch), `Edge`/`EdgeType`, structural derivation, `touch()` (recency+strength), causal edges |
| `memory/config.py` | `MemoryConfig` + sub-configs (frozen pydantic) — the full hyperparameter surface |
| `memory/embeddings.py` | `Embedder` protocol; `HashingEmbedder` (default, offline, deterministic) + `LiteLLMEmbedder` (NVIDIA gateway) |
| `memory/store.py` | SQLite-centric `MemoryStore` (one file: records + graph + vector blobs); index built from `config.vector.backend` |
| `memory/vector_backends.py` | `VectorIndex` protocol + 3 impls: `NumpyVectorIndex` (default), `SqliteVecVectorIndex` (sqlite-vec, same file), `ChromaVectorIndex` (embedded/HTTP) + `make_vector_index` factory |
| `memory/retrieval.py` | hybrid candidate recall, ACT-R base-level + relevance + importance scoring, k-hop associative spread, pluggable `QueryStrategy`s |
| `memory/forgetting.py` | Ebbinghaus online retention (slowed by `strength`) + offline prune (protected types, age/importance guards) |
| `memory/reflection.py` | `ReflectionEngine`: dedup/merge → edge formation → re-score → prune; optional LLM `reasoner` hook for episode→skill abstraction |
| `memory/monitoring.py` | `MemoryStats` counters + `MemoryWritten`/`MemoryRecalled`/`MemoryInjected`/`ReflectionCompleted` RUNTIME_EVENT events (existing bus, out of LLM context) |
| `memory/manager.py` | `MemoryManager.install/uninstall` (BeforeTurn injection · write-on-event · top-level `intercept` reflection) + `MemoryToolsMixin` + `memory_stats()`/`log_summary()` |
| `examples/memory_bench/` | long-horizon benchmark (LongCLI-Bench-style fail→pass + regression) over both backends + real gpt-5.4 / text-embedding-3-large, with memory-usage monitoring |

**v1 tradeoffs chosen** (favouring minimal deps + offline testability; all reversible
behind the protocols):
- **Vectors:** `numpy` brute-force cosine is the zero-dependency **default**;
  `sqlite_vec` (ANN in the same SQLite file) and `chroma_embedded`/`chroma_http` are
  selectable via `config.vector.backend` (optional deps, lazy-imported). All three give
  identical ranking (verified). Final default tier still open (Q1).
- **Embeddings:** default `HashingEmbedder` (zero-config, deterministic) so the system
  and tests run with no network; `LiteLLMEmbedder` for the real NVIDIA endpoint
  (`text-embedding-3-large`, with the OpenAI `dimensions` param).
- **Recall path is synchronous** → safe to call from sync event handlers (no event-loop
  juggling); the real embedder uses `litellm.embedding` (sync).
- **Reflection default is deterministic** (no LLM); the generative episode→skill step
  activates only when a `reasoner` is supplied.
- **Injection** uses a refreshed dynamic context block on `BeforeTurn` (cadence configurable).

### 5.2 Milestone plan (as built)

Module: `src/nooa/memory/` (`store.py`, `embeddings.py`, `schema.py`,
`retrieval.py`, `reflection.py`, `forgetting.py`, `manager.py`, `config.py`).

| Milestone | Deliverable | Verifiable by |
|-----------|-------------|---------------|
| M0 | `schema.py` (Memory/Edge/types) + `MemoryStore` protocol + `MemoryConfig` | unit: schema round-trip, config merge |
| M1 | `embeddings.py` (NVIDIA gateway) + SQLite-centric store + write/read/hybrid-search | unit + integration: write→search returns it |
| M2 | `MemoryToolsMixin` + `MemoryManager.install/uninstall` (no hooks yet) | tool visible in `doc(self)`; uninstall clean |
| M3 | Pre-turn spontaneous injection (protected dynamic block, self-gated) | injected block appears; off ⇒ no block |
| M4 | Event-driven writes + dedup-on-write decision engine | salient event ⇒ one (deduped) memory |
| M5 | Forgetting & decay (online activation bookkeeping, no LLM) | activation math; protected-type exemption |
| M6 | Reflection orchestrator (NREM/REM/renormalize) incl. offline prune/merge driven by M5 | before/after store snapshot diff |
| M7 | Graph edges + multi-hop retrieval (`hops≥1`) | k-hop returns linked-but-dissimilar memory |
| M8 | Tracing/telemetry decisions, docs, examples (§7) | traces clean; examples run |

Ordering rationale: prove the store + retrieval (M1) before wiring hooks (M3+); forgetting
(M5) before reflection (M6) because reflection *executes* forgetting's prune decisions.

## 6. Testing plan + regressions

**Unit** — schema serialization; retrieval scoring (ACT-R base-level, spread); k-hop
traversal (decay, cycle handling, fanout caps); dedup decision engine; each reflection op
(merge/abstract/re-score/prune); forgetting (decay math, fetch-boost, prune threshold,
protected-type exemption, archive vs hard-delete).

**Integration** — end-to-end: `remember → recall` returns it; spontaneous block injects on
relevant turn; event → write → reflect → retrieve produces a consolidated `skill`; multi-hop
returns a linked-but-dissimilar memory; embedding `passage`/`query` asymmetry honored.

**Regressions (the additive guarantee — highest priority):**
- An agent **without** `install()` is byte-for-byte unchanged (prompt, traces, behavior).
- `enabled=False` ⇒ no dynamic block, no event subs firing real work, tools `@hidden`/raise.
- Per-turn injection adds **bounded** latency (self-gating: no re-embed when state unchanged);
  assert an SLA ceiling on added per-turn cost.
- Memory DB uses a separate file/connection — no deadlock vs the session snapshot flock.
- Background reflections are cancelled on `uninstall`/shutdown; half-written reflections are
  transactional/idempotent (no corruption on crash).
- Tracing noise controlled (per-turn recall `@no_trace`; reflections traced).

**Eval** — task-level lift on (a) a long-horizon autonomous benchmark (skill reuse within a
run) and (b) a recurring-tasks-over-sessions scenario (cross-session recall). Metric:
success rate / steps-to-solve with memory on vs off, plus store growth + retrieval latency.

## 7. Examples plan

Under `examples/` (each with the `README.md` the repo requires for experiments):
- **Quickstart** — opt an existing agent into memory in ~3 lines (`install` + mixin); show a
  fact remembered in one call and recalled in the next.
- **Long-horizon autonomous task** — agent accumulates and *reuses* a verified `skill`
  within a single long run; show the spontaneous block surfacing it.
- **Recurring-tasks-over-time** — same agent across N sessions; later sessions recall earlier
  decisions/preferences (cross-session `info`).
- **Reflection demo** — before/after store snapshot showing episodes consolidated into a
  `skill`, duplicates merged, low-value memories pruned (forgetting).
- **Graph/multi-hop demo** — a query that only succeeds via a causal `derived_from` hop.
- **Ablation harness** — toggle `enabled`, vary `hops`/`top_k`/weights to show the
  hyperparameter surface (ties into the eval).

---

## Appendix — Open design questions (Phase 2)

Prioritized; full rationale in [`research-notes.md` Part D §4](./research-notes.md).

**Resolved this pass (author decisions):**
- *Forgetting* is first-class — online decay + offline pruning (§4.2.5).
- *Storage* metadata+graph in SQLite always; vector index behind a pluggable `VectorBackend`,
  default `sqlite-vec`, Chroma as escape hatch — **final tier deferred** pending a memory-count
  estimate (§4.5, ↓ Q1).
- *Spontaneous query* — pluggable, config-selectable/composable `QueryStrategy`s (§4.3.1).
- *Inject cadence* — configurable (`inject_cadence`, §4.4).
- *Reflection trigger* — post-task `intercept("agent_call")`, top-level only (§4.4).

**P0 — still open, block in-depth design:**
1. **Storage default tier confirmation** — confirm `sqlite-vec` default once we estimate
   expected memory counts per agent/project (drives the brute-force-KNN-vs-HNSW call). The
   `VectorBackend` protocol keeps this reversible, but the default + benchmark target matter.
2. **Write amplification & salience** — which events truly warrant a write; the concrete
   salience function + dedup window so the store doesn't grow unbounded. *(Now the top
   unresolved correctness risk.)*

**P1 — correctness/robustness:**
3. Concurrency & SQLite locking (separate file/conn; WAL; reflect-writes-vs-task-reads).
4. Background-reflection lifetime/failure (pending-task tracking, cancel-on-uninstall,
   transaction boundary/idempotency).
5. Snapshot interaction (memory is external/cross-session; `nosnapshot` markers; restore
   semantics).
6. Graph-hop scoring composition & cycle/fanout caps for `hops≥2`.
7. `QueryStrategy` multi-query merge semantics (union dedup vs. per-strategy quotas) and the
   `self_gated` state-hash definition (what counts as "state changed").

**P2 — quality/observability:**
8. Conscious-vs-spontaneous overlap (shared dedup cache so the LLM isn't shown a memory
   twice).
9. Embedding model availability + dim/version stamp on the collection (migration on change).
10. Tracing per-op (`@no_trace` for per-turn recall; trace reflections) — partly resolved:
    `set_dynamic_protected` keeps the injected block from being first-evicted.

**P1 — correctness/robustness:**
6. Concurrency & SQLite locking (separate file/conn; WAL; reflect-writes-vs-task-reads).
7. Background-reflection lifetime/failure (pending-task tracking, cancel-on-uninstall,
   transaction boundary/idempotency).
8. Snapshot interaction (memory is external/cross-session; `nosnapshot` markers; restore
   semantics).
9. Graph-hop scoring composition & cycle/fanout caps for `hops≥2`.

**P2 — quality/observability:**
10. Conscious-vs-spontaneous overlap (shared dedup cache so the LLM isn't shown a memory
    twice).
11. Embedding model availability + dim/version stamp on the collection (migration on change).
12. Tracing per-op (`@no_trace` for per-turn recall; trace reflections) — partly resolved:
    `set_dynamic_protected` keeps the injected block from being first-evicted.
