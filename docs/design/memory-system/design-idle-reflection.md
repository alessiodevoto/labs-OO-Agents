# Memory System — Design: Idle Reflection (`/reflection on|off`)

> **Status:** DESIGN (next TUI feature on top of the memory MR,
> branch `feat/memory-todo-owner-refs-observability`).
> **Companions:** [`plan-todo-identity-references-observability.md`](./plan-todo-identity-references-observability.md)
> (M5 observability substrate, M6 `/memories`, M8 viewer Dashboard) ·
> [`design.md`](./design.md) §4.2.4 (reflection ops) ·
> [`results.md`](./results.md) §2 (when reflection helps/hurts).

---

## 0. Goal in one paragraph

A TUI mode, toggled with **`/reflection on|off`**, in which the agent
consolidates its long-term memory **while the user isn't looking**: reflection
starts after a response completes *and only if memory changed since the last
consolidation*, runs asynchronously without blocking anything, is **interrupted
promptly and safely the moment new input arrives**, shows a gliding indicator
while active, and leaves an audit trail — runtime events, a dedicated trace
span, and a "last reflections" summary in both the `/memories` explorer and the
web viewer. This turns the v1 finding that *inline* post-task reflection is a
mixed bag (`results.md` §2: helps under retrieval bottleneck, hurts pinpoint
recall, always costs latency) into a free-lunch schedule: consolidation happens
in human think-time, like its biological namesake — sleep.

## 1. What exists today (anchors)

| Piece | Where | Relevance |
|---|---|---|
| Reflection ops | `memory/reflection.py` — `consolidate()` runs 6 discrete, per-item-committed ops: `_merge_duplicates` → `_reconsolidate` → `_form_edges` → `_rescore_importance` → `_abstract` → `prune()` | Already step-wise: each op loops over memories/clusters and commits per item — an interruption between items leaves a consistent store *by construction* |
| Background precedent | `manager.py` `_reflect_middleware` + `_async_reflect`: `ReflectionPolicy.background=True` already runs `consolidate()` in `run_in_executor` against the shared store, with `_pending` task tracking + cancel-on-uninstall | The concurrency model (executor thread + shared WAL store + per-item commits) is shipped and tested; idle reflection extends it rather than inventing a new one |
| Trigger today | `ReflectionPolicy.trigger: "post_task" \| "manual"`; TUI installs default → reflects **inline after every top-level call** | Idle mode must *replace* post_task in the TUI (set `trigger="manual"`), or every prompt pays the consolidation latency twice |
| Audit substrate (M5) | `store.maintenance_log` (+ `maintenance_history()`), `ReflectionCompleted` event, tracing bridge (`memory.reflected` span event), `MemoryStats.reflections` | The summary surfaces already read this — idle reflection only needs to *write richer rows* |
| Surfaces (M6/M8) | `/memories` explorer header + detail; viewer Dashboard already renders `maintenance` history | Add one "last reflection" line + an `interrupted` badge |
| TUI status line | `tui_application.py` owns the status bar; agent-activity spinner ticks via app invalidation | The gliding indicator is one more status segment on the existing repaint cadence |
| Dirty signal | `MemoryWritten` events (`op=add/update/forget`) via `manager._emit` | The "memory changed" gate subscribes to what already fires |

## 2. UX specification

### 2.1 The command

```
/reflection on       # enable idle reflection for this agent (persisted)
/reflection off      # disable (also cancels a run in progress)
/reflection          # status: enabled?, last run summary, dirty count
```

Persistence mirrors `/memory`: a `tui.reflection` bool (+ per-agent
`tui.reflection_agents` override map) written to the project `config.toml` via
the existing `_set_toml_table_value` path (`commands.py:1131-1170`).
`/reflection on` requires memory to be attached (`required_capabilities =
frozenset({"memory"})`); enabling it flips the installed manager's policy to
`trigger="manual"` so the middleware path never double-reflects.

### 2.2 The gliding indicator

While a reflection run is active, the status bar shows an animated segment:

```
✦ reflecting ▁▂▃▅▃▂▁      (a small wave gliding right on each tick)
```

- Rendered by the existing status-line composer; animation advances one frame
  per repaint tick (~150 ms), reusing the agent-activity ticker — **no new
  timer** when the spinner infrastructure already invalidates the app.
- Disappears the moment the run finishes or is interrupted; on interruption it
  flashes one final frame `✦ reflection interrupted` for a single tick (no
  lingering UI).
- Never shown while the agent itself is responding (reflection only runs when
  the agent is otherwise idle, §3).

### 2.3 The summaries

- **`/memories` explorer**: the header's KPI line gains
  `last reflection: 2m ago — merged 3, +5 edges, pruned 1 (idle, 1.4s)`;
  interrupted runs render `… (idle, interrupted @ form_edges, 0.3s)`.
- **Web viewer Dashboard**: the existing maintenance table gains the new
  columns (`trigger`, `interrupted`, `duration_ms`, `phases_completed`) — the
  payload already flows through `/api/memory/stats`.
- **Tracer**: each idle run is a real span (§6) — visible on the session
  timeline between turns, with the report as attributes.

## 3. Lifecycle — one state machine, owned by the TUI

```
                +--------------------- new input arrives ----------------------+
                v                                                              |
  IDLE --(response done)--> ELIGIBLE? --(dirty>0 && enabled)--> DEBOUNCE --> RUNNING
                |                |                                 |            |
                |                +-- not dirty / disabled --> IDLE +--(input)-->+--> INTERRUPTING --> IDLE
                |                                                               |
                +---------------------------------------------- COMPLETED --> IDLE
```

A single owner object, **`ReflectionRunner`** (new,
`nemo_oo_agents_cli/tui/reflection_runner.py`), holds the entire state:
`_task: asyncio.Task | None`, `_stop: threading.Event`, `_state`, and the
last-report cache for the status command. It is created by the session when
memory is configured and torn down with it.

**Start rule (the feature list, made precise):**

1. *"after response is over"* — the session signals the runner from the same
   place it returns the prompt to the user (agent turn finished, input queue
   empty). Any pending agent work (queued system messages, keep-going
   continuations, background producers about to dispatch) counts as "not
   idle" — the runner asks the queue manager before starting.
2. *"after the memory has changed"* — the runner subscribes to `MemoryWritten`
   (`em.on("MemoryWritten", ...)`) and keeps a **dirty counter**; add/update/
   forget/reinforce all count. `reflect*` resets it. No dirt → no run. (This
   is a subscription on the reader side, not a new manager field — the events
   already fire for every mutation path including write-on-event.)
3. **Debounce (default 10 s, configurable)** — a pause between "response
   done" and start, so a user typing a follow-up never sees a start/interrupt
   churn. Arriving input during DEBOUNCE cancels silently (no indicator ever
   shown). `tui.reflection_debounce_s`, settable in `settings.yaml` (§8).

**Stop rule:** the input dispatch path (the same place the TUI enqueues a user
prompt onto the agent, plus `/clear`, `/switch`, session exit, `/reflection
off`, and memory reconfiguration) calls `runner.interrupt()`:

```python
async def interrupt(self, grace: float | None = None) -> None:
    grace = self._config.reflection_grace_s if grace is None else grace
    self._stop.set()                       # per-item checks observe this (§4)
    if self._task is not None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._task), grace)
    # proceed regardless: per-item commits make overlap safe (§5)
```

The user's prompt is **never** held hostage: after `grace`
(`tui.reflection_grace_s`, default 0.5 s) the prompt dispatches even if the
reflection thread is still finishing its current item.

## 4. Interruptible consolidation (the memory-core change)

`ReflectionEngine.consolidate()` stays as-is (benches, manual `reflect()`).
One new engine entry point threads a stop probe through every loop:

```python
def consolidate_interruptible(
    self, *, should_stop: Callable[[], bool],
    reasoner=None, reconciler=None,
) -> ReflectionReport:
```

- Every op checks `should_stop()` **between items** (anchors: the `for m in
  anchors:` loop in `_merge_duplicates`, the cluster loop in `_reconsolidate`,
  the per-memory loops in `_form_edges` / `_rescore_importance`, the
  per-abstraction loop in `_abstract`, the prune loop in
  `ForgettingEngine.prune`) and **between ops**. On stop it returns immediately
  with what was already committed.
- LLM steps (`reasoner`/`reconciler`) additionally check `should_stop()`
  **before** each call and skip the remaining generative work when stopping —
  an in-flight LLM call is not killed mid-request; its result is discarded
  (nothing is committed after the stop flag is observed). This bounds
  cancellation latency at *one deterministic item* (µs–ms) in the default
  config, and at most *one already-started LLM call* when a reasoner is wired.
- `ReflectionReport` gains `interrupted: bool = False`,
  `stopped_in: str | None` (op name), `duration_ms: float = 0.0` — additive,
  every existing consumer keeps working.
- Implementation shape: rather than duplicating the six ops, each op takes an
  optional `should_stop` parameter defaulting to `lambda: False`;
  `consolidate()` calls them with the default. Net-new logic is one `if
  should_stop(): break` per loop plus the orchestrator (~40 lines, no
  duplication).

`MemoryManager` gains the thin wrapper the runner calls (mirrors `reflect()`
including stats/events/maintenance/logging):

```python
def reflect_interruptible(self, should_stop, *, trigger: str = "idle") -> ReflectionReport
```

## 4b. The generative phase — IMPLEMENTED (on by default)

When `tui.reflection_generative: true` (the default) and reflection is on, an
idle run is a two-phase pipeline, all inside the same executor call and span:

```
phase 1: EPISODE   — the LLM summarizes the recent session window (last ~30
                     events, gathered on the agent loop) into one episode
                     record; "not noteworthy" -> no write. The runner's own
                     write is excluded from the dirty counter (no self-
                     retrigger).
phase 2: CONSOLIDATE — reflect_interruptible with the LLM reconciler
                     (paraphrase/stale clusters at cos >= 0.6, capped at
                     max_clusters_per_reflection per run) and the LLM
                     reasoner — which sees the fresh episode in the SAME pass.
```

All three callables (`llm_episode_writer`, `llm_reconciler`, `llm_reasoner` —
`memory/generative.py`) bind the session model lazily (`/model` switches apply
to the next run), honor the stop flag before every LLM call, and are
containment-wrapped: malformed output or a failed episode write skips that
item and never blocks consolidation. Headline regression: the real dogfooding
cluster (three "Reflection harness duplicate" memories, tags verbatim,
pairwise hashing cosine ~0.7) consolidates to one record with REFINES
provenance.

## 5. Concurrency analysis (why this is safe)

- **Precedent:** `ReflectionPolicy.background=True` already runs the same ops
  in `run_in_executor` against the shared store while the agent may act. Idle
  reflection uses the identical execution vehicle (executor thread off the
  agent's loop, driven by `ReflectionRunner`), so no new store-sharing mode is
  introduced.
- **Consistency:** every op commits per item; there is no long transaction to
  roll back. Interruption between items — or even a 500 ms overlap with the
  next turn's first recall — leaves the store valid; at worst the next idle
  window re-derives the remaining merges (the ops are idempotent over an
  already-consolidated store: cosine ≥ threshold pairs that were merged are
  archived and drop out of the candidate set).
- **Own-scope discipline (M3):** the engine only ever touches the installed
  owner's + unowned memories, so an idle reflection on a shared project store
  cannot disturb another agent's records mid-run.
- **Vector-index staleness:** same-process, same `MemoryStore` instance —
  the numpy index is updated by the ops themselves; no `data_version` concern.
- **Dirty-during-run:** writes that land while a run executes re-increment the
  dirty counter *after* the runner snapshots it at start; the next idle window
  picks them up. No run-forever loop: a run never re-triggers itself
  (`ReflectionCompleted` does not count as dirt).

## 6. Events, tracing, and the audit trail

New runtime events (`monitoring.py`, `RUNTIME_EVENT` role, bridged like the
existing four):

| Event | Fields | Span event |
|---|---|---|
| `ReflectionStarted` | `trigger` ("idle"/"manual"/"post_task"), `dirty` | `memory.reflection_started` |
| `ReflectionCompleted` *(existing, extended)* | + `trigger`, `interrupted`, `stopped_in`, `duration_ms` | `memory.reflected` *(existing)* |

**The tracer gap and its fix:** the M5 bridge attaches span events to the
*current* span — but idle reflection runs between turns, when **no span is
active**, so its events would vanish. The runner therefore opens its own span
when tracing is available:

```python
with tracer.start_as_current_span("memory.reflection",
        attributes={"memory.trigger": "idle", "memory.owner": ..., "memory.db_path": ...}):
    report = manager.reflect_interruptible(stop, trigger="idle")
```

The existing bridge then lands `memory.reflected` (with the full report) inside
it, and the run shows up on the session timeline in the viewer — feature (5)'s
"summary in the tracer" for free. Without opentelemetry this degrades to a
no-op exactly like the bridge does.

**maintenance_log:** `reflect_interruptible` writes the same `kind="reflect"`
row with the extended report (`trigger`, `interrupted`, `stopped_in`,
`duration_ms` ride inside the JSON — no store schema change). `/memories` and
the viewer Dashboard read it as they already do.

## 7. TUI wiring (files and responsibilities)

| Piece | File | Change |
|---|---|---|
| `ReflectionRunner` | `tui/reflection_runner.py` (new) | The §3 state machine: debounce, start gate (enabled ∧ dirty ∧ agent-idle), executor run via `agent_run_async`, `interrupt()`, last-report cache, indicator state |
| Idle signal | `tui/session.py` | Call `runner.on_response_done()` where the session hands the prompt back to the user; call `await runner.interrupt()` at the head of user-input dispatch, `/clear`, `/switch` (`_do_swap`), and shutdown |
| Command | `tui/commands.py` | `ReflectionCommand` (`/reflection [on\|off]`), `required_capabilities={"memory"}`, persisted via the `/memory` pattern; `on` remaps the manager policy to `trigger="manual"`; registered in `_command_classes` |
| Config | `tui/config.py` | `reflection: bool = False`, `reflection_agents: dict[str, bool]`, `reflection_debounce_s: float = 10.0`, `reflection_grace_s: float = 0.5` — all TUIConfig fields, hence settable via layered `settings.yaml` (user → project → `NEMO_OO_SETTINGS`) |
| Bootstrap | `tui/bootstrap.py` | When reflection is enabled for the agent, build `MemoryConfig` with `reflection=ReflectionPolicy(trigger="manual")`; construct the runner next to `configure_tui_memory` and re-wire it on session swap |
| Indicator | `tui/tui_application.py` | One status-bar segment reading `runner.indicator_frame()`; advances on the existing repaint tick; empty string when inactive |
| `/memories` header | `tui/memory_explorer.py` | Append the `last reflection: …` line from `store.maintenance_history(1)` |

The runner talks to memory **only through the manager** (`agent.memory._mgr`),
on the agent thread's loop, using the same `agent_run/agent_run_async` bridge
every other memory touchpoint uses.

## 8. Configuration surface (complete)

| Knob | Where | Default | Meaning |
|---|---|---|---|
| `tui.reflection` / `tui.reflection_agents` | TUIConfig; toggled by `/reflection`, persisted to the project `config.toml` (the `/memory` pattern) | `false` | The idle-reflection switch, global + per-agent |
| `tui.reflection_debounce_s` | TUIConfig field → settable in layered `settings.yaml` under `tui:` | `10.0` | Idle pause after a response before a run starts |
| `tui.reflection_grace_s` | TUIConfig field → settable in layered `settings.yaml` under `tui:` | `0.5` | Max wait for a run to stop before a new prompt dispatches anyway |
| `ReflectionPolicy.trigger="manual"` | set by bootstrap when idle mode is on | — | Prevents double reflection via the post_task middleware |
| (unchanged) `ReflectionPolicy.*` thresholds | `MemoryConfig` | v1 defaults | What consolidation does remains policy-configured |

No new memory-core config: interruptibility is an argument, not a mode.

## 9. Test plan

**Engine (`tests/memory/test_memory_reflection_interrupt.py`, offline):**
- `should_stop` firing after N probe calls stops within one item: returned
  report has `interrupted=True`, `stopped_in` names the op, and the store is
  consistent (no dangling edges to archived rows; counts match the partial
  report).
- Idempotent resume: run interrupted at each op boundary (parametrized), then
  run `consolidate()` to completion — final store state equals the
  never-interrupted baseline (canonical: same active contents/edges).
- `should_stop=lambda: False` produces a report identical to `consolidate()`
  (byte-for-byte compatibility for the default path).
- Reasoner gating: a reasoner that records invocations is never called after
  the stop flag is set; nothing it returned post-stop is committed.

**Manager:** `reflect_interruptible` emits Started + Completed(with flags),
writes the extended maintenance row, resets the runner-visible dirty signal
semantics (`ReflectionCompleted` is not dirt), and updates stats.

**Runner (TUI, `tests/cli/test_reflection_runner.py`, fake clock/agent):**
- Start gate truth table: {enabled, dirty, agent-idle} — runs only when all
  three hold; debounce cancellation on input during DEBOUNCE never sets the
  indicator.
- Interrupt latency: with a slow synthetic op (item sleep 50 ms ×100),
  `interrupt()` returns within grace and the run reports `interrupted=True`;
  the store is consistent after.
- Input never blocked: dispatching a prompt while a (synthetic, stuck-item)
  run refuses to stop still proceeds after `grace` — asserted with timestamps.
- Lifecycle: `/reflection off` mid-run cancels; `/clear`, session swap, and
  detach all tear the runner down without leaking the executor task
  (`_pending`-style tracking, cancel-on-uninstall like the v1 precedent).
- Indicator: frames advance while RUNNING, empty when IDLE, single
  "interrupted" flash frame on cancel.
- Command: `/reflection on|off|status` round-trip + config.toml persistence +
  `trigger="manual"` remap asserted on the installed manager.

**Tracing:** with the in-memory OTel exporter (M5 test pattern): an idle run
creates a `memory.reflection` span containing a `memory.reflected` event whose
attributes carry `interrupted`/`trigger` — and *without* a runner-opened span,
the same events would be dropped (regression-pins the §6 gap).

**Surfaces:** `/memories` header renders the last-reflection line from a seeded
maintenance row (incl. the interrupted variant); viewer Dashboard columns
appear (extend `tests/viewer/test_memory_routes.py` fixture with an
interrupted row).

**End-to-end stress (offline, `examples/memory_bench/` or a test):** loop 50×
{write 5 memories → start idle reflection → interrupt after random 0–20 ms} →
assert the store never corrupts (all rows readable, `user_version` intact,
edge targets exist among rows), dirty converges to 0 after one uninterrupted
pass, and `maintenance_history` shows the mix of completed/interrupted rows.

**Dogfooding acceptance:** `/reflection on` in a real session; watch the
indicator appear ~2 s after a response and vanish instantly on typing; confirm
the `/memories` header line, the Dashboard rows, and the reflection spans on
the trace timeline. Friction log anything that feels laggy — the two latency
knobs (`tui.reflection_debounce_s`, `tui.reflection_grace_s`) are the tunable.

## 10. Risks & open questions

1. **Reflection can still hurt pinpoint recall** (`results.md` §2) — idle mode
   removes the *latency* cost, not the *abstraction* cost. Defaults stay
   conservative (deterministic ops only; no reasoner unless wired). Dogfood
   verdict decides whether idle mode defaults to on eventually.
2. **LLM-reasoner cancellation latency** — bounded at one in-flight call;
   if dogfooding wires a reasoner and the bound annoys, the next step is
   passing the stop event into the reasoner for request-level cancellation
   (out of scope here).
3. **Indicator real estate** — the status bar is shared; if a long agent name
   squeezes it, the segment degrades to a single glyph `✦`.
4. **Battery/CPU on huge stores** — a full pass over 10⁵ memories every idle
   window is wasteful even at per-item speed; if dogfooding shows it, add a
   dirty-threshold gate (`start only when dirty ≥ K`) — one constant, not a
   redesign.
5. **Interaction with `inject_open_todos="always"`** — reflection re-scoring
   can promote todos; harmless, but the dashboard's injected→used rate is the
   thing to watch for feedback loops.
