---
name: root-cause
description: Reproduce a bug, write a failing test that pins it down, and find the root cause. Outputs a fix plan but does NOT implement the fix — that's /tdd's job.
user-invocable: true
argument-hint: "<bug description>  |  <umbrella-id> [bug description]"
---

# Root-cause

**Goal: understand the bug before you touch the code.** Three outputs
on the umbrella todo's vars:

- `repro_cmd` — a shell command that reliably triggers the failure
- `failing_test_code` — a pytest test that currently fails because of
  the bug (this seeds `/tdd`)
- `root_cause` — one-paragraph explanation grounded in specific file:line
- `fix_plan` — ordered list of concrete changes (but no code yet)

Do not write the fix. Do not edit production code (other than tests).
`/tdd` does that next.

## Arguments

`$ARGUMENTS` is either:

- **`<bug description>`** — fresh root-cause, creates an umbrella.
- **`<umbrella-id> [description]`** — continuing from `/brainstorm`
  (or a previous `/root-cause` pass). Reuse the umbrella so state
  chains.

## Flow

```python
arg = "$ARGUMENTS".strip()
if not arg:
    self.message("What bug? Give me symptoms, a stack trace, or a failing command.")
    return_result(RespondResult(kind="NEED_INPUT", explanation="need bug symptoms, stack trace, or failing command"))

tokens = arg.split(maxsplit=1)
first = tokens[0]
existing = self.todo.get(first) if len(first) == 8 else None

if existing is not None:
    umbrella = existing
    description = tokens[1] if len(tokens) > 1 else ""
else:
    description = arg
    umbrella = self.todo.add(f"fix: {description[:60]}")
    self.todo.comment(umbrella.id, f"🔍 opened root-cause on: {description}")
```

## Phase 1 — reproduce

A bug you can't trigger on demand isn't a bug you can fix. First goal:
one command that fails deterministically.

- If the user gave a stack trace, work backwards from the top frame
  using `self.shell.run("rg ...")` / `self.shell.read`, or
  `self.repo.symbols` / `self.repo.refs`.
- If the user described symptoms, figure out a minimal invocation
  (failing test, curl, CLI command) that shows it.
- Try it. If it doesn't reproduce, **stop and ask** — don't guess at
  different repros.

```python
# Once you have a command that fails:
result = await self.shell.run("<your repro command>")
assert result.returncode != 0, "command didn't fail — is this actually the bug?"
self.todo.set_var(umbrella.id, "repro_cmd", "<the command>")
self.todo.comment(umbrella.id, f"🔧 reproduces via: <command>")
```

## Phase 2 — pin it with a test

Write a pytest test that currently fails because of the bug. This is
what `/tdd` will flip from RED → GREEN. Put it in the right test
directory for the repo; don't invent new structure.

```python
test_code = '''
def test_login_race():
    # Test that pins down the bug.
    ...
    assert result.success  # currently fails
'''
# Write the test file:
test_path = "tests/auth/test_race.py"
await self.shell.write_file(test_path, test_code)

# Run it — confirm it's RED for the right reason (not a syntax error):
result = await self.shell.run(f"pytest {test_path} -v")
assert result.returncode != 0
self.todo.set_var(umbrella.id, "failing_test_code", test_code)
self.todo.set_var(umbrella.id, "failing_test_path", test_path)
self.todo.comment(umbrella.id, f"🧪 {test_path} RED — reproduces the bug")
```

## Phase 3 — find the root cause

Read the code along the failure path. `self.shell.read`,
`self.shell.run("rg ...")`, `self.repo.refs`, walk
the stack. **Find the specific line that's wrong**, not a vague area.

Two anti-patterns to avoid:

1. **Papering over.** Adding a `try/except`, a `None` check, or a
   retry that swallows the original failure. Fix the cause, not the
   symptom.
2. **"It's a race".** "Race condition" is often the default
   hand-wave. If you say that, you must name the two operations that
   race and the shared state they contend on.

When you think you have it:

```python
self.todo.set_var(umbrella.id, "root_cause",
    "session.py:42 reads self._token then writes on refresh without "
    "holding self._lock, so a concurrent refresh sees stale token and "
    "overwrites the new one."
)
self.todo.comment(umbrella.id, "🔍 root cause: races on refresh — session.py:42")
```

## Phase 4 — plan the fix (don't implement)

Ordered list of concrete changes. Each item names a file and what
will change. No diffs, no code yet.

```python
self.todo.set_var(umbrella.id, "fix_plan", [
    "Add threading.Lock to Session.__init__",
    "Wrap refresh() critical section in self._lock",
    "Update test_login_race to simulate concurrent refresh",
])
self.todo.comment(umbrella.id, "📋 fix plan captured (3 steps)")
```

## Present the report

```python
spec = self.todo.get_var(umbrella.id, "spec") or {}
lines = [
    f"# Root-cause — {umbrella.title}",
    "",
    f"**Repro:** `{self.todo.get_var(umbrella.id, 'repro_cmd')}`",
    f"**Failing test:** `{self.todo.get_var(umbrella.id, 'failing_test_path')}` (RED)",
    "",
    "## Root cause",
    self.todo.get_var(umbrella.id, "root_cause") or "_(not yet identified)_",
    "",
    "## Fix plan",
    *(f"{i + 1}. {step}" for i, step in enumerate(self.todo.get_var(umbrella.id, "fix_plan") or [])),
    "",
    "---",
    f"Ready to implement? `/tdd {umbrella.id}` will flip the test "
    "GREEN by walking this plan.",
    f"Revise: `/root-cause {umbrella.id} <what you'd change>`",
]
self.message("\n".join(lines))
self.todo.comment(umbrella.id, "🔍 root-cause report presented")
return_result(RespondResult(kind="DONE", explanation="root-cause report presented; waiting for the next user request"))
```

## Guidelines

- **One bug at a time.** If you discover a second bug while reproducing
  the first, note it as an `open_question` on the spec — don't fix it.
- **No fix code in this skill.** `/tdd` owns the implementation.
- **Test path first.** A failing test that pins the bug is the most
  valuable artefact — even if the diagnosis is wrong, the test keeps
  everyone honest.
