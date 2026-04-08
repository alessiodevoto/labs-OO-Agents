# Prompt Redesign Proposal: System Prompt + Strategy Prompt + Task Message

**Date:** 2026-02-16
**Goal:** Reduce prompt size, eliminate mixed signals, improve LLM task comprehension across both simple (sentiment classification) and complex (multi-step code-act) scenarios.

## Problem Summary

The current prompts use ~480 words across three locations (system prompt, strategy prompt, prefill reasoning) with:
- 3 competing identity frames (OOP object, abstract obligation, notebook user)
- 3 duplicated copies of DEFAULT_EXECUTION_MODE
- Meta-architecture explanations the LLM doesn't need
- Method signature formatting that triggers "implement this method" reasoning
- Static "Available on self" section redundant with dynamic `doc(self)` block

See analysis in conversation for full breakdown with LLM reasoning evidence.

## Design Principles

1. **One frame**: Data scientist in a notebook session. Consistent everywhere.
2. **One authority**: Each instruction appears in exactly one place.
3. **Show, don't explain**: Let `doc(self)` show capabilities instead of a static preview.
4. **Concrete over abstract**: "submit your answer" not "fulfill the call."
5. **Positive framing**: Say what to do, not what not to do.
6. **Scale naturally**: The same framing works for `return_result("positive")` and for 10-turn code-act debugging sessions.

## Components Being Changed

| Component | File | Current role |
|-----------|------|-------------|
| `_system_prompt()` | `agent.py:358` | Agent identity, execution model, context explanation |
| `strategy_instructions()` | `codeact.py:280` | Tool descriptions, mode selection, REPL rules, forbidden ops |
| `_build_task_message()` | `codeact.py:324` | Per-call task framing |
| `InspectInputsPrefill` | `prefill.py:92` | Prefill reasoning text |

## Proposed Prompts

### 1. System Prompt (`_system_prompt()`)

**Current:** ~250 words
**Proposed:** ~100 words

```
You are {class_name}, a Python agent working in an interactive session.

When a method is called, the input parameters are pre-loaded as local variables.
Your job: produce the return value.

## How to work

Think of yourself as a data scientist in a Jupyter notebook.
You can run code cells to explore data, call tools, and compute results.
When you have the answer, submit it.

- `return_result(value)` — submit your answer (use when you already know it)
- `execute_python(code)` — run a code cell (use when you need computation)

If you can determine the answer by reading the inputs, submit it directly.
```

**What changed:**
- Identity: "Python agent in an interactive session" — not "Python object of type X"
- The Jupyter/data-scientist analogy is explicit — activates the right mental model
- Tool descriptions are here (single source of truth for what they do)
- Mode selection rule is here once (single source of truth for when to use each)
- Removed: "not to implement the method as source code" (negative framing)
- Removed: "What you see in context" meta-architecture section
- Removed: "Available on self" static preview (doc(self) handles this)
- Removed: FUNCTION_EXECUTION_CONTEXT label
- Removed: DEFAULT_EXECUTION_MODE label
- The class name is still shown for identity, but without `self` framing

**Scales to complex cases because:**
- "run code cells to explore data, call tools, and compute results" covers multi-step code-act
- "call tools" covers tool usage via self
- doc(self) block (unchanged) shows all available tools/methods/state dynamically

### 2. Strategy Prompt (`strategy_instructions()`)

**Current:** ~200 words
**Proposed:** ~120 words

```
## Interactive Session Rules

- Variables persist across code cells (like Jupyter)
- Async context: use `await` directly (no `asyncio.run()`)
- `print()` / `pprint()` for debug output
- `doc(obj)` to inspect any object's interface
- Large outputs are auto-truncated; use slicing to inspect ranges
- `Out[n]` references the result of cell n; `Out[-1]` is the latest
- `return_result()` can also be called from within `execute_python()` code
- You MUST call a tool each turn. Do not answer in plain text.

## Restrictions

These will raise errors if used:
- `import` (everything is pre-imported; see Execution Context)
- `eval()`, `exec()`, `compile()`, `__import__()`
- `input()`, `breakpoint()`
- `globals()`, `locals()`, `vars()`
- `asyncio.run()`, `loop.run_until_complete()`
```

**What changed:**
- Renamed from "STRATEGY_PROMPT" to a descriptive heading
- Removed tool descriptions (moved to system prompt — single source of truth)
- Removed DEFAULT_EXECUTION_MODE (moved to system prompt)
- Removed examples (the system prompt's rule is clear enough; examples created noise)
- Renamed "Forbidden" to "Restrictions" — less dramatic, more informative
- Added note that `return_result()` works inside `execute_python()` code (was in tool description)
- Kept all REPL rules — these are genuinely useful for complex multi-turn sessions
- Renamed section to "Interactive Session Rules" — reinforces the notebook mental model

**Scales to complex cases because:**
- REPL rules (variable persistence, Out[n], async) are essential for multi-turn work
- Restrictions prevent common errors regardless of task complexity
- `doc(obj)` instruction helps LLMs discover interfaces on complex objects

### 3. Task Message (`_build_task_message()`)

**Current:**
```
**Call** `1`: `async def classify(self, text: str) -> str`

Classify the sentiment of a single text.

See STRATEGY_PROMPT for tools and DEFAULT_EXECUTION_MODE for when to use each.
```

**Proposed:**
```
## Task: classify()

Classify the sentiment of a single text.

Args:
    text: The text to classify

Returns:
    One of: "positive", "negative", "neutral"

Return type: `str`
```

**Template:**
```
## Task: {method_name}()

{original_call.docstring}

Return type: `{format_type(return_type)}`
```

**What changed:**
- Heading says "Task: method()" not "Call 1: async def method(self, ...)"
- No method signature — eliminates "implement this method" trigger entirely
- No cross-references to STRATEGY_PROMPT or DEFAULT_EXECUTION_MODE
- Return type shown explicitly and separately
- The docstring (which includes Args/Returns from the method) carries the task description
- Call ID removed from the heading (it's not useful to the LLM)

**Note on docstring:** The docstring from the agent method (`classify`'s docstring) already contains the Args and Returns sections. The template just renders the docstring directly, plus adds the programmatic return type for validation clarity.

**Scales to complex cases because:**
- Complex methods have detailed docstrings with args, returns, examples
- Return type is always shown — critical for Pydantic models and complex types
- No artificial constraints on what the docstring can contain

### 4. Prefill Reasoning (`InspectInputsPrefill`)

**Current:**
```python
reasoning(f"""Executing classify().

DEFAULT_EXECUTION_MODE: Inspect inputs first.
- Know the answer? → return_result()
- Need code-act? → execute_python()

Reviewing inputs:""")
print(f"Call: {_call.format_signature()}")
```

**Proposed:**
```python
reasoning(f"""Inspecting inputs for {method_name}().""")
print(f"Task: {method_name}()")
```

**What changed:**
- Removed DEFAULT_EXECUTION_MODE repetition (already in system prompt)
- Removed `format_signature()` — no more `async def classify(self, text: str) -> str`
- Simplified reasoning to just state what's happening
- Changed "Call:" to "Task:" — consistent with task message heading

The rest of the prefill (pprint of each parameter, return type for complex types) stays the same — that's genuinely useful.

## What Stays Unchanged

- **`doc(self)` block** — Dynamic, shows actual tools/methods/state. No changes needed.
- **`execution_context` block** — Shows pre-imported modules. No changes needed.
- **`context_api` / `events_api` blocks** — Content unchanged, but see conditional inclusion note below.
- **Error feedback messages** (text-only response, empty response) — Already good, reference tools by name consistently.
- **`return_result` validation errors** — Already good.
- **`_build_execute_python_tool()` / `_build_return_result_tool()`** — Tool schemas unchanged.

## Future Consideration: Conditional Framework Blocks

Not part of this proposal, but worth noting: `context_api` and `events_api` blocks add ~100 tokens of noise for agents that don't use them (like SentimentSingleAgent). A future change could make `wants_framework_block()` conditional based on whether the agent actually uses these features. This is a separate change that doesn't affect prompt text.

## Test Impact

One test references `DEFAULT_EXECUTION_MODE`:

```
tests/runtime/test_event_manager_events.py:314
    assert "DEFAULT_EXECUTION_MODE" in code
```

This test validates `InspectInputsPrefill` output. It will need updating to match the new prefill text (remove the `DEFAULT_EXECUTION_MODE` assertion, update the `reasoning()` content assertion).

## Token Estimate

| Component | Current (approx) | Proposed (approx) | Savings |
|-----------|------------------|-------------------|---------|
| System prompt | ~250 words | ~100 words | -60% |
| Strategy prompt | ~200 words | ~120 words | -40% |
| Task message | ~40 words | ~30 words | -25% |
| Prefill reasoning | ~30 words | ~10 words | -67% |
| **Total** | **~520 words** | **~260 words** | **~50%** |

## Rollout

1. Implement all four changes together (they're interdependent — removing duplication requires all locations to be updated simultaneously)
2. Update the one test that checks prefill content
3. Run capability phase 1 tests to measure impact
4. Run full test suite to catch any regressions
