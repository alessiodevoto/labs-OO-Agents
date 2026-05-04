---
name: ship
description: Verify the umbrella todo is truly done — all sub-todos complete, tests green, no unaddressed blocking review findings — then offer to open an MR.
user-invocable: true
argument-hint: "<umbrella-id>"
---

# Ship

**Goal: the final gate before the change leaves the laptop.** Verify
the evidence, then offer to push + open an MR.

Reads:

- `commits`, `test_results`, `diff_summary` (from `/tdd`)
- `review_findings` (from `/review`)
- `spec.acceptance_criteria` (from `/brainstorm`)
- All sub-todos of the umbrella — they should all be `done`

Does **not** write new vars — just marks the umbrella done and closes
the loop.

## Arguments

```python
arg = "$ARGUMENTS".strip()
if not arg:
    self.message("Usage: /ship <umbrella-id>.")
    return_result(RespondResult(kind="GET_USER_INPUT"))

umbrella_id = arg.split()[0]
umbrella = self.todo.get(umbrella_id)
if umbrella is None:
    self.message(f"No todo {umbrella_id}.")
    return_result(RespondResult(kind="GET_USER_INPUT"))
```

## Gate 1 — sub-todos all done

```python
unfinished = [
    t for t in self.todo.list_todos()
    if umbrella.id in t.deps and t.status != "done"
]
if unfinished:
    self.message(
        "Not shippable — these sub-todos are still open:\n\n"
        + "\n".join(f"- [{t.id}] {t.title} ({t.status})" for t in unfinished)
    )
    return_result(RespondResult(kind="GET_USER_INPUT"))
```

## Gate 2 — test suite green

```python
test_results = self.todo.get_var(umbrella.id, "test_results") or {}
if not test_results.get("passed"):
    # Re-run to be sure — maybe state has changed since /tdd
    final = await self.bash.run("pytest --tb=short")
    if final.return_code != 0:
        self.message(
            "Not shippable — test suite is failing:\n\n"
            f"```\n{final.stdout[-2000:]}\n```"
        )
        return_result(RespondResult(kind="GET_USER_INPUT"))
    self.todo.set_var(umbrella.id, "test_results",
                      {"cmd": "pytest --tb=short", "passed": True,
                       "stdout_tail": final.stdout[-1000:]})
```

## Gate 3 — no unaddressed blockers from /review

```python
findings = self.todo.get_var(umbrella.id, "review_findings") or []
blocking = [f for f in findings if f.get("severity") == "blocking"]
if blocking:
    lines = ["Not shippable — unaddressed blocking findings:\n"]
    for f in blocking:
        lines.append(f"- **{f['reviewer']}** {f.get('location', '')}: {f.get('issue')}")
    lines.append("\nAddress these via /tdd, then re-run /review, then /ship.")
    self.message("\n".join(lines))
    return_result(RespondResult(kind="GET_USER_INPUT"))
```

## Gate 4 — acceptance criteria self-check

Lightweight: just show the user the AC list so they confirm each
visually. Don't try to verify programmatically — the tests do that.

```python
spec = self.todo.get_var(umbrella.id, "spec") or {}
ac = spec.get("acceptance_criteria", [])
commits = self.todo.get_var(umbrella.id, "commits") or []
diff_summary = self.todo.get_var(umbrella.id, "diff_summary") or ""

lines = [
    f"# Ship gate — {umbrella.title}",
    "",
    "## ✅ Verification",
    f"- Sub-todos: all done ({len([t for t in self.todo.list_todos() if umbrella.id in t.deps])} total)",
    f"- Tests: {test_results.get('cmd')} — green",
    f"- Review: {len(findings)} findings, no blockers outstanding",
    "",
    "## Commits",
    diff_summary or "(none)",
    "",
    "## Acceptance criteria",
    *(f"- [ ] {c}" for c in ac) if ac else ["_(none recorded)_"],
    "",
    "---",
    "Reply `ship` to commit any remaining work + open an MR, "
    "`push` to push without opening an MR, or describe changes.",
]
self.message("\n".join(lines))
self.todo.comment(umbrella.id, "🚢 ship gate passed — waiting for user OK")
return_result(RespondResult(kind="GET_USER_INPUT"))
```

## On user confirmation

When the user replies `ship` or `push`, the same skill is re-invoked
with the next turn's input. The current turn's job is just to pass
the gates and ask.

If the user says `ship`, the **next turn** handles:

1. Stage + commit anything uncommitted (docstring touch-ups, etc.).
2. Push the branch (`git push -u origin HEAD`).
3. If `ship`, open an MR via `glab mr create` with the spec as
   description. If `push`, stop here.
4. Mark umbrella done: `self.todo.done(umbrella.id)` + journal.
5. Print the MR URL or the push confirmation.

## Guidelines

- **Every gate is a blocker.** Failing one stops the flow; the user
  either fixes it or explicitly overrides.
- **Never force-push from `/ship`.** If the remote has diverged,
  report it and let the user decide.
- **Don't rewrite commit history.** Each `/tdd` commit is the unit of
  work; preserve it so a reviewer can walk the diff commit-by-commit.
- **MR body = spec.** Pull `spec.goal` as the title lead, requirements +
  acceptance_criteria as the body, `commits` as the changelog. No
  flowery narrative.
