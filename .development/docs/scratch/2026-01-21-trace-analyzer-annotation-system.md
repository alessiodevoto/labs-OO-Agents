# Trace Analyzer Annotation System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a systematic workflow for annotating agent traces and evaluating the trace analyzer agent's diagnosis quality.

**Architecture:** Users annotate traces in the trace-viewer web UI at the failing span. A Claude Code skill normalizes these raw annotations into structured eval data (failing_methods, human_tags, human_comment). The eval pipeline transforms annotations and runs LLM judge scorers to measure analyzer quality.

**Tech Stack:** Python 3.12, FastAPI (trace-viewer backend), eval_pipeline framework, Claude Code skills

---

## Phase 1: Fix Current Blockers

### Task 1.1: Fix Pydantic Validation Error in OverviewResult

The `mechanical_findings` field expects `list[str]` but the agent returns `list[dict]`.

**Files:**
- Modify: `util/e2e_optimization/src/e2e_optimization/diagnostic_report.py`
- Test: Manual - run analyzer on a trace

**Step 1: Read the current OverviewResult model**

Run: Read `diagnostic_report.py` and find `OverviewResult` class

**Step 2: Update mechanical_findings type to accept dicts**

```python
# In OverviewResult class, change:
mechanical_findings: list[str] = Field(default_factory=list)

# To:
mechanical_findings: list[str | dict] = Field(default_factory=list)
```

**Step 3: Run analyzer to verify fix**

Run: `python -m e2e_optimization.analyzer_agent /path/to/test/trace.006trace.jsonl`
Expected: Phase 1 completes without validation error

**Step 4: Commit**

```bash
git add util/e2e_optimization/src/e2e_optimization/diagnostic_report.py
git commit -m "fix: allow dict in OverviewResult.mechanical_findings"
```

---

### Task 1.2: Verify Eval Pipeline Template Substitution Fix

The template fix from earlier needs verification with a full eval run.

**Files:**
- Already modified: `util/eval_pipeline/src/eval_pipeline/scoring.py`
- Test: `experiments/trace_analyzer_eval/config.yaml`

**Step 1: Run eval with DEBUG to see judge prompts**

Run:
```bash
cd /Volumes/dev/dev/fix
DEBUG_JUDGE_INPUT=results/debug python -m eval_pipeline \
  --config experiments/trace_analyzer_eval/config.yaml \
  --runs 1 --parallel 1 --limit 1
```

**Step 2: Check debug output for template substitution**

Run: `cat results/debug/judge_input_*.txt | head -50`
Expected: See actual values like `Expected outcome: FAILURE` not `{expected[outcome]}`

**Step 3: Commit template fix if not already committed**

```bash
git add util/eval_pipeline/src/eval_pipeline/scoring.py
git commit -m "fix: support nested dict template syntax in LLMJudgeScorer"
```

---

## Phase 2: Annotate Initial Trace Set

### Task 2.1: Identify 10-15 Candidate Traces

Find diverse failure traces from recent eval runs to annotate.

**Files:**
- Read: `experiments/evaluation-ablations/results/*/traces/*.006trace.jsonl`
- Create: `docs/scratch/trace-annotation-candidates.md`

**Step 1: List recent eval result directories**

Run: `ls -lt experiments/evaluation-ablations/results/ | head -10`

**Step 2: Find failed traces with diverse patterns**

Look for:
- Different benchmarks (dabstep, bfcl, tau_bench)
- Different failure modes (wrong answer, format error, timeout)
- Different agents/models

**Step 3: Create candidate list**

Create `docs/scratch/trace-annotation-candidates.md`:
```markdown
# Trace Annotation Candidates

## Selection Criteria
- Mix of benchmarks
- Diverse failure modes
- Clear failure point identifiable

## Candidates

| # | Trace Path | Benchmark | Failure Mode | Notes |
|---|------------|-----------|--------------|-------|
| 1 | .../dabstep_49.006trace.jsonl | dabstep | wrong-computation | count vs rate |
| 2 | ... | ... | ... | ... |
```

**Step 4: Commit candidate list**

```bash
git add docs/scratch/trace-annotation-candidates.md
git commit -m "docs: add trace annotation candidate list"
```

---

### Task 2.2: Annotate First Trace in Trace-Viewer

Manual annotation using existing trace-viewer UI.

**Files:**
- Use: trace-viewer web UI
- Output: annotations stored in trace-viewer backend

**Step 1: Start trace-viewer**

Run: `cd util/trace-viewer && python -m backend.main`
Open: http://localhost:5001

**Step 2: Load first candidate trace**

Navigate to the trace file in the UI

**Step 3: Find the failing span**

Browse the trace timeline, identify where the agent made the wrong decision

**Step 4: Add annotation at failing span**

Click the span, press 'a' to annotate:
- Comment: Describe what went wrong (e.g., "Agent used fraud COUNT instead of RATE")
- Tags: Add behavior tags (e.g., `wrong-computation`, `count-vs-rate`)
- Hypothesis tags: Add if known (e.g., `hypothesis:missing-documentation`)

**Step 5: Add to eval set**

Click "+ Eval Set" button:
- Tier: `frontier` (or `stable` if confident)
- Source: benchmark name (e.g., `dabstep`)

---

### Task 2.3: Repeat Annotation for Remaining Traces

Repeat Task 2.2 for each trace in the candidate list.

**Target:** 10-15 annotated traces with diverse failure modes

---

## Phase 3: Build Annotation Normalization Skill

### Task 3.1: Create Skill Scaffold

**Files:**
- Create: `.claude/skills/normalize-trace-annotation.md`

**Step 1: Create skill file**

```markdown
---
name: normalize-trace-annotation
description: Transform raw trace-viewer annotations into structured eval data
---

# Normalize Trace Annotation

## Overview

This skill transforms raw annotations from the trace-viewer into the structured
format required for trace analyzer evaluation.

## Inputs

The user has annotated a trace in the trace-viewer. The skill needs:
1. Trace path (from user or current context)
2. Session ID to fetch annotations from trace-viewer API

## Workflow

### Step 1: Fetch Raw Annotations

```bash
curl http://localhost:5001/api/traces/{session_id}/annotations
```

### Step 2: Load Trace Structure

Use TraceExplorer to understand the trace:
- What agent/method was running?
- What session_ids exist?
- What was the outcome?

### Step 3: Extract Failing Method Info

From the annotated span, extract:
- `agent_name`: From span attributes
- `method_name`: From span name or parent
- `session_id`: From span's session
- `turn_index`: If applicable

### Step 4: Build Eval Expected Format

```json
{
  "kwargs": {
    "trace_path": "/path/to/trace.006trace.jsonl"
  },
  "expected": {
    "outcome": "FAILURE",
    "human_tags": ["tag1", "tag2"],
    "human_comment": "Comment from annotation",
    "failing_methods": [
      {
        "agent_name": "X",
        "method_name": "Y",
        "session_id": "Z"
      }
    ]
  }
}
```

### Step 5: Ask Clarifying Questions (if needed)

If ambiguous:
- Multiple annotated spans → ask which is primary
- Missing tags → suggest common tags
- Unclear failure mode → ask for clarification

### Step 6: Append to Eval JSONL

Append the normalized entry to:
`experiments/trace_analyzer_eval/tests/data/traces.jsonl`

## Output

Confirm the entry was added and show summary.
```

**Step 2: Commit skill**

```bash
git add .claude/skills/normalize-trace-annotation.md
git commit -m "feat: add normalize-trace-annotation skill scaffold"
```

---

### Task 3.2: Implement Annotation Fetching

**Files:**
- Modify: `.claude/skills/normalize-trace-annotation.md`

**Step 1: Add trace-viewer API integration code**

Add Python code block to skill that fetches annotations:

```python
import requests

def fetch_annotations(session_id: str, base_url: str = "http://localhost:5001") -> list[dict]:
    """Fetch annotations for a trace session from trace-viewer API."""
    response = requests.get(f"{base_url}/api/traces/{session_id}/annotations")
    response.raise_for_status()
    return response.json()
```

**Step 2: Add TraceExplorer integration**

```python
from trace_explorer import TraceExplorer

def load_trace_context(trace_path: str) -> dict:
    """Load trace and extract context for annotation normalization."""
    trace = TraceExplorer.from_file_with_eval(trace_path)

    return {
        "outcome": "FAILURE" if trace.eval_result and not trace.eval_result.get("passed") else "SUCCESS",
        "sessions": [
            {
                "session_id": s.session_id,
                "agent_name": s.agent_name,
                "method_name": s.method_name,
            }
            for s in trace.sessions
        ]
    }
```

**Step 3: Commit**

```bash
git add .claude/skills/normalize-trace-annotation.md
git commit -m "feat: add annotation fetching to normalize skill"
```

---

### Task 3.3: Implement Normalization Logic

**Files:**
- Modify: `.claude/skills/normalize-trace-annotation.md`

**Step 1: Add span-to-method extraction**

```python
def extract_failing_method(annotation: dict, trace: TraceExplorer) -> dict:
    """Extract failing method info from annotated span."""
    span_id = annotation.get("span_id")

    # Find the session containing this span
    for session in trace.sessions:
        if session.session_id in span_id or any(
            span_id in str(turn) for turn in session.turns
        ):
            return {
                "agent_name": session.agent_name,
                "method_name": session.method_name,
                "session_id": session.session_id,
            }

    # Fallback: use first session
    if trace.sessions:
        s = trace.sessions[0]
        return {
            "agent_name": s.agent_name,
            "method_name": s.method_name,
            "session_id": s.session_id,
        }

    return {"agent_name": "unknown", "method_name": "unknown", "session_id": span_id}
```

**Step 2: Add full normalization function**

```python
def normalize_annotation(trace_path: str, annotations: list[dict]) -> dict:
    """Transform raw annotations into eval expected format."""
    trace = TraceExplorer.from_file_with_eval(trace_path)

    # Determine outcome from eval result
    outcome = "FAILURE"
    if trace.eval_result and trace.eval_result.get("passed"):
        outcome = "SUCCESS"

    # Collect tags and comments from all annotations
    all_tags = []
    comments = []
    failing_methods = []

    for ann in annotations:
        # Extract tags (exclude hypothesis: prefix for human_tags)
        tags = ann.get("tags", [])
        human_tags = [t for t in tags if not t.startswith("hypothesis:")]
        all_tags.extend(human_tags)

        # Extract comment
        if ann.get("comment"):
            comments.append(ann["comment"])

        # Extract failing method from span
        failing_methods.append(extract_failing_method(ann, trace))

    # Deduplicate
    all_tags = list(set(all_tags))

    return {
        "kwargs": {
            "trace_path": trace_path
        },
        "expected": {
            "outcome": outcome,
            "human_tags": all_tags,
            "human_comment": " | ".join(comments) if comments else "",
            "failing_methods": failing_methods
        }
    }
```

**Step 3: Commit**

```bash
git add .claude/skills/normalize-trace-annotation.md
git commit -m "feat: add normalization logic to skill"
```

---

### Task 3.4: Add Interactive Refinement

**Files:**
- Modify: `.claude/skills/normalize-trace-annotation.md`

**Step 1: Add clarifying question prompts**

```markdown
## Clarifying Questions

If the skill detects ambiguity, ask:

### Multiple Annotated Spans
"I found annotations on {n} spans. Which is the PRIMARY failure point?"
- Option A: span at turn {x} - "{description}"
- Option B: span at turn {y} - "{description}"
- Option C: Include all as failing_methods

### Missing Tags
"Your annotation has no behavior tags. Common tags for this pattern:"
- wrong-computation
- task-misinterpretation
- missing-documentation
- format-error
Which apply? (comma-separated, or type custom)

### Unclear Comment
"The comment '{comment}' is brief. Would you like to expand it for the eval?"
```

**Step 2: Commit**

```bash
git add .claude/skills/normalize-trace-annotation.md
git commit -m "feat: add interactive refinement to normalize skill"
```

---

## Phase 4: Run Eval Baseline

### Task 4.1: Run Full Eval on Annotated Set

**Files:**
- Use: `experiments/trace_analyzer_eval/config.yaml`
- Output: `results/trace_analyzer/`

**Step 1: Verify annotations are in eval JSONL**

Run: `wc -l experiments/trace_analyzer_eval/tests/data/traces.jsonl`
Expected: 10+ lines (one per annotated trace)

**Step 2: Run eval with both models**

Run:
```bash
cd /Volumes/dev/dev/fix
python -m eval_pipeline \
  --config experiments/trace_analyzer_eval/config.yaml \
  --runs 1 --parallel 5
```

**Step 3: Analyze results**

Run:
```bash
cat results/trace_analyzer/*/traceanalyzer_*.006eval.jsonl | \
  python -c "import sys,json; lines=[json.loads(l) for l in sys.stdin if l.strip()]; \
  results=[l for l in lines if l.get('_type')=='result']; \
  passed=sum(1 for r in results if r.get('passed')); \
  print(f'Pass rate: {passed}/{len(results)} ({100*passed/len(results):.1f}%)')"
```

**Step 4: Document baseline in journal**

Update `docs/scratch/dabstep-optimization-journal.md` with:
- Pass rate per model
- Common failure patterns
- Next steps

**Step 5: Commit results**

```bash
git add docs/scratch/dabstep-optimization-journal.md
git commit -m "docs: add trace analyzer eval baseline results"
```

---

## Phase 5: Iterate and Improve (Future)

### Task 5.1: Tag Taxonomy Clustering

Build automated clustering of similar tags to normalize taxonomy.

### Task 5.2: Update Analyzer Failure Modes

Based on annotation patterns, update the hardcoded failure modes in `analyzer_agent.py`.

### Task 5.3: Eval Set Growth

Continue annotating traces as you work on other projects, using the normalize skill.

---

## Summary

| Phase | Tasks | Status |
|-------|-------|--------|
| 1. Fix Blockers | 1.1-1.2 | Ready |
| 2. Annotate Traces | 2.1-2.3 | Ready |
| 3. Build Skill | 3.1-3.4 | Ready |
| 4. Run Baseline | 4.1 | Ready |
| 5. Iterate | 5.1-5.3 | Future |

**Total estimated tasks:** ~15 bite-sized steps
**Recommended approach:** Subagent-driven for Phases 1-2, then review before Phase 3
