# MR Metrics Visibility Options

## Problem Statement

Currently, capability metrics are published using GitLab's metrics report feature with OpenMetrics format. This has significant UX issues:

1. **Yellow exclamation mark (⚠️) on ANY change** - Whether metrics improved or degraded, reviewers see the same warning icon
2. **Poor formatting** - Metrics are shown in a small, collapsible section that's easy to miss
3. **No intuitive feedback** - Reviewers can't quickly determine if capabilities improved or worsened
4. **Not prominent** - The metrics section is buried in the MR widget

**Goal**: Provide at-a-glance visibility into whether capabilities improved or worsened, with clear visual indicators.

---

## Options Analysis

### Option 1: MR Comment with Formatted Summary (⭐ RECOMMENDED)

**Approach**: Post an automatic comment on the MR with a beautifully formatted metrics summary, including comparison against the base branch.

**Example Output**:
```markdown
## 📊 Capability Test Results

| Metric | Base (main) | This MR | Change |
|--------|-------------|---------|--------|
| Tests Passed | 16/18 | 17/18 | ✅ +1 |
| Success Rate | 88.9% | 94.4% | ✅ +5.5% |

### Summary: ✅ Capabilities IMPROVED

<details>
<summary>Per-test breakdown</summary>

| Test | Base | MR | Status |
|------|------|-----|--------|
| sentiment_single | ✅ | ✅ | ➖ Same |
| calculate_batch | ❌ | ✅ | ✅ Fixed |
| router_basic | ✅ | ✅ | ➖ Same |
</details>

---
*Run: [capability-test #12345](link) | Commit: abc123*
```

**Pros**:
- ✅ **Highly visible** - Comments appear prominently in MR discussion
- ✅ **Rich formatting** - Tables, emojis, collapsible sections
- ✅ **Comparison built-in** - Can fetch baseline from main branch
- ✅ **Updatable** - Can edit the same comment on re-runs
- ✅ **Works with GitLab Free tier**

**Cons**:
- ❌ Requires GitLab API calls from CI
- ❌ Needs to track comment ID for updates

**Implementation Complexity**: Medium

**Implementation**:
```yaml
capability-test:
  script:
    # ... run tests ...
    - |
      # Post/update MR comment with results
      python util/ci/post_mr_comment.py \
        --results experiments/capability_eval/results/ \
        --compare-branch $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
```

---

### Option 2: Job Name with Status Indicator

**Approach**: Dynamically set the CI job name to include the result status.

**Example**: Instead of `capability-test`, show `capability-test ✅ 94.4% (+5.5%)`

**Pros**:
- ✅ Very simple to implement
- ✅ Visible in pipeline overview

**Cons**:
- ❌ Limited space (job names truncate)
- ❌ No comparison against baseline
- ❌ Can't show detailed breakdown
- ❌ GitLab doesn't support dynamic job names well

**Implementation Complexity**: Low (but limited capability)

---

### Option 3: External Status Check

**Approach**: Use GitLab's external status checks API to create a pass/fail check with custom description.

**Example**: Shows as a status check "Capabilities: ✅ Improved (94.4%, +5.5%)" or "Capabilities: ❌ Regressed (82.2%, -6.7%)"

**Pros**:
- ✅ Appears as a formal status check
- ✅ Can block merge if desired
- ✅ Clear pass/fail semantics

**Cons**:
- ❌ Requires GitLab Premium or Ultimate
- ❌ Limited text in status description
- ❌ More complex API setup

**Implementation Complexity**: Medium-High

---

### Option 4: Custom Badge Generation

**Approach**: Generate an SVG badge showing metrics status, host as job artifact, embed in MR.

**Example**: ![Capabilities](https://img.shields.io/badge/capabilities-94.4%25_%E2%86%91_+5.5%25-brightgreen)

**Pros**:
- ✅ Very visual
- ✅ Can be embedded anywhere

**Cons**:
- ❌ Requires hosting/serving badge images
- ❌ Can't easily update MR description automatically
- ❌ Manual embedding in MR description
- ❌ Complex artifact URL handling

**Implementation Complexity**: High

---

### Option 5: Keep Metrics Report + Add Comment Summary

**Approach**: Keep the existing metrics.txt report AND add an MR comment for visibility.

**Pros**:
- ✅ Best of both worlds
- ✅ Detailed data still available
- ✅ High visibility summary

**Cons**:
- ❌ Slightly more CI complexity

**Implementation Complexity**: Medium

---

## Recommendation: Option 1 (MR Comment)

**Rationale**:
1. **Most visible** - Comments are front and center in MRs
2. **Most flexible** - Can include any formatting, tables, details
3. **Comparison-ready** - Can easily show base vs MR
4. **No additional GitLab tier required** - Works with Free
5. **Industry standard** - Many CI tools use this pattern (CodeRabbit, Codecov, etc.)

---

## Implementation Plan

### Phase 1: Create MR Comment Script

Create `util/ci/post_mr_comment.py`:

```python
#!/usr/bin/env python3
"""Post capability test results as MR comment."""

import os
import json
import requests
from pathlib import Path

GITLAB_API = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
PROJECT_ID = os.environ["CI_PROJECT_ID"]
MR_IID = os.environ.get("CI_MERGE_REQUEST_IID")
JOB_TOKEN = os.environ["CI_JOB_TOKEN"]

def get_baseline_metrics(target_branch: str) -> dict | None:
    """Fetch baseline metrics from target branch artifacts."""
    # Implementation: fetch from latest pipeline on target branch
    ...

def format_comment(current: dict, baseline: dict | None) -> str:
    """Format the MR comment with comparison."""
    ...

def find_existing_comment() -> int | None:
    """Find our previous comment to update instead of creating new."""
    ...

def post_or_update_comment(body: str):
    """Post new comment or update existing one."""
    headers = {"PRIVATE-TOKEN": JOB_TOKEN}  # or use CI_JOB_TOKEN with appropriate scope

    existing = find_existing_comment()
    if existing:
        # Update existing comment
        url = f"{GITLAB_API}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes/{existing}"
        requests.put(url, headers=headers, json={"body": body})
    else:
        # Create new comment
        url = f"{GITLAB_API}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes"
        requests.post(url, headers=headers, json={"body": body})
```

### Phase 2: Update CI Pipeline

```yaml
capability-test:
  script:
    # Run tests
    - uv run python -m eval_pipeline ...
    # Parse results
    - uv run python util/ci/parse_capability_results.py ... --output metrics.txt
    # Post MR comment (only on MRs)
    - |
      if [ -n "$CI_MERGE_REQUEST_IID" ]; then
        uv run python util/ci/post_mr_comment.py \
          --results experiments/capability_eval/results/ \
          --compare-branch "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
      fi
  artifacts:
    reports:
      metrics: metrics.txt  # Keep for historical tracking
```

### Phase 3: Add Baseline Comparison

Store baseline metrics on main branch and compare:

1. On `main` branch: Store metrics as artifact
2. On MR: Fetch baseline from latest `main` pipeline
3. Calculate deltas and format comparison table

---

## API Authentication Note

The `CI_JOB_TOKEN` has limited permissions. For posting MR comments, you may need to:

1. **Use a Project Access Token** with `api` scope, stored as CI variable
2. **Or** grant additional permissions to the CI job token in Project Settings → CI/CD → Token Access

---

## Example Comment Formats

### Improvement:
```
## 📊 Capability Test Results

✅ **Capabilities IMPROVED** from 88.9% → 94.4% (+5.5%)

| Metric | main | This MR | Δ |
|--------|------|---------|---|
| Tests Passed | 16/18 | 17/18 | +1 ✅ |
| Success Rate | 88.9% | 94.4% | +5.5% ✅ |
```

### Regression:
```
## 📊 Capability Test Results

❌ **Capabilities REGRESSED** from 88.9% → 77.8% (-11.1%)

| Metric | main | This MR | Δ |
|--------|------|---------|---|
| Tests Passed | 16/18 | 14/18 | -2 ❌ |
| Success Rate | 88.9% | 77.8% | -11.1% ❌ |

### Failing Tests:
- `sentiment_single` - was passing, now failing
- `calculate_batch` - was passing, now failing
```

### No Change:
```
## 📊 Capability Test Results

➖ **No change** in capabilities (88.9%)

| Metric | main | This MR | Δ |
|--------|------|---------|---|
| Tests Passed | 16/18 | 16/18 | - |
| Success Rate | 88.9% | 88.9% | - |
```

---

## Summary

| Option | Visibility | Comparison | Complexity | Recommendation |
|--------|------------|------------|------------|----------------|
| MR Comment | ⭐⭐⭐ | ✅ | Medium | **✅ Recommended** |
| Job Name | ⭐ | ❌ | Low | Not recommended |
| External Status | ⭐⭐ | ✅ | High | GitLab Premium only |
| Badge | ⭐⭐ | ❌ | High | Not recommended |
| Keep Both | ⭐⭐⭐ | ✅ | Medium | Good alternative |

**Next Step**: Implement Option 1 (MR Comment approach)
