# Agent-facing memory description analysis

## Summary

The current memory system is implemented as a `MemorySkill` mounted at `self.memory`, but part of the agent-facing instruction block still describes the API as if memory methods are installed directly on the agent (`self.remember`, `self.recall`, etc.). In a TUI session with the skill active, those direct aliases are absent, so the injected guidance can lead the agent to call methods that do not exist.

The core memory implementation is usable, but the agent-facing contract is split across three surfaces that do not fully agree:

1. the injected `MEMORY_SCHEMA_GUIDE` in `src/nooa/memory/manager.py`,
2. the `MemorySkill` docstring in `src/nooa/memory/memory_skill/__init__.py`, and
3. the actual public methods inherited from `MemoryToolsMixin` and exposed on `self.memory`.

This report captures the misleading or missing pieces and suggests concrete changes.

## Evidence inspected

Relevant files and runtime observations:

- `src/nooa/memory/manager.py`
  - `MEMORY_SCHEMA_GUIDE` tells the agent to call `self.remember(...)`, `self.update_memory(...)`, `self.forget(...)`, `self.associate(...)`, and `self.recall(...)`.
  - `MemoryToolsMixin` defines the conscious memory API as `remember`, `update_memory`, `forget`, `recall`, `search`, and `associate`.
- `src/nooa/memory/memory_skill/__init__.py`
  - `MemorySkill` registers as `nemo.memory`, so the active runtime surface is `self.memory`.
  - Its class docstring correctly describes `self.memory.remember(...)`, `self.memory.recall(...)`, etc.
- Runtime session check:
  - `hasattr(self, "remember") == False`
  - `hasattr(self.memory, "remember") == True`
- `src/nooa/memory/schema.py`
  - The persisted `Memory` model contains additional fields such as `owner`, `status`, `related_files`, `chat_turn_ref`, `valid_from`, `valid_to`, `trigger`, `entities`, and `place_or_task` that are not exposed by the public tool methods.
- `src/nooa/memory/store.py`
  - `owner` and `status` are promoted SQL columns and are authoritative when rows are read back.
- `docs/design/memory-system/plan-todo-identity-references-observability.md`
  - Describes planned identity/reference/observability work, including TODO lifecycle, owner semantics, and “pass by reference” conventions.

## Findings

### 1. The injected prompt uses the wrong call surface

`MEMORY_SCHEMA_GUIDE` currently says:

```text
self.remember(...)
self.update_memory(...)
self.forget(...)
self.associate(...)
self.recall(...)
```

In the current skill-based installation, the agent actually receives a `MemorySkill` at `self.memory`. The working API is:

```python
self.memory.remember(...)
self.memory.recall(...)
self.memory.search(...)
self.memory.update_memory(...)
self.memory.forget(...)
self.memory.associate(...)
self.memory.reflect()
self.memory.stats()
```

This is the most important mismatch because it can directly prevent memory use. If the agent follows the injected memory prompt literally, it will call missing methods.

#### Recommendation

Pick one of these and make all surfaces agree:

- **Preferred:** update `MEMORY_SCHEMA_GUIDE` to consistently use `self.memory.*` because that is the skill-facing API in TUI sessions.
- **Alternative:** install compatibility aliases on the agent (`self.remember`, `self.recall`, etc.) when `MemorySkill` attaches, then document both forms. This is convenient but increases namespace collision risk and makes the skill less explicit.

The least surprising fix is to make the guide say `self.memory.*` everywhere.

### 2. The system prompt says “Other systems extract memories behind your back” but the code also auto-writes events

The injected guide says:

```text
Other systems extract memories for the agent behind its back; here, you decide what to keep...
```

This sentence is intended to contrast this design with automatic harness extraction. However, the implementation still has write-on-event hooks:

- `WritePolicy.on_events = ("Notification", "Error")`
- `_on_write_event()` calls `self.remember(...)` internally for those events
- post-task reflection can write an episode via `_write_episode()`

So the agent is the main conscious curator, but not the only writer. The statement is directionally true about design philosophy, but misleading as an operational description.

#### Recommendation

Clarify the distinction:

> You own conscious memory curation. The runtime may also write limited operational memories for configured events and task episodes; treat those as system-authored entries that you may refine or forget when they become inaccurate.

This tells the agent to expect auto-written memories and to curate them instead of assuming every memory was deliberately authored.

### 3. `search()` is described as keyword search, but it is implemented as graphless recall

The `MemorySkill` docstring says:

```text
self.memory.search(query) (keyword)
```

`MemoryToolsMixin.search()` is implemented as:

```python
return mem.recall(query, k=k, hops=0)
```

That path still uses dense embedding retrieval plus sparse keyword candidates; it only disables graph spread. It is not a pure keyword search, and it does not expose exact-match or field-filter semantics.

#### Recommendation

Rename the description, not necessarily the method:

- `recall(query)`: associative recall using dense + sparse retrieval, scoring, and graph spread.
- `search(query)`: term-focused recall using dense + sparse retrieval with graph spread disabled.

If pure keyword search is desired, add a separate method such as `keyword_search(query, k=5)` or expose `MemoryStore.keyword_search()` through a tool with clear semantics.

### 4. Injected recalled memories omit memory IDs

Spontaneous recall injection formats memories like:

```text
- [info] MR plan document location
```

This is compact, but it withholds the ID. The agent cannot update, forget, or associate an injected memory without performing an explicit `recall()` or `search()` call to retrieve IDs.

#### Recommendation

Include stable references in the injected memory block, for example:

```text
- [info id=abc123] MR plan document location
```

or:

```text
- [info] MR plan document location (`abc123`)
```

The guide should also say: “Use the memory ID from recalled results when updating, forgetting, or associating memories.”

### 5. The public tool API exposes only a subset of the schema

The `Memory` model supports rich metadata:

- `owner`
- `status`
- `related_files`
- `chat_turn_ref`
- `valid_from` / `valid_to`
- `trigger`
- `entities`
- `place_or_task`
- `salience`
- `confidence`
- `mood`

The public `self.memory.remember()` tool only accepts:

```python
content, type, importance, tags, title
```

`update_memory()` only accepts:

```python
content, importance, type, tags
```

That means the agent-facing prompt cannot honestly tell the agent to “set fields deliberately” beyond type, importance, and tags. It also cannot directly author intent triggers, related files, owner, or TODO status through the documented tool surface.

#### Recommendation

Either narrow the prompt to match the exposed API or expand the API to match the schema.

A pragmatic split:

- Keep the simple public API for common use.
- Add an advanced method such as:

```python
self.memory.remember_full(
    content,
    *,
    type="info",
    importance="MEDIUM",
    salience=None,
    confidence=None,
    tags=None,
    entities=None,
    related_files=None,
    owner=None,
    status=None,
    trigger=None,
    valid_from=None,
    valid_to=None,
    title=None,
)
```

or add selected fields to `remember()` and `update_memory()` now, especially `related_files`, `owner`, `status`, `trigger`, and `entities`.

### 6. TODO/status semantics are not fully reflected in the injected guide

The plan document references TODO lifecycle and proposes calls such as `self.memory.remember(content, type="todo", ...)`. The schema/store currently include `status` plumbing, and the codebase appears to be evolving toward TODO memories.

The injected guide still lists only:

```text
info, skill, episode, intent, reflection
```

Depending on the current branch, the code may include `todo` in `MemoryType`, while the public docs and injected prompt do not mention it. If `todo` is accepted by the schema, the prompt is incomplete. If it is not accepted, the plan text is ahead of implementation.

#### Recommendation

Make the source of truth explicit:

- If TODO memory is implemented, add `todo` to `MEMORY_SCHEMA_GUIDE`, `MemorySkill.remember()` docstring, tests, and examples.
- If TODO memory is planned but not implemented, mark it clearly as future work in design docs and avoid showing `type="todo"` as a usable call.

Also expose `status` if TODO memories are intended to be agent-authored.

### 7. Valid edge relation names are not visible in the prompt

`associate(a_id, b_id, relation)` supports typed edges via `EdgeType`:

- `derived_from`
- `created_by`
- `supports`
- `contradicts`
- `refines`
- `related`
- `causes`
- `precedes`
- `part_of`
- `triggers`

The injected guide only says to link related memories with `associate(id_a, id_b, relation)`. It does not tell the agent which relation names are valid or when to use them.

The manager implementation falls back to `related` for invalid relation strings, which avoids crashes but can silently lose semantic information.

#### Recommendation

Document the allowed relation names in the injected guide or `associate()` docstring. Prefer rejecting invalid relation strings at the public boundary, or at least returning/logging which relation was actually written.

### 8. Deduplication behavior is underexplained

The guide says:

```text
Near-duplicate writes are auto-merged, so prefer writing over worrying about overlap.
```

Implementation detail: `remember()` checks nearest neighbors, and if a same-type memory exceeds `dedup_threshold`, it reinforces the existing row rather than creating a new one. It updates access/reinforcement and max importance/salience, but it does not merge tags/title/content from the new memory at write time.

This can surprise the agent: a call to `remember()` returns an ID, but the exact content it attempted to write may not be present if it matched an existing memory.

#### Recommendation

Clarify:

> `remember()` may return an existing memory ID when the new memory is a near-duplicate. If the new wording materially sharpens the memory, call `update_memory(id, content=..., tags=...)` after recall/search confirms the current content.

### 9. Store scope and persistence are not explained to the agent

The implementation supports configurable store paths. In this TUI runtime, the active memory DB is session-scoped under `.nooa/sessions/...-memory.db`. Other docs mention `.nooa/memory/memory.sqlite` as a default/project path.

For correct behavior, the agent should know whether a memory is session-local, project-local, or shared across agents/users. The current prompt only says “persistent long-term memory,” which may imply broader durability than the configured path provides.

#### Recommendation

Expose store scope in a small context block or `self.memory.stats()`/`self.memory.status()` method, e.g.:

```text
Memory store: session-scoped SQLite at .nooa/sessions/<session>-memory.db
```

The injected prompt should avoid overpromising and say “persistent according to the configured memory store.”

### 10. The prompt says “recall before acting,” but not when or how

The guide says:

```text
RECALL before acting — self.recall(query) to reuse what you already know.
```

This is directionally good but vague. It can lead to either over-recalling on trivial tasks or under-recalling on tasks that need prior context. The runtime also performs spontaneous recall injection, but the guide does not explain how to use injected memories versus explicit recall.

#### Recommendation

Add a short policy:

- Use injected recalled memories as hints, not ground truth.
- Explicitly call `self.memory.recall()` before work involving prior user preferences, project plans, prior bugs, stored file paths, or continuing work across turns.
- Use `self.memory.search()` when looking for a known term, file path, ID, or named plan.
- Do not recall for one-off general questions unless memory context is likely relevant.

### 11. The docs mix historical/core-agent API and skill API

Some design docs and examples still discuss direct methods such as `self.remember(...)`. That was accurate for `MemoryToolsMixin` mixed into an agent class, but it is misleading when memory is installed as a skill.

#### Recommendation

Adopt naming conventions in docs:

- Use `self.memory.*` for the skill-based TUI/user-facing API.
- Mention `MemoryToolsMixin` only in implementation/design sections and make clear that its methods live on the object that inherits it. In `MemorySkill`, that object is `self.memory`, not the agent.
- Add a migration note: older docs that say `self.remember(...)` refer to direct mixin installation; skill users should call `self.memory.remember(...)`.

## Proposed updated injected guide

A replacement for `MEMORY_SCHEMA_GUIDE` should be short but operationally accurate:

```markdown
## Your long-term memory

You have a persistent long-term memory via `self.memory`. You own conscious
curation: write durable, reusable knowledge, refine it when it changes, and
forget/archive memories that are wrong or obsolete. The runtime may also create
limited operational memories for configured events and task episodes; curate
those too when you encounter them.

Use:

- `self.memory.remember(content, type=..., importance=..., tags=[...], title=...)`
  to store one distilled, self-contained memory. Do not store raw transcripts.
- `self.memory.recall(query, k=5)` for associative recall using semantic,
  keyword, recency, importance, and graph signals.
- `self.memory.search(query, k=5)` for term-focused recall with graph spread
  disabled.
- `self.memory.update_memory(id, ...)` to sharpen or correct an existing memory.
- `self.memory.forget(id)` to archive an obsolete or wrong memory.
- `self.memory.associate(id_a, id_b, relation="related")` to link memories.
- `self.memory.reflect()` to manually consolidate memories when useful.
- `self.memory.stats()` to inspect memory usage counters.

Schema fields exposed by the simple tool API:

- `type`: `info`, `skill`, `episode`, `intent`, `reflection` [and `todo` if enabled]
- `importance`: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `TRIVIAL`
- `tags`: salient retrieval cues such as names, files, dates, project areas
- `title`: short human-readable heading

Relation names: `related`, `supports`, `contradicts`, `refines`, `derived_from`,
`created_by`, `causes`, `precedes`, `part_of`, `triggers`.

`remember()` may return an existing memory ID when a near-duplicate is found.
If the new information materially changes the memory, recall/search the ID and
update it explicitly.

Use injected recalled memories as hints. For updates, forgetting, and links, use
explicit recall/search results so you have stable memory IDs.
```

## Suggested implementation changes

### P0: Fix agent-facing API mismatch

- Change `MEMORY_SCHEMA_GUIDE` from `self.*` to `self.memory.*`.
- Update `src/nooa/memory/README.md`, `docs/design/memory-system/results.md`, and examples that are intended for skill users.
- Add a test that installs `MemorySkill` on a TUI-like agent and verifies the injected guide mentions `self.memory.remember` and not `self.remember`.

### P1: Add memory IDs to spontaneous recall context

- Change `_format_recall()` to include compact IDs.
- Consider including title plus a short content snippet for disambiguation.
- Add tests that injected recall output contains IDs usable by `update_memory()`.

### P1: Clarify `search()` semantics

- Update docstrings from “keyword” to “term-focused recall; graph spread disabled.”
- Optionally add a true keyword-only tool if exact sparse lookup matters.

### P1: Document relation names

- Add the `EdgeType` values to `associate()` docstring and the injected guide.
- Consider raising on invalid relations instead of silently falling back to `related`.

### P2: Align public API with schema or narrow the schema promised to agents

- Either expose advanced fields through `remember()`/`update_memory()` or stop implying that the agent can set the full schema deliberately.
- Highest-value fields to expose: `related_files`, `entities`, `owner`, `status`, `trigger`, `valid_from`, and `valid_to`.

### P2: Add memory status/introspection

Add a method such as:

```python
self.memory.status()
```

that returns:

- enabled/disabled
- store path and scope
- active tools
- embedding/vector backend
- spontaneous recall settings
- write-on-event policy
- reflection policy
- store size

This reduces the need to inspect code to understand runtime behavior.

### P2: Make TODO support unambiguous

- If `MemoryType.TODO` is implemented, document it and expose `status`.
- If it is still planned, remove or clearly label examples that present `type="todo"` as implemented.

## Conclusion

The memory system is functional, but the agent-facing description is currently inconsistent with the skill-based runtime. The most serious issue is the direct-method guidance (`self.remember`) in `MEMORY_SCHEMA_GUIDE`, because the active TUI surface is `self.memory.remember`. Fixing that prompt, adding IDs to recalled-memory injections, clarifying search/dedup/auto-write behavior, and aligning the public API with the schema would make the system much easier for agents to use correctly without code inspection.
