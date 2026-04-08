#!/usr/bin/env python3
"""Parse capability test results and generate GitLab CI metrics.

This script parses .006eval.jsonl files and outputs:
1. GitLab metrics report (JSON) for visualization in MR
2. Summary for job log
3. Exit code (0=all passed, non-zero if failures)

Usage:
    python parse_capability_results.py results_dir/

Output:
    - metrics.json: GitLab metrics report format
    - Prints summary to stdout
    - Exit code 0 if all tests passed (or improvement), 1 if any failures
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eval_pipeline import Tier


def parse_eval_file(eval_file: Path) -> dict[str, Any]:
    """Parse a .006eval.jsonl file and extract metrics.

    Returns:
        dict with keys: passed, total, success_rate, test_results, metadata
    """
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

    # Calculate metrics
    total = len(results)
    passed = sum(1 for r in results if r.get("passed", False))
    success_rate = (passed / total * 100) if total > 0 else 0.0
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
    mode_accuracy = (mode_correct_count / mode_total_count * 100) if mode_total_count > 0 else 0.0

    # Extract token counts from completion
    token_usage = {
        "total_input_tokens": completion.get("total_input_tokens", 0) if completion else 0,
        "total_output_tokens": completion.get("total_output_tokens", 0) if completion else 0,
        "total_tokens": completion.get("total_tokens", 0) if completion else 0,
    }

    return {
        "passed": passed,
        "total": total,
        "success_rate": success_rate,
        "passed_by_tier": passed_by_tier,
        "total_by_tier": total_by_tier,
        "success_rate_by_tier": success_rate_by_tier,
        "results": results,
        "metadata": metadata,
        "completion": completion,
        "mode_accuracy": mode_accuracy,
        "mode_correct_count": mode_correct_count,
        "mode_total_count": mode_total_count,
        **token_usage,
    }


def find_latest_eval_file(results_dir: Path) -> Path | None:
    """Find the most recent .006eval.jsonl file in the results directory."""
    eval_files = list(results_dir.glob("**/*.006eval.jsonl"))
    if not eval_files:
        return None
    # Sort by modification time, newest first
    return sorted(eval_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def generate_gitlab_metrics(data: dict[str, Any]) -> str:
    """Generate GitLab metrics report in OpenMetrics text format.

    See: https://docs.gitlab.com/ci/testing/metrics_reports.html

    Returns:
        OpenMetrics text format string with actionable metrics for MR review
    """
    lines = []

    # Overall metrics
    lines.append(f"capability_tests_passed {data['passed']}")
    lines.append(f"capability_tests_total {data['total']}")
    lines.append(f"capability_success_rate_percent {data['success_rate']:.2f}")

    # Per-tier metrics (rate, passed, total)
    for tier in Tier:
        lines.append(
            f'capability_success_rate_percent{{tier="{tier.value}"}} {data["success_rate_by_tier"][tier.value]:.2f}'
        )
        lines.append(
            f'capability_tests_passed{{tier="{tier.value}"}} {data["passed_by_tier"][tier.value]}'
        )
        lines.append(
            f'capability_tests_total{{tier="{tier.value}"}} {data["total_by_tier"][tier.value]}'
        )

    lines.append(f"capability_total_input_tokens {data['total_input_tokens']}")
    lines.append(f"capability_total_output_tokens {data['total_output_tokens']}")
    lines.append(f"capability_total_tokens {data['total_tokens']}")
    lines.append(f"mode_selection_accuracy_percent {data['mode_accuracy']:.2f}")
    lines.append(f"mode_selection_correct {data['mode_correct_count']}")
    lines.append(f"mode_selection_total {data['mode_total_count']}")
    # Add EOF marker (required by OpenMetrics format)
    lines.append("# EOF")

    return "\n".join(lines)


def print_summary(data: dict[str, Any]) -> None:
    """Print human-readable summary to stdout."""
    print("\n" + "=" * 60)
    print("CAPABILITY TEST RESULTS")
    print("=" * 60)

    if data.get("metadata"):
        meta = data["metadata"]
        print(f"Suite: {meta.get('suite_name', 'unknown')}")
        print(f"Timestamp: {meta.get('timestamp', 'unknown')}")
        if meta.get("models"):
            models = meta["models"]
            if isinstance(models, list) and models:
                if isinstance(models[0], dict):
                    model_names = [m.get("id", m.get("model_name", "unknown")) for m in models]
                else:
                    model_names = models
                print(f"Models: {', '.join(model_names)}")

    print(f"\nOverall: {data['passed']}/{data['total']} passed ({data['success_rate']:.1f}%)")
    print("By Tier:")
    for tier in Tier:
        print(
            f"  {tier.value.capitalize()}: {data['passed_by_tier'][tier.value]}/{data['total_by_tier'][tier.value]} passed ({data['success_rate_by_tier'][tier.value]:.1f}%)"
        )
    print(
        f"\nMode Selection Accuracy: {data['mode_correct_count']}/{data['mode_total_count']} ({data['mode_accuracy']:.1f}%)"
    )
    print("\nToken Usage:")
    print(f"  Input tokens:   {data['total_input_tokens']:>10,}")
    print(f"  Output tokens:  {data['total_output_tokens']:>10,}")
    print(f"  Total tokens:   {data['total_tokens']:>10,}")

    # Group by test category
    categories = {}
    for result in data["results"]:
        test_id = result.get("base_test_id", result.get("test_id", ""))
        # Remove model suffix if present (e.g., "sentiment_single_gpt4" -> "sentiment_single")
        test_name = "_".join(test_id.split("_")[:-1]) if "_" in test_id else test_id

        passed = result.get("passed", False)
        categories.setdefault(test_name, {"passed": 0, "total": 0})
        categories[test_name]["total"] += 1
        if passed:
            categories[test_name]["passed"] += 1

    print("\nPer-test breakdown:")
    for test_name, counts in sorted(categories.items()):
        status = "✓" if counts["passed"] == counts["total"] else "✗"
        rate = (counts["passed"] / counts["total"] * 100) if counts["total"] > 0 else 0
        print(f"  {status} {test_name:35s} {counts['passed']}/{counts['total']} ({rate:.0f}%)")

    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Parse capability test results for GitLab CI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Directory containing .006eval.jsonl results",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("metrics.txt"),
        help="Output path for GitLab metrics (OpenMetrics text format, default: metrics.txt)",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit with code 1 if any tests failed (default: always exit 0)",
    )

    args = parser.parse_args()

    # Find latest eval file
    eval_file = find_latest_eval_file(args.results_dir)
    if not eval_file:
        print(f"ERROR: No .006eval.jsonl files found in {args.results_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing: {eval_file}")

    # Parse results
    data = parse_eval_file(eval_file)

    # Generate GitLab metrics report (OpenMetrics text format)
    metrics = generate_gitlab_metrics(data)
    with open(args.output, "w") as f:
        f.write(metrics)
    print(f"Wrote metrics to: {args.output}")

    # Print summary
    print_summary(data)

    # Exit code based on --fail-on-error flag
    if args.fail_on_error and data["passed"] < data["total"]:
        print(f"FAILED: {data['total'] - data['passed']} tests failed")
        sys.exit(1)
    else:
        print("CI job completed successfully (failures don't block merge)")
        sys.exit(0)


if __name__ == "__main__":
    main()
