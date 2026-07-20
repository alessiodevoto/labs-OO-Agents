#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tier promotion system for capability tests.

This script analyzes test results and creates GitLab MRs for tier promotions.

Usage:
    python tier_promotions.py results/ci/ --create-mr
    python tier_promotions.py results/ci/ --create-mr --dry-run
"""

import argparse
import difflib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote

import requests

from eval_pipeline import Tier

dotenv_path = Path(".env")
if dotenv_path.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path)
    except ImportError:
        pass

GITLAB_API = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
PROJECT_ID = os.environ.get("CI_PROJECT_ID", "test-project-id")
PIPELINE_ID = os.environ.get("CI_PIPELINE_ID", "1234567890")
COMMIT_SHA = os.environ.get("CI_COMMIT_SHA", "1234567890")
DEFAULT_BRANCH = os.environ.get("CI_DEFAULT_BRANCH", "main")
CURRENT_BRANCH = os.environ.get("CI_COMMIT_BRANCH") or os.environ.get(
    "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "feature/test-branch"
)
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "test-gitlab-token")
STABLE_THRESHOLD = float(os.environ.get("STABLE_THRESHOLD", "90"))
FRONTIER_THRESHOLD = float(os.environ.get("FRONTIER_THRESHOLD", "60"))
HORIZON_THRESHOLD = float(os.environ.get("HORIZON_THRESHOLD", "0"))

MR_TITLE_TEMPLATE = (
    "chore: promote test tiers ({stable_count} to stable, {frontier_count} to frontier)"
)
MR_DESCRIPTION_TEMPLATE = """
# Tier Promotion

## Promoted to Stable ({stable_count} tests)
{stable_tests_list}

**Criteria:** Tests passing at ≥{stable_threshold:.0f}% across 3 runs

## Promoted to Frontier ({frontier_count} tests)
{frontier_tests_list}

**Criteria:** Tests passing at >{horizon_threshold:.0f}% across 3 runs (showing some capability)

**Pipeline:** {pipeline_id}
**Commit:** {commit_sha}
"""


class TierPromotions(TypedDict):
    stable: list[str]
    frontier: list[str]


def apply_tier_changes(original_text: str, promotion_data: TierPromotions) -> str:
    """Apply tier changes to config YAML text with minimal diff.

    Instead of reserializing the entire YAML (which changes formatting),
    this does targeted line replacements for tier values only.
    """
    lines = original_text.split("\n")
    result_lines = []

    tier_updates = {
        test_name: tier_name
        for tier_name, test_names in promotion_data.items()
        for test_name in test_names
    }

    current_test = None
    i = 0
    while i < len(lines):
        line = lines[i]

        name_match = re.match(r"^\s*- name:\s+(\S+)", line)
        if name_match:
            current_test = name_match.group(1)

        tier_match = re.match(r"^(\s+)tier:\s+(\S+)", line)
        if tier_match and current_test in tier_updates:
            indent = tier_match.group(1)
            new_tier = tier_updates[current_test]
            result_lines.append(f"{indent}tier: {new_tier}")
            i += 1
            continue

        result_lines.append(line)
        i += 1

    return "\n".join(result_lines)


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

    token_usage = {}
    if completion:
        token_usage = {
            "total_input_tokens": completion.get("total_input_tokens", 0),
            "total_output_tokens": completion.get("total_output_tokens", 0),
            "total_tokens": completion.get("total_tokens", 0),
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
        "mode_accuracy": mode_accuracy,
        "mode_correct_count": mode_correct_count,
        "mode_total_count": mode_total_count,
        **token_usage,
    }


def find_latest_eval_file(results_dir: Path) -> Path | None:
    """Find the most recent .noo-eval.jsonl file in the results directory."""
    eval_files = list(results_dir.glob("**/*.noo-eval.jsonl"))
    if not eval_files:
        return None
    return sorted(eval_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def check_promotions(results_dir: Path) -> tuple[TierPromotions, dict[str, str]]:
    """Analyze test results and identify promotion candidates.

    Args:
        results_dir: Directory containing .noo-eval.jsonl results

    Returns:
        Tuple of (promotions dict, tier_map dict) where tier_map contains original tiers from results
    """
    eval_file = find_latest_eval_file(results_dir)
    if not eval_file:
        return {"stable": [], "frontier": []}, {}

    data = parse_eval_file(eval_file)
    test_results = defaultdict(list)
    for result in data["results"]:
        test_case = result.get("test_case", "")
        # Strip numeric sample ID suffix (_001, _002, etc.) to get config test name
        parts = test_case.rsplit("_", 1)
        base_name = parts[0] if len(parts) == 2 and parts[1].isdigit() else test_case

        test_results[base_name].append(
            {
                "passed": result.get("passed", False),
                "tier": result.get("tier", "stable"),
            }
        )

    promotions: TierPromotions = {"stable": [], "frontier": []}
    tier_map = {}

    for test_name, tests in test_results.items():
        if not tests:
            continue

        tier = tests[0]["tier"]
        tier_map[test_name] = tier
        passes = sum(1 for t in tests if t["passed"])
        total = len(tests)
        pass_rate = (passes / total * 100) if total > 0 else 0

        if tier == Tier.FRONTIER.value and pass_rate >= STABLE_THRESHOLD:
            promotions["stable"].append(test_name)
        elif tier == Tier.HORIZON.value and pass_rate > HORIZON_THRESHOLD:
            promotions["frontier"].append(test_name)

    return promotions, tier_map


def _format_test_list(test_names: list[str]) -> str:
    """Format a list of test names as markdown bullet points."""
    return "_None_" if not test_names else "\n".join(f"- {name}" for name in test_names)


def _print_promotion_summary(promotion_data: TierPromotions, tier_map: dict[str, str]) -> None:
    """Print a summary of proposed tier promotions."""
    print("\nProposed tier promotions:")
    for new_tier, test_names in promotion_data.items():
        for test_name in test_names:
            old_tier = tier_map.get(test_name, "unknown")
            print(f"  {test_name}: {old_tier} → {new_tier}")


def create_mr(
    promotion_data: TierPromotions,
    tier_map: dict[str, str],
    config_path: Path,
    gitlab_token: str,
    project_id: str = PROJECT_ID,
    gitlab_api: str = GITLAB_API,
    current_branch: str = CURRENT_BRANCH,
    default_branch: str = DEFAULT_BRANCH,
    commit_sha: str = COMMIT_SHA,
    pipeline_id: str = PIPELINE_ID,
    dry_run: bool = False,
) -> dict[str, str] | None:
    """Create GitLab MR with tier promotions.

    Args:
        promotion_data: Output from check_promotions()
        tier_map: Original tiers from test results
        config_path: Path to config.yaml
        gitlab_token: GitLab API token (required)
        project_id: GitLab project ID (required, defaults to CI_PROJECT_ID)
        gitlab_api: GitLab API endpoint (required, defaults to CI_API_V4_URL)
        current_branch: Current branch name (defaults to CI_COMMIT_BRANCH)
        default_branch: Default branch name (defaults to CI_DEFAULT_BRANCH)
        commit_sha: Commit SHA (defaults to CI_COMMIT_SHA)
        pipeline_id: Pipeline ID (defaults to CI_PIPELINE_ID)
        dry_run: If True, only show what would be done

    Returns:
        MR creation response or None if no candidates

    Raises:
        ValueError: If required parameters are missing
    """
    if not dry_run:
        if not project_id:
            raise ValueError("project_id is required (set CI_PROJECT_ID)")
        if not gitlab_api:
            raise ValueError("gitlab_api is required (set CI_API_V4_URL)")
        if not gitlab_token:
            raise ValueError("gitlab_token is required (set GITLAB_TOKEN)")

    with open(config_path) as f:
        original_config = f.read()

    _print_promotion_summary(promotion_data, tier_map)

    stable_count = len(promotion_data["stable"])
    frontier_count = len(promotion_data["frontier"])

    branch_name = (
        f"chore/tier-promotion-{commit_sha[:8]}"
        if current_branch == default_branch
        else f"chore/tier-promotion-{current_branch.replace('/', '-')}"
    )

    target_branch = current_branch
    updated_config = apply_tier_changes(original_config, promotion_data)

    if updated_config == original_config:
        print("\n⚠️  Warning: Config file already up-to-date with target tiers.")
        print("   No changes needed. The config may have been updated previously.")
        return None

    triggering_commit = f" (triggered by {commit_sha[:8]})" if commit_sha else ""
    commit_message = f"chore: promote tests to higher tiers{triggering_commit}"

    mr_title = MR_TITLE_TEMPLATE.format(
        stable_count=stable_count,
        frontier_count=frontier_count,
    )

    mr_description = MR_DESCRIPTION_TEMPLATE.format(
        stable_count=stable_count,
        frontier_count=frontier_count,
        stable_tests_list=_format_test_list(promotion_data["stable"]),
        frontier_tests_list=_format_test_list(promotion_data["frontier"]),
        stable_threshold=STABLE_THRESHOLD,
        horizon_threshold=HORIZON_THRESHOLD,
        pipeline_id=pipeline_id,
        commit_sha=commit_sha,
    ).strip()

    if dry_run:
        print("\n" + "=" * 80)
        print("DRY RUN - MR Preview")
        print("=" * 80)
        print(f"\nBranch: {branch_name}")
        print(f"Target: {target_branch}")
        print(f"Project ID: {project_id or 'NOT SET'}")
        print(f"GitLab API: {gitlab_api or 'NOT SET'}")
        print(f"MR Title: {mr_title}")
        print(f"\nMR Description:\n{mr_description}")
        print(f"\nCommit Message:\n{commit_message}")
        print("\nConfig Changes:")
        diff = difflib.unified_diff(
            original_config.splitlines(keepends=True),
            updated_config.splitlines(keepends=True),
            fromfile="config.yaml (current)",
            tofile="config.yaml (proposed)",
        )
        print("".join(diff))
        print("=" * 80)
        return None

    headers = {"PRIVATE-TOKEN": gitlab_token}

    print(f"\nChecking if branch exists: {branch_name}")
    branch_name_encoded = quote(branch_name, safe="")
    branch_check_url = (
        f"{gitlab_api}/projects/{project_id}/repository/branches/{branch_name_encoded}"
    )
    resp = requests.get(branch_check_url, headers=headers, timeout=10)

    if resp.status_code == 404:
        print(f"Creating new branch: {branch_name}")
        branch_url = f"{gitlab_api}/projects/{project_id}/repository/branches"
        resp = requests.post(
            branch_url,
            headers=headers,
            json={"branch": branch_name, "ref": target_branch},
            timeout=10,
        )
        resp.raise_for_status()
    else:
        print(f"Branch already exists: {branch_name}, will push to it")

    print(f"Committing changes to {config_path}")
    commit_url = f"{gitlab_api}/projects/{project_id}/repository/commits"

    resp = requests.post(
        commit_url,
        headers=headers,
        json={
            "branch": branch_name,
            "commit_message": commit_message,
            "actions": [
                {
                    "action": "update",
                    "file_path": str(config_path),
                    "content": updated_config,
                }
            ],
        },
        timeout=10,
    )
    resp.raise_for_status()

    print("Checking for existing merge request...")
    list_mr_url = f"{gitlab_api}/projects/{project_id}/merge_requests"
    resp = requests.get(
        list_mr_url,
        headers=headers,
        params={"source_branch": branch_name, "target_branch": target_branch, "state": "opened"},
        timeout=10,
    )
    resp.raise_for_status()
    existing_mrs = resp.json()

    if existing_mrs:
        mr_data = existing_mrs[0]
        print(f"MR already exists: {mr_data['web_url']}")
        print("Updating MR title and description...")
        update_url = f"{gitlab_api}/projects/{project_id}/merge_requests/{mr_data['iid']}"
        resp = requests.put(
            update_url,
            headers=headers,
            json={"title": mr_title, "description": mr_description},
            timeout=10,
        )
        resp.raise_for_status()
        print("MR title and description updated")
    else:
        print("Creating new merge request...")
        resp = requests.post(
            list_mr_url,
            headers=headers,
            json={
                "source_branch": branch_name,
                "target_branch": target_branch,
                "title": mr_title,
                "description": mr_description,
                "remove_source_branch": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        mr_data = resp.json()
        print(f"\nMR created: {mr_data['web_url']}")

    return {
        "web_url": mr_data.get("web_url"),
        "iid": mr_data.get("iid"),
        "title": mr_title,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Check for tier promotions and create GitLab MR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Directory containing .noo-eval.jsonl results",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("tests/capability/config.yaml"),
        help="Path to config.yaml (default: tests/capability/config.yaml)",
    )
    parser.add_argument(
        "--create-mr",
        action="store_true",
        help="Create GitLab MR with tier updates",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    args = parser.parse_args()

    print(f"Analyzing results in: {args.results_dir}")
    promotion_data, tier_map = check_promotions(args.results_dir)
    candidates = len(promotion_data["stable"]) + len(promotion_data["frontier"])

    if candidates > 0:
        print(f"\nFound {candidates} promotion candidate(s):")
        if promotion_data["stable"]:
            print(
                f"  - {len(promotion_data['stable'])} to Stable: {', '.join(promotion_data['stable'])}"
            )
        if promotion_data["frontier"]:
            print(
                f"  - {len(promotion_data['frontier'])} to Frontier: {', '.join(promotion_data['frontier'])}"
            )
    else:
        print("No promotion candidates found.")

    if not args.create_mr or candidates == 0:
        sys.exit(0)

    create_mr(
        promotion_data,
        tier_map,
        config_path=args.config,
        gitlab_token=GITLAB_TOKEN,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
