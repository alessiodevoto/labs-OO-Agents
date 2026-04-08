# Visibility Design: Critical Review and Simplification Option

**Date:** 2026-03-11
**Status:** Implemented (simplified model in place)
**Context:** Design issues raised on MR 438 (e.g. "two spaces"); goal is cleanest design, not lowest effort.

## Current Design (MR 438)

Two scopes, two defaults:

| Scope | Default | Opt-in | Opt-out |
|-------|---------|--------|---------|
| **Module level** (imports, constants, functions) | **HIDDEN** | `with visible:` | — |
| **Agent class** (methods, fields) | **VISIBLE** | — | `@hidden`, `Annotated[T, hidden]` |
| **Types in public API** | **AUTO-PROMOTED** | (follows from above) | (hide method/field) |

So:

- **Inside the agent class:** everything is visible to the LLM unless you explicitly hide it.
- **Outside (module):** nothing is visible unless you put it inside `with visible:`.

That creates two different rules in one file: "opt out" in the class, "opt in" in the module. In normal Python there is no such split: module-level and class-level names are all "there"; visibility is not a separate concept.

## Design critique (summary)

- The **two spaces** (class = visible by default, module = hidden by default) don't exist in normal Python and are cognitively heavy.
- One reason for "module = hidden by default" was **intentional imports** (don't expose `json`/`os`/etc. by accident). That goal may not justify the overhead of `with visible:` everywhere.

So the question is: can we have **one rule** — **everything visible by default, hide explicitly** — for both module and class?

## Proposed Simplification: Single Rule (Python-Style)

**Rule:** All names (module-level and agent-level) are **visible to the agent by default**. You **explicitly hide** what the agent must not see or use.

Concretely:

- **Module:** Imports, constants, functions, classes defined at module top level → all visible to `exec_globals` (and thus to the LLM) unless explicitly hidden.
- **Agent:** Methods and fields stay as today: visible by default, `@hidden` / `Annotated[T, hidden]` to hide.
- **Safety:** Still enforced by:
  - `DEFAULT_BLOCKED_MODULES` and `_strip_blocked_modules(exec_globals)` (e.g. no `subprocess`, `socket` in exec_globals).
  - `BlockingCallValidator` (e.g. no `time.sleep`, `os.system` even if `time`/`os` are in globals).
- **Opt-out at module level:** Use the **same single concept** — `agent006.hidden` — with the same three mechanisms we already have for the class, just applied at module scope:
  - **Decorator:** `@hidden` on module-level functions (same `_agent006_hidden` attribute; filter them out when building exec_globals).
  - **Annotated:** Module-level variables with `Annotated[T, hidden]` (e.g. `API_KEY: Annotated[str, hidden] = "..."`). Resolve via the module's `__annotations__` (same logic as `is_hidden_field` but for the module).
  - **Context manager:** `with hidden:` — on exit, names that appeared in the module during the block (same diff-on-exit as `with visible:`) are added to the module's hidden set and excluded from exec_globals. Covers unannotated constants and imports (e.g. `with hidden: import secrets`).

**Coverage:** No gap. Functions → decorator; annotated variables → `Annotated[T, hidden]`; everything else (imports, unannotated vars) → `with hidden:`. One concept, three mechanisms, no extra API or convention (e.g. no need for underscore-at-module-level).

## Module scope only: no per-agent visibility

With the above, visibility is **module-scoped**: all agent classes in the same module share the same exec_globals (same hidden set). So two agents in one file see the same module-level names.

**Rule:** If two agents need different visibility, put them in **two different modules**. Each module has one hidden set; no per-agent override.

**Does this always work?** Yes. Cases that might seem to need per-agent visibility can be handled by structure:

- **Safe vs power agent:** Put `SafeAgent` in `safe_agent.py` (that module hides everything except the minimal surface) and `PowerUserAgent` in `power_agent.py` (that module hides less). Shared types/helpers live in a third module (e.g. `shared.py`) and are imported by both; each agent module controls what it exposes via its own `@hidden` / `with hidden:`.
- **Runtime choice by role:** Choose which agent class to instantiate (e.g. `SafeAgent` vs `PowerUserAgent` from the two modules); no need for one class with two surfaces.
- **Many agents, same surface:** One module, multiple agent classes, one shared hidden set — no problem.

There is no case where "different surface → different module" fails: shared code is factored into a common module; each "surface" is one module. Keeping visibility strictly module-scoped avoids per-agent repetition and keeps the model simple: **one module, one visibility; different visibility, different module.**

## Comparison

| Aspect | Current (two spaces) | Simplified (one rule) |
|--------|---------------------|------------------------|
| **Mental model** | Two rules: class = opt-out, module = opt-in | One rule: visible by default, hide when needed |
| **Python alignment** | Custom split (no direct Python analogue) | Closer to "what you see in the module/class is what's there" |
| **Intentional imports** | Achieved by "only `with visible:` gets into exec_globals" | Replaced by "everything in module in exec_globals, minus blocked_modules and optional hidden set" |
| **Accidental exposure** | Hard: nothing exposed without `with visible:` | Possible if dev adds a dangerous import and doesn't hide it; mitigated by blocked_modules + validator |
| **Boilerplate** | Every agent file that needs `json`/etc. must use `with visible:` | No `with visible:` for "allow this import"; only optional "hide this" |
| **Breaking change** | — | Yes: today "no visible block" ⇒ empty exec_globals; new behaviour would expose full module (minus blocked/hidden) |

## Risks of the Simplified Model

1. **Accidental exposure:** A developer adds `import subprocess` at top level and forgets.
   - **Mitigation:** `subprocess` is in `DEFAULT_BLOCKED_MODULES`, so it's stripped from exec_globals and the validator still blocks its use. So "dangerous" stdlib is already handled.
   - Remaining risk: project-specific sensitive helpers could be exposed → use **module-level hidden** (same `@hidden` / `Annotated[T, hidden]` / `with hidden:` as above).

2. **Large modules:** A module with many imports and helpers might expose more than the author intends.
   - **Mitigation:** Mark the few names that must be hidden with `@hidden`, `Annotated[T, hidden]`, or `with hidden:`. Default "everything visible" is consistent and easy to reason about.

## Recommendation

- **For a world-class, pre-alpha library:** Prefer the **simplified, single-rule design** (everything visible by default, hide explicitly). It aligns with Python's mental model and reuses one concept (`hidden`) everywhere: same three mechanisms (decorator, `Annotated[T, hidden]`, context manager) for both class and module level. No extra API; safety remains in blocked_modules + validator.
- **Implement when:** As a follow-up work package after the current auto-promotion work is settled. No need to block the current MR; this can be a separate "visibility v2" or "visibility simplification" MR with a short migration note (e.g. "if you relied on 'no visible block = empty exec_globals', you must now use the module-level hidden set or blocked_modules").

## References

- MR 438: feat: blocking call prevention + unified visible/hidden visibility model (merged).
- Current implementation: `src/agent006/visibility.py` (`_Visible`, `filter_module_globals`, `_agent006_visible_names`).
- Safety layer: `src/agent006/runtime/restrictions.py` (`DEFAULT_BLOCKED_MODULES`, `DEFAULT_BLOCKED_CALLS`), `_strip_blocked_modules` and `BlockingCallValidator` in actor/code_validator.
