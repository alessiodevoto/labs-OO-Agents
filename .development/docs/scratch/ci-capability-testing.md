# CI Capability Testing Setup

This document describes how to run Agent006 capability tests in GitLab CI to track LLM performance improvements or regressions across code changes.

## Overview

The capability test suite evaluates Agent006's core functionality across different LLMs:
- **Scale awareness** - When to answer directly vs write code
- **REPL exploration** - Using introspection for complex state
- **Subagent routing** - Creating and coordinating child agents
- **Stateful multi-turn** - Maintaining state across method calls

The CI integration runs these tests automatically on all MRs and main commits, reporting metrics in the GitLab MR interface similar to code coverage reports.

## Features

✅ **Non-blocking** - Test results don't prevent merge
✅ **Automatic execution** - Runs on all MRs and main commits
✅ **Metrics visualization** - Results shown in GitLab MR UI
✅ **Trend tracking** - Compare results across MRs
✅ **Fast execution** - Uses single model for CI speed (~5-10 minutes)

## Setup

### 1. Configure API Key in GitLab

The capability tests require access to NVIDIA Inference API to run LLM models.

**Option A: Project-level CI/CD Variable (Recommended)**

1. Navigate to your GitLab project
2. Go to Settings → CI/CD → Variables
3. Add a new variable:
   - **Key**: `NVIDIA_INFERENCE_API_KEY`
   - **Value**: Your NVIDIA API key
   - **Flags**:
     - ✅ Protect variable (only available on protected branches)
     - ✅ Mask variable (hide in job logs)
     - ⬜ Expand variable reference (leave unchecked)
   - **Environments**: All (default)

**Option B: Group-level Variable (For Multiple Projects)**

1. Navigate to your GitLab group
2. Go to Settings → CI/CD → Variables
3. Add the same variable as above

**How to Get an NVIDIA API Key:**

1. Go to https://build.nvidia.com
2. Sign in with your NVIDIA account
3. Navigate to "API Keys" section
4. Generate a new API key
5. Copy the key (keep it secure!)

### 2. Verify CI Configuration

The `.gitlab-ci.yml` file should contain the `capability-test` job:

```yaml
capability-test:
  stage: capability
  allow_failure: true  # Won't block merge
  only:
    - merge_requests
    - main
  # ... rest of configuration
```

## Usage

### Running in GitLab CI

The capability tests run automatically on every MR and commit to main:

1. Create a merge request with your changes
2. Push your changes - the pipeline starts automatically
3. Go to the MR's "Pipelines" tab
4. The `capability-test` job runs in the "capability" stage
5. Wait for results (~5-10 minutes)

The job will:
- Run capability tests with `gpt-oss-20b` model
- Generate metrics report
- Display results in the MR "Metrics" section

### Viewing Results

**In the Pipeline:**
- Click on the `capability-test` job to see console output
- Scroll to end for summary table with pass/fail per test category

**In the Merge Request:**
- Go to MR → Overview → scroll to "Metrics"
- See two actionable metrics:
  - `capability_tests_passed` - Number of tests passed (e.g., 16)
  - `capability_success_rate_percent` - Success rate (e.g., 88.89)

**Comparing Across MRs:**
- GitLab shows metric changes between base and head
- Green arrow ↑ = improvement (e.g., 88.89% ↑ 5.0%)
- Red arrow ↓ = regression (e.g., 88.89% ↓ 10.0%)
- Helps identify if your changes affected LLM performance

**Detailed Breakdown:**
- Per-category results (sentiment, calculate, router, etc.) are in the job log
- Only the two most actionable metrics appear in the MR for clean reviews

### Running Locally

To run the same tests locally before pushing:

```bash
# Install dependencies
uv sync --all-extras
uv pip install -e util/eval_pipeline/

# Set API key
export NVIDIA_INFERENCE_API_KEY="your_key_here"

# Run tests (same config as CI)
uv run python -m eval_pipeline \
  --config experiments/capability_eval/config.yaml \
  --models gpt-oss-20b \
  --runs 1 \
  --parallel 5

# Generate metrics report
uv run python util/ci/parse_capability_results.py \
  experiments/capability_eval/results/ \
  --output metrics.txt

# View metrics
cat metrics.txt
```

## Configuration

### Testing Different Models

The CI runs only `gpt-oss-20b` for speed. To test other models locally:

```bash
# Single model
uv run python -m eval_pipeline \
  --config experiments/capability_eval/config.yaml \
  --models qwen3-80b

# Multiple models
uv run python -m eval_pipeline \
  --config experiments/capability_eval/config.yaml \
  --models gpt-oss-20b,qwen3-80b,claude-haiku
```

Available models (see `experiments/capability_eval/config.yaml`):
- `gpt-oss-20b` - Fast reasoning model (CI default)
- `qwen3-80b` - Large instruct model
- `claude-haiku` - Fast Claude model
- `nemotron3-nano-30b` - NVIDIA reasoning model

### Testing Specific Test Suites

```bash
# Only scale awareness tests
uv run python -m eval_pipeline \
  --config experiments/capability_eval/config.yaml \
  --test sentiment_single,sentiment_batch,calculate_single,calculate_batch

# Only routing tests
uv run python -m eval_pipeline \
  --config experiments/capability_eval/config.yaml \
  --test router_analyze,router_validate,router_transform
```

### Adjusting CI Job

Edit `.gitlab-ci.yml` to customize the `capability-test` job:

**Make it manual trigger instead of automatic:**
```yaml
capability-test:
  when: manual  # Require manual trigger via Play button
```

**Test multiple models:**
```yaml
script:
  - uv run python -m eval_pipeline \
      --config experiments/capability_eval/config.yaml \
      --models gpt-oss-20b,qwen3-80b \  # Add more models
      --runs 1 \
      --parallel 5
```

**Only run on specific files changed:**
```yaml
capability-test:
  rules:
    - changes:
        - src/nemo_oo_agents/runtime/**/*
        - src/nemo_oo_agents/strategies/**/*
        - packages/unifiedllm/**/*
```

## Interpreting Results

### Success Rate Guidelines

- **90-100%**: Excellent - LLM following patterns correctly
- **70-89%**: Good - Some edge cases failing
- **50-69%**: Fair - Several capability gaps
- **<50%**: Poor - Major LLM integration issues

### Common Failure Patterns

**Scale Awareness Tests Fail**
- LLM writing code for simple single-item tasks
- LLM answering directly for batch tasks
- → Check prompt templates in strategy files

**Router Tests Fail**
- LLM not creating child agents correctly
- Parameter extraction issues
- → Check child agent documentation in prompts

**Stateful Tests Fail**
- State not persisted across method calls
- LLM regenerating methods incorrectly
- → Check PERSISTENT vs EPHEMERAL settings

### When to Run Capability Tests

✅ **Recommended:**
- Changes to prompt templates or strategies
- Updates to LLM client interface
- New generation strategy implementations
- Changes to agent decorator or runtime

⬜ **Optional:**
- Bug fixes in unrelated code
- Documentation updates
- Test suite changes

❌ **Not Needed:**
- Version bumps
- CI configuration changes
- Dependency updates (unless LLM-related)

## Troubleshooting

### Job Fails with "No .006eval.jsonl files found"

The eval pipeline didn't complete successfully. Check job logs for errors:
```bash
# Look for errors in the pipeline run
# Common issues:
# - API key not set or invalid
# - Model endpoint unreachable
# - Import errors (dependencies not installed)
```

### API Key Not Working

1. Verify key is set: Go to Settings → CI/CD → Variables
2. Check key hasn't expired (regenerate if needed)
3. Ensure "Mask variable" is checked (not "Expand variable")
4. Try running locally with same key to verify it works

### Tests Timeout

Some models are slower than others. Increase timeout:
```yaml
capability-test:
  timeout: 30m  # Default is usually 1h
```

Or use faster models:
```yaml
--models gpt-oss-20b  # Fast
# Instead of:
--models nemotron3-nano-30b  # Slower (more thorough reasoning)
```

### Metrics Not Showing in MR

GitLab metrics reports require:
1. Job must complete (even with test failures)
2. `metrics.txt` must be valid OpenMetrics text format
3. Must use `reports: metrics:` in artifacts
4. GitLab version must support metrics reports (13.0+)

Check the metrics file is valid:
```bash
# Download artifact from job
# Then validate:
cat metrics.txt
# Should show exactly:
# capability_tests_passed 16
# capability_success_rate_percent 88.89
# # EOF
```

- [Capability Test Documentation](../experiments/capability_eval/README.md)
- [Eval Pipeline Documentation](../util/eval_pipeline/README.md)
- [Agent006 Programming Guide](../.cursor/rules/nemo_oo_agents-programming-guide.md)
- [GitLab Metrics Reports](https://docs.gitlab.com/ee/ci/metrics_reports.html)
