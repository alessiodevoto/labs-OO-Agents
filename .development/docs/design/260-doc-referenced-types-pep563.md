# Issue #260: doc() referenced types from method signatures under PEP 563

## Problem

`discover_referenced_types(cls)` omits custom types that appear **only** in a method
signature (parameter or return annotation) when the agent's module uses
`from __future__ import annotations` (PEP 563). Under PEP 563 every annotation is a
**string**, and `_extract_types_from_hint` no-ops on strings, so the type is never
discovered. The LLM then sees only the bare type name in the signature, not the
type's fields/docstring under `## Referenced Types`.

Field discovery already handles this (it falls back to `_extract_types_from_type_string`
which `eval()`s the annotation against module globals). Method-signature discovery has
no equivalent — that asymmetry is the bug.

## Root cause

In `src/nooa/agentdoc/_discover.py`, both the standalone-callable branch
(lines ~80-93) and the class-method loop (lines ~117-142) read annotations via
`inspect.signature(attr)` and pass `param.annotation` / `sig.return_annotation`
straight to `_extract_types_from_hint`. Under PEP 563 those values are strings, which
`_extract_types_from_hint` ignores.

## Fix

Introduce one helper, `_extract_types_from_callable(attr, discovered)`, used by both
branches:

1. Try `typing.get_type_hints(attr)` (default `include_extras=False`) to obtain
   fully-resolved annotations (this resolves PEP 563 strings and forward refs). On
   success, extract from each value. `include_extras=False` is intentional: the
   existing `_extract_types_from_hint` unwraps `Annotated` via `args[0]`, so stripping
   metadata is harmless and preserves current `test_discover_from_annotated` behavior.
2. Build an eval context **mirroring `_extract_types_from_type_string` exactly**:
   `vars(typing)` first, then `vars(module)` (module globals override typing) via
   `inspect.getmodule(attr)`. Use the **identical** exception set for per-annotation
   eval: `(NameError, AttributeError, TypeError, SyntaxError, ValueError)`. The two
   resolution paths must not diverge.
3. Walk `inspect.signature(attr)` params + return. For each annotation:
   - if a resolved hint exists for that name, use it;
   - else if the annotation is a `str`, `eval()` it against the context and extract
     (resilient per-annotation fallback for when `get_type_hints` raises wholesale on a
     single unresolvable name);
   - else extract the live annotation object directly. This preserves identical
     behavior for eager (non-PEP-563) modules where annotations are already live
     objects — no double-processing, no regression.

`get_type_hints` raises if *any* annotation is unresolvable, so the per-annotation
string fallback keeps the rest working. All exceptions are swallowed (best-effort
discovery, matching existing behavior).

This replaces the duplicated signature-walking logic in both branches with one call.

### PEP 563 fixture constraint (test-trap)

Under PEP 563 the annotation survives only as a **string**, so a type defined inside a
function body (local scope) is unresolvable by *both* `get_type_hints` and the
eval-fallback (`NameError`) — module globals are all that's available. Therefore **all
fixture types in the new PEP-563 test module must be defined at module level** (unlike
`test_referenced_types.py`, which defines fixtures inside test functions). This is an
unavoidable PEP 563 limitation, not a gap — and it is acceptable because real agent
types are always module-level (project convention: API types must be defined/imported
at module level).

## Files to touch

- `src/nooa/agentdoc/_discover.py` — add helper, call it from both branches.
- `tests/agentdoc/test_discover_pep563.py` (new) — regression tests.

## Tests

New file `tests/agentdoc/test_discover_pep563.py`. Because PEP 563 is module-scoped,
the test module itself starts with `from __future__ import annotations` so annotations
are genuinely strings.

- `test_solo_agent_return_type_discovered` — the issue's exact repro: a single
  `Agent` subclass with `async def go(self) -> Res: ...` discovers `Res`.
- `test_param_type_discovered` — param-only custom type discovered.
- `test_generic_return_discovered` — `list[Res]` / `Res | None` return discovered.
- `test_annotated_param_discovered` — `Annotated[Res, "desc"]` param/return discovered
  under PEP 563 (the one construct with special unwrapping).
- `test_standalone_function_discovered` — module-level function branch resolves strings.
- `test_eager_annotations_still_work` — guard against regression for non-PEP-563
  (live-object) annotations (covered by existing tests too, but assert here).

Acceptance also requires running the full existing `tests/agentdoc/test_referenced_types.py`
and `tests/agentdoc/test_discover_pydantic_forward_refs.py` suites green, since they
exercise the eager path and the class-method loop being rewritten.

Avoid depending on a real LLM: use a lightweight `Agent` subclass with `llm=` stub if
needed, or test `discover_referenced_types` on a plain class with a method (the
discover logic does not require Agent). Prefer a plain class + standalone function to
keep tests fast and dependency-free, plus one real `Agent` subclass mirroring the issue.

## Acceptance criteria (from issue)

- `discover_referenced_types(Solo)` returns `[Res]` under PEP 563.
- doc() shows `## Referenced Types` for custom return/param types regardless of PEP 563.
- Quoted/forward-ref annotations resolve same as eager ones.
- Regression test with a `from __future__ import annotations` module.
