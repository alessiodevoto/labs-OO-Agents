---
name: tdd
description: Implement the fix plan from /root-cause via red → green → refactor cycles. Commits automatically after each green step unless `show_diffs=True` is set on the umbrella todo.
user-invocable: true
argument-hint: "<umbrella-id>"
---

# TDD

**Goal: take a fix plan and a failing test, end with all tests
GREEN, with small focused commits along the way.**

Reads from the umbrella todo:

- `fix_plan` — ordered list of steps (from `/root-cause`)
- `failing_test_path` — the test that should flip RED → GREEN
- `spec.acceptance_criteria` — optional, used to decide "done"
- `show_diffs` — if truthy, pause after each green step to show the
  diff and wait for user approval before committing

Writes:

- `test_results` — dict of {cmd, passed, failed}
- `diff_summary` — one-line summary per commit
- `commits` — list of committed SHAs

## Arguments

```python
arg = "$ARGUMENTS".strip()
if not arg:
    self.message("Usage: /tdd <umbrella-id>. Run /root-cause first if you don't have one yet.")
    return_result(RespondResult(kind="GET_USER_INPUT"))

umbrella_id = arg.split()[0]
umbrella = self.todo.get(umbrella_id)
if umbrella is None:
    self.message(f"No todo {umbrella_id}. Start with /brainstorm or /root-cause.")
    return_result(RespondResult(kind="GET_USER_INPUT"))

fix_plan = self.todo.get_var(umbrella.id, "fix_plan")
failing_test_path = self.todo.get_var(umbrella.id, "failing_test_path")
if not fix_plan or not failing_test_path:
    self.message(
        f"Todo {umbrella.id} isn't ready for /tdd — missing fix_plan or "
        f"failing_test_path. Run /root-cause first."
    )
    return_result(RespondResult(kind="GET_USER_INPUT"))

show_diffs = bool(self.todo.get_var(umbrella.id, "show_diffs"))
commits: list[str] = self.todo.get_var(umbrella.id, "commits") or []
```

## Red check (start each run with this)

Confirm the failing test is still RED before doing anything else. If
it's already GREEN, something shifted — stop and ask.

```python
red = await self.bash.run(f"pytest {failing_test_path} -x")
if red.return_code == 0:
    self.todo.comment(umbrella.id, "⚠️ failing test is unexpectedly GREEN — stopping")
    self.message(f"`{failing_test_path}` is already passing. Did someone else fix this? "
                 "Re-run /root-cause to verify.")
    return_result(RespondResult(kind="GET_USER_INPUT"))
self.todo.comment(umbrella.id, f"🧪 {failing_test_path} RED as expected — starting TDD loop")
```

## Cycle: for each step in fix_plan

Create a sub-todo per plan step so progress is visible in
`<todo_status>`. Walk them in order.

```python
# Build sub-todos once (idempotent — skip if already made)
step_todo_ids = self.todo.get_var(umbrella.id, "step_todos") or []
if not step_todo_ids:
    step_todo_ids = [
        self.todo.add(f"tdd step {i + 1}: {step}", deps=[umbrella.id]).id
        for i, step in enumerate(fix_plan)
    ]
    self.todo.set_var(umbrella.id, "step_todos", step_todo_ids)
```

For each step:

1. **Implement the minimal change.** Edit the file(s) named in the
   step. Resist the urge to clean up nearby code.
2. **Run the failing test.** If it's still RED after the change,
   iterate in the same step (don't move on). If it goes GREEN,
   proceed.
3. **Run the full test suite.** Did anything *else* break? If yes,
   that's a reversal — journal it and decide whether to fix forward
   or back out.
4. **Commit.** Small, self-explanatory, one message per step. Unless
   `show_diffs=True`, commit without pausing.

```python
for step_id in step_todo_ids:
    step = self.todo.get(step_id)
    if step.status == "done":
        continue

    # 1. Implement
    self.todo.comment(step_id, f"🔧 implementing: {step.title}")
    # (edit files via self.files.edit_file / self.files.write)

    # 2. Target test
    tgt = await self.bash.run(f"pytest {failing_test_path} -x")
    step_passed = tgt.return_code == 0
    self.todo.comment(
        step_id,
        f"🧪 target test {'GREEN' if step_passed else 'still RED'} "
        f"after implementing step",
    )
    if not step_passed:
        # Stay on this step; let the user see what went wrong
        self.message(
            f"Step `{step.title}` didn't flip the test. Output:\n\n"
            f"```\n{tgt.stdout[-2000:]}\n```"
        )
        return_result(RespondResult(kind="GET_USER_INPUT"))

    # 3. Full suite
    full = await self.bash.run("pytest -x --tb=short")
    if full.return_code != 0:
        self.todo.comment(step_id, "⚠️ regression in another test; investigating")
        self.message(
            f"Step `{step.title}` made the target test pass but broke "
            f"something else:\n\n```\n{full.stdout[-2000:]}\n```\n\n"
            f"Fix forward or revert?"
        )
        return_result(RespondResult(kind="GET_USER_INPUT"))

    # 4. Commit (or show-and-pause if user opted in)
    if show_diffs:
        diff = await self.bash.run("git diff --stat")
        self.message(f"Step `{step.title}` ready to commit:\n\n```\n{diff.stdout}\n```\n\n"
                     f"Reply `yes` to commit, or describe changes.")
        return_result(RespondResult(kind="GET_USER_INPUT"))

    msg = f"{step.title}\n\nPart of TDD for: {umbrella.title}"
    await self.bash.run("git add -A")
    commit = await self.bash.run(f"git commit -m {msg!r}")
    sha = (await self.bash.run("git rev-parse --short HEAD")).stdout.strip()
    commits.append(sha)
    self.todo.set_var(umbrella.id, "commits", commits)
    self.todo.done(step_id)
    self.todo.comment(umbrella.id, f"✅ step committed {sha} — {step.title}")
```

## Final verification

Once all steps are done, run the full suite once more and capture
results:

```python
final = await self.bash.run("pytest --tb=short")
passed = final.return_code == 0
self.todo.set_var(umbrella.id, "test_results", {
    "cmd": "pytest --tb=short",
    "passed": passed,
    "stdout_tail": final.stdout[-1000:],
})
diff_summary = (await self.bash.run(
    "git log --oneline HEAD~%d..HEAD" % len(commits)
)).stdout.strip()
self.todo.set_var(umbrella.id, "diff_summary", diff_summary)

if passed:
    self.todo.comment(umbrella.id, f"✅ all tests GREEN across {len(commits)} commits")
    next_step = f"/review {umbrella.id}"
else:
    self.todo.comment(umbrella.id, "⚠️ final run still has failures")
    next_step = "investigate failures before /review"

self.message(
    f"# TDD — {umbrella.title}\n\n"
    f"**Commits:** {len(commits)}\n"
    f"{diff_summary}\n\n"
    f"**Test suite:** {'✅ all green' if passed else '❌ still failing'}\n\n"
    f"Next: `{next_step}`"
)
return_result(RespondResult(kind="GET_USER_INPUT"))
```

## Guidelines

- **Red → Green → (sometimes) Refactor.** Don't refactor during the
  green step. Get the test passing first, then refactor only if the
  code is obviously hurting.
- **Small commits.** One plan step = one commit. Don't squash until
  `/ship`.
- **Stop on unexpected regressions.** Don't paper over a broken test
  by marking it xfail — that's how real bugs get buried.
- **No scope creep.** `/tdd` implements the `fix_plan`, nothing
  else. If you spot another bug, add a comment to the umbrella todo
  and move on.
