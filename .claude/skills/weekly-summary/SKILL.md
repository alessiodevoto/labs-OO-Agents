---
name: weekly-summary
description: Summarize the past week's development work from git commits, GitLab merge requests, and GitLab issues. Produces "What we achieved" + "What's next" sections. Use when the user asks for a weekly summary, weekly update, status report, or wants to know what was accomplished last week and what's coming up.
---

# Weekly Summary

Generate a concise, executive-style weekly update with two sections:

1. **What we achieved last week** — completed work (commits, merged MRs, closed issues).
2. **What's next** — 1-2 high-level bullets categorizing open issues by theme.

## Audience

Write for a **developer who has heard of the project — maybe saw a presentation a month ago — but does not work on it day-to-day.** They are technical enough to follow concepts, but they do not know your internal class names, MR numbers, branch conventions, or which issue is which.

Four rules that follow from this:

1. **No MR or issue references.** Drop `!NN` and `#NN` entirely. They are noise to this audience — the reader cannot click them, the numbers carry no information, and counts ("~10 open issues") just create dread without illumination. If a specific item is worth naming, name it by what it does, not by its number.
2. **Translate internal names into capabilities.** `BashSession`, `TruncationConfig`, `CodeAct`, `litellm`, `ShellTools` — these mean nothing to the audience. Replace with what they *do* ("the shell session that runs agent commands", "how the framework trims long context", "the code-execution strategy", "the LLM client layer", "the shell-and-repo tools agents use to edit code"). It is fine to drop a name once after explaining it, but never lead with the name.
3. **Lead with user-visible impact — but include feedback loops.** The audience cares about two things: *what can the framework now do that it couldn't before?* (features) and *how is the team learning what to improve next?* (measurement, evals, telemetry, dashboards). Both belong in the summary. Pure refactors with no observable effect can be skipped or folded into a single "internal cleanup" line.
4. **Treat measurement and feedback loops as first-class.** New telemetry, evals, instrumentation, and dashboards are *not* internal plumbing — they signal that the project has a credible iteration loop, which is exactly what a non-day-to-day reader uses to judge whether the project is healthy. Surface them with the framing *"the team can now see / measure / learn X"*, not *"added metrics for Y"*. When something ships in this category, either give it its own bullet or fold it explicitly into the related capability bullet — never drop it because "it's just observability."

A good test: **can a reader who only sees the bullets understand the trajectory of the project this week?** Not the list of activities — the trajectory. If the bullets read as a changelog, rewrite them as a story. And before finalizing, scan the merged work once more with this question: *"did anything ship this week that lets the team measure or learn something new about the system?"* If yes, make sure it's visible in the summary.

## Workflow

### Step 1: Gather completed work from the past 7 days

Define a portable "7 days ago" timestamp once so the rest of the workflow can reuse it. The fallback chain covers both GNU `date` (Linux) and BSD `date` (macOS):

```bash
SINCE="$(date -d '7 days ago' --iso-8601 2>/dev/null || date -v-7d '+%Y-%m-%d')"
```

Commits on main:

```bash
git log main --since="7 days ago" --oneline --no-merges
```

Merged MRs (note: this `glab` build uses `--merged`, not `--state=merged`):

```bash
# Narrow to MRs touched in the past 7 days where supported by your glab build:
glab mr list --merged --updated-after="$SINCE" --per-page=100
# Fallback if --updated-after is not available:
glab mr list --merged --per-page=100
```

`--updated-after` filters on the *updated_at* field, which is a superset of "merged in the last 7 days" — an MR can be updated without being newly merged. For any MR that's not obviously fresh from the listing summary, confirm by inspecting the `merged_at` field via `glab mr view <IID>`. Do **not** use the listing's "Created at" / "X days ago" label as a proxy for merge date.

Closed issues:

```bash
# Narrow to issues touched in the past 7 days where supported:
glab issue list --closed --updated-after="$SINCE" --per-page=100
# Fallback:
glab issue list --closed --per-page=100
```

Same caveat as MRs: the listing's "Created at" / "X days ago" label is the *creation* date, not the closure date — an issue created three months ago and closed yesterday will look stale in the listing but belongs in the summary. For any candidate, verify by inspecting `closed_at` via `glab issue view <IID>`.

For details on a specific MR or issue:

```bash
glab mr view <MR_IID>
glab issue view <ISSUE_IID>
```

**Read the body of substantial MRs — do not judge from the subject line alone.** A one-line commit subject systematically *undersells* large work: "redesign X export" can hide a from-scratch, multi-phase rebuild that is the single biggest thing that shipped. Before deciding what to feature or cut, rank merged work by diff size and open the largest handful — plus any whose subject is opaque jargon (acronyms, "redesign…", "refactor…") — and read what they actually did:

```bash
# Rank merged commits by churn to find the heavy hitters:
git log main --since="7 days ago" --no-merges --stat --format="%h %s" | less
# Or read the MR description (often a design doc + phase list) for any opaque/large item:
glab mr view <MR_IID>
```

`git ... --stat` shows *which files* changed, not *why it matters* — for significance, read the MR description, not just the stat.

### Step 2: Gather open work for "What's next"

Open MRs (work currently in flight) — note `--opened` is deprecated; bare `glab mr list` returns open by default:

```bash
glab mr list --per-page=50
```

Open issues (the backlog feeding the next week):

```bash
glab issue list --per-page=100
```

If issues use labels for categorization, group by label:

```bash
glab issue list --label=<label> --per-page=50
```

### Step 2.5: Gather work from related repos

Some team members work in adjacent repos that ship alongside the primary project. For each one configured below, pull merged MRs and closed issues from the past 7 days, plus a quick glance at open work to feed "What's next" if anything notable is in flight. Use `-R <namespace/repo>` to target the repo.

**Related repos (edit per team — replace the example below with the adjacent repos your team actually ships from; remove this section entirely if there are none):**
- `pfurgale/kdd-cup-2026` — KDD Cup 2026 challenge

For each configured related repo, run the same date-narrowed queries as the primary repo:

```bash
REPO=<namespace/repo>
# Reuses $SINCE from Step 1; re-export if running in a fresh shell:
# SINCE="$(date -d '7 days ago' --iso-8601 2>/dev/null || date -v-7d '+%Y-%m-%d')"
glab -R "$REPO" mr list --merged --updated-after="$SINCE" --per-page=50
glab -R "$REPO" issue list --closed --updated-after="$SINCE" --per-page=50
glab -R "$REPO" mr list --per-page=20      # open MRs (in-flight)
glab -R "$REPO" issue list --per-page=20   # open issues (backlog)
```

Each related repo gets **at most one bullet** in "What we achieved" (compressed summary, no theme breakdown), and optionally a one-line mention in "What's next" if there's notable in-flight work.

### Step 3: Analyze and group

**For "What we achieved":**
- Group completed work from the **primary repo** into 3-5 logical themes that map to *capabilities the project now has* — not categories of activity (features/fixes/refactors). Bad theme: "Bug Fixes". Good theme: "Agents can now reliably edit large codebases".
- Order by significance, most impactful first. If a theme would only land for an internal reader, drop it or merge it.
- If a commit is unclear, inspect via `git show <sha> --stat`. If it is large or opaquely named, read its MR description (see Step 1) — significance is rarely visible in the subject line.
- For each **related repo**, add exactly one bullet at the end summarizing the week's activity at a high level.

**For "What's next":**
- Look at open issues + open MRs together — they represent the active workstream.
- Categorize into **1-2 high-level themes** named by user-visible direction, not internal labels. Examples: "Making agents easier to author", "Hardening reliability for long-running agent tasks".
- **One bullet = one coherent theme.** If a bullet joins two unrelated efforts with "and" (e.g. user onboarding *and* an internal runtime fix), split them or drop the weaker one — a conflated bullet reads as two half-thoughts.
- Do **not** cite issue counts. The audience cannot calibrate "10 open issues" against the project's normal volume, so the number reads as either alarming or pointless. Describe the *shape* of the work instead.
- If a theme has a clearly named in-flight effort worth surfacing, describe what it does in plain terms (no MR number, no branch name).

### Step 4: Write the summary

```markdown
**Project Name** — Weekly Update

**What we achieved**

- **Capability or direction.** One-to-two sentences in plain terms: what's now possible / more reliable / better, and why a non-day-to-day reader should care. No MR or issue numbers, no internal class names.
- **Another capability.** …

**What's next**

- **High-level direction.** Plain-language description of the focus area and the most notable in-flight effort, named by what it does.
- **Second direction.** Same shape — keep it to 1-2 themes total.
```

**Length budget.** Target ≤ ~250 words total, fitting on one screen. Then do an explicit **tightening pass** before presenting:
- Delete any phrase that sounds good but states no fact ("at scale", "far less of a black box", "correct by construction", "robust and scalable").
- Cut clauses that merely restate the bullet's bold title in other words.
- If a bullet runs past two sentences, the third is almost always compressible into the first two.

### Step 5: Emit a sources appendix (for the author, not the reader)

The reader-facing summary carries no MR/issue numbers — but the person *running* this skill needs traceability to trust and edit it. After the summary, append a collapsed block mapping each bullet to its sources, so "where did this come from?" and "give me the links" are answered up front:

```markdown
<details>
<summary>Sources (for the author — not part of the summary)</summary>

- **<bullet title>** — !312 (ATIF exporter redesign), !349 (shell bake-off)
- **<bullet title>** — #228, !360 (trace-explorer thin client)
…
</details>
```

Use full GitLab URLs if the summary will be shared somewhere clickable (`<base>/-/merge_requests/<IID>`, `<base>/-/issues/<NN>`); get `<base>` from `git remote get-url origin`.

### Step 6: Write the summary to `WEEKLY_SUMMARY.md`

After presenting the summary in chat, write the final summary — the reader-facing sections **plus** the collapsed sources appendix — to `WEEKLY_SUMMARY.md` in the repo root, overwriting any previous week's file. This gives a stable, shareable artifact on disk.

- Path: repo root (e.g. `<repo>/WEEKLY_SUMMARY.md`); get the repo root from `git rev-parse --show-toplevel`.
- Content: the same Markdown shown in chat — `## What we achieved`, `## What's next`, then the `<details>` sources block.
- Always overwrite; this file reflects the most recent run, not a history.

## Format Rules

- Use the project name from the repo (e.g., "NeMo OO Agents", "AAD Framework").
- Each bullet starts with a **bold theme title** followed by a period, then plain-text description.
- Keep each bullet to 1-2 sentences max.
- **No MR numbers (`!NN`), issue numbers (`#NN`), branch names, or commit hashes.** This summary is for readers who cannot follow those references.
- **Avoid internal jargon and class names.** If you must name an internal concept, briefly say what it does the first time it appears, then use the name sparingly. Prefer the capability over the name.
- "What we achieved": 3-5 bullets, ordered by impact, active past tense ("Added…", "Made…", "Replaced…").
- **"What's next": strictly 1-2 high-level bullets.** Categorization of the direction, not enumeration of the backlog. No issue counts.
- Focus on *what's now possible or more reliable, and why it matters* — not raw activity.

## Example Output

**AAD Framework** — Weekly Update

**What we achieved**

- **Plug-in metrics make experiments comparable across runs.** Replaced the old single-purpose results module with a metrics layer that can mix LLM-as-judge, pytest assertions, and trace-derived statistics — and writes everything to a structured log so old runs can be re-analyzed without re-running them.
- **Agents can now be graded against a written skill.** A new judge reads a skill description and decides whether an agent actually followed it, giving us a quality signal for the long tail of behaviors that don't have a pass/fail test.
- **Long experiments resume instead of restarting.** The new run loop checkpoints on every success, supports parallel workers, and watches output stability — so a 12-hour eval that crashes at hour 11 picks up where it left off.
- **KDD Cup 2026 challenge.** Stood up the baseline retrieval pipeline and dataset loader and closed out the early evaluation-harness work; the team now has a working leaderboard scaffold to iterate on.

**What's next**

- **Hardening the evaluation framework.** Cleaning up how checks are declared, making the judge more consistent run-to-run, and reducing flakiness in trace collection — most notable in-flight effort is a decorator-based way to write checks, currently in review.
- **Sandbox and CI ergonomics.** Smoothing out Docker networking inside our sandbox and making CI runners more robust to transient failures; a proxy-aware sandbox mode is in draft. KDD Cup side, ranking-model experiments are in flight.
