---
name: review
description: Critique the diff from /tdd via up to 4 parallel Doer subagents (correctness / scope / style / security). Surfaces blocking and major findings; hides minor nits unless the user asks.
user-invocable: true
argument-hint: "<umbrella-id>"
---

# Review

**Goal: independent criticism before anything ships.** Four
reviewer subagents, run in parallel, each with a narrow lens:

| Reviewer      | Looks for                                                 |
|---------------|-----------------------------------------------------------|
| `correctness` | logic bugs, missed edge cases, tests that don't cover    |
|               | what they claim, contracts broken                         |
| `scope`       | unrelated changes, drive-by refactors, new abstractions   |
|               | the task didn't need                                      |
| `style`       | conventions from the surrounding code, dead code,         |
|               | over-explanatory comments                                 |
| `security`    | unchecked input, secrets in code/logs, command injection, |
|               | path traversal, unsafe deserialisation                    |

Reads from the umbrella todo:

- `spec` — so reviewers know what was supposed to happen
- `acceptance_criteria` — the bar to measure against
- `commits` + `diff_summary` — what actually happened
- `root_cause` + `fix_plan` — context on why

Writes `review_findings`: a list of `{reviewer, severity, location,
issue, suggestion}` dicts. Severity is `blocking` | `major` | `minor`.

## Arguments

```python
arg = "$ARGUMENTS".strip()
if not arg:
    self.message("Usage: /review <umbrella-id>.")
    return_result(RespondResult(kind="NEED_INPUT", explanation="usage error: need an umbrella todo id for review"))

umbrella_id = arg.split()[0]
umbrella = self.todo.get(umbrella_id)
if umbrella is None:
    self.message(f"No todo {umbrella_id}.")
    return_result(RespondResult(kind="NEED_INPUT", explanation="todo id was not found; need a valid umbrella todo id"))

commits = self.todo.get_var(umbrella.id, "commits") or []
if not commits:
    self.message(f"Todo {umbrella.id} has no commits yet — run /tdd first.")
    return_result(RespondResult(kind="NEED_INPUT", explanation="no commits found; run /tdd before review"))
```

## Build reviewer todos + dispatch in parallel

One sub-todo per reviewer so progress shows in `<todo_status>`.
Parallel-run them with `asyncio.gather`.

```python
spec = self.todo.get_var(umbrella.id, "spec") or {}
acceptance = spec.get("acceptance_criteria", [])
diff_cmd = f"git log --oneline HEAD~{len(commits)}..HEAD"
diff = (await self.bash.run(
    f"git diff HEAD~{len(commits)} -- . ':(exclude)*.lock'"
)).stdout[:20_000]  # cap — reviewers share context

reviewers = [
    ("correctness",
     "Review for logic bugs and missed edge cases. Does the diff do "
     "what the spec says? Are the tests actually covering the claim? "
     "Ignore style/scope/security — other agents handle those."),
    ("scope",
     "Review for scope creep. Are there changes unrelated to the "
     "spec? New abstractions, drive-by refactors, commented-out "
     "code, TODO-for-later comments? Flag each."),
    ("style",
     "Review for style drift — breaks from conventions in the "
     "surrounding code, dead code, over-explanatory comments "
     "('increments counter by 1'). Ignore correctness/scope/security."),
    ("security",
     "Review for security issues: unchecked user input, secrets in "
     "code or logs, command injection, path traversal, unsafe "
     "deserialisation. Flag anything that could be exploited."),
]

REVIEW_PROMPT_TEMPLATE = """
You are a {lens} reviewer. {instructions}

Context:
- Goal: {goal}
- Acceptance criteria:
{criteria}
- Fix plan that was implemented:
{plan}
- Root cause that was being fixed:
{root_cause}

Diff to review ({n_commits} commits):

```diff
{diff}
```

Output JSON list — empty if nothing worth flagging. Each finding:
{{
  "severity": "blocking" | "major" | "minor",
  "location": "path/to/file.py:LINE",
  "issue": "<one-line problem>",
  "suggestion": "<optional fix hint>"
}}

Prefer fewer, higher-signal findings over a long list. If the diff
is clean on your lens, return `[]` and stop.
""".strip()

review_todos = []
for lens, instr in reviewers:
    prompt = REVIEW_PROMPT_TEMPLATE.format(
        lens=lens,
        instructions=instr,
        goal=spec.get("goal", umbrella.title),
        criteria="\n".join(f"  - {c}" for c in acceptance) or "  (none specified)",
        plan="\n".join(f"  {i + 1}. {s}" for i, s in enumerate(
            self.todo.get_var(umbrella.id, "fix_plan") or []
        )) or "  (none)",
        root_cause=self.todo.get_var(umbrella.id, "root_cause") or "(none)",
        n_commits=len(commits),
        diff=diff,
    )
    rt = self.todo.add(f"Review ({lens})", deps=[umbrella.id])
    self.todo.update(rt.id, notes=prompt)
    review_todos.append((lens, rt))
    self.todo.comment(umbrella.id, f"👁 dispatched {lens} reviewer: {rt.id}")

async def _run_reviewer(lens: str, todo):
    summary = await self.make_doer().execute(todo)
    return lens, summary

results = await asyncio.gather(
    *[_run_reviewer(lens, t) for lens, t in review_todos],
    return_exceptions=True,
)
```

## Parse findings + rank

Each reviewer's summary is a string that should contain the JSON list.
Be tolerant — reviewers sometimes wrap JSON in prose.

```python
import json, re
findings: list[dict] = []
for outcome in results:
    if isinstance(outcome, Exception):
        self.todo.comment(umbrella.id, f"⚠️ reviewer crashed: {outcome}")
        continue
    lens, summary = outcome
    # Extract the JSON array from the reviewer's reply
    m = re.search(r"\[\s*(?:\{.*?\}\s*,?\s*)*\]", summary, re.DOTALL)
    if not m:
        continue
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        continue
    for f in parsed:
        f["reviewer"] = lens
        findings.append(f)

self.todo.set_var(umbrella.id, "review_findings", findings)

severity_rank = {"blocking": 0, "major": 1, "minor": 2}
findings.sort(key=lambda f: (severity_rank.get(f.get("severity"), 3), f.get("reviewer", "")))
```

## Present the shortlist

Show blocking + major. Hide minor unless the user asks for the full list.

```python
blocking = [f for f in findings if f.get("severity") == "blocking"]
major = [f for f in findings if f.get("severity") == "major"]
minor = [f for f in findings if f.get("severity") == "minor"]

lines = [f"# Review — {umbrella.title}", ""]
if not findings:
    lines.append("✅ All four reviewers found nothing actionable.")
else:
    if blocking:
        lines += ["## 🛑 Blocking", ""]
        for f in blocking:
            lines.append(f"- **{f['reviewer']}** {f.get('location', '')}: {f.get('issue')}")
            if f.get("suggestion"):
                lines.append(f"  - suggestion: {f['suggestion']}")
    if major:
        lines += ["", "## ⚠️  Major", ""]
        for f in major:
            lines.append(f"- **{f['reviewer']}** {f.get('location', '')}: {f.get('issue')}")
    if minor:
        lines += ["", f"_{len(minor)} minor findings hidden. Ask to see them._"]

lines += [
    "",
    "---",
    "Address blocking issues and re-run `/review`, or skip to "
    f"`/ship {umbrella.id}` if the remaining findings are acceptable.",
]
self.message("\n".join(lines))
self.todo.comment(
    umbrella.id,
    f"👁 review complete: {len(blocking)} blocking, {len(major)} major, "
    f"{len(minor)} minor",
)
return_result(RespondResult(kind="DONE", explanation="review findings presented; waiting for the next user request"))
```

## Guidelines

- **4 reviewers max.** More reviewers = more noise, not more signal.
  If a lens doesn't apply (e.g. pure docs diff → skip security), it's
  fine to omit.
- **Prefer fewer findings.** Reviewers should drop minor nits unless
  they indicate a pattern; the user can ask for the full list.
- **Don't fix in this skill.** `/review` reports. If the user wants
  the blockers fixed automatically, they follow up with `/tdd` on a
  new plan derived from the findings.
