# Issue 192 — Silence summarizer private-helper spans

Issue: https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/192

## Problem

The OpenInference tracing exporter records spans for every `@hidden` private helper on `SummarizationAgent` and its subclasses. These helpers are pure-Python event-handler glue with no LLM calls, but they fire on every turn and drown out the spans an agent author actually wants to inspect.

`@hidden` only suppresses the method from `doc(self)` / LLM API view; it does **not** disable tracing. Per CLAUDE.md (and `_should_trace` in `src/nemo_oo_agents/metaclass.py:132-155`), private and dunder methods are traced by default when `_enable_tracing = True` unless explicitly marked `@no_trace`.

## Fix (targeted, smallest blast radius)

Decorate every non-generation `@hidden` helper in `src/nemo_oo_agents/agents/summarization.py` with `@no_trace`. Leave `summarize()` (the only true LLM call) traced.

For the helpers being changed here, decorator order between `@hidden` and `@no_trace` does not matter: both write metadata onto the function (`agentdoc/_visibility.py:51-62` sets `_agentdoc_hidden`; `metaclass.py:259-289` sets `_no_trace`) and neither produces a wrapper at decoration time. (Note: this is *not* generally true — `@no_trace` combined with `@strategy` does interact with an existing wrapper's `_tracing_enabled[0]` flag, per `metaclass.py:281-288`. That path is not exercised by this change because none of the helpers we are touching use `@strategy`.)

Out of scope: the broader question of whether `@hidden` should *imply* `@no_trace` framework-wide. The issue's acceptance criterion asks for a decision recorded here; recommendation below.

## Methods to decorate

`SummarizationAgent` (base):
1. `_install` (line 148)
2. `_uninstall` (line 161)
3. `_handle_before_turn` (line 178)
4. `_handle_after_turn` (line 183)
5. `_should_summarize` (line 220) — base stub
6. `_compute_range` (line 234) — base stub
7. `_schedule_summarization` (line 290)
8. `_run_summarization` (line 310) — async
9. `_apply_pending_summary` (line 327)
10. `_get_events_in_range` (line 362)
11. `_render_range_to_markdown` (line 394)

`TokenBudgetSummarizer`:
12. `_should_summarize` (line 513)
13. `_compute_range` (line 529)

`MethodSummarizer`:
14. `_should_summarize` (line 583)
15. `_compute_range` (line 595)
16. `_is_root_call` (line 629)

Generation method that **must remain traced**: `summarize()` at line 262.

## Implementation steps

1. In `src/nemo_oo_agents/agents/summarization.py`:
   - Add `from nemo_oo_agents.metaclass import no_trace` (matches the existing per-module import style — `Agent` from `nemo_oo_agents.agent`, `hidden` from `nemo_oo_agents.agentdoc`).
   - Decorate every method listed above with `@no_trace`, immediately after `@hidden`.

2. Add a regression test in `tests/agents/test_summarization_agents.py` (or a new `tests/agents/test_summarization_tracing.py` if cleaner) that:
   - Iterates each class's own `__dict__` filtered to `inspect.isfunction(...)` to discover methods (not annotated fields).
   - For every `@hidden` *method* on `SummarizationAgent`, `TokenBudgetSummarizer`, and `MethodSummarizer`: assert `getattr(cls.method, "_no_trace", False) is True`. **No `_original` indirection** — because `@no_trace` causes `_should_trace` to return False, the metaclass never wraps these methods (see `metaclass.py:87-94` for async, `106-109` for sync), so the class attribute *is* the original function.
   - For `summarize()`: it goes through `@strategy`, which *does* wrap. Assert `getattr(cls.summarize._original, "_no_trace", False) is False`, and that `cls.summarize._tracing_enabled[0] is True` (the runtime flag that survives the wrapper).
   - Use `is_hidden_method` from `nemo_oo_agents.agentdoc._visibility` to detect `@hidden`.

3. Run the focused tests, then the full test suite, then ruff lint.

## Test plan

- `uv run pytest tests/agents/ -x -q`
- `uv run pytest -x -q` (broader smoke)
- `uv run ruff check src/nemo_oo_agents/agents/summarization.py tests/agents/`

## Acceptance criteria mapping

- [x] Zero spans for the listed `_*` helpers — guaranteed because `_should_trace` returns False for any method with `_no_trace`, and the sync/async wrappers are only attached when `_should_trace` returns True (`metaclass.py:78-109`).
- [x] `summarize()` still produces a span — it remains undecorated by `@no_trace`; generation methods are traced by default (`metaclass.py:82-83`).
- [x] Existing summarization tests still pass — pure metadata change; no behavior change.
- [x] Decision recorded on `@hidden` ⇒ `@no_trace` framework-wide.

## Recommendation on `@hidden` ⇒ `@no_trace` (framework-wide)

**Recommendation: do NOT couple them.** They answer different questions:

- `@hidden`: should the LLM see this method via `doc(self)`? (visibility/API surface)
- `@no_trace`: should this method produce a tracing span? (observability)

A hidden helper *can* be interesting in a trace (e.g. it executes complex deterministic logic the developer wants to inspect). Conversely, a public method can be a thin orchestrator the developer doesn't care to see as a span. Coupling them would force users to choose between LLM-visibility and trace-visibility, which the two-decorator design correctly keeps orthogonal.

The right ergonomic fix, if any, is a class-level toggle (e.g. `_trace_hidden_methods: bool = False` opt-out per class) — out of scope here. For now, per-method `@no_trace` on noisy infrastructure helpers is the right blast radius.
