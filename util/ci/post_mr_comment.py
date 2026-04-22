#!/usr/bin/env python3
"""Update MR description with capability test results comparison.

This script updates the GitLab MR description with a formatted metrics section showing:
- Current capability test results
- Comparison against the target branch (baseline)
- Clear visual indicators (✅/❌) for improvements/regressions

On repeated runs, it finds and replaces the existing metrics section (preserving other content).

Usage:
    python post_mr_comment.py results_dir/ [--compare-branch main]

Environment Variables Required:
    CI_API_V4_URL       - GitLab API URL (e.g., https://gitlab.com/api/v4)
    CI_PROJECT_ID       - Project ID
    CI_MERGE_REQUEST_IID - MR internal ID (only present on MR pipelines)
    CI_JOB_TOKEN        - Job token for API auth (or use GITLAB_TOKEN for broader access)
    CI_PIPELINE_ID      - Current pipeline ID (for linking)
    CI_COMMIT_SHORT_SHA - Short commit SHA (for display)
"""

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from eval_pipeline import Tier

# Framework-caught errors the CodeAct strategy actively protects against.
# Scanning trace spans lets us surface them even when the agent recovered
# and the top-level result is marked passed=True. These counters are
# emitted by util/ci/parse_capability_results.py into metrics.txt; we
# duplicate the scan here so the MR description table can include them
# without depending on the sibling script's output files.
_SELF_RECURSION_RE = re.compile(r"calling self\.\w+.*forbidden.*recursion", re.I | re.S)
_IMPORT_RESTRICTED_RE = re.compile(r"RestrictedCodeError.*import", re.I | re.S)


def _scan_trace_errors(traces_dir: Path) -> dict[str, int]:
    """Count self-recursion and restricted-import errors across trace spans."""
    self_recursion = 0
    restricted_import = 0

    if not traces_dir.is_dir():
        return {"self_recursion": 0, "restricted_import": 0}

    for trace_path in traces_dir.glob("*.jsonl"):
        for raw in trace_path.open():
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for rs in doc.get("resourceSpans", []):
                for ss in rs.get("scopeSpans", []):
                    for span in ss.get("spans", []):
                        err_type = ""
                        err_msg = ""
                        for attr in span.get("attributes", []):
                            k = attr.get("key")
                            v = (attr.get("value") or {}).get("stringValue") or ""
                            if k == "error.type":
                                err_type = v
                            elif k == "error.message":
                                err_msg = v
                        if not err_type:
                            continue
                        blob = f"{err_type}: {err_msg}"
                        if _SELF_RECURSION_RE.search(blob):
                            self_recursion += 1
                        elif _IMPORT_RESTRICTED_RE.search(blob):
                            restricted_import += 1

    return {
        "self_recursion": self_recursion,
        "restricted_import": restricted_import,
    }


# Markers to identify our metrics section in MR description
METRICS_START_MARKER = "<!-- capability-metrics-start -->"
METRICS_END_MARKER = "<!-- capability-metrics-end -->"

# Environment variables
# Load .env file if available
dotenv_path = Path(".env")
if dotenv_path.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path)
    except ImportError:
        # dotenv not installed; skip loading .env
        pass

GITLAB_API = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
PROJECT_ID = os.environ.get("CI_PROJECT_ID")
MR_IID = os.environ.get("CI_MERGE_REQUEST_IID")
PIPELINE_ID = os.environ.get("CI_PIPELINE_ID")
COMMIT_SHA = os.environ.get("CI_COMMIT_SHORT_SHA", "unknown")
PROJECT_URL = os.environ.get("CI_PROJECT_URL", "")

# Auth token - prefer GITLAB_TOKEN (project access token) over CI_JOB_TOKEN
# CI_JOB_TOKEN may not have permission to update MR description
AUTH_TOKEN = os.environ.get("GITLAB_TOKEN") or os.environ.get("CI_JOB_TOKEN")

# Tier thresholds from CI/CD variables
STABLE_THRESHOLD = float(os.environ.get("STABLE_THRESHOLD", "90"))
FRONTIER_THRESHOLD = float(os.environ.get("FRONTIER_THRESHOLD", "60"))
HORIZON_THRESHOLD = float(os.environ.get("HORIZON_THRESHOLD", "0"))


def get_headers() -> dict[str, str]:
    """Get headers for GitLab API requests."""
    if not AUTH_TOKEN:
        raise ValueError("No auth token found. Set GITLAB_TOKEN or CI_JOB_TOKEN")
    return {"PRIVATE-TOKEN": AUTH_TOKEN, "Content-Type": "application/json"}


def parse_eval_file(eval_file: Path) -> dict[str, Any]:
    """Parse a .noo-eval.jsonl file and extract metrics."""
    metadata = None
    results = []
    completion = None

    with open(eval_file) as f:
        for line in f:
            data = json.loads(line)
            line_type = data.get("_type")

            if line_type == "metadata":
                metadata = data.get("metadata", {})
            elif line_type == "result":
                results.append(data)
            elif line_type == "completion":
                completion = data

    total = len(results)
    passed = sum(1 for r in results if r.get("passed", False))
    success_rate = (passed / total * 100) if total > 0 else 0.0

    # Calculate tier breakdown
    passed_by_tier = {
        tier.value: sum(
            1 for r in results if r.get("tier", "stable") == tier.value and r.get("passed", False)
        )
        for tier in Tier
    }
    total_by_tier = {
        tier.value: sum(1 for r in results if r.get("tier", "stable") == tier.value)
        for tier in Tier
    }
    success_rate_by_tier = {
        tier.value: (passed_by_tier[tier.value] / total_by_tier[tier.value] * 100)
        if total_by_tier[tier.value] > 0
        else 0
        for tier in Tier
    }

    mode_correct_count = 0
    mode_total_count = 0
    for result in results:
        scores = result.get("scores", {})
        mode_scorer_data = next(
            (s for s in scores.values() if "mode_correct" in s.get("metrics", {})),
            None,
        )
        if mode_scorer_data:
            mode_total_count += 1
            if mode_scorer_data["metrics"]["mode_correct"]:
                mode_correct_count += 1
    mode_accuracy = mode_correct_count / mode_total_count * 100 if mode_total_count > 0 else 0.0

    # Extract token counts from completion
    token_usage = {}
    if completion:
        token_usage = {
            "total_input_tokens": completion.get("total_input_tokens", 0),
            "total_output_tokens": completion.get("total_output_tokens", 0),
            "total_tokens": completion.get("total_tokens", 0),
        }

    # Scan the sibling traces/ directory for framework-caught errors
    # (self-recursion, forbidden import). These are invisible at the
    # result level because the CodeAct loop catches them and retries.
    trace_errors = _scan_trace_errors(eval_file.parent / "traces")

    return {
        "passed": passed,
        "total": total,
        "success_rate": success_rate,
        "passed_by_tier": passed_by_tier,
        "total_by_tier": total_by_tier,
        "success_rate_by_tier": success_rate_by_tier,
        "results": results,
        "metadata": metadata,
        "mode_accuracy": mode_accuracy,
        "mode_correct_count": mode_correct_count,
        "mode_total_count": mode_total_count,
        **token_usage,
        **trace_errors,
    }


def find_latest_eval_file(results_dir: Path) -> Path | None:
    """Find the most recent .noo-eval.jsonl file."""
    eval_files = list(results_dir.glob("**/*.noo-eval.jsonl"))
    if not eval_files:
        return None
    return sorted(eval_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def fetch_baseline_metrics(target_branch: str) -> dict[str, Any] | None:
    """Fetch baseline metrics from the latest successful pipeline on target branch.

    This looks for the metrics.txt artifact from the capability-test job
    on the target branch's latest pipeline.
    """
    if not all([GITLAB_API, PROJECT_ID, AUTH_TOKEN]):
        print("Warning: Cannot fetch baseline - missing env vars")
        return None

    headers = get_headers()

    try:
        # Get latest successful pipeline on target branch
        pipelines_url = f"{GITLAB_API}/projects/{PROJECT_ID}/pipelines"
        params = {"ref": target_branch, "status": "success", "per_page": 5}
        resp = requests.get(pipelines_url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        pipelines = resp.json()

        if not pipelines:
            print(f"No successful pipelines found on {target_branch}")
            return None

        # Try to find a pipeline with capability-test job artifacts
        for pipeline in pipelines:
            pipeline_id = pipeline["id"]

            # Get jobs for this pipeline
            jobs_url = f"{GITLAB_API}/projects/{PROJECT_ID}/pipelines/{pipeline_id}/jobs"
            resp = requests.get(jobs_url, headers=headers, timeout=10)
            resp.raise_for_status()
            jobs = resp.json()

            # Find capability-test job
            cap_job = next(
                (j for j in jobs if j["name"] == "capability-test" and j["status"] == "success"),
                None,
            )
            if not cap_job:
                continue

            # Try to download metrics.txt artifact
            job_id = cap_job["id"]
            artifact_url = f"{GITLAB_API}/projects/{PROJECT_ID}/jobs/{job_id}/artifacts/metrics.txt"
            resp = requests.get(artifact_url, headers=headers, timeout=10)

            if resp.status_code == 200:
                # Parse OpenMetrics format
                metrics = {}
                for line in resp.text.strip().split("\n"):
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        metrics[parts[0]] = float(parts[1])

                result = {
                    "pipeline_id": pipeline_id,
                }

                if "capability_success_rate_percent" in metrics:
                    result["success_rate"] = metrics["capability_success_rate_percent"]
                if "capability_tests_passed" in metrics:
                    result["passed"] = int(metrics["capability_tests_passed"])
                if "capability_tests_total" in metrics:
                    result["total"] = int(metrics["capability_tests_total"])
                elif "capability_tests_passed" in metrics and result.get("success_rate", 0) > 0:
                    # Fallback for old metrics.txt format without capability_tests_total
                    result["total"] = int(result["passed"] / (result["success_rate"] / 100))
                if "capability_total_input_tokens" in metrics:
                    result["total_input_tokens"] = int(metrics["capability_total_input_tokens"])
                if "capability_total_output_tokens" in metrics:
                    result["total_output_tokens"] = int(metrics["capability_total_output_tokens"])
                if "capability_total_tokens" in metrics:
                    result["total_tokens"] = int(metrics["capability_total_tokens"])
                if "mode_selection_accuracy_percent" in metrics:
                    result["mode_accuracy"] = metrics["mode_selection_accuracy_percent"]
                if "mode_selection_correct" in metrics:
                    result["mode_correct_count"] = int(metrics["mode_selection_correct"])
                if "mode_selection_total" in metrics:
                    result["mode_total_count"] = int(metrics["mode_selection_total"])
                elif "mode_selection_correct" in metrics and result.get("mode_accuracy", 0) > 0:
                    # Fallback for old metrics.txt format without mode_selection_total
                    result["mode_total_count"] = int(
                        result["mode_correct_count"] / (result["mode_accuracy"] / 100)
                    )
                # Framework-caught errors (new counters — may be missing on
                # baselines from before these were added).
                if "capability_self_recursion_errors" in metrics:
                    result["self_recursion"] = int(metrics["capability_self_recursion_errors"])
                if "capability_restricted_import_errors" in metrics:
                    result["restricted_import"] = int(
                        metrics["capability_restricted_import_errors"]
                    )

                # Extract per-tier metrics from OpenMetrics format
                # Format: capability_success_rate_percent{tier="stable"} 88.50
                #         capability_tests_passed{tier="stable"} 85
                #         capability_tests_total{tier="stable"} 96
                passed_by_tier = {}
                total_by_tier = {}
                success_rate_by_tier = {}

                for tier in Tier:
                    rate_key = f'capability_success_rate_percent{{tier="{tier.value}"}}'
                    passed_key = f'capability_tests_passed{{tier="{tier.value}"}}'
                    total_key = f'capability_tests_total{{tier="{tier.value}"}}'

                    if rate_key in metrics:
                        success_rate_by_tier[tier.value] = metrics[rate_key]
                    else:
                        success_rate_by_tier[tier.value] = 0.0

                    if passed_key in metrics:
                        passed_by_tier[tier.value] = int(metrics[passed_key])
                    else:
                        passed_by_tier[tier.value] = 0

                    if total_key in metrics:
                        total_by_tier[tier.value] = int(metrics[total_key])
                    else:
                        total_by_tier[tier.value] = 0

                result["passed_by_tier"] = passed_by_tier
                result["total_by_tier"] = total_by_tier
                result["success_rate_by_tier"] = success_rate_by_tier

                if len(result) > 1:
                    return result

        print(f"No capability-test artifacts found on {target_branch}")
        return None

    except requests.RequestException as e:
        print(f"Warning: Failed to fetch baseline: {e}")
        return None


def generate_progress_bar(
    current_rate: float,
    baseline_rate: float | None,
    num_blocks: int = 20,
) -> str:
    """Generate a visual progress bar showing current value and delta.

    Args:
        current_rate: Current success rate (0-100)
        baseline_rate: Baseline success rate (0-100), or None if no baseline
        num_blocks: Total number of blocks in the bar (default 20 = 5% each)

    Returns:
        String of emoji blocks representing the progress bar
    """
    # Calculate block counts
    current_blocks = round(current_rate / 100 * num_blocks)
    empty_blocks = num_blocks - current_blocks

    if baseline_rate is None:
        # No baseline - just show current in blue
        return "🟦" * current_blocks + "⬜" * empty_blocks

    baseline_blocks = round(baseline_rate / 100 * num_blocks)
    delta_blocks = current_blocks - baseline_blocks

    if delta_blocks > 0:
        # Improvement: baseline in blue, gains in green
        return "🟦" * baseline_blocks + "🟩" * delta_blocks + "⬜" * empty_blocks
    elif delta_blocks < 0:
        # Regression: current in blue, losses in red, then empty
        lost_blocks = abs(delta_blocks)
        return "🟦" * current_blocks + "🟥" * lost_blocks + "⬜" * empty_blocks
    else:
        # No change: all blue
        return "🟦" * current_blocks + "⬜" * empty_blocks


def format_metrics_section(current: dict[str, Any], baseline: dict[str, Any] | None) -> str:
    """Format the metrics section for MR description."""
    lines = [METRICS_START_MARKER, "", "## 🧪 Capability Test Results", ""]

    # Add merge status line based on stable tier threshold
    stable_rate = current.get("success_rate_by_tier", {}).get("stable", 0)
    stable_total = current.get("total_by_tier", {}).get("stable", 0)
    if stable_total > 0:
        threshold = f"(threshold: {STABLE_THRESHOLD:.0f}%)"
        if stable_rate >= STABLE_THRESHOLD:
            lines.append(f"✅ **Merge OK** — Stable tier at {stable_rate:.1f}% {threshold}")
        else:
            lines.append(f"🚫 **Merge Blocked** — Stable tier at {stable_rate:.1f}% {threshold}")
        lines.append("")
        lines.append("---")
        lines.append("")

    current_rate = current["success_rate"]
    current_passed = current["passed"]
    current_total = current["total"]
    current_total_tokens = current.get("total_tokens", 0)
    current_output_tokens = current.get("total_output_tokens", 0)

    if baseline:
        baseline_rate = baseline["success_rate"]
        baseline_passed = baseline["passed"]
        baseline_total = baseline["total"]
        baseline_total_tokens = baseline.get("total_tokens", 0)
        baseline_output_tokens = baseline.get("total_output_tokens", 0)

        delta_rate = current_rate - baseline_rate
        delta_passed = current_passed - baseline_passed
        delta_total_tokens = current_total_tokens - baseline_total_tokens
        delta_output_tokens = current_output_tokens - baseline_output_tokens

        # Delta text for baseline comparison
        if delta_rate > 0:
            delta_text = f"**+{delta_rate:.1f}%**"
        elif delta_rate < 0:
            delta_text = f"**{delta_rate:.1f}%**"
        else:
            delta_text = "**±0%**"

        # Visual progress bar with percentage (colors show improvement/regression)
        progress_bar = generate_progress_bar(current_rate, baseline_rate)
        lines.append(f"**{current_rate:.1f}%** {progress_bar} {delta_text}")
        lines.append("")

        # Tests passing summary
        if delta_passed > 0:
            lines.append(
                f"{current_passed}/{current_total} tests passing *(+{delta_passed} from baseline)*"
            )
        elif delta_passed < 0:
            lines.append(
                f"{current_passed}/{current_total} tests passing *({delta_passed} from baseline)*"
            )
        else:
            lines.append(f"{current_passed}/{current_total} tests passing *(no change)*")

        lines.append("")

        # Comparison table
        lines.append("| Metric | Baseline | This MR | Change |")
        lines.append("|--------|----------|---------|--------|")

        # Tests passed row
        passed_delta = f"+{delta_passed}" if delta_passed > 0 else str(delta_passed)
        passed_emoji = "✅" if delta_passed > 0 else ("❌" if delta_passed < 0 else "➖")
        lines.append(
            f"| Tests Passed | {baseline_passed}/{baseline_total} | "
            f"{current_passed}/{current_total} | {passed_delta} {passed_emoji} |"
        )

        # Overall success rate row
        rate_delta = f"+{delta_rate:.1f}%" if delta_rate > 0 else f"{delta_rate:.1f}%"
        rate_emoji = "✅" if delta_rate > 0.5 else ("❌" if delta_rate < -0.5 else "➖")
        lines.append(
            f"| Success Rate | {baseline_rate:.1f}% | {current_rate:.1f}% | "
            f"{rate_delta} {rate_emoji} |"
        )

        # Output tokens row
        output_tokens_delta = (
            f"+{delta_output_tokens:,}" if delta_output_tokens > 0 else f"{delta_output_tokens:,}"
        )
        output_tokens_emoji = (
            "⚠️" if delta_output_tokens > 0 else ("✅" if delta_output_tokens < 0 else "➖")
        )
        lines.append(
            f"| Output Tokens | {baseline_output_tokens:,} | {current_output_tokens:,} | "
            f"{output_tokens_delta} {output_tokens_emoji} |"
        )

        # Total tokens row
        total_tokens_delta = (
            f"+{delta_total_tokens:,}" if delta_total_tokens > 0 else f"{delta_total_tokens:,}"
        )
        total_tokens_emoji = (
            "⚠️" if delta_total_tokens > 0 else ("✅" if delta_total_tokens < 0 else "➖")
        )
        lines.append(
            f"| Total Tokens | {baseline_total_tokens:,} | {current_total_tokens:,} | "
            f"{total_tokens_delta} {total_tokens_emoji} |"
        )

        # Mode selection accuracy row
        baseline_mode_accuracy = baseline.get("mode_accuracy", 0.0)
        current_mode_accuracy = current.get("mode_accuracy", 0.0)
        baseline_mode_str = (
            f"{baseline.get('mode_correct_count', 0)}/{baseline.get('mode_total_count', 0)} "
            f"({baseline_mode_accuracy:.1f}%)"
            if baseline.get("mode_total_count", 0) > 0
            else "N/A"
        )
        current_mode_str = (
            f"{current['mode_correct_count']}/{current['mode_total_count']} "
            f"({current_mode_accuracy:.1f}%)"
        )
        if baseline.get("mode_total_count", 0) > 0:
            mode_delta = current_mode_accuracy - baseline_mode_accuracy
            mode_delta_str = f"+{mode_delta:.1f}%" if mode_delta > 0 else f"{mode_delta:.1f}%"
            mode_emoji = "✅" if mode_delta > 0.5 else ("❌" if mode_delta < -0.5 else "➖")
            mode_change_str = f"{mode_delta_str} {mode_emoji}"
        else:
            mode_change_str = "N/A"
        lines.append(
            f"| Mode Selection Accuracy | {baseline_mode_str} | {current_mode_str} | {mode_change_str} |"
        )

        # Framework-caught error counters (self-recursion attempts, forbidden
        # import attempts). These are errors the CodeAct loop intercepted and
        # the agent recovered from, so they don't appear in result.error at
        # all; tracking the counts directly lets us see prompt lever drift
        # before it affects pass rate.
        def _error_row(label: str, baseline_val: Any, current_val: int) -> str:
            if baseline_val is None:
                return f"| {label} | N/A | {current_val} | (new metric) |"
            delta = current_val - baseline_val
            delta_str = f"+{delta}" if delta > 0 else str(delta)
            # Fewer framework-caught errors = improvement.
            emoji = "✅" if delta < 0 else ("⚠️" if delta > 0 else "➖")
            return f"| {label} | {baseline_val} | {current_val} | {delta_str} {emoji} |"

        lines.append(
            _error_row(
                "Self-recursion attempts",
                baseline.get("self_recursion"),
                current.get("self_recursion", 0),
            )
        )
        lines.append(
            _error_row(
                "Restricted-import attempts",
                baseline.get("restricted_import"),
                current.get("restricted_import", 0),
            )
        )

    else:
        # No baseline available - show current with progress bar
        progress_bar = generate_progress_bar(current_rate, None)
        lines.append(f"**{current_rate:.1f}%** {progress_bar}")
        lines.append("")
        lines.append(f"{current_passed}/{current_total} tests passing")
        lines.append("")

        # Show token usage if available
        if "total_tokens" in current and current["total_tokens"] > 0:
            lines.append("**Token Usage:**")
            lines.append("")
            lines.append(f"- Output tokens: {current['total_output_tokens']:,}")
            lines.append(f"- Total tokens: {current['total_tokens']:,}")
            lines.append("")

        if current.get("mode_total_count", 0) > 0:
            lines.append(
                f"**Mode Selection Accuracy**: {current['mode_correct_count']}/{current['mode_total_count']} ({current['mode_accuracy']:.1f}%)"
            )
            lines.append("")

        # Framework-caught error counters
        if "self_recursion" in current or "restricted_import" in current:
            lines.append("**Framework-caught errors** (invisible at result level):")
            lines.append("")
            lines.append(f"- Self-recursion attempts: {current.get('self_recursion', 0)}")
            lines.append(f"- Restricted-import attempts: {current.get('restricted_import', 0)}")
            lines.append("")

        lines.append(
            "*No baseline available for comparison (first run or no previous successful pipeline)*"
        )

    # Add per-tier breakdown table
    if current.get("results"):
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>📊 Per-tier breakdown</summary>")
        lines.append("")

        if baseline:
            # Comparison format with baseline
            lines.append("| Tier | Baseline | This MR | Change | Expected |")
            lines.append("|------|----------|---------|--------|----------|")

            for tier in Tier:
                baseline_passed = baseline.get("passed_by_tier", {}).get(tier.value, 0)
                baseline_total = baseline.get("total_by_tier", {}).get(tier.value, 0)
                current_passed = current.get("passed_by_tier", {}).get(tier.value, 0)
                current_total = current.get("total_by_tier", {}).get(tier.value, 0)

                baseline_rate = baseline.get("success_rate_by_tier", {}).get(tier.value, 0)
                current_rate = current.get("success_rate_by_tier", {}).get(tier.value, 0)

                delta_passed = current_passed - baseline_passed
                delta_rate = current_rate - baseline_rate

                delta_passed_str = f"+{delta_passed}" if delta_passed > 0 else str(delta_passed)
                delta_rate_str = f"+{delta_rate:.1f}%" if delta_rate > 0 else f"{delta_rate:.1f}%"
                delta_emoji = "✅" if delta_rate > 0.5 else ("❌" if delta_rate < -0.5 else "➖")

                # Determine emoji based on threshold compliance only
                if tier.value == "stable":
                    threshold = STABLE_THRESHOLD
                    emoji = "✅" if current_rate >= threshold else "⚠️"
                    expected = f"≥{threshold:.0f}%"
                elif tier.value == "frontier":
                    threshold = FRONTIER_THRESHOLD
                    emoji = "✅" if current_rate >= threshold else "⚠️"
                    expected = f"≥{threshold:.0f}%"
                elif tier.value == "horizon":
                    emoji = "✅" if current_rate > HORIZON_THRESHOLD else "➖"
                    expected = f">{HORIZON_THRESHOLD:.0f}%"
                else:
                    emoji = "➖"
                    expected = "N/A"

                lines.append(
                    f"| {tier.value.capitalize()} | {baseline_passed}/{baseline_total} ({baseline_rate:.1f}%) | "
                    f"{current_passed}/{current_total} ({current_rate:.1f}%) | "
                    f"{delta_passed_str} / {delta_rate_str} {delta_emoji} | {expected} |"
                )

        else:
            lines.append("| Tier | Status | Expected |")
            lines.append("|------|--------|----------|")

            for tier in Tier:
                passed = current.get("passed_by_tier", {}).get(tier.value, 0)
                total = current.get("total_by_tier", {}).get(tier.value, 0)
                rate = passed / total * 100 if total > 0 else 0.0
                tier_label = tier.value.capitalize()

                # Determine emoji and expected display based on tier
                if tier.value == "stable":
                    threshold = STABLE_THRESHOLD
                    emoji = "✅" if rate >= threshold else "⚠️"
                    expected = f"≥{threshold:.0f}%"
                elif tier.value == "frontier":
                    threshold = FRONTIER_THRESHOLD
                    emoji = "✅" if rate >= threshold else "⚠️"
                    expected = f"≥{threshold:.0f}%"
                elif tier.value == "horizon":
                    threshold = HORIZON_THRESHOLD
                    emoji = "✅" if rate > threshold else "➖"
                    expected = f">{threshold:.0f}%"
                else:
                    emoji = "➖"
                    expected = "N/A"

                status = f"{emoji} {passed}/{total} ({rate:.1f}%)"
                lines.append(f"| {tier_label} | {status} | {expected} |")

        lines.append("")
        lines.append("</details>")

    # Add per-test breakdown in collapsible section
    if current.get("results"):
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>📋 Per-test breakdown</summary>")
        lines.append("")
        lines.append("| Test | Status |")
        lines.append("|------|--------|")

        # Group by test category
        categories: dict[str, dict[str, int]] = {}
        for result in current["results"]:
            test_id = result.get("base_test_id", result.get("test_id", ""))
            test_name = "_".join(test_id.split("_")[:-1]) if "_" in test_id else test_id
            passed = result.get("passed", False)
            categories.setdefault(test_name, {"passed": 0, "total": 0})
            categories[test_name]["total"] += 1
            if passed:
                categories[test_name]["passed"] += 1

        for test_name, counts in sorted(categories.items()):
            if counts["passed"] == counts["total"]:
                status = f"✅ {counts['passed']}/{counts['total']}"
            else:
                status = f"❌ {counts['passed']}/{counts['total']}"
            lines.append(f"| {test_name} | {status} |")

        lines.append("")
        lines.append("</details>")

    # Footer with metadata
    lines.append("")

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    footer_parts = [f"*Updated: {timestamp}*"]

    if PIPELINE_ID and PROJECT_URL:
        footer_parts.append(
            f"*Pipeline: [#{PIPELINE_ID}]({PROJECT_URL}/-/pipelines/{PIPELINE_ID})*"
        )

    if COMMIT_SHA:
        footer_parts.append(f"*Commit: `{COMMIT_SHA}`*")

    lines.append(" | ".join(footer_parts))
    lines.append("")
    lines.append(METRICS_END_MARKER)

    return "\n".join(lines)


def get_mr_description() -> str | None:
    """Fetch current MR description."""
    if not all([GITLAB_API, PROJECT_ID, MR_IID, AUTH_TOKEN]):
        return None

    headers = get_headers()
    url = f"{GITLAB_API}/projects/{PROJECT_ID}/merge_requests/{MR_IID}"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("description", "") or ""
    except requests.RequestException as e:
        print(f"Warning: Failed to fetch MR description: {e}")
        return None


def update_mr_description(new_description: str) -> bool:
    """Update MR description.

    Returns True if successful, False otherwise.
    """
    if not all([GITLAB_API, PROJECT_ID, MR_IID, AUTH_TOKEN]):
        print("Warning: Missing required env vars for updating MR")
        print(f"  CI_API_V4_URL: {'set' if GITLAB_API else 'missing'}")
        print(f"  CI_PROJECT_ID: {'set' if PROJECT_ID else 'missing'}")
        print(f"  CI_MERGE_REQUEST_IID: {'set' if MR_IID else 'missing'}")
        print(f"  AUTH_TOKEN: {'set' if AUTH_TOKEN else 'missing'}")
        return False

    headers = get_headers()
    url = f"{GITLAB_API}/projects/{PROJECT_ID}/merge_requests/{MR_IID}"

    try:
        resp = requests.put(url, headers=headers, json={"description": new_description}, timeout=10)
        resp.raise_for_status()
        print("Successfully updated MR description")
        return True
    except requests.RequestException as e:
        print(f"Error updating MR description: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response: {e.response.text}")
        return False


def update_description_with_metrics(current_description: str, metrics_section: str) -> str:
    """Update description with metrics section.

    If markers are found, replace that section.
    If not found, append to the end.
    """
    # Pattern to match existing metrics section (including markers)
    pattern = re.compile(
        re.escape(METRICS_START_MARKER) + r".*?" + re.escape(METRICS_END_MARKER),
        re.DOTALL,
    )

    if pattern.search(current_description):
        # Replace existing section
        new_description = pattern.sub(metrics_section, current_description)
        print("Found existing metrics section, replacing it")
    else:
        # Append to end (with separator)
        separator = "\n\n---\n\n" if current_description.strip() else ""
        new_description = current_description.rstrip() + separator + metrics_section
        print("No existing metrics section found, appending to description")

    return new_description


def main():
    parser = argparse.ArgumentParser(
        description="Update MR description with capability test results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Directory containing .noo-eval.jsonl results",
    )
    parser.add_argument(
        "--compare-branch",
        type=str,
        default="main",
        help="Branch to compare against for baseline (default: main)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print updated description instead of posting to GitLab",
    )

    args = parser.parse_args()

    # Check if we're in an MR context
    if not MR_IID and not args.dry_run:
        print(
            "Not running in MR context (CI_MERGE_REQUEST_IID not set). Skipping description update."
        )
        sys.exit(0)

    # Find and parse results
    eval_file = find_latest_eval_file(args.results_dir)
    if not eval_file:
        print(
            f"ERROR: No .noo-eval.jsonl files found in {args.results_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Parsing results from: {eval_file}")
    current = parse_eval_file(eval_file)

    # Fetch baseline for comparison
    print(f"Fetching baseline from {args.compare_branch} branch...")
    baseline = fetch_baseline_metrics(args.compare_branch)
    if baseline:
        print(
            f"Found baseline: {baseline['passed']}/{baseline['total']} "
            f"({baseline['success_rate']:.1f}%)"
        )
    else:
        print("No baseline found, will show results without comparison")

    # Format metrics section
    metrics_section = format_metrics_section(current, baseline)

    if args.dry_run:
        # In dry-run, show what the metrics section looks like
        print("\n" + "=" * 60)
        print("DRY RUN - Metrics section to add/update:")
        print("=" * 60)
        print(metrics_section)
        print("=" * 60)
    else:
        # Fetch current description
        current_description = get_mr_description()
        if current_description is None:
            print("Warning: Could not fetch MR description, cannot update")
            sys.exit(0)

        # Update description with metrics
        new_description = update_description_with_metrics(current_description, metrics_section)

        # Push updated description
        success = update_mr_description(new_description)
        if not success:
            print("Warning: Failed to update MR description, but continuing (non-blocking)")

    sys.exit(0)


if __name__ == "__main__":
    main()
