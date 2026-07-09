# Memory System — Design: TUI Scope Defaults & Owner Identity

> **Status:** DECIDED & IMPLEMENTED — command surface `/memory on|local|off`
> (`on` = shared project store, `local` = per-session) and owner = explicit
> config else the agent's class name, with the legacy-spelling heal.
> **Trigger:** dogfooding — `/memory on` produced per-session memory silos under
> `.nemo_oo/sessions/`, defeating cross-session recall; and the `owner` values
> observed in real stores are illegible and inconsistent.
> **Companions:** [`plan-todo-identity-references-observability.md`](./plan-todo-identity-references-observability.md)
> §3 (owner semantics) · [`addendum-agent-facing-contract.md`](./addendum-agent-facing-contract.md)
> (the guide now *displays* the owner — which is how this was noticed).

---

## 0. The two-axis model (the source of the confusion)

Memory isolation in the TUI is governed by two independent axes that both feel
like "isolation" but compose multiplicatively:

|  | **Scope** (which FILE) | **Owner** (which ROWS in that file) |
|---|---|---|
| Decided by | `tui.memory` / `/memory <scope>` | `MemoryConfig.owner`, stamped per write |
| Values | `off` · `session` · `project` | any string; `""` = unowned/shared |
| Read control | none — a store either is or isn't the one you opened | `recall(query)` = mine + unowned; `owner="*"` = everyone; `owner="name"` = theirs |

**The key fact:** `owner="*"` widens the *row filter within one store*. It can
never see across store **files**. With `session` scope every session writes its
own `.nemo_oo/sessions/<session-id>-memory.db`, so cross-session recall is
impossible *regardless of owner* — the rows live in files nobody opens again
(session resume via `-c` being the one exception).

## 1. Current behavior (verified against the code + real stores)

- `/memory on` → **`session` scope** (`commands.py`:
  `scope = "session" if subcmd == "on" else subcmd`). Store =
  `Path(agent_db).with_name(f"{session_id}-memory.db")`.
- `/memory project` (M7) → the shared per-working-dir store
  `<working_dir>/.nemo_oo/memory/memory.sqlite`. This is the configuration the
  dogfooding question asks for — **it exists today**, it just isn't what `on`
  gives you, and nothing points at it.
- Owner = `tui_agent_memory_key(agent, config)` =
  `config.tui.agent_spec or f"{module}:{qualname}"` — a string invented for a
  *different job* (keying the `[tui.memory_agents]` scope-preference table in
  `config.toml`).

Observed in this repo's real dogfooding stores (the motivating evidence):

```
.nemo_oo/sessions/04aaac87-...-memory.db     (a silo; rows unowned)
.nemo_oo/sessions/82030aac-...-memory.db     owner = "nemo_oo_agents_cli.tui.agent:TUIAgent"
.nemo_oo/memory/memory.sqlite                owner = "" and "TUIAgent"
```

Three spellings of the same conceptual agent across three files: the library
default (`type(agent).__name__` → `TUIAgent`), the TUI key
(`module:qualname`), and pre-owner rows (`""`). Nobody can type
`recall(owner="nemo_oo_agents_cli.tui.agent:TUIAgent")` reliably — least of
all the LLM, which is the actual caller.

## 2. Problem 1 — the default scope silos memories

### Why `session` is the wrong meaning for "on"

- The injected guide literally sells the feature as *"commitments that must
  survive the session"* — under `session` scope they don't (the file is
  abandoned when the session is, and `delete_session` unlinks it).
- The benchmark evidence (`results.md` §1) shows memory's decisive win **is**
  cross-session recall; `session` scope keeps only the least valuable slice.
- `owner="*"`, shared todos, cross-agent transfer (M3), the project Dashboard —
  all of it assumes one store with many writers.

### What `session` scope is still good for

Ephemeral experiments: benchmarks, one-off agents, "try memory without
committing to a durable store". It should remain available — explicitly.

### Options

| Option | Meaning of `/memory on` | Notes |
|---|---|---|
| A (status quo) | `session` | Keeps surprising everyone the way it surprised us |
| **B (DECIDED)** | **`project`** | "on" = the useful thing: durable, shared across sessions and agents in this working dir. Per-session stays as an explicit mode |
| C | flip the global default `tui.memory: project` (memory on for everyone) | Too aggressive while memory is opt-in; revisit after dogfooding |

**Decision: B, with a friendlier vocabulary.** The command speaks in *modes*,
not scope internals:

```
/memory on      -> project scope   (global: one store per working dir, shared
                                    across sessions and agents)
/memory local   -> session scope   (this session only; the ephemeral mode)
/memory off
/memory status  -> "Memory: on (shared across sessions, project-wide)" etc.
```

The old `project`/`session` words are no longer accepted by the command; they
remain the *internal* scope literals (`tui.memory`, `[tui.memory_agents]` in
config.toml persist the literals unchanged, so existing persisted preferences
keep working). The `project` store guards are unchanged (delete-session never
unlinks it; `tui.memory_path` override rules apply). Config default stays
`"off"` — only the meaning of opting in changed.

### Migration of existing session silos

Not automatic (their rows may be junk from experiments). Provide a small
utility instead — `nemo oo memory merge <src.db>... <dst.sqlite>`:
copies active memories (embedding blobs included) + edges into the
destination via the normal `store.add` path, stamping rows that have no owner
with a `--owner` argument. Priority P2; a one-line hint in `/memory status`
("N session stores exist under .nemo_oo/sessions — merge with …") makes the
strays discoverable.

## 3. Problem 2 — owner identity is illegible and inconsistent

### Analysis: one string doing two jobs

`tui_agent_memory_key` exists to key **configuration** (`[tui.memory_agents]`
scope preferences). That job needs *uniqueness and stability*; legibility is
irrelevant — `module:qualname` is a fine config key. M7 then reused it as the
**owner**, whose job is the opposite: it is an *agent- and human-facing name* —
rendered in the guide ("your identity: …"), typed by the LLM in
`recall(owner="…")`, shown as a column in `/memories` and the viewer, and used
by teammates to ask "what did the planner learn?". For that job
`nemo_oo_agents_cli.tui.agent:TUIAgent` fails on every count, and it also
**diverges from the library default** (`type(agent).__name__`), which is how
the same agent ended up under three spellings.

### Requirements for a good owner

1. Short and typable by an LLM (`recall(owner="tui")` should be plausible).
2. Stable across sessions and launches.
3. **Identical no matter how memory was installed** (TUI skill vs library
   mixin) — one agent, one name.
4. Distinct for genuinely different agents sharing one project store.
5. Overridable when a human wants meaningful names ("planner", "reviewer").

### Options

| Option | Owner value | Verdict |
|---|---|---|
| A (status quo) | agent_spec / `module:qualname` | Fails 1, 3 |
| B | session id | Fails 1, 2, 4 — worst of all worlds (a new "owner" per session would re-create the silo problem inside the shared file) |
| **C (recommended)** | **explicit config, else `type(agent).__name__`** | Matches the library default exactly (fixes 3); short (1); stable (2); distinct enough (4); config covers the rest (5) |
| D | derived slug (strip `Agent`, snake-case) | Prettier, but now *three* naming schemes exist in the wild; C converges on the one that already has data |

**Decision: C**, concretely — **what an owner looks like now:**

| Situation | Owner value (role[@instance]) | How it reads in practice |
|---|---|---|
| Stock TUI agent | `TUIAgent@04aaac87` | guide: "your identity: TUIAgent@04aaac87"; other agents read the role: `recall(owner="TUIAgent")` |
| Custom agent `--agent ./planner.py:PlannerAgent` | `PlannerAgent@<sess8>` | role stable across sessions; instance names the session |
| Global human name (`tui.memory_owner: "elad"`) | `elad@<sess8>` | one human identity, per-session instances |
| Per-agent alias (`memory_owner_agents: {...: "planner"}`) | `planner@<sess8>` | meaningful team names on a shared store |
| Library install (no TUI, no session) | `type(agent).__name__` (bare role) | same scheme, instance omitted |

And the machinery:

- New TUI config: `tui.memory_owner: str | None = None` (global) and
  `tui.memory_owner_agents: dict[str, str] = {}` (per config-key override) —
  settable in `settings.yaml`; resolution:
  `memory_owner_agents[key] → memory_owner → type(agent).__name__`.
- `configure_tui_memory` passes that as `MemoryConfig.owner` instead of the
  config key. `tui_agent_memory_key` keeps its original, single job (config
  keying) — the two roles are separated again.
- Class-name collisions (two different `MyAgent` classes in one project store)
  are accepted as rare and *visible* (the `/memories` owner column shows the
  merge); the per-agent override is the escape hatch. No auto-disambiguation
  machinery — it would reintroduce illegible names to solve a problem nobody
  has hit.

### Healing the existing spellings (implemented)

A one-time, idempotent rename at configure time:

- `store.rename_owner(old, new)` (one `UPDATE memories SET owner=? WHERE
  owner=?` + data JSON untouched — owner column is authoritative on read).
- On install, if the resolved owner is the new-style name and the store
  contains rows under this agent's *legacy* key (`module:qualname` /
  agent_spec), rename them. Unowned (`""`) rows are left alone — they are
  deliberately shared.
- Logged as a `maintenance_log` row (`kind="rename_owner"`) so the Dashboard
  shows the heal.

## 4. Resulting configuration surface

```yaml
# settings.yaml
tui:
  memory: project            # off | session | project   (internal literals; default off)
  memory_owner: null         # null -> agent class name; or a human name ("planner")
  memory_owner_agents: {}    # per-agent overrides, keyed by the config key
  memory_path: null          # unchanged: relative override under the project dir
```

`/memory` command vocabulary: `on` (project scope) · `local` (session scope) ·
`off` · `status`.

## 5. Test plan

- **Command remap:** `/memory on` persists + configures `project` scope; the
  store path is the project one; `session` still reachable explicitly.
- **Owner resolution matrix:** {override-per-agent, global, default} ×
  {TUI skill, library mixin} — the same class yields the same owner on both
  install paths (the 3-spellings regression, pinned).
- **Cross-session recall (the motivating scenario):** session 1 (project
  scope) remembers; session 2 recalls it with plain `recall()` — same owner —
  and a *different agent class* in session 3 sees it only via `owner="*"`.
- **Legacy heal:** a store seeded with `module:qualname` rows → configure →
  rows renamed, maintenance row written, second configure is a no-op;
  unowned rows untouched.
- **Guide disclosure:** the rendered guide shows the short owner.
- **Merge utility (P2):** src silo rows appear in dst with embeddings intact.

## 5b. Hierarchical owner — IMPLEMENTED

**The owner IS the instance name:** `role@instance`, e.g. `TUIAgent@04aaac87`
(agent class or alias `@` first 8 hex of the session id). A bare `role` with no
`@` is a valid owner — library installs with no session, and every existing row
— so old data composes with zero migration. Enforcement operates on the
**role part** by default, which is what keeps cross-session recall and
curation working under instance-bearing names.

### Semantics

`role(owner)` = everything before the `@`.

| You pass to recall/search | Matches |
|---|---|
| `owner=None` (default; harness-resolved — the agent never types its own name) | **role scope**: all instances of your role (`TUIAgent`, `TUIAgent@*`) + unowned `''` |
| `owner="PlannerAgent"` | that role, all instances (read-only) |
| `owner="PlannerAgent@b1dbf591"` | that exact instance (+ unowned) — "what did that session write?" |
| `owner="*"` | everyone |

- **Write guard is role-based**: update/forget/associate allowed when
  `role(m.owner) == role(self.owner)` or the row is unowned — today's session
  curates and closes what yesterday's wrote. Dedup-reinforce, reflection,
  forgetting, injection, and stats all run at role scope (anything narrower
  fragments knowledge per session).
- **Validation**: the role part must not contain `@` or SQL wildcards
  (`%`, `_`) — enforced at config (raise, no sanitizing). Class names satisfy
  this by construction; aliases are checked.
- **Provenance**: the owner column now answers "which instance wrote this"
  directly. `session_ref` (full uuid) stays on access records; the
  manager-stamped `created` record is kept for the `trace_ref` deep-link and
  because the owner column is mutable (heals, merges) while the access log is
  append-only.
- **Heal target change**: legacy `module:qualname` rows fold to the **bare
  role** (`TUIAgent`), never to the current session's full tag — old rows must
  not be misattributed to today's instance. Bare-role rows are first-class
  under role scope, so healed data behaves identically.
- **Surfaces**: `/memory status` shows the identity ("you are
  TUIAgent@04aaac87" + scope + store path); the injected guide's identity line
  renders the full owner; `store_kpis`' by-owner grouping keys on the role
  (one dashboard row per role, not per session).

### Implemented slice

1. Core role semantics: store filters (`all_memories`/`count`/
   `keyword_search`/`knn` post-filter), spread confinement, both engines
   (constructed at role scope), the writable guard, owner validation.
   Also fixed en route: the `always` open-todo section was never
   owner-scoped (an M3 patch miss) — now role-scoped and regression-pinned.
2. TUI: owner composed as `role@sess8` in `configure_tui_memory`
   (library installs stay bare-role); heal retargeted to the bare role;
   `/memory status` shows identity + store; KPI by-owner grouping keys on
   the role (cross-owner reads likewise compare roles).

Everything below remains **deferred**.

## 5c. DEFERRED — the todo claim protocol (and friends)

Instance-bearing owners do **not** fix concurrent double-fire: two live
sessions share role scope by design, so both still see an open todo. The fix
is execution exclusivity — deferred until dogfooding demands it. Recorded so
the design isn't relost:

- **The model**: a shared task board with name tags. A todo gains
  `in_progress` between `open` and `done`; *claiming* moves it there with the
  claimer's owner written on it (`claimed_by`, `claimed_at`).
- **Claiming is one indivisible statement** ("set to in_progress with my name
  WHERE still open" — rowcount 1 = you won, 0 = skip). Look-then-grab is a
  race; grab-and-see-if-you-got-it is not.
- **Lease**: a claim older than the TTL (~15 min, configurable) is
  reclaimable inside the same statement — a crashed session can never wedge a
  task.
- **Claimed-aware injection**: other sessions render it as
  `[todo:in_progress@TUIAgent@04aaac87]` — visible, not actionable; `always`
  mode stops pushing it.
- **Surface**: one `claim(id) -> bool` tool + one guide line ("claim before
  acting; False means another session has it"); `update_memory(id,
  status="OPEN")` releases. Store cost: `claimed_by`/`claimed_at` columns
  (SCHEMA_VERSION 3), since a compare-and-set needs real columns.
- **Interim guidance while deferred**: keep `inject_open_todos="relevant"`
  (the default), avoid two concurrent same-role sessions in one working dir
  when actionable todos are in play; `intent` shares the same hazard.

Also deferred (unchanged): `nemo oo memory merge` / `gc` utilities, an
optional human-friendly session counter ("session #17") as a display alias,
and the user dimension for multi-user stores.

## 6. Open questions



1. **Multi-user shared stores** — `.nemo_oo/memory/memory.sqlite` is per
   checkout and gitignored, so "shared" today means *across sessions and
   agents on one machine*. If a team ever shares a store (network volume),
   owner needs a user dimension (`elad/planner`); out of scope until real.
2. **Should `project` become the global default (`tui.memory: project`)?**
   Deferred to the end of the dogfooding period, per the plan's exit
   questions.
3. **Session-store garbage collection** — silos accumulate under
   `.nemo_oo/sessions/`; `delete_session` cleans its own, but abandoned ones
   linger. A `nemo oo memory gc` sibling of the merge utility (P2).
