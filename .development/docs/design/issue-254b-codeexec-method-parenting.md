# Issue 254 (root cause) — nest method calls under the code_execution they ran in

## Problem

A method invoked from inside generated code (`self.submit()` during `execute_python`) is
parented in the OTel trace to the **enclosing agent method** (`generate_pocs`), not to the
`code_execution` span it actually ran inside. It therefore becomes a *sibling* of the long
`generation` span, and any faithful tree render bunches such calls after `generation`'s whole
subtree ("submit at the end"), even though they fired mid-execution.

## Root cause (verified)

`before_agent_call` (`_hooks_impl.py:157`) picks its parent span **solely** from
`parent_call_id` (nearest enclosing *agent-method* call, from the per-task agent call stack).
It never consults the active `code_execution` span — even though that span is in the same
per-task `_get_active_spans()` dict. Span parenting is a per-span-type patchwork (methods →
`parent_call_id`; generations → `agent_call_id`; code_execution → `generation_id`), all from one
original commit, with no deliberate rationale for excluding code_execution. Confirmed:
- **ATIF is decoupled** — `atif/exporter.py` nests via `parent_generation_id` (event field) and
  never reads OTel `parent_span_id`. So changing the OTel span parent does not affect ATIF.
- The concurrency design (per-task call-stack contextvar for `gather` isolation) explains *how*
  the parent is chosen, not *why* code_execution is excluded — the active code_execution span is
  also tracked per-task, so parenting under it is equally concurrency-safe.

## Fix

Track the currently-executing `code_execution` span in a **per-task `ContextVar`**, and have
`before_agent_call` parent a method span under it when the method was invoked **directly** from
that execution's code.

### Mechanism

1. New module-level contextvar in `_hooks_impl.py`:
   ```python
   _current_code_execution: ContextVar[tuple[Span, str | None] | None] =
       ContextVar("current_code_execution", default=None)
   ```
   Holds `(code_execution_span, owning_call_id)` where `owning_call_id` is the call_id of the
   agent whose generated code is running.

2. `before_code_execution`: after creating the span, capture
   `owning_call_id = _get_agent_call_stack()[-1]` (local import from
   `runtime.context_vars`; the stack top is the executing agent — pushed by
   `method_wrapper.py:149` before the body runs) and `token = _current_code_execution.set((span,
   owning_call_id))`. Return `token` in the hook context dict.

3. `after_code_execution`: `_current_code_execution.reset(token)` (guarded), restoring any outer
   execution — proper nesting for nested `execute_python`.

4. `before_agent_call`: after the existing `parent_span = spans_dict.get(parent_call_id)`, add:
   ```python
   active = _current_code_execution.get()
   if active is not None and active[1] is not None and active[1] == parent_call_id:
       parent_span = active[0]   # method called directly from this agent's executing code
   ```
   - Direct call (`submit`): `parent_call_id == owning_call_id` → nest under code_execution. ✅
   - Transitive call (`submit` → `_helper`): `parent_call_id == submit_id != owning_call_id`
     → unchanged, nests under `submit`. ✅
   - Parallel subagents launched from code (`gather`): each task inherits the contextvar; each
     subagent's top method nests under the spawning code_execution. ✅ (more accurate than today)

The `agent.parent_call_id` **attribute** is still set from `parent_call_id` (unchanged), so
event/ATIF semantics are preserved — only the OTel span tree parent changes.

## Why it's safe

- Per-task `ContextVar` → no cross-task leakage under `asyncio.gather` (same discipline as the
  existing `_agent_call_stack_var`).
- Only fires when a code_execution is active **and** the method is a direct child of the
  executing agent → normal nested method calls and non-exec calls are untouched (contextvar is
  `None` outside code execution).
- OTel allows an AGENT-kind span under a TOOL-kind span; it accurately models "code that called
  a method."

## Validation (before reverting the viewer experiments)

Build/keep a small agent that reproduces the real structure: an orchestrator whose generation
code (a) calls a deterministic method like `self.submit(...)` several times and (b) launches
parallel sub-agents via `gather`, each of which also calls methods from its own code. Then:
1. Run it with tracing enabled; export the OTLP spans.
2. Assert in the spans that:
   - each `submit` span's `parentSpanId` == a `code_execution` span (not the agent method);
   - a `_helper`-style transitive call still parents under its caller method;
   - parallel sub-agent top spans parent under the spawning `code_execution`;
   - `agent.parent_call_id` attribute is unchanged.
3. Eyeball in the viewer that the faithful tree render now shows `submit` nested at its true
   spot — with the **viewer reverted to plain tree order** (drop the concurrency re-ordering /
   coloring experiments once parenting is correct).

The user will provide the real agent code; we simplify it but keep the structure
(orchestrator + parallel subagents + methods called from code) for the validation run.

## Out of scope

- Viewer changes: once parenting is correct, revert the experimental ordering/coloring in
  `frontend-react` back to the plain faithful-tree render (separate follow-up commit).
- ATIF / event semantics: unchanged.
