---
name: brainstorm
description: Elicit requirements, constraints, and acceptance criteria BEFORE any design or code. Use when the user asks for a feature / change without a concrete spec, or when the request is ambiguous.
user-invocable: true
argument-hint: "<topic>  |  <umbrella-id> <user-feedback>"
---

# Brainstorm

**Goal: produce a structured spec the user approves before anybody
writes code.** Do not design the implementation. Do not propose files
to change. Just interrogate the problem until the requirements,
constraints, and acceptance criteria are unambiguous.

Read `doc(self.todo)` if you haven't already — this skill stores its
work on a todo's vars + comments so later skills can consume it.

## Arguments

`$ARGUMENTS` is either:

- **`<topic>`** — fresh brainstorm. Create a new umbrella todo.
- **`<umbrella-id> <feedback>`** — revise an existing spec based on
  user feedback. Load the current `spec` var, apply the feedback,
  re-present.

Detect by checking if the first token matches an existing todo id.

## Flow

```python
arg = "$ARGUMENTS".strip()
if not arg:
    self.message("Brainstorm about what?")
    return_result(RespondResult(kind="GET_USER_INPUT"))

tokens = arg.split(maxsplit=1)
first = tokens[0]
existing = self.todo.get(first) if len(first) == 8 else None

if existing is not None:
    # Revision round on an existing umbrella
    umbrella = existing
    feedback = tokens[1] if len(tokens) > 1 else ""
    current_spec = umbrella.vars.get("spec") or {}
else:
    # Fresh brainstorm
    topic = arg
    umbrella = self.todo.add(f"brainstorm: {topic}")
    current_spec = {
        "goal": topic,
        "requirements": [],
        "constraints": [],
        "acceptance_criteria": [],
        "open_questions": [],
    }
    umbrella.v.spec = current_spec
    self.todo.comment(umbrella.id, f"📋 started brainstorm on: {topic}")
    feedback = ""
```

## What to ask

Work through the spec **one dimension at a time**. Ask 1-3 questions
per turn, then return to the user. Don't dump a giant questionnaire.

Dimensions to cover before calling the spec complete:

1. **Goal.** One sentence. What does success look like?
2. **Requirements.** What must be true when done? Be concrete.
   Bad: "good performance". Good: "p95 < 100ms on the benchmark
   set in tests/perf/".
3. **Constraints.** What's off-limits or fixed? Existing API
   compatibility, dependencies we can't add, files that must not
   move, deadline.
4. **Acceptance criteria.** How does the user / reviewer know it's
   done? Ideally testable. Bad: "it works". Good: "existing tests
   stay green, and the new ``test_streaming_retry`` covers the retry
   path".
5. **Risks / unknowns.** What might block or surprise us? Note each
   as an `open_question` so `/root-cause` or `/tdd` can revisit.

Each reply from the user updates the spec:

```python
# After the user answers some questions, merge into spec:
current_spec["requirements"].append("all existing callers keep working")
current_spec["acceptance_criteria"].append("pytest tests/ green")
current_spec["open_questions"].append("should retry be opt-in or default?")
umbrella.v.spec = current_spec
self.todo.comment(umbrella.id, "📋 captured 2 reqs, 1 open question")
```

## When to stop asking

Stop when **all** are true:

- Goal is one sentence the user signs off on.
- ≥ 1 concrete requirement.
- ≥ 1 acceptance criterion.
- `open_questions` is empty or the user explicitly flags them as
  "OK to resolve during implementation".

Don't keep fishing for edge cases once the user is ready. A good
brainstorm is short.

## Present the final spec

Render the spec as Markdown, ask for sign-off, surface the next step:

```python
import json
spec = umbrella.v.spec
lines = [
    f"# Spec — {spec['goal']}",
    "",
    "## Requirements",
    *(f"- {r}" for r in spec["requirements"]),
    "",
    "## Constraints",
    *(f"- {c}" for c in spec["constraints"]) or ["_(none)_"],
    "",
    "## Acceptance criteria",
    *(f"- {a}" for a in spec["acceptance_criteria"]),
]
if spec["open_questions"]:
    lines += ["", "## Still open", *(f"- {q}" for q in spec["open_questions"])]

lines += [
    "",
    "---",
    f"Spec looks good? Next: `/root-cause {umbrella.id}` to find the fix "
    f"(if this is a bug) or `/tdd {umbrella.id}` to start implementing.",
    f"Revise the spec: `/brainstorm {umbrella.id} <feedback>`",
]
self.message("\n".join(lines))
self.todo.comment(umbrella.id, "📋 spec presented for approval")
return_result(RespondResult(kind="GET_USER_INPUT"))
```

## Guidelines

- **Don't design.** No "I'd implement this with a decorator" or
  "we could add a new table". That's `/tdd`'s job.
- **Don't fish indefinitely.** 3-5 turns, max. If the user pushes back
  ("just do it"), accept what you have and move on.
- **Journal via comments.** Every time the spec materially changes,
  `self.todo.comment(umbrella.id, "...")` so the audit trail survives.
