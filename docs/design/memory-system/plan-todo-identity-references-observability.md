# Memory System — MR Plan: TODOs · Identity · References · Observability

> **Status:** PLANNED (design for the next memory-system MR, branch `feat/memory-followups`).
> **Baseline:** the merged v1 subsystem (`src/nooa/memory/`, MR !483 + !487).
> **Companions:** [`design.md`](./design.md) (v1 decision record) ·
> [`addendum-skill-reflection-verbal-descriptors.md`](./addendum-skill-reflection-verbal-descriptors.md)
> (skill interface, verbal ladders) · [`results.md`](./results.md) (benchmark evidence).
> All `file:line` anchors below were verified against the current `main` tree.

---

## 0. Goal in one paragraph

Four additive extensions to the memory subsystem: (1) a **`todo` memory type** — durable
prospective memory with an open/done lifecycle, surfaced automatically until resolved;
(2) an **`owner` identity** on every memory so multiple agents/sessions can share one
store, each filtering to its own memories by default and fetching others' explicitly;
(3) **references** — memories carry typed pointers to live agent state (vars, context
blocks, files, todos, other memories) instead of only frozen text, following the
framework's pass-by-reference convention and directly attacking the documented
stale-memory failure mode (`results.md` §4); and (4) **observability** — memory events
bridged into tracing, a memory browser tab in the TUI, and a Memory tab in the web trace
viewer. Verified by unit/integration tests, three cheap targeted benchmarks, one
EBR-style behavioral benchmark (original content — Epoch's EBR-Bench itself is not
public), and structured team dogfooding.

## 1. Where each feature anchors in the current code

A condensed map (full details in the v1 docs); this is the ground truth the design
builds on.

| Concern | Anchor |
|---|---|
| Type taxonomy | `MemoryType` StrEnum — `memory/schema.py:28-36` (`info/skill/episode/intent/reflection/scratch`); LLM-facing doc in `MEMORY_SCHEMA_GUIDE` — `memory/manager.py:58-82` |
| Record model | `Memory(BaseModel)` — `schema.py:82-197`; opaque refs today: `source_task_ref`, `chat_turn_ref`, `related_files` (plain strings) |
| SQL DDL | `_SCHEMA` — `store.py:48-76`: 8 promoted columns (`type, importance, salience, strength, created_at, last_accessed, access_count, archived`) + `data` JSON blob + `memory_edges`; **no schema-version pragma yet** |
| Identity today | **None.** One SQLite file = one flat namespace; path from `MemoryConfig.path` else `.nooa/memory/memory.sqlite` (`manager.py:139-143`). TUI derives per-session files `{session_id}-memory.db` (`nooa_cli/tui/bootstrap.py:80-138`) |
| Retrieval | `RetrievalEngine.recall` — `retrieval.py:71-142` (dense ∪ sparse → ACT-R scoring → spread); the **only** filter anywhere is `archived` (+ `mem_type` on `keyword_search`, `store.py:268`) |
| Tool surface | `MemoryToolsMixin` — `manager.py:511-607` (`remember/update_memory/forget/recall/search/associate`); shipped to agents as `self.memory` via `MemorySkill` (`memory/memory_skill/__init__.py:23`) |
| Agent live state | `agent.vars` (`SnapshotVars`, snapshot-backed, `nooa_cli/tui/agent.py:338`, proxied as `self.v`); context blocks (`runtime/context_manager.py:75,117`); CodeAct REPL `session_locals` (`strategies/codeact.py:586-599`); per-todo vars `self.todo.<id>.v` (`tools/todo.py:94`) |
| Session todo list | `TodoManager` skill (`nemo.todo` → `self.todo`, `tools/todo.py:117`) — session-scoped, snapshot-backed, **not** long-term memory |
| Monitoring | `MemoryWritten/MemoryRecalled/MemoryInjected/ReflectionCompleted` (`monitoring.py:30-65`) — `RUNTIME_EVENT` role, `record=False` → **never persisted, never traced** (`manager.py:311-316`, `runtime/event_manager.py:144-146`) |
| TUI browser framework | `ExplorerView`/`ExplorerModel`/`render_explorer` (`tui/explorer_base.py`) + host subview machinery (`tui/tui_application.py:533-627`); command registry `tui/commands.py:2550-2617` |
| Web viewer | FastAPI app `viewer/main.py:145` + React SPA (`viewer/frontend-react/`); tabs in `App.tsx`; generic `DataTable` component; **reads only `traces.db`** — no memory awareness |

### Engineering guidelines (house rules, carried from the addendum)

1. No `getattr` (except facing agent-generated code). 2. No fallbacks/back-compat shims —
raise on failure. 3. No duplication. 4. Essentials only. 5. Separation of concerns.
6. Minimal change. 7. Net-LOC discipline. Plus: **verbal ALL-CAPS surfaces at the agent
boundary, numeric internals** (the descriptors convention).

---

## 2. Feature 1 — `todo` memory type

### 2.1 Decision: new type, distinct from `intent` and from `TodoManager`

Three things could be conflated; the design keeps them distinct and documents the split:

| Thing | Scope | Lifetime | Role |
|---|---|---|---|
| `self.todo` (`TodoManager`) | one session (snapshot-backed) | working memory | the agent's *current* task list: deps, per-todo vars, comments |
| `MemoryType.INTENT` | long-term store | until fired | trigger-based reminder: "when X happens, do Y" (`trigger` dict, `schema.py:114`) |
| **`MemoryType.TODO` (new)** | long-term store | until resolved | durable prospective commitment: "Y must get done (eventually / by ...)" with an explicit open→done lifecycle |

- `INTENT` keeps its cue/trigger semantics; its guide text (`manager.py:78`) currently
  says "reminder / TODO" — reword to "trigger-based reminder" so the taxonomy is crisp.
- **No bridge to `TodoManager` in this MR** (a `self.todo.done()` → auto-`remember`
  promotion is tempting but is a separate, opt-in follow-up; keeping them orthogonal
  avoids write amplification — the top open risk from v1, `design.md` Q2).
- Deliberately **not** merging `intent` into `todo`: `intent` firing is cue-match logic,
  `todo` is lifecycle state. Revisit after dogfooding; if `intent` sees no use, deprecate
  it then (guideline: don't design for hypothetical reuse now).

### 2.2 Schema & store changes

- `MemoryType.TODO = "todo"` (`schema.py:28`).
- New field `status: str | None` on `Memory` (`schema.py:82`) — **only meaningful for
  `todo`** (validator: non-`todo` memories must keep `status=None`; `todo` defaults to
  `"open"`). Values: `open | done | dropped`. Verbal surface at the tool boundary:
  `OPEN · DONE · DROPPED` (state machine, not an ordered ladder — lives beside the
  `descriptors.py` ladders as a plain allowed-set with the same raise-on-unknown rule).
- **Promote `status` to a real column + index** in `_SCHEMA` (`store.py:48`) — the TUI
  tab, the viewer tab, spontaneous injection, and the forgetting guard all filter on it
  (`WHERE type='todo' AND status='open'`). Piggybacks on the same v2 migration as
  `owner` (§3.3), so one migration covers both.
- Optional `due: float | None` (unix ts) in the data JSON (not promoted) — rendered as
  relative time in surfaces; no scheduler semantics in this MR.

### 2.3 Behavior

- **Write:** `self.memory.remember(content, type="todo", ...)`; `_as_type` accepts it
  (raises on unknowns as today). Dedup-on-write applies unchanged.
- **Resolve:** `update_memory(memory_id, status="DONE")` (extend the existing tool,
  `manager.py:564` — no new tool). `DROPPED` for abandoned items. Setting `status` on a
  non-todo raises.
- **Spontaneous surfacing — configurable, conservative default:**
  `SpontaneousConfig.inject_open_todos: Literal["always", "relevant", "off"] = "relevant"`.
  - `"relevant"` (default): open todos are ordinary memories — they surface in the
    spontaneous block only when similar to the current query, and via explicit
    `recall`/`search`. No special injection path; the only addition is rendering the
    `[open]` status in excerpts.
  - `"always"`: the full prospective-memory behavior — a bounded section of open todos
    appended inside the existing `recalled_memories` block by `_format_recall`
    (`manager.py:366`), capped by `open_todos_k: int = 5` (importance desc, then age),
    sharing the existing `context_char_budget`. One block, not two — avoids a second
    dynamic-block eviction surface.
  - `"off"`: never auto-surfaced (excluded from the spontaneous block even when
    similar); explicit `recall`/`search` only.

  Default is `"relevant"` because unconditional injection is unproven — dogfooding
  (§7.3) runs one week `"relevant"` / one week `"always"` to decide the shipped default
  with evidence. Like every memory knob, this lives on the frozen `MemoryConfig`
  (`config.py:129`): library users pass it at install
  (`MemorySkill(MemoryConfig(spontaneous=SpontaneousConfig(inject_open_todos="always")))`
  or `config.merge_with(...)`); the TUI builds that config in `configure_tui_memory`
  (`bootstrap.py:136`), so TUI exposure is a `tui.memory_todos` setting mapped onto the
  knob there (M7).
- **Forgetting guard:** open todos are never pruned — extend `is_protected`
  (`forgetting.py:46`) with `m.type is TODO and m.status == "open"`. `done`/`dropped`
  todos decay like anything else (they become part of history).
- **Guide:** add the type + lifecycle + "prefer `self.todo` for within-session task
  tracking; use `type='todo'` for commitments that must survive the session" to
  `MEMORY_SCHEMA_GUIDE` (`manager.py:74-79`).

---

## 3. Feature 2 — identity: `owner` + shared stores

### 3.1 Semantics

- Every memory gets `owner: str` — the stable identifier of the agent that wrote it.
- **Default read scope = own memories** (plus legacy/unowned, see §3.3). Cross-agent
  access is always explicit.
- Writes always stamp the writer's own `owner`. **Cross-owner `update_memory`/`forget`
  raise** in this MR (cooperative-multi-agent write policies are a real design question —
  deferred; a read-only view of others' memories is the safe default).

### 3.2 Owner resolution (who am I?)

Precedence at `MemoryManager.install` time, stored as `self.owner` on the manager:

1. `MemoryConfig.owner: str | None` — explicit (new top-level knob, `config.py:129`).
2. TUI: pass the existing stable per-agent key `tui_agent_memory_key(agent)`
   (`bootstrap.py:67-71`) as `MemoryConfig.owner` when building the config
   (`bootstrap.py:136`) — the TUI already solved agent identity; reuse it.
3. Library default: `type(agent).__name__` — honest default for "same agent class =
   same agent"; documented. (No auto-UUID: an unstable owner would orphan every memory
   on the next run — worse than a coarse one.)

`session_ref: str | None` is additionally recorded in the data JSON (provenance only,
not promoted, not filtered) — the TUI passes `session_id`; the viewer uses it to link
records back to traces.

### 3.3 Store & migration

- DDL: `owner TEXT NOT NULL DEFAULT ''` + `idx_mem_owner` (and `status TEXT` +
  `idx_mem_status` from §2.2) in `_SCHEMA` (`store.py:48`).
- **Introduce `PRAGMA user_version` migrations now** (the store has none). One tiny
  `_migrate(conn)` in `MemoryStore.__init__`, hybrid on purpose: the *upgrades* are
  introspection-guarded idempotent `ALTER TABLE`s (`PRAGMA table_info` existence
  checks, so a half-migrated file converges), while the version key does the one thing
  introspection cannot — **raise loudly when the file is newer than the code**
  (`user_version > SCHEMA_VERSION`), which matters precisely because the `project`
  scope (§3.5) puts one shared DB under processes running different checkouts. Fresh
  DB → stamped v2 directly; v1 file → two ALTERs + indexes → stamped v2.
- **Legacy rows have `owner=''` — defined as *unowned/shared*: matched by every owner
  filter.** This makes the migration semantics-preserving (an existing single-agent DB
  behaves identically) and gives teams a deliberate "shared/common" namespace (write to
  it via `MemoryConfig.owner=""`).
- `_row_to_memory` (`store.py:121`) overrides `owner`/`status` from the columns (same
  pattern as `archived` today).

### 3.4 Filter threading

`OwnerFilter = str | None` convention across the stack: `None` → own + unowned;
`"*"` → everyone; `"<name>"` → that owner + unowned.

- `store.keyword_search(..., owner=...)`, `all_memories(..., owner=...)`,
  `count(..., owner=...)` — plain SQL `WHERE`.
- `store.knn(vec, k, owner=None)` — the `VectorIndex` protocol has no metadata filter
  (`vector_backends.py:31-38`); **post-filter with oversampling** (query `4k`, filter,
  refill once if short). Uniform across all three backends; chroma-native `where`
  filtering is a later optimization, not v1 (keep one code path).
- `RetrievalEngine.recall(..., owner=...)` (`retrieval.py:71`) applies the filter to
  both stage-1 pools; **associative spread stays within the filtered candidate set**
  (an edge must not leak another agent's memory into a default-scoped recall).
- Manager ops (`manager.py:190,250,260,296`) resolve `None` → `self.owner` and pass
  through; spontaneous injection and reflection operate **own-scope only**
  (reflection must never merge/archive another agent's memories).
- Tool surface: `recall(query, k=5, owner=None)` / `search(query, k=5, owner=None)`
  gain the parameter; guide documents `owner="*"` ("look at what other agents know")
  and named fetch. `remember` gets **no** owner parameter (you write as yourself).

### 3.5 Shared-store operational reality (same file, many writers)

- Same-process multi-agent: fine today — each manager has its own connection; WAL is
  already enabled for file DBs (`store.py:82+`).
- **Cross-process/cross-manager staleness is the real bug to close:** `NumpyVectorIndex`
  is rebuilt in-memory at open (`store.py:_load_index`) — writes by another manager are
  invisible until reopen. Fix: `MemoryStore.refresh_if_changed()` checks SQLite's
  `PRAGMA data_version` (changes when *another* connection commits) at the top of each
  read op; on change, reload the index. Cheap (one pragma per read), correct for both
  numpy and sqlite_vec paths. Add `busy_timeout` for write contention.
- Docs: recommend `vector.backend="sqlite_vec"` for shared stores (index lives in the
  same file; no rebuild cost on refresh).
- **TUI scope:** `tui.memory` gains `"project"` (config `Literal` at
  `tui/config.py:116-121`): one project-level DB at the legacy default path
  `.nooa/memory/memory.sqlite` (`manager.py:139-143`), `owner` = the per-agent key —
  this is what actually turns on *shared* memory for dogfooding (§7). `"session"`
  behavior unchanged. `session_manager.delete_session` cleanup (`session_manager.py:340`)
  must **not** delete the project DB.

---

## 4. Feature 3 — references (pass-by-reference memory)

### 4.1 Model

Memories become `metadata + text + references`. **References are structured metadata,
not markup inside the text**: `content` stays plain prose (never parsed for pointers),
and the machine-readable refs live in a separate field. The compact `"kind:key"` string
form exists only at the agent-facing tool boundary, where it is parsed into the
structured object (raising on malformed input) — same pattern as the verbal descriptor
ladders. New field on `Memory` (`schema.py:82`), stored in the data JSON (no column —
references are resolved, not filtered on):

```python
class MemoryRef(BaseModel):
    kind: Literal["var", "context", "file", "todo", "memory"]
    key: str                      # var name / block key / relative path / todo id / memory id
    preview: str | None = None    # value snapshot captured at write time (truncated)
    captured_at: float = 0.0
```

String form at every agent-facing surface: `"<kind>:<key>"` — e.g.
`remember("the migration plan lives in self.v.plan", type="info", references=["var:plan", "file:docs/design/memory-system/plan-....md"])`.

Kinds map onto the framework's existing by-reference state:

| kind | resolves against | anchor |
|---|---|---|
| `var` | `agent.vars[key]` (snapshot-backed, survives sessions) | `tui/agent.py:338`, `storage/snapshot_vars.py:31` |
| `context` | `agent.context_manager[key]` (static or last-resolved dynamic) | `runtime/context_manager.py:151` |
| `file` | `Path(key).read_text()` — **relative to project dir, must stay under it**, capped bytes | same containment rule as `tui.memory_path` (`bootstrap.py:122-130`) |
| `todo` | `agent.todo.get(key)` (when the skill is attached) | `tools/todo.py:170` |
| `memory` | `store.get(key)` — for prose cross-references; **typed relations stay edges** (`associate`), this is not an edge replacement | `store.py:235` |

### 4.2 Resolution — names only, never eval

New module `memory/references.py`:

```python
def resolve(agent, store, ref: MemoryRef) -> ResolvedRef
# ResolvedRef: value_repr (truncated), status: LIVE | DANGLING
```

- **Strict name lookup only. No expression evaluation.** The `DynamicContext` expr
  mechanism (`runtime/actor.py:2806`) is the framework's eval channel, but memory
  content can originate from *other agents* (feature 2) and from LLM output — evaluating
  stored strings from the store would be an injection primitive. A `key` is a dict
  key / relative path, nothing more. (Guideline 2: a bad key raises at write time if
  malformed; resolves to `DANGLING` at read time if absent.)
- `preview` is captured at `remember()` time (truncated `repr`/head). On resolution:
  `LIVE` → render the current value; `DANGLING` (var gone, file deleted, other agent's
  namespace) → render the preview marked `[stale snapshot @ <time>]`. Honest semantics
  for cross-agent recall: agent B reading A's `var:plan` sees A's write-time preview,
  clearly labeled.

### 4.3 Surfaces

- `remember(..., references: list[str] | None = None)` on the mixin (`manager.py:539`);
  `update_memory` can replace them. Malformed ref strings raise (`kind` not in the
  registry, absolute/escaping file path).
- `_format_recall` (`manager.py:366`) resolves references inline for the injected
  block and for `recall`/`search` excerpts, within the existing char budget:

  ```
  - [info#a1b2] the migration plan lives in self.v.plan
      ref var:plan (LIVE) → {'phase': 2, 'owner_col': 'done', ...}
  ```
- One new read helper on the tool surface: `deref(ref: str) -> str` — lets the agent
  pull a referenced value on demand without re-recalling. (Total new tool count: 1.)
- **Guide convention** (`MEMORY_SCHEMA_GUIDE`): *"pass everything by reference: when a
  memory is about live state (a var, a file, a todo), store a short description plus a
  `references=[...]` pointer instead of pasting the value — referenced values are
  re-read fresh at recall time."* This is the behavioral fix for `results.md` §4
  (stale memory misleads the agent): the reference resolves to current reality.

---

## 5. Feature 4 — observability

Four layers, from measurement model to UI. Today memory is *invisible* twice over:
the four monitoring events are `RUNTIME_EVENT` + `record=False` — never persisted,
never exported (`manager.py:311-316`, `event_manager.py:144-146`) — and even
per-memory bookkeeping misses the biggest surface: deliberate `recall` touches the
record, but **spontaneous injection intentionally does not** (`manager.py:373`,
`touch=False`, to keep injection from self-reinforcing ACT-R activation), so injected
memories leave no per-memory trace. `access_log` is capped bare timestamps — no
channel, no session, no fetch point.

### 5.1 The measurement model — self-contained access history on the record

Principle: **every time a memory reaches the agent (or is consulted by the system),
that is an *access* with a channel — recorded on the memory itself.** The `Memory`
record is fully self-contained: copy/export one row and its whole usage story travels
with it — no side table, no separate log to keep consistent. Design constraint from
review: *the memory is the unit of observability*.

The existing bare-timestamp `access_log: list[float]` (`schema.py:105`) is upgraded to
structured entries, plus uncapped per-channel counters:

```python
class AccessRecord(BaseModel):
    ts: float
    channel: Literal["recalled", "searched", "injected", "reinforced", "reflected", "deref"]
    reader_owner: str = ""        # who fetched (cross-owner analysis)
    session_ref: str | None = None
    trace_ref: str | None = None  # active span id → deep-link into the viewer trace
    query: str | None = None      # truncated; None for reinforced/reflected
    score: float | None = None
    rank: int | None = None
    components: dict | None = None  # {rel, rec, imp, spread} at fetch time

class Memory(BaseModel):
    ...
    access_log: list[AccessRecord]  # capped ring (ObservabilityConfig.access_log_cap=64→configurable)
    # uncapped totals, survive log rotation ("spontaneous_access etc."):
    recalled_count: int = 0
    searched_count: int = 0
    injected_count: int = 0
    reinforced_count: int = 0
    deref_count: int = 0
```

- **One write path:** `Memory.log_access(record)` (extending `touch()`, `schema.py:151`)
  appends to the capped log, bumps the matching counter, updates `last_accessed_at` —
  then `store.save`. Everything lives in the record's `data` JSON; the promoted
  `access_count`/`last_accessed` columns keep working for cheap list-view sorting.
- **ACT-R math is byte-for-byte unchanged:** `base_level_activation` (`retrieval.py:36`)
  consumes only entries whose channel *touches* (`recalled/searched/reinforced/deref`).
  `injected` entries are **logged without touching** — spontaneous injection still
  cannot self-reinforce activation (`manager.py:373` semantics preserved), it just
  stops being invisible. `access_count`/`strength` likewise only move on touching
  channels (back-compat for every existing consumer).
- **Migration is a validator, not SQL:** legacy `data` JSON has floats in `access_log`;
  a Pydantic coercion maps each to `AccessRecord(ts=t, channel="recalled")` on read.
  (The v2 SQL migration, §3.3, stays owner/status-only.)
- **Write cost, stated honestly:** logging injections means saving the injected
  records each spontaneous turn (top-k row updates, one transaction, self-gated
  cadence already bounds frequency). Ring cap keeps records small; totals survive
  rotation via the counters. `ObservabilityConfig(access_log_cap, log_injections=True)`
  can dial it down.
- One deliberate exception: **`maintenance_log`** — a tiny same-file table
  (`ts, kind, report JSON`, one row per `reflect()`/`prune()` run). Reflection history
  describes the *store*, not any single memory, so it can't live on a record; it stays
  inside the same SQLite file (still nothing external to track).

**Per-memory usage stats** (derived; shown in the TUI detail pane and web detail):
last fetched (when + channel), **last fetch point** (session + trace deep-link), fetch
counts total and by channel, mean rank & score when fetched, injected-but-never-used
flag, current activation + **prune ETA** (retention forecast under the active
`ForgetPolicy`), `strength`/`reinforcement_count`, access sparkline.

**Store-level KPIs** (web dashboard + TUI header line), each mapped to the question it
answers — this is the "is the memory system working, and where to improve it" surface:

| Question | Stats |
|---|---|
| Is the store *used* at all? | % never-fetched (older than 7d), fetch concentration (top-10% share), median days-since-last-fetch, fetches by channel |
| Is writing calibrated? | adds vs dedup-reinforces (dedup hit-rate), writes by source (tool / event / episode), growth per week, archive rate |
| Is retrieval good? | injection fill (memories & chars vs budget, truncation rate), **injected→used rate** (same memory deliberately recalled/deref'd later in the task), mean rank of eventually-used memories, recalls per task |
| Do TODOs work? | open count, median open age, done rate, resurface→done rate (accessed then closed within the task) |
| Do references work? | deref count, LIVE vs DANGLING rate by kind |
| Does sharing work? | cross-owner read matrix (reader × writer), unowned-namespace usage |
| Is maintenance healthy? | reflection/prune history (`maintenance_log`), merged/pruned per run, activation distribution, protected fraction |
| Is it cheap? | per-turn injection latency, embedding failures/retries, index refreshes (`data_version` staleness hits), store bytes |

**The retrieval debugger — `explain(query)`:** a dry-run recall (no touch, no access
rows) that returns the full candidate table: stage-1 source (dense / sparse / both),
per-component scores (`rel/rec/imp/spread`), owner/archived filter outcomes, and hop-
spread contributions. This answers *"why wasn't memory X recalled for query Q"* — the
single most useful tool for tuning `ScoringWeights`/`RetrievalConfig` — exposed as
`MemoryManager.explain()` (tests/benchmarks), `GET /api/memory/explain` and a query box
in the web Memory tab. (TUI exposure deferred — the web view fits a table better.)

`MemoryStats` (`monitoring.py:71`) stays as the cheap in-process counter snapshot;
everything above is derived from the tables by a new `memory/observability.py` (SQL +
dataclasses), shared by the TUI, the viewer routes, and benchmark reports.

### 5.2 Tracing bridge (substrate)

New `memory/tracing_bridge.py`, installed by `MemoryManager._install_hooks` when tracing
is active (lazy optional import; zero cost otherwise):

- Subscribes to the four events (`EventManager.on`, `event_manager.py:187`) and emits
  them as **span events on the current active span** (`memory.written`,
  `memory.recalled`, `memory.injected`, `memory.reflected`) with the event fields as
  attributes. Memory activity then appears inside the very `execute_python`/method spans
  where it happened — no new span hierarchy to invent.
- The events get **richer payloads**: `MemoryRecalled`/`MemoryInjected` carry the
  memory ids + scores (not just counts), so a trace shows *which* memories surfaced at
  each step; conversely the bridge hands the current span id back as `trace_ref` for
  the access log (§5.1) — the two stores cross-link.
- Stamps two session-level attributes once: **`memory.db_path`** (absolute) and
  **`memory.owner`** — this is what lets the web viewer *discover* which memory DB
  belongs to a trace session (the discovery gap found in the viewer analysis).
- Extends `MemoryStats` (`monitoring.py:71`) with `todos_open/todos_done`,
  `refs_resolved/refs_dangling`, `cross_owner_recalls`, `injection_latency_ms` so
  `stats()`/benchmarks see the new features.

### 5.3 TUI memory tab

Mirror the `/jobs` pattern end-to-end (all anchors verified):

- `tui/memory_explorer.py`: `MemoryExplorerRow` (with `search_text` =
  title+content+tags+owner+type) + `MemoryExplorerView(ExplorerView)`
  (`explorer_base.py:277`) — `format_row`: type glyph, TODO status, owner (project
  scope), `importance_label`, **fetch count + last-fetched age**, title/preview;
  `detail_lines`: full content via `render_markdown_lines` (`explorer_base.py:111`),
  metadata block, a **Usage section** (the §5.1 per-memory stats: counts by channel,
  last fetch point, mean rank, activation, prune ETA), **resolved references**
  (LIVE/DANGLING), edges with target previews. Header line = the KPI one-liner
  (store size, % never-fetched, open todos, dedup rate) from `observability.py`.
- Row actions (`ExplorerConfig.actions`): `f` forget (archive), `d` mark todo DONE —
  both routed through the manager on the agent thread via `agent_run` (the exact
  pattern `MemoryCommand` already uses, `commands.py:1326-1346`). Reads too:
  `agent_run(lambda: store.all_memories(...))` — never touch the store from the UI
  thread.
- Wiring: `TUIApplication.open_memory_explorer` (mirror `tui_application.py:533-538`) →
  frontend delegate (`frontend.py:200-203`) → **`/memories` command** (`/memory` is
  taken by the on/off toggle, `commands.py:1295`) registered in `_command_classes`
  (`commands.py:2550`), `required_capabilities = frozenset({"memory"})` so it only
  registers when the skill is attached.

### 5.4 Web viewer Memory tab

Mirror the Traces tab end-to-end; three views (Records / Dashboard / Explain):

- Backend `viewer/memory_routes.py` (`/api/memory`): `GET /dbs` (discovery), `GET
  /records?db=&owner=&type=&status=&q=` (list, paginated, `keyword_search` for `q`),
  `GET /record/{id}?db=` (detail: content + `neighbors()` edges + references +
  usage stats + the record's own `access_log` — self-contained, no join), `GET
  /stats?db=` (the §5.1 KPI dashboard payload, aggregated by scanning records), `GET
  /explain?db=&q=` (retrieval debugger). Opens
  the memory DB **read-only** (`file:...?mode=ro` URI) via `MemoryStore` +
  `observability.py` — same DB-backed-route precedent as `annotation_routes.py`.
- Discovery = union of: paths recorded in trace resource attrs (`memory.db_path`, once
  §5.2 lands), `.nooa/memory/*.sqlite` under the project dir, and
  `*-memory.db` next to TUI session DBs. Explicit `?db=` always works.
- Frontend: `NavLink` "Memory" + routes in `App.tsx:50-60`.
  - **Records:** `pages/MemoryList.tsx` on the generic `DataTable`
    (`components/DataTable.tsx`) — columns: type, status, owner, importance,
    **fetches, last fetched**, created (relative), preview, edge count; filters for
    owner/type/status; `pages/MemoryDetail.tsx` reusing the `TraceDetail` header
    pattern + `CodeBox` for content, metadata grid, the **Usage panel** (counts by
    channel, mean rank, activation, prune ETA) and the **access-history table with
    `trace_ref` deep-links into the trace view** ("last fetch point", clickable),
    references (write-time previews, labeled), edge list linking to other records.
  - **Dashboard:** the KPI table of §5.1 with small trend charts (growth,
    never-fetched %, injected→used rate, channel mix, cross-owner matrix).
  - **Explain:** query box → scored candidate table (per-component scores, stage-1
    source, filter outcomes) — retrieval tuning without leaving the browser.
- **Read-only in v1.** (The annotations write-back pattern exists if editing is ever
  wanted.) Graph visualization: out of scope; the edge list links suffice.

---

## 6. Implementation plan

Ordering: schema/store first (one migration), then features on top, observability last
(it renders what the features produce). Each milestone is independently green.

| M | Deliverable | Verifiable by |
|---|---|---|
| M1 | Store v2: `owner` + `status` columns, `user_version` migration, `refresh_if_changed()` (data_version), owner filters on `keyword_search/knn/all_memories/count` | unit: v1-file migration round-trip; two-store cross-connection visibility; oversampled knn filter correctness |
| M2 | `todo` type: enum + `status` field/validator + verbal states, tool acceptance, 3-mode `inject_open_todos` knob, forgetting guard, guide text | unit: lifecycle + validator raises; integration: each mode behaves (`always` injects next task, `relevant` only on similarity, `off` never); done todo drops out; protected from prune |
| M3 | Owner end-to-end: config knob, install-time resolution, filter threading through retrieval/manager/tools, cross-owner update/forget raise, spread confinement | integration: agents A+B on one file — default isolation, `owner="*"` fetch, B cannot forget A's memory |
| M4 | References: `MemoryRef`, `references.py` resolver (names-only), tool params + `deref`, `_format_recall` rendering, previews + DANGLING | unit: each kind LIVE/DANGLING, path containment raises; integration: stale-var scenario shows live value |
| M5 | Observability substrate: `AccessRecord` + per-channel counters on `Memory`, `log_access` single write path, legacy-float coercion, `maintenance_log` table, enriched events, tracing bridge (+`trace_ref` capture), `ObservabilityConfig`, `observability.py` (KPIs), `explain()` | unit: one `AccessRecord` per channel with correct touch semantics (injection logs, doesn't reinforce ACT-R); v1 float logs coerce; explain returns component breakdown; integration: traced run's JSONL contains `memory.*` span events with ids |
| M6 | TUI `/memories` explorer (rows with fetch stats, detail + Usage section, KPI header, `f`/`d` actions, agent-thread reads) | TUI tests: view renders from a seeded store incl. usage stats; action round-trip via fake agent_run; manual smoke in the TUI |
| M7 | TUI `project` scope (config literal, bootstrap path + owner, delete-session guard) | integration: two TUI sessions share the project DB with distinct owners; session delete keeps it |
| M8 | Viewer: `memory_routes.py` (records/accesses/stats/explain) + Memory tab (Records / Dashboard / Explain views, trace deep-links), discovery, committed `dist/` rebuild | FastAPI TestClient route tests over a fixture DB (incl. stats + explain payloads); `npm run build`; manual smoke |
| M9 | Benchmarks (§7.2) + docs + dogfood kickoff (§7.3) | benchmark READMEs with results tables; `results.md` extended |

MR shape: this is one MR (per your goal) but **commit-per-milestone** so review maps to
the table; M8 (viewer) and M9 (benchmarks) are natural split-out candidates into stacked
MRs if review size becomes an issue — M1–M7 have no dependency on them.

Suggested branch rename to match the convention: `feat/memory-todo-owner-refs-observability`.

## 7. Testing plan

### 7.1 Unit + integration (per milestone, `tests/memory/` + TUI/viewer test dirs)

The table in §6 lists the gating tests. Cross-cutting regressions (the v1 additive
guarantee still holds):

- Agent without the skill: byte-for-byte unchanged; `enabled=False` inert.
- v1 DB opened by v2 code: migrated once, identical single-agent behavior
  (`owner=''` = unowned matches everything).
- All 16 existing `tests/memory/` modules stay green untouched (except deliberate
  guide-text assertions).
- Injection latency: open-todo section respects the existing char budget; no second
  dynamic block.

### 7.2 Benchmarks

**Verdict on Earthborne Rangers (EBR-Bench, Epoch AI, Jul 1 2026):** *not usable
directly; adopt its protocol.* Researched from the primary source
(https://epoch.ai/publications/earthborne-rangers-benchmark): no public code, data, or
harness; the game content is copyrighted and Epoch explicitly does not distribute it; no
Benchmarking-Hub entry. Signal caveats even with access: the memory ceiling is small
(an expert-written oracle guide adds only **+2–3.5 of 21 objectives**), variance is high
(final-2-of-10 playthrough averages, ≤3 runs/model), models reward-hack it (Gemini 3.1
Pro ended playthroughs early to protect scored runs), and runs are extremely long. What
*is* valuable — and different from LoCoMo/LongMemEval's declarative recall — is the
**protocol**: repeated episodes where only memory persists, scoring restricted to the
final 20% (80% is a learning phase), a **no-memory / self-authored-memory / oracle-guide**
three-arm design, and behavioral metrics (exploration coverage, repeated-mistake rate).
That tests whether memory *changes behavior*, which is exactly what features 1–3 claim.

Four benchmarks, ordered cheap→expensive; the first three are small and land inside
this MR, the fourth is the headline and may trail as its own commit/MR:

1. **`todo_prospective.py`** (new) — prospective memory across sessions. Session k
   plants commitments ("after the migration lands, update the README"); sessions k+1..n
   present the completion cue; measure **fire rate** (commitment acted on without being
   re-told) and false-fire rate, ON vs OFF. Oracle solver (deterministic, offline) +
   real-model arm, following the `bench.py` pattern (`event_manager.clear()` between
   sessions, `run_condition(memory_on=...)` — `examples/memory_bench/bench.py:162`).
2. **`shared_memory.py`** (new) — two agents, one store. Agent A ingests facts; agent B
   answers questions with (a) default own-scope (should fail — isolation correctness),
   (b) `owner="*"` (should succeed — sharing benefit). Metrics: accuracy per arm +
   leakage (B's default recall must return zero A-owned memories).
3. **`memory_effect.py` reference arm** (extend existing) — the stale-memory scenario
   (`results.md` §4) gains a third arm: memory stored **by reference** (`var:`/`file:`).
   Expected: copy-arm HURTS (documented), reference-arm resolves live and HELPS.
   This is the single most direct proof of feature 3.
4. **`ranger_bench.py`** (new, EBR-style, original content — no IP) — a small
   text-based expedition campaign game we author ourselves (seeded, rules-enforced in
   ~300 lines of plain Python): 5-day segments, per-day action economy with a
   fatigue-like resource, 3–4 "loadout" archetypes chosen before each playthrough,
   ~15 objectives with card-interaction-style gotchas. Protocol per EBR: N=10
   playthroughs per run, **context wiped between playthroughs, only the memory
   subsystem persists**, score = objectives on the final 2 playthroughs. Arms: memory
   OFF / ON / ON+oracle (a hand-written strategy note as the ceiling). Metrics:
   objectives, **loadout-exploration coverage** (did it remember what it tried — pushes
   TODO usage), **repeated-mistake rate** (same gotcha hit twice), fire-rate of
   self-written todos ("try the river route next run"). Fixed seeds → variance we
   control, unlike EBR. Effort estimate: ~1–2 weeks including tuning; `README.md` with
   research question/design/metrics per the experiments rule.

### 7.3 Dogfooding

Turn the system on ourselves, structured:

- **Setup:** `tui.memory = project` scope (M7) on this repo for the team working in the
  TUI; owners = per-agent keys; tracing on so the viewer tab has data. Launch:
  `uv run nemo oo tui --model claude-opus-4-8` (or `gpt-5.5` — bundled aliases route
  via the internal gateway; `NVIDIA_INTERNAL_API_KEY` in `secrets.yaml`), then
  `/memory on`.
- **Protocol (2 weeks):** each participant works normally but (a) lets the agent keep
  durable todos (`type="todo"`) for real follow-ups, (b) stores by-reference memories
  for living artifacts (plans in `self.v`, design docs), (c) opens `/memories` and the
  viewer Memory tab at least daily to inspect what accumulated. Week 1 runs
  `inject_open_todos="relevant"`, week 2 `"always"` — the comparison decides the
  shipped default.
- **Collected:** weekly KPI snapshots from the dashboard endpoint (`/api/memory/stats`
  — never-fetched %, injected→used rate, dedup hit-rate, todo done-rate, dangling-ref
  rate, cross-owner matrix, store size), plus a shared friction log (wrong/noisy
  injections, stale previews, todo spam, latency). When an injection looks wrong,
  capture it with `explain(query)` — those become the retrieval-tuning corpus.
- **Exit questions:** Do open todos actually resurface at the right moment? Does
  shared scope surface *another* agent's knowledge usefully at least once? What's the
  dangling-ref rate in practice? Do we want `intent` at all? Findings feed a
  `results.md` §5 and the next iteration's cut list.

## 8. Risks & open questions

1. **Write amplification, again** — in `"always"` mode a todo-happy agent could spam
   the block. Mitigation: conservative `"relevant"` default, `open_todos_k` cap +
   importance ordering in `"always"`; the dogfood A/B decides the shipped default.
   (Carried from v1 Q2.)
2. **Cross-owner write policy** — v1 raises; teams may legitimately want a curator agent
   that consolidates everyone's memories. Deferred until dogfooding shows the need.
3. **Vector-filter efficiency** — post-filter oversampling degrades if one owner
   dominates a big shared store; chroma-native `where` / sqlite-vec metadata filtering
   is the known optimization path.
4. **Reference security** — names-only lookup and project-dir containment are the load-
   bearing guarantees; any future "expression references" proposal must clear the
   cross-agent injection bar explicitly.
5. **Viewer staleness** — the viewer reads the DB read-only while agents write;
   WAL + read-only URI is safe, but a list view can be seconds stale. Acceptable; noted
   in the UI.
6. **`intent` vs `todo` overlap** — resolved by narrowing docs now, deprecation decision
   after dogfooding.
7. **ranger_bench design risk** — a game we author can be accidentally too easy/hard;
   budget a tuning pass with the oracle arm as the calibration probe (oracle ≫ OFF must
   hold, or the game doesn't reward knowledge).

---

## Appendix — feature-to-file touch list (quick review map)

| File | F1 todo | F2 owner | F3 refs | F4 obs |
|---|---|---|---|---|
| `memory/schema.py` | type + status | owner field | MemoryRef + field | `AccessRecord`, per-channel counters, structured capped `access_log`, `log_access` |
| `memory/store.py` | status col | owner col, migration, filters, refresh | — | `maintenance_log` table, read-only open |
| `memory/config.py` | `inject_open_todos` (3-mode), `open_todos_k` | owner knob | — | `ObservabilityConfig` (`access_log_cap`, `log_injections`) |
| `memory/observability.py` (new) | — | — | — | derived KPIs, per-memory usage stats, explain assembly |
| `memory/retrieval.py` | — | owner threading + spread confinement | — | score-component capture, `explain()` dry-run |
| `memory/forgetting.py` | open-todo guard | — | — | — |
| `memory/manager.py` | guide, injection, tools | resolution, stamping, tool params | tool params, `deref`, format | bridge install |
| `memory/references.py` (new) | — | — | resolver | — |
| `memory/tracing_bridge.py` (new) | — | — | — | span events + attrs |
| `memory/monitoring.py` | stats fields | stats fields | stats fields | stats fields |
| `tui/` (cli pkg) | explorer actions | project scope | detail rendering | `/memories` + explorer |
| `viewer/` | status col/filter | owner col/filter | detail rendering | routes + tab |
| `examples/memory_bench/` | todo_prospective, ranger_bench | shared_memory | memory_effect ref-arm | stats in reports |
