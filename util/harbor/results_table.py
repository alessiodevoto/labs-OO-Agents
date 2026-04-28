#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Print a summary table of Harbor benchmark results.

Reads Harbor job directories and outputs per-benchmark metrics:
pass rate, mean reward, error counts, and token usage.

Usage:
    # All configured benchmarks (reads latest run in each jobs_dir):
    python util/harbor/results_table.py

    # Specific job directories:
    python util/harbor/results_table.py /raid/rcabral/home/harbor_jobs/locomo_baseline \
                                         /raid/rcabral/home/harbor_jobs/locomo_react_baseline

    # Include per-task rows:
    python util/harbor/results_table.py --verbose
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


# Default job dirs to scan (all active benchmark configurations).
_DEFAULT_JOB_DIRS = [
    "/raid/rcabral/home/harbor_jobs/locomo_baseline",
    "/raid/rcabral/home/harbor_jobs/locomo_react_baseline",
    "/raid/rcabral/home/harbor_jobs/terminal_bench_baseline",
    "/raid/rcabral/home/harbor_jobs/terminal_bench_react_baseline",
    "/raid/rcabral/home/harbor_jobs/terminal_bench_specialized",
    "/raid/rcabral/home/harbor_jobs/membench_baseline",
    "/raid/rcabral/home/harbor_jobs/membench_react_baseline",
    "/raid/rcabral/home/harbor_jobs/dabstep_baseline",
    "/raid/rcabral/home/harbor_jobs/dabstep_react_baseline",
]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _best_run_dir(job_dir: Path) -> Path | None:
    """Return the run directory with the most completed trials.

    Falls back to the most recent directory if none have any results yet.
    """
    runs = sorted(
        [d for d in job_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
        reverse=True,
    )
    if not runs:
        return None
    best = max(
        runs,
        key=lambda d: sum(1 for _ in d.glob("*/result.json")),
    )
    # If best has 0 results, return the latest (in-progress)
    if not list(best.glob("*/result.json")):
        return runs[0]
    return best


def _parse_trial(trial_dir: Path) -> dict:
    """Parse one trial directory into a flat result dict."""
    result = _read_json(trial_dir / "result.json")

    task_name = result.get("task_name") or trial_dir.name.rsplit("__", 1)[0]

    # Reward
    reward: float | None = None
    vr = result.get("verifier_result") or {}
    rewards = vr.get("rewards") or {}
    if "reward" in rewards:
        reward = float(rewards["reward"])

    # Exception
    exc_info = result.get("exception_info") or {}
    exc_type = exc_info.get("exception_type")

    # Token counts: try agent/result.json first (written by runner.py),
    # fall back to Harbor's agent_result field.
    n_in: int | None = None
    n_out: int | None = None
    agent_result_file = trial_dir / "agent" / "result.json"
    if agent_result_file.exists():
        ar_inner = _read_json(agent_result_file)
        n_in = ar_inner.get("n_input_tokens")
        n_out = ar_inner.get("n_output_tokens")

    if n_in is None or n_out is None:
        ar = result.get("agent_result") or {}
        n_in = ar.get("n_input_tokens")
        n_out = ar.get("n_output_tokens")

    return {
        "task_name": task_name,
        "reward": reward,
        "exc_type": exc_type,
        "n_input_tokens": n_in,
        "n_output_tokens": n_out,
    }


def _collect(job_dir: Path) -> list[dict]:
    """Collect all trial results from the latest run in job_dir."""
    if not job_dir.is_dir():
        return []
    run_dir = _best_run_dir(job_dir)
    if run_dir is None:
        return []
    trials = []
    for trial_dir in sorted(run_dir.iterdir()):
        if not trial_dir.is_dir():
            continue
        result_json = trial_dir / "result.json"
        if not result_json.exists():
            continue
        trials.append(_parse_trial(trial_dir))
    return trials


def _summarize(name: str, trials: list[dict]) -> dict:
    completed = [t for t in trials if t["exc_type"] is None]
    errors = [t for t in trials if t["exc_type"] is not None]
    rewarded = [t for t in completed if t["reward"] is not None]
    passed = [t for t in rewarded if t["reward"] >= 1.0]

    mean_reward = sum(t["reward"] for t in rewarded) / len(rewarded) if rewarded else None
    pass_rate = len(passed) / len(rewarded) * 100 if rewarded else None

    has_tokens = [t for t in completed if t["n_input_tokens"] is not None]
    total_in = sum(t["n_input_tokens"] for t in has_tokens) if has_tokens else None
    total_out = sum(t["n_output_tokens"] for t in has_tokens if t["n_output_tokens"]) if has_tokens else None

    # Error breakdown
    err_counts: dict[str, int] = {}
    for t in errors:
        err_counts[t["exc_type"] or "unknown"] = err_counts.get(t["exc_type"] or "unknown", 0) + 1

    return {
        "name": name,
        "n_total": len(trials),
        "n_completed": len(completed),
        "n_error": len(errors),
        "n_rewarded": len(rewarded),
        "n_passed": len(passed),
        "pass_rate": pass_rate,
        "mean_reward": mean_reward,
        "total_in": total_in,
        "total_out": total_out,
        "err_counts": err_counts,
    }


def _fmt(val, fmt=".1f", fallback="—"):
    return f"{val:{fmt}}" if val is not None else fallback


def _fmt_tokens(val):
    if val is None:
        return "—"
    if val >= 1_000_000:
        return f"{val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val/1_000:.0f}K"
    return str(val)


def print_table(summaries: list[dict]) -> None:
    col_name = max(len(s["name"]) for s in summaries) + 2
    header = (
        f"{'Benchmark':<{col_name}}"
        f"{'Done':>6}"
        f"{'Err':>5}"
        f"{'Pass%':>7}"
        f"{'Reward':>8}"
        f"{'In':>9}"
        f"{'Out':>8}"
    )
    print("\n" + header)
    print("-" * len(header))
    for s in summaries:
        done_str = f"{s['n_completed']}/{s['n_total']}"
        row = (
            f"{s['name']:<{col_name}}"
            f"{done_str:>6}"
            f"{s['n_error']:>5}"
            f"{_fmt(s['pass_rate'], '.1f') + '%':>7}"
            f"{_fmt(s['mean_reward'], '.3f'):>8}"
            f"{_fmt_tokens(s['total_in']):>9}"
            f"{_fmt_tokens(s['total_out']):>8}"
        )
        print(row)
        if s["err_counts"]:
            for etype, count in sorted(s["err_counts"].items(), key=lambda x: -x[1]):
                short = etype.replace("Error", "Err")
                print(f"  {'':>{col_name - 2}}  {short}: {count}")
    print()


def print_verbose(name: str, trials: list[dict]) -> None:
    print(f"\n--- {name} ---")
    completed = [t for t in trials if t["exc_type"] is None]
    errors = [t for t in trials if t["exc_type"] is not None]
    for t in sorted(completed, key=lambda x: x["task_name"]):
        tok_str = ""
        if t["n_input_tokens"] is not None:
            tok_str = f"  in={_fmt_tokens(t['n_input_tokens'])} out={_fmt_tokens(t['n_output_tokens'])}"
        reward_str = f"reward={t['reward']:.2f}" if t["reward"] is not None else "reward=?"
        print(f"  ✓ {t['task_name']:50s}  {reward_str}{tok_str}")
    for t in sorted(errors, key=lambda x: x["task_name"]):
        exc = (t["exc_type"] or "?").replace("Error", "Err")
        print(f"  ✗ {t['task_name']:50s}  {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Print Harbor benchmark results table.")
    parser.add_argument(
        "job_dirs",
        nargs="*",
        help="Harbor job directories to scan (default: all configured benchmarks).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-task breakdown after the summary table.",
    )
    args = parser.parse_args()

    dirs = [Path(d) for d in args.job_dirs] if args.job_dirs else [Path(d) for d in _DEFAULT_JOB_DIRS]

    summaries = []
    all_trials: dict[str, list[dict]] = {}
    for job_dir in dirs:
        name = job_dir.name
        trials = _collect(job_dir)
        if not trials:
            print(f"  (no results in {job_dir})")
            continue
        summaries.append(_summarize(name, trials))
        all_trials[name] = trials

    if summaries:
        print_table(summaries)

    if args.verbose:
        for name, trials in all_trials.items():
            print_verbose(name, trials)


if __name__ == "__main__":
    main()
