# Memory System — Addendum: the Agent-Facing Contract

> **Status:** APPLIED (commit `fix(memory): agent-facing contract…`, branch
> `feat/memory-todo-owner-refs-observability`).
> **Source:** [`agent-facing-memory-description-analysis.md`](./agent-facing-memory-description-analysis.md)
> — written by a **live oo-tui agent dogfooding the memory system** while this
> MR was mid-flight (around M2). This addendum records the disposition of each
> finding and the drift-prevention mechanism that keeps the contract honest.

The analysis found that the agent-facing contract was split across three
surfaces that disagreed: the injected `MEMORY_SCHEMA_GUIDE`, the `MemorySkill`
docstring, and the actual tool methods. Its P0 was real and serious: **the
guide told skill-mounted agents to call `self.remember(...)`, which does not
exist on their surface** (`self.memory.remember`). An agent following the
injected instructions literally would fail on its first memory write.

## Disposition of the findings

| # | Finding | Disposition |
|---|---------|-------------|
| 1 | Guide uses `self.*` but the skill surface is `self.memory.*` (**P0**) | **Fixed structurally.** The guide is a template rendered per host: `MemoryConfig.api_prefix` = `"self."` for the mixin install; `MemorySkill` sets `"self.memory."`. One source of truth — the wrong namespace can no longer be *written*, and an anti-drift test makes sure it can no longer be *shipped* (see below). The skill's duplicate inject/delete path was removed; the manager owns injection for both hosts. |
| 2 | "Other systems extract memories behind your back" vs the write-on-event/episode hooks | **Fixed.** The guide now says the runtime auto-writes a few operational memories and instructs the agent to *curate those like its own*. |
| 3 | `search()` documented as "keyword" but implemented as graphless recall | **Fixed.** Docstrings + guide now say "term-focused recall (dense + keyword retrieval, graph spread disabled)". A true keyword-only tool was deliberately **not** added (essentials only; revisit if dogfooding shows demand). |
| 4 | Injected memories omit ids → agent can't update/forget them | **Fixed, plus ergonomics.** Injected/recalled lines render `[type#a1b2c3d4]`; `update_memory`/`forget`/`associate` accept the unique 8-char prefix (`store.resolve_id` — **raises** on an ambiguous prefix, never guesses; <6 chars never matches). `memory:` references resolve prefixes too (ambiguous foreign refs degrade to DANGLING, never raise at read time). |
| 5 | Tool API exposes a subset of the schema while the guide says "set the fields deliberately" | **Prompt narrowed; API deliberately kept small.** The guide now enumerates exactly what the agent sets: `type, importance, tags, title, references,` and (todos) `status`. Already widened by this MR relative to the analysis snapshot: `status` (M2) and `references` (M4). `owner` is *stamped*, never agent-settable (identity, M3). `remember_full(...)` with `trigger/valid_from/valid_to/entities/related_files` is **deferred** — dogfooding decides whether agents actually need those fields before we grow the surface. |
| 6 | `todo` missing from the guide | **Already fixed by M2** (the analysis ran mid-flight): the guide documents `todo`, its lifecycle, and `update_memory(id, status="DONE")`. |
| 7 | Relation names invisible; unknown relations silently became `related` | **Fixed ×2.** The guide + `associate()` docstring list the full `EdgeType` vocabulary, and the tool boundary now **raises** on an unknown relation (consistent with `to_status`/`to_numeric`; the old fallback test now asserts the raise). |
| 8 | Dedup-on-write surprising (returns an existing id) | **Fixed.** Guide: "A near-duplicate write reinforces the existing memory and returns ITS id — if your new wording is sharper, follow up with `update_memory(id, content=...)`." |
| 9 | Store scope/persistence invisible ("persistent" over-promises) | **Fixed.** The rendered guide states the store path and writer identity (`Store: … (your identity: …)`) — known at install time. Session vs project scope is a TUI setting (M7); `stats()` carries counters. A full `status()` introspection method is **deferred** (the `/memories` explorer + viewer Dashboard now expose runtime state to humans; the guide line covers the agent's need). |
| 10 | "Recall before acting" too vague | **Fixed (compactly).** Guide: recall before work touching prior decisions/preferences/plans/files; skip for one-offs; `search` for known terms; injected memories are hints, not ground truth. Kept to three lines — the guide is paid for on every session. |
| 11 | Docs mix the mixin API (`self.*`) and skill API (`self.memory.*`) | **Convention recorded here:** user/TUI-facing docs say `self.memory.*`; `self.*` appears only in mixin/implementation contexts, stated explicitly. The rendered guide is per-host correct by construction. A full sweep of older docs (`memory/README.md`, `results.md`) is folded into the MR's normal doc pass rather than a separate change. |

## Why tests didn't catch the P0 (post-mortem)

Every test agent was a **mixin install** (`class MemAgent(MemoryToolsMixin,
Agent)`), where `self.remember` genuinely exists — so all behavior tests
passed. The guide's *text* was asserted for content ("does it mention
CRITICAL"), never for **executability against the host it was injected into**.
Prompt text is data: nothing fails when documentation lies. The skill path was
round-trip tested (`skill.remember(...)` works), but no test connected surface
A (the injected words) to surface B (the host's namespace). It took the actual
consumer — a live agent reading the prompt and calling what it says — to hit
the gap. That is precisely what the dogfooding phase exists to find.

## The drift-prevention mechanism

Two layers, both structural rather than procedural:

1. **Single rendered source of truth.** There is exactly one guide template;
   the tool prefix, store path, and identity are *rendered from the live
   config/manager* at install. The skill no longer carries its own copy of the
   instructions. A future host (new prefix, new mount point) sets
   `api_prefix` and gets a correct guide for free.
2. **The executability test** (`tests/memory/test_memory_contract.py`):
   every `` `self.*(` `` / `` `self.memory.*(` `` call mentioned in the
   *rendered* guide is extracted by regex and asserted to be a callable on the
   host it was rendered for — for **both** hosts — plus every name in
   `MemoryConfig.tools` must exist on the mixin. Adding a method to the guide
   without implementing it (or removing/renaming a method the guide still
   mentions) fails CI. The same file pins the id-prefix round-trip, the
   ambiguity raise, and the store/identity disclosure.

The third layer is not a test: **keep dogfooding**. The analysis that produced
this addendum is the `explain()`/friction-log loop from the plan's §7.3 working
as designed — agent-facing affordance gaps (missing ids, unexplained dedup) are
invisible to unit tests because they are about what the *consumer can discover*,
not what the code does. Wrong-feeling agent interactions should keep landing in
`docs/design/memory-system/` as analyses like the source document.

## Deferred (revisit after the dogfooding period)

- `remember_full(...)` exposing `trigger`, `valid_from/valid_to`, `entities`,
  `related_files` — grow the surface only on demonstrated need.
- A dedicated pure-keyword search tool (`store.keyword_search` pass-through).
- `self.memory.status()` runtime introspection (config + policies + backend).
- Sweep of pre-MR docs that show `self.*` calls in skill-user contexts.
