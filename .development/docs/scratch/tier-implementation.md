# Tier Implementation

## Problem Statement

Tests are not categorized by maturity level. Need tiered structure for regression protection vs progress tracking.

## Three-Tier Structure

| Tier | Description | Pass Threshold | CI Behavior |
|------|-------------|----------------|-------------|
| **Stable** | Must pass - regression guard | >=90% | Blocks merge if failing |
| **Frontier** | At the edge of capability | ~60-80% | Track progress, no blocking |
| **Horizon** | Aspirational - cannot yet handle | N/A | Track for future capability |

## Acceptance Criteria

- [x] All tests assigned to a tier (all tests marked as `stable` in config_ci.yaml)
- [x] CI reports grouped by tier (metrics.txt includes tier-specific rates)
- [x] Stable tier blocking implemented (exits 1 if stable < 90%)
- [x] Tier promotion system with auto-MR creation (runs on main, dry-run on MRs)
- [x] Tier progression tracking dashboard (fetches from local artifacts, displays time-series charts)

## Implementation Summary

### Phase 1: Tiering Infrastructure

**Core Changes:**
- Added `Tier` enum to `eval_types.py` (STABLE, FRONTIER, HORIZON)
- Extended data flow: config.yaml → config.py → TestConfig → EvalTest → Sample → EvalTestResult → .006eval.jsonl
- Updated `parse_capability_results.py` to group metrics by tier using OpenMetrics label syntax
- Updated `post_mr_comment.py` to display tier breakdown in MR descriptions
- Added CI threshold checking with awk-based float comparison (stable tier blocks merge)
- All 13 tests classified as `stable` tier initially

**Metrics Format (OpenMetrics):**
```
capability_success_rate_percent 86.21
capability_success_rate_percent{tier="stable"} 100.00
capability_success_rate_percent{tier="frontier"} 0.00
capability_success_rate_percent{tier="horizon"} 0.00
capability_tests_passed 25
capability_tests_passed{tier="stable"} 25
```

**MR Comment Format:**
- Overall success rate displayed prominently
- Collapsible "Per-tier breakdown" table with Stable/Frontier/Horizon rows
- When baseline available: shows `Baseline | This MR | Change` columns with delta-based emojis
- When no baseline: shows `Tier | Status` columns
- No hardcoded threshold logic (only delta-based indicators)

### Phase 2: Tier Promotion System

**Implementation:**
- Script: `util/ci/tier_promotions.py`
- Analyzes latest `.006eval.jsonl` from results directory
- Groups results by base test name (strips `_NNN` numeric suffixes)
- Identifies promotion candidates based on aggregated pass rates

**Promotion Criteria:**
- Frontier → Stable: Pass rate >=90% across 3 runs within same pipeline
- Horizon → Frontier: Pass rate >0% (at least 1/3 passes)

**CI Integration:**
- Tests run with `--runs 3` parameter
- Main branch: Creates MR via GitLab API if promotions found
- MR branches: Dry-run preview only (no MR creation)
- Uses `GITLAB_TOKEN` from CI/CD variables (api scope required)

**MR Creation:**
- Creates branch: `chore/tier-promotion-{sha}` or `chore/tier-promotion-{source-branch}`
- Updates `config_ci.yaml` with minimal diff (line-by-line tier value replacement)
- Commits with descriptive message listing promoted tests
- Opens MR with promotion report in description
- Updates existing MR if branch already exists

### Phase 3: Local Experiment Metrics Dashboard

**Backend (`util/prompt-optimization/viewer/backend/main.py`):**
- Endpoint: `GET /api/experiments/metrics`
- Scans local `experiments/` directory for experiment folders
- Loads `.006eval.jsonl` results from each experiment
- Calculates tier-specific metrics from test results
- Returns nested structure: `{ overall: {...}, stable: {...}, frontier: {...}, horizon: {...} }`
- Each tier contains: `success_rate`, `tests_passed`, `tests_total`
- **Key fix**: `overall_total` computed as sum of all tier totals for consistency
- Defaults to stable if tier is not specified for tiers without data (backwards compatible)

**Frontend (`util/prompt-optimization/viewer/frontend/js/views/experiment-summary.js`):**
- "Historical Metrics" section with tier quick stats
- Auto-loads on experiment view
- Displays current experiment metrics compared to historical data
- Two Chart.js time-series graphs:
  1. Success rates over time (overall + stable/frontier/horizon)
  2. Test counts over time (overall + stable/frontier/horizon)
- **Improved labeling**: X-axis shows full timestamps (`date.toLocaleString()`)
- **Current experiment highlighting**: Labeled "★ Current" with distinct styling (larger radius, golden background, orange border)
- **Zero-value plotting**: All tiers plotted even when success rate is 0%
- **Label overflow fix**: Rotated labels with auto-skip and bottom padding
- Sorted chronologically (oldest to newest) for timeline view

**Data Flow:**
```
Local filesystem
  → Scan experiments/ directory
    → For each experiment: Load .006eval.jsonl
      → Parse results with tier information
        → Group by tier
          → Calculate pass rates and counts
            → Compute overall_total = sum(tier totals)
              → Return to frontend
                → Sort experiments by timestamp
                  → Render Chart.js graphs with improved labels
```

## Reference Implementation Details

### Phase 1 Architecture

Tier information flows through the system:

```
config.yaml (tier: stable)
    ↓
TestConfig (config.py)
    ↓
EvalTest (evaluator.py)
    ↓
Sample (pipeline.py)
    ↓
EvalTestResult (eval_types.py)
    ↓
.006eval.jsonl file
    ↓
parse_capability_results.py (groups by tier)
    ↓
metrics.txt (OpenMetrics format with tier labels)
    ↓
.gitlab-ci.yml (checks stable tier threshold, blocks merge if <90%)
```

### Key Components Modified

#### 1. Tier Enum Definition (`eval_types.py`)

```python
from enum import Enum

class Tier(str, Enum):
    """Test maturity tier."""
    STABLE = "stable"
    FRONTIER = "frontier"
    HORIZON = "horizon"
```

**Rationale:** Defined in `eval_types.py` as a foundational type with no dependencies, used throughout the pipeline and part of `.006eval.jsonl` schema.

#### 2. Config Schema (`config.py`)

```python
from .eval_types import Tier

@dataclass
class TestConfig:
    name: str
    description: str
    tier: Tier  # NEW FIELD
    agent_module: str
    agent_class: str
    # ... other fields
```

Parsing defaults to `Tier.STABLE` if not specified for backwards compatibility.

#### 3. Evaluation Types (`eval_types.py`)

```python
class EvalTestResult(BaseModel):
    agent_class: str
    method: str
    display_name: str | None = None
    tier: Tier  # NEW FIELD (required)
    # ... other fields
```

#### 4. Evaluator (`evaluator.py`)

```python
@dataclass
class EvalTest:
    name: str
    agent_class: type
    method: str
    data: list[Task]
    scorers: list[ScorerConfig]
    tier: Tier  # NEW FIELD (required, before optional fields)
    description: str = ""
```

#### 5. Pipeline (`pipeline.py`)

```python
@dataclass
class Sample:
    task: Task
    method: str
    agent_class: str
    scorers: list[ScorerConfig]
    tier: Tier  # NEW FIELD (required, before optional fields)
    agent_factory: Callable[[], Agent]
    model: str | None = None
    # ... other optional fields
```

**Note:** Positioned before optional fields to satisfy Python dataclass requirements.

#### 6. Metrics Parser (`parse_capability_results.py`)

Groups results by tier using `Tier` enum for iteration:

```python
from eval_pipeline import Tier

def parse_eval_file(eval_file: Path) -> dict[str, Any]:
    # ... parse results ...

    passed_by_tier = {tier.value: 0 for tier in Tier}
    total_by_tier = {tier.value: 0 for tier in Tier}

    for result in results:
        tier = result.get("tier", "stable")
        total_by_tier[tier] += 1
        if result.get("passed", False):
            passed_by_tier[tier] += 1

    success_rate_by_tier = {
        tier: (passed_by_tier[tier] / total_by_tier[tier] * 100)
        if total_by_tier[tier] > 0 else 0
        for tier in passed_by_tier
    }

    return {
        "passed": total_passed,
        "total": total_tests,
        "success_rate": overall_rate,
        "passed_by_tier": passed_by_tier,
        "total_by_tier": total_by_tier,
        "success_rate_by_tier": success_rate_by_tier,
    }
```

Generates OpenMetrics format with tier labels:

```python
def generate_gitlab_metrics(data: dict[str, Any]) -> str:
    lines = [
        f"capability_tests_passed {data['passed']}",
        f"capability_success_rate_percent {data['success_rate']:.2f}",
    ]

    for tier in Tier:
        rate = data["success_rate_by_tier"][tier.value]
        passed = data["passed_by_tier"][tier.value]
        lines.append(f'capability_success_rate_percent{{tier="{tier.value}"}} {rate:.2f}')
        lines.append(f'capability_tests_passed{{tier="{tier.value}"}} {passed}')

    return "\n".join(lines)
```

#### 7. MR Comment Formatter (`post_mr_comment.py`)

Refactored to use `Tier` enum and remove hardcoded thresholds:

```python
from eval_pipeline import Tier

def format_metrics_section(current, baseline=None):
    # Build tier breakdown table
    if baseline:
        # Show: Tier | Baseline | This MR | Change
        for tier in Tier:
            current_rate = current["success_rate_by_tier"].get(tier.value, 0)
            baseline_rate = baseline["success_rate_by_tier"].get(tier.value, 0)
            delta = current_rate - baseline_rate
            emoji = "📈" if delta > 0 else "📉" if delta < 0 else "➡️"
            # ... format row
    else:
        # Show: Tier | Status
        for tier in Tier:
            rate = current["success_rate_by_tier"].get(tier.value, 0)
            # ... format row
```

**Key change:** Removed all threshold-based emoji logic. Only uses delta-based emojis when baseline is available.

#### 8. CI Configuration (`.gitlab-ci.yml`)

```yaml
variables:
  STABLE_THRESHOLD: 90
  FRONTIER_THRESHOLD: 60
  HORIZON_THRESHOLD: 0

capability-test:
  script:
    # Run tests 3 times each
    - uv run python -m eval_pipeline
        --config tests/capability/config_ci.yaml
        --runs 3
        --parallel 40
        --quiet

    # Generate metrics
    - uv run python util/ci/parse_capability_results.py results/ci/ --output metrics.txt

    # Post MR comment
    - |
      if [ -n "$CI_MERGE_REQUEST_IID" ]; then
        uv run python util/ci/post_mr_comment.py results/ci/ \
          --compare-branch "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
      fi

    # Check for tier promotions (main: creates MR, MRs: dry-run)
    - |
      BRANCH="${CI_COMMIT_BRANCH:-${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME}}"
      if [ "$BRANCH" = "main" ]; then
        uv run python util/ci/tier_promotions.py results/ci/ --create-mr || true
      else
        uv run python util/ci/tier_promotions.py results/ci/ --create-mr --dry-run || true
      fi

    # Check tier thresholds (awk for float comparison)
    - |
      STABLE_RATE=$(grep 'capability_success_rate_percent{tier="stable"}' metrics.txt | awk '{print $2}')

      if [ -n "$STABLE_RATE" ]; then
        STABLE_PASS=$(awk -v rate="$STABLE_RATE" -v thresh="$STABLE_THRESHOLD" \
          'BEGIN {print (rate >= thresh) ? "1" : "0"}')

        if [ "$STABLE_PASS" = "0" ]; then
          echo "STABLE TIER FAILED: ${STABLE_RATE}% < ${STABLE_THRESHOLD}%"
          exit 1
        fi
      fi

  allow_failure: false
  artifacts:
    reports:
      metrics: metrics.txt
    paths:
      - capability-results.zip
      - metrics.txt
    expire_in: 7 days
```

### Design Decisions

#### Decision 1: Tier Enum Location
**Choice:** Define `Tier` in `eval_types.py`

**Rationale:**
- Low-level type with no dependencies
- Part of `.006eval.jsonl` schema
- Follows dependency inversion principle (high-level modules import from low-level)

#### Decision 2: Default Tier Value
**Choice:** Default to `Tier.STABLE` if not specified

**Rationale:**
- Backwards compatible with tests lacking tier field
- Conservative default (treat unknown tests as regression guards)
- Forces explicit classification for frontier/horizon

#### Decision 3: OpenMetrics Label Syntax
**Choice:** Use `capability_success_rate_percent{tier="stable"}` format

**Rationale:**
- Standard Prometheus/OpenMetrics label syntax
- GitLab metrics reports natively support labeled metrics
- Enables future time-series queries by tier

#### Decision 4: Blocking Behavior
**Choice:** Only stable tier blocks merge

**Rationale:**
- Stable = regression guard (must maintain quality)
- Frontier = progress tracking (expected to vary)
- Horizon = aspirational (expected to fail)

#### Decision 5: Promotion State Tracking
**Choice:** No persistent state, analyze current pipeline only

**Rationale:**
- Simpler implementation (no state management)
- Using `--runs 3` in single pipeline satisfies "3 consecutive runs"
- Reduces risk of stale state causing incorrect promotions

#### Decision 6: MR Comment Thresholds
**Choice:** Remove hardcoded tier thresholds from `post_mr_comment.py`

**Rationale:**
- Thresholds are CI policy, not reporting concern
- Only show delta-based emojis when baseline available
- Cleaner separation: CI enforces thresholds, reports show data

## Phase 2 Implementation Details

### Tier Promotions Script (`util/ci/tier_promotions.py`)

**Key Functions:**

1. `check_promotions(results_dir: Path) -> tuple[TierPromotions, dict[str, str]]`
   - Finds latest `.006eval.jsonl` file
   - Parses results and groups by base test name (strips `_NNN` suffix)
   - Calculates pass rate across all runs
   - Returns promotion candidates and original tier map

2. `create_mr(promotion_data, tier_map, config_path, gitlab_token, ...)`
   - Reads original `config_ci.yaml` as text
   - Uses `apply_tier_changes()` for minimal diff (line-by-line replacement)
   - Creates branch or updates existing branch
   - Commits changes with descriptive message
   - Creates or updates MR via GitLab API

**Test Name Parsing:**
```python
# Extract base test name from test_case
test_case = result.get("test_case", "")  # "calculate_simple_001"
parts = test_case.rsplit("_", 1)
base_name = parts[0] if len(parts) == 2 and parts[1].isdigit() else test_case
# Result: "calculate_simple"
```

**Promotion Logic:**
```python
for test_name, tests in test_results.items():
    tier = tests[0]["tier"]
    passes = sum(1 for t in tests if t["passed"])
    total = len(tests)
    pass_rate = (passes / total * 100) if total > 0 else 0

    if tier == Tier.FRONTIER.value and pass_rate >= 90.0:
        promotions["stable"].append(test_name)
    elif tier == Tier.HORIZON.value and pass_rate > 0.0:
        promotions["frontier"].append(test_name)
```

**Usage:**
```bash
# Dry-run (show what would be promoted)
python util/ci/tier_promotions.py results/ci/ --create-mr --dry-run

# Create MR (requires GITLAB_TOKEN in environment)
python util/ci/tier_promotions.py results/ci/ --create-mr
```

### GitLab API Integration

**Required Environment Variables:**
- `GITLAB_TOKEN` - Personal access token with `api` scope
- `CI_PROJECT_ID` - GitLab project ID
- `CI_API_V4_URL` - GitLab API endpoint (defaults to https://gitlab.com/api/v4)
- `CI_COMMIT_SHA` - Current commit SHA
- `CI_PIPELINE_ID` - Current pipeline ID
- `CI_COMMIT_BRANCH` or `CI_MERGE_REQUEST_SOURCE_BRANCH_NAME` - Branch name

**API Calls:**
1. `GET /projects/{id}/repository/branches/{branch}` - Check if branch exists
2. `POST /projects/{id}/repository/branches` - Create new branch
3. `POST /projects/{id}/repository/commits` - Commit changes
4. `GET /projects/{id}/merge_requests?source_branch={branch}&state=opened` - Check for existing MR
5. `POST /projects/{id}/merge_requests` - Create new MR
6. `PUT /projects/{id}/merge_requests/{iid}` - Update existing MR description

## Phase 3 Implementation Details

### Backend Metrics Endpoint

**Endpoint:** `GET /api/experiments/metrics`

**Implementation (`util/prompt-optimization/viewer/backend/main.py`):**

```python
from pathlib import Path
from datetime import datetime
from eval_pipeline import Tier

def _calculate_tier_metrics_from_results(results: list[EvalTestResult]) -> dict:
    """Calculate tier-specific metrics from evaluation results."""
    tier_stats = {
        tier.value: {"tests_passed": 0, "tests_total": 0} for tier in Tier
    }

    for result in results:
        tier = getattr(result, 'tier', None)
        if tier and tier in tier_stats:
            tier_stats[tier]["tests_total"] += 1
            if result.passed:
                tier_stats[tier]["tests_passed"] += 1

    # Calculate overall as sum of all tiers (ensures consistency)
    overall_total = sum(stats["tests_total"] for stats in tier_stats.values())
    overall_passed = sum(stats["tests_passed"] for stats in tier_stats.values())

    metrics = {
        "overall": {
            "success_rate": (overall_passed / overall_total * 100) if overall_total > 0 else 0.0,
            "tests_passed": overall_passed,
            "tests_total": overall_total,
        }
    }

    # Add per-tier metrics
    for tier in Tier:
        stats = tier_stats[tier.value]
        metrics[tier.value] = {
            "success_rate": (stats["tests_passed"] / stats["tests_total"] * 100)
                           if stats["tests_total"] > 0 else 0.0,
            "tests_passed": stats["tests_passed"],
            "tests_total": stats["tests_total"],
        }

    return metrics

async def _get_local_metrics():
    """Load metrics from all local experiment directories."""
    experiments_root = Path("experiments")
    if not experiments_root.exists():
        return []

    all_metrics = []
    for exp_dir in sorted(experiments_root.iterdir()):
        if not exp_dir.is_dir():
            continue

        eval_file = exp_dir / ".006eval.jsonl"
        if not eval_file.exists():
            continue

        # Load results
        results = []
        with open(eval_file) as f:
            for line in f:
                if line.strip():
                    results.append(EvalTestResult.model_validate_json(line))

        if results:
            metrics = _calculate_tier_metrics_from_results(results)
            all_metrics.append({
                "experiment_id": exp_dir.name,
                "created_at": datetime.fromtimestamp(exp_dir.stat().st_mtime).isoformat(),
                "tier_metrics": metrics
            })

    return sorted(all_metrics, key=lambda x: x["created_at"])

@app.get("/api/experiments/metrics")
async def get_experiments_metrics():
    """Get aggregate metrics across local experiments."""
    return await _get_local_metrics()
```

**Response Structure:**
```json
[
  {
    "experiment_id": "exp_20260112_183045",
    "created_at": "2026-01-12T18:30:45",
    "tier_metrics": {
      "overall": {
        "success_rate": 86.21,
        "tests_passed": 25,
        "tests_total": 29
      },
      "stable": {
        "success_rate": 100.0,
        "tests_passed": 25,
        "tests_total": 25
      },
      "frontier": {
        "success_rate": 0.0,
        "tests_passed": 0,
        "tests_total": 4
      },
      "horizon": {
        "success_rate": 0.0,
        "tests_passed": 0,
        "tests_total": 0
      }
    }
  }
]
```

### Frontend Chart Rendering

**Implementation (`util/prompt-optimization/viewer/frontend/js/views/experiment-summary.js`):**

```javascript
async loadMetrics() {
    const response = await fetch('/api/experiments/metrics');
    const experiments = await response.json();
    this.renderExperimentMetrics(experiments);
}

renderExperimentMetrics(experiments) {
    // Sort by date (oldest first for timeline)
    experiments.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));

    // Render stats and charts
    this.renderExperimentChart(experiments);
}

drawExperimentChart(experiments) {
    const currentExpId = this.currentExperiment?.id;

    // Extract labels with full timestamps
    const labels = experiments.map(exp => {
        const date = new Date(exp.created_at);
        const isCurrentExp = exp.experiment_id === currentExpId;
        return isCurrentExp ? '★ Current' : date.toLocaleString();
    });

    // Extract data series (always default to 0 for missing values)
    const overallRates = experiments.map(exp =>
        exp.tier_metrics?.overall?.success_rate ?? 0
    );
    const stableRates = experiments.map(exp =>
        exp.tier_metrics?.stable?.success_rate ?? 0
    );
    const frontierRates = experiments.map(exp =>
        exp.tier_metrics?.frontier?.success_rate ?? 0
    );
    const horizonRates = experiments.map(exp =>
        exp.tier_metrics?.horizon?.success_rate ?? 0
    );

    // Highlight current experiment with distinct styling
    const pointStyles = experiments.map(exp =>
        exp.experiment_id === currentExpId ? 'circle' : 'circle'
    );
    const pointRadii = experiments.map(exp =>
        exp.experiment_id === currentExpId ? 8 : 4
    );
    const pointBackgroundColors = experiments.map((exp, idx) => {
        if (exp.experiment_id === currentExpId) {
            return 'rgba(251, 191, 36, 0.8)'; // Golden/yellow for current
        }
        return undefined; // Use dataset default
    });
    const pointBorderColors = experiments.map((exp, idx) => {
        if (exp.experiment_id === currentExpId) {
            return 'rgb(249, 115, 22)'; // Orange border for current
        }
        return undefined; // Use dataset default
    });

    // Render Chart.js line chart
    new Chart(document.getElementById('experiment-metrics-chart'), {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'Overall',
                    data: overallRates,
                    borderColor: '#3b82f6',
                    borderWidth: 3,
                    pointStyle: pointStyles,
                    pointRadius: pointRadii,
                    pointBackgroundColor: pointBackgroundColors,
                    pointBorderColor: pointBorderColors
                },
                {
                    label: 'Stable',
                    data: stableRates,
                    borderColor: '#10b981',
                    pointStyle: pointStyles,
                    pointRadius: pointRadii
                },
                {
                    label: 'Frontier',
                    data: frontierRates,
                    borderColor: '#f59e0b',
                    pointStyle: pointStyles,
                    pointRadius: pointRadii
                },
                {
                    label: 'Horizon',
                    data: horizonRates,
                    borderColor: '#ef4444',
                    pointStyle: pointStyles,
                    pointRadius: pointRadii
                }
            ]
        },
        options: {
            scales: {
                x: {
                    ticks: {
                        maxRotation: 45,
                        minRotation: 45,
                        autoSkip: true,
                        maxTicksLimit: 10
                    }
                },
                y: {
                    beginAtZero: true,
                    max: 100,
                    ticks: {
                        callback: value => value + '%'
                    }
                }
            },
            layout: {
                padding: {
                    bottom: 20  // Prevent label overflow
                }
            }
        }
    });
}
```

## Testing

### Local Testing

```bash
# Run tests with tier tracking
cd nemo_oo_agents-src
uv run python -m eval_pipeline \
    --config tests/capability/config_ci.yaml \
    --runs 3 \
    --parallel 40

# Check metrics output
cat results/ci/*/metrics.txt

# Preview tier promotions (dry-run)
uv run python util/ci/tier_promotions.py results/ci/ --create-mr --dry-run

# Test MR comment formatting
uv run python util/ci/post_mr_comment.py results/ci/ --compare-branch main
```

### CI Testing

1. Push branch to trigger CI pipeline
2. Check job logs for tier results
3. Verify MR comment shows tier breakdown
4. On main: Verify tier promotion MR is created if candidates found
5. On MR: Verify dry-run output in job logs

## Troubleshooting

### Issue: Tier not appearing in .006eval.jsonl

**Cause:** Tier field not propagated through pipeline

**Solution:** Verify tier field exists at each step:
1. `config_ci.yaml` has `tier: stable`
2. `TestConfig` parses tier from YAML
3. `EvalTest` receives tier from TestConfig
4. `Sample` receives tier from EvalTest
5. `EvalTestResult` receives tier from Sample

### Issue: CI blocking incorrectly

**Cause:** Float comparison in bash using `[` instead of `awk`

**Solution:** Use awk for float comparison:
```bash
STABLE_PASS=$(awk -v rate="$STABLE_RATE" -v thresh="$STABLE_THRESHOLD" \
    'BEGIN {print (rate >= thresh) ? "1" : "0"}')
```

### Issue: Experiments not appearing in viewer

**Cause:** Missing `.006eval.jsonl` files in experiment directories

**Solution:**
1. Verify experiments are in `experiments/` directory
2. Check that each experiment has a `.006eval.jsonl` file
3. Ensure results are valid JSONL format

### Issue: Overall metrics don't match tier sum

**Cause:** Overall total calculated incorrectly

**Solution:** Ensure `overall_total` is computed as sum of all tier totals:
```python
overall_total = sum(stats["tests_total"] for stats in tier_stats.values())
overall_passed = sum(stats["tests_passed"] for stats in tier_stats.values())
```

### Issue: Chart labels overflowing container

**Cause:** Long timestamp labels without rotation

**Solution:** Configure Chart.js axis ticks with rotation and padding:
```javascript
scales: {
    x: {
        ticks: {
            maxRotation: 45,
            minRotation: 45,
            autoSkip: true,
            maxTicksLimit: 10
        }
    }
},
layout: {
    padding: { bottom: 20 }
}
```

### Issue: Tier promotion MR not created

**Cause:** Missing `GITLAB_TOKEN` or wrong branch

**Solution:**
1. Verify `GITLAB_TOKEN` is set in CI/CD variables
2. Ensure token has `api` scope
3. Check branch name (only runs on main)

## Current Status: Local Evaluation Viewer

**As of Jan 2026:** The evaluation viewer runs locally and compares performance over previous local runs for various splits. When you run experiments locally, the viewer provides:

- Historical comparison across all local experiment runs
- Tier-specific metrics visualization (Stable, Frontier, Horizon)
- Time-series charts showing success rates and test counts
- Full timestamp labeling with current experiment highlighting

This gives developers immediate feedback on how their changes affect capability test performance across all tiers.

## Future Roadmap

### Deployed CI-Integrated Viewer

**Goal:** Deploy a version of the eval viewer that tracks progress on eval runs from main branch, providing a birds-eye view of project progress over time (e.g., "did we improve over the past 2 weeks?").

**Implementation Plan:**
- Deploy eval viewer alongside TPM agent using the same CI deployment pipeline
- Viewer will fetch metrics from GitLab pipeline artifacts on main branch
- Provides team-wide visibility into capability progression
- Motivating tool to see concrete progress (or identify regressions)

**Status:** To be picked up in separate MR

### Test Suite Optimization (If Needed)

If capability tests become too time-consuming, consider splitting the test strategy:

1. **MR Pipeline:** Run only stable tier tests (regression guard)
   - Fast feedback on critical functionality
   - Blocks merge if regressions detected

2. **Nightly Pipeline on Main:** Run full test suite (all tiers)
   - Complete coverage including frontier and horizon tests
   - Feeds into deployed eval viewer for progress tracking
   - No blocking, purely informational

This would maintain merge velocity while preserving comprehensive capability tracking.
