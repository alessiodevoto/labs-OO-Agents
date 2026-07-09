> **Update (final shipped approach).** After review, the rendering was taken
> further than the interim "signatures + LLM-backed/Plain split" below: the whole
> `<execution_context>` block is now rendered as a **Python stub** (`.pyi`-style)
> — `import` lines for dependencies, `class X: ...` / `def f(...) -> T: """doc"""`
> stubs for module-defined symbols — mirroring how `doc(type(self))` renders the
> agent's own API. Functions render **uniformly**: the plain-vs-generation
> distinction is treated as an implementation detail and is NOT exposed (the
> `async` keyword in the signature is the only calling-convention signal).
>
> Validated with an A/B capability eval (`tests/capability/config.yaml`, 37
> tests, runs=3) toggling stub vs. the interim prose rendering across three
> models — stub was neutral-to-positive everywhere with no regressions, and the
> gains concentrated on the frontier tier (multi-step/routing) and weaker/open
> models:
>
> | model | overall Δ (stub−legacy) | frontier Δ |
> |---|---|---|
> | gpt-5-mini | +2.7pp | +13.9pp |
> | gpt-5.2 (large) | ±0.0pp | ±0.0pp |
> | nemotron-3-super | +8.3pp | +22.2pp |
>
> Per-turn prompt size was ~unchanged; token deltas were dominated by trajectory
> length. The sections below describe the earlier interim step and are kept for
> history.

# Issue 227 — Render module-level functions with full signatures in `<execution_context>`

## Problem

`CodeActStrategy.execution_context()` (`src/nooa/strategies/codeact.py`)
renders the **Available functions (defined in agent module)** bucket as a bare,
comma-joined list of names. Agent *methods*, by contrast, are rendered via
`doc(type(self))` with full signatures, return types, and docstrings in the
`<self>` block.

Consequence: a CodeAct agent that can *see* a module-level standalone
`@strategy(...)` generation function has no way to know it is `async` /
LLM-backed. It calls it without `await` (un-awaited coroutines → wrong results)
or ignores it and re-implements the logic inline.

## Root cause

In `execution_context()` the `functions_defined` list collects only names
(line ~414) and renders them as `', '.join(sorted(functions_defined))`
(line ~436–440). No signature, no `async` marker, no return type, no docstring.

## Fix

Render each module-level function with the same fidelity methods get, by reusing
the existing `agentdoc.doc()` machinery (it already renders functions — including
`@strategy` standalone wrappers — as `async def name(...) -> T: """doc"""`).

Concretely, in `execution_context()`:

1. Change `functions_defined` to collect `(name, obj)` pairs instead of names.
2. Replace the bare-name rendering block with one that:
   - Emits the `**Available functions** (defined in agent module):` header.
   - Adds a `Tip: Use doc(<name>) ...` nudge (parity with the "Available types"
     line, which already has one).
   - Splits the functions into **LLM-backed generation functions**
     (`getattr(obj, "_needs_generation", False)` — set by `@strategy` on
     ellipsis-body standalones) and **plain helpers**.
   - **Imported generation functions** (a `@strategy` standalone imported from
     another module) land in `imported_items`, not `functions_defined`. Pull
     any `_needs_generation` callable out of `imported_items` and render it
     alongside the module-defined generation functions, so an imported
     standalone also gets the async/await marker. Remaining imported items
     (plain classes/callables) keep the bare-name list.
   - For generation functions, prints a short note that they are async /
     LLM-backed and must be `await`ed (and can be fanned out with
     `asyncio.gather`), followed by `doc(fn1, fn2, ..., inline_depth=0)`.
   - For plain helpers, prints `doc(fn1, fn2, ..., inline_depth=0)`.
   - `inline_depth=0` keeps the output to signatures + docstrings without
     re-expanding referenced type bodies (those types are already listed under
     **Available types**, so expanding them here would duplicate content).
   - Render is sorted by name and wrapped in a `try/except` that falls back to
     the previous bare-name list, so a `doc()` failure can never break prompt
     construction.

### Rendering sketch

```text
**Available functions** (defined in agent module):
  Tip: Use `doc(<name>)` to inspect any function's full signature.

LLM-backed generation functions — `async`, so `await` them (fan out with `asyncio.gather`):

async def categorize_ticket(text: str) -> Literal[bug, feature, question]:
    """Categorize a support ticket into one of three buckets."""

Plain helpers:

def plain_helper(x: int, y: int = 3) -> int:
    """Add two numbers."""
```

(If only one category is present, only that sub-block is emitted.)

## Files touched

- `src/nooa/strategies/codeact.py` — `execution_context()` only.
  `_extract_module_context()` already surfaces the right objects (visibility fix
  already merged); no change needed there.

## Tests

`tests/strategies/test_execution_context_leaks.py` already has a
`TestModuleLevelFunctionsVisible` class with a module fixture containing a
`@strategy(PredictStrategy())` standalone (`classify_item`), a `plain_helper`,
and a `@hidden` `secret_helper`. Extend it (or add a sibling class) with:

- `test_generation_function_rendered_with_signature` — `async def classify_item(`
  and its return type / docstring appear in the `<execution_context>` block.
- `test_generation_function_marked_llm_backed` — the block tells the LLM the
  generation function is async / must be awaited (assert on the `await` /
  generation note near `classify_item`).
- `test_plain_helper_rendered_with_signature` — `def plain_helper(x: int)`
  appears (not just the bare name).
- `test_doc_tip_present_for_functions` — a `doc(` tip line is present for the
  functions bucket.
- Keep existing assertions green: `Available functions`, `classify_item`,
  `plain_helper` present; `secret_helper` absent.

Add a fallback-safety check is optional (hard to trigger); rely on the
`try/except` for robustness.

## Acceptance criteria (from issue)

- [x] `<execution_context>` shows signatures + docstrings + async/strategy
      markers for module-level functions.
- [x] Hidden functions remain excluded (unchanged — handled upstream by
      `filter_module_globals`).
- [~] Agent can call a standalone `@strategy` function awaited/gathered without
      an explicit how-to in the calling docstring — addressed by the async/
      LLM-backed marker; full behavioral regression (live LLM) is out of scope
      for unit tests but the rendering now carries the needed signal.

## Out of scope

- The quickstart-14 ticket-triage example referenced in the issue no longer
  exists (file repurposed to the ATIF trajectory exporter). No behavioral
  live-LLM regression test is added; the unit tests assert the rendering carries
  the async/LLM-backed calling convention, which was the missing information.
