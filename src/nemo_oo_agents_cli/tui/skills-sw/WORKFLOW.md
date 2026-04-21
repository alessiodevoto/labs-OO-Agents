# SWE workflow skills — conventions

Five skills that scaffold software-engineering work end-to-end:

```
/brainstorm  →  /root-cause  →  /tdd  →  /review  →  /ship
   spec         repro+plan      diff     findings    done
```

Each skill reads from and writes to a single **umbrella todo** using
`t.v.<key>` (persistent data) and `self.todo.comment`
(chronological journal). That's how state crosses turn boundaries
where the Python REPL namespace doesn't.

## Todo lifecycle

The **first** skill invoked (typically `/brainstorm` or `/root-cause`)
creates the umbrella todo and returns its id. Every subsequent skill
takes that id as an argument so they accumulate state on the same
todo. When the umbrella todo is marked `done` (via `/ship` or
manually), the workflow is complete.

```python
# /brainstorm "new login flow"  →  creates umbrella
t = self.todo.add("brainstorm: new login flow")
t.v.spec = {...}
self.todo.comment(t.id, "📋 spec captured — 4 requirements, 2 open questions")

# /root-cause <id>  →  reuses it
t.v.fix_plan = [...]
self.todo.comment(t.id, "🔍 root cause: races on refresh")
```

## Vars-key contract

Keep the key set small and stable. New keys need a line in this doc.

| Key                   | Type               | Written by       | Read by                                 |
|-----------------------|--------------------|------------------|-----------------------------------------|
| `spec`                | `dict`             | `/brainstorm`    | `/root-cause`, `/tdd`, `/review`, `/ship` |
| `repro_cmd`           | `str`              | `/root-cause`    | `/tdd`                                  |
| `failing_test_code`   | `str`              | `/root-cause`    | `/tdd`                                  |
| `root_cause`          | `str`              | `/root-cause`    | `/tdd`, `/review`                       |
| `fix_plan`            | `list[str]`        | `/root-cause`    | `/tdd`                                  |
| `test_results`        | `dict`             | `/tdd`           | `/review`, `/ship`                      |
| `diff_summary`        | `str`              | `/tdd`           | `/review`, `/ship`                      |
| `commits`             | `list[str]`        | `/tdd`           | `/ship`                                 |
| `review_findings`     | `list[dict]`       | `/review`        | `/ship`                                 |
| `show_diffs`          | `bool` (opt-in)    | user             | `/tdd`                                  |

Acceptance criteria live inside ``spec`` — read as
``spec["acceptance_criteria"]``; not a separate top-level key.

**Shape of `spec`:**

```python
{
    "goal": "one-sentence description",
    "requirements": ["what must be true when done"],
    "constraints": ["limits / guardrails"],
    "acceptance_criteria": ["measurable pass/fail tests — e.g. 'pytest tests/ green'"],
    "open_questions": [],  # empty when spec is approved
}
```

**Shape of `review_findings`:**

```python
[
    {"reviewer": "correctness|scope|style|security",
     "severity": "blocking|major|minor",
     "location": "file.py:42",
     "issue": "short description",
     "suggestion": "optional fix hint"},
    ...
]
```

## Commenting conventions

Every skill journals meaningful milestones via
`self.todo.comment(umbrella_id, ...)`. Keep each comment short and
prefix with an emoji tag so the log is scannable:

| Emoji | Meaning                  | Example                                          |
|-------|--------------------------|--------------------------------------------------|
| 📋    | spec / plan captured     | `📋 spec captured — 4 reqs, 1 open question`     |
| 🔍    | investigation / insight  | `🔍 root cause: races on refresh in session.py`  |
| 🧪    | test written / run       | `🧪 test_login_race RED — reproduces the bug`    |
| ✅    | step complete            | `✅ green — implemented lock in session.py`      |
| 🧹    | refactor                 | `🧹 extracted _acquire_lock helper`              |
| 🔧    | code change              | `🔧 patched session.py:42 to hold the mutex`     |
| 👁    | review finding           | `👁 correctness: lock not released on exception` |
| 🚢    | ship action              | `🚢 committed abcd123 — lock fix + test`         |
| ⚠️    | surprise / reversal      | `⚠️ fix broke an unrelated test; investigating`  |

## User-confirm gates

Skills pause for user input at these points:

- **`/brainstorm`** after presenting the spec — waits for OK or revision.
- **`/root-cause`** after presenting the fix plan — waits for OK.
- **`/tdd`** after presenting each committed change *only if* the user
  set `show_diffs=True` on the umbrella todo; otherwise runs to
  completion and summarises at the end.
- **`/review`** after presenting findings — waits for the user to pick
  which blocking issues to address.
- **`/ship`** before opening an MR if one is requested.

Every pause uses:

```python
self.message("...")
return_result(RespondResult(kind="GET_USER_INPUT"))
```

so the user can redirect at each boundary.

## Handing off between skills

The umbrella todo id is the handoff token. Skills should print it
prominently at their exit:

```python
self.message(
    f"Spec captured — next: /root-cause {t.id}\n"
    f"or refine the spec with /brainstorm {t.id} <feedback>"
)
```

This lets the user drive the workflow one step at a time, or chain
automatically when they already know what they want.
