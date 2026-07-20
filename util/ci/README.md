# CI Utilities

Utilities for GitLab CI integration.

## post_mr_comment.py

Updates the MR **description** with a formatted capability test summary. This provides **much better visibility** than GitLab's built-in metrics widget, with clear ✅/❌ indicators for improvements/regressions.

### Features

- **Visual indicators**: Clear ✅ (improved), ❌ (regressed), ➖ (unchanged) emojis
- **Baseline comparison**: Compares against the latest successful pipeline on the target branch
- **Updates in-place**: On repeated runs, finds and replaces the metrics section (preserves other description content)
- **Collapsible details**: Per-test breakdown in an expandable section

### How It Works

The script uses HTML markers to identify the metrics section:

```markdown
<!-- capability-metrics-start -->
## 📊 Capability Test Results
...
<!-- capability-metrics-end -->
```

- **If markers found**: Replaces that section with updated metrics
- **If not found**: Appends metrics section to the end of the description

This means your MR description template and any content you've written is preserved!

### Example Output

```markdown
## 📊 Capability Test Results

✅ **Capabilities IMPROVED** from 88.9% → 94.4% (+5.5%)

| Metric | Baseline | This MR | Change |
|--------|----------|---------|--------|
| Tests Passed | 16/18 | 17/18 | +1 ✅ |
| Success Rate | 88.9% | 94.4% | +5.5% ✅ |
```

### Usage

```bash
# Basic usage (in CI)
python util/ci/post_mr_comment.py experiments/capability_eval/results/

# Specify comparison branch
python util/ci/post_mr_comment.py experiments/capability_eval/results/ --compare-branch main

# Dry run (prints metrics section without updating GitLab)
python util/ci/post_mr_comment.py experiments/capability_eval/results/ --dry-run
```

### Required Environment Variables

- `CI_API_V4_URL` - GitLab API URL
- `CI_PROJECT_ID` - Project ID
- `CI_MERGE_REQUEST_IID` - MR internal ID (only set in MR pipelines)
- `GITLAB_TOKEN` - Project access token with `api` scope (or `CI_JOB_TOKEN` if configured)
- `CI_PIPELINE_ID` - Current pipeline ID (for linking)
- `CI_COMMIT_SHORT_SHA` - Commit SHA (for display)

### GitLab Setup

1. **Create a Project Access Token** with `api` scope:
   - Go to Project → Settings → Access Tokens
   - Create token with `api` scope
   - Copy the token value

2. **Add as CI/CD Variable**:
   - Go to Project → Settings → CI/CD → Variables
   - Add variable `GITLAB_TOKEN` with the token value
   - Mark as "Masked" for security

---

## parse_capability_results.py

Parses NeMo OO Agents capability test results (`.noo-eval.jsonl`) and generates GitLab metrics reports.

### Usage

```bash
# Basic usage
python parse_capability_results.py experiments/capability_eval/results/

# Specify output path
python parse_capability_results.py experiments/capability_eval/results/ --output metrics.txt

# Fail CI job if tests failed (not recommended - defeats purpose of metrics)
python parse_capability_results.py experiments/capability_eval/results/ --fail-on-error
```

### Output

**Console Output:**
```
============================================================
CAPABILITY TEST RESULTS
============================================================
Suite: capability
Timestamp: 2025-12-15T12:30:45.132590
Models: gpt-oss-20b

Overall: 16/18 passed (88.9%)

Per-test breakdown:
  ✓ calculate_batch_001                 1/1 (100%)
  ✓ calculate_single_001                1/1 (100%)
  ...
============================================================
```

**metrics.txt (OpenMetrics Text Format):**
```
capability_tests_passed 16
capability_success_rate_percent 88.89
# EOF
```

Only two actionable metrics are exported for clean MR reviews:
- `capability_tests_passed` - Number of tests that passed (magnitude)
- `capability_success_rate_percent` - Success rate percentage (quality signal)

Detailed per-category breakdowns are available in the job log.

### GitLab Integration

This script is used in the `capability-test` CI job:

```yaml
capability-test:
  script:
    - python -m eval_pipeline --config experiments/capability_eval/config.yaml ...
    - python util/ci/parse_capability_results.py experiments/capability_eval/results/ --output metrics.txt
  artifacts:
    reports:
      metrics: metrics.txt  # GitLab displays in MR
```

Metrics appear in GitLab MR interface under "Metrics" section, showing:
- Current values
- Change from base branch (↑ improvement, ↓ regression)
- Trend visualization

### Exit Codes

- **0**: Always (unless `--fail-on-error` is set), for non-blocking behavior
- **1**: Only with `--fail-on-error` AND test failures

Default is exit 0 to allow metrics to be informational rather than blocking.

## Documentation

See [docs/ci-capability-testing.md](../../docs/ci-capability-testing.md) for complete setup guide.
