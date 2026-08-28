# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Local SkillsBench result and summary artifacts.

BenchFlow owns sandbox execution and scoring.  The NOOA CLI persists a smaller
stable schema that is easy for humans, tests, and experiment docs to consume
without importing BenchFlow internals.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

OUTCOME_PASSED = "passed"
OUTCOME_FAILED = "failed"
OUTCOME_ERRORED = "errored"
OUTCOME_VERIFIER_ERRORED = "verifier_errored"
OUTCOME_UNSCORED = "unscored"


@dataclass
class ConditionResult:
    """Host-side summary for one nooa SkillsBench rollout."""

    condition: str
    rollout_dir: str
    passed: bool
    reward: float | None
    error: str | None
    verifier_error: str | None
    agent_return_code: int
    outcome: str = OUTCOME_UNSCORED
    activated_skills: list[str] | None = None
    skipped: bool = False


def _git_commit(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _run_manifest(
    *,
    task: str,
    model: str,
    sandbox: str,
    conditions: tuple[str, ...],
    skillsbench_dir: Path,
    jobs_dir: Path,
    repo_src: Path,
) -> dict[str, Any]:
    return {
        "task": task,
        "model": model,
        "sandbox": sandbox,
        "conditions": list(conditions),
        "skillsbench_dir": str(skillsbench_dir),
        "jobs_dir": str(jobs_dir),
        "repo_commit": _git_commit(repo_src),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _infer_outcome(
    *,
    passed: bool,
    reward: float | None,
    error: str | None,
    verifier_error: str | None,
) -> str:
    """Infer a stable local outcome for old summaries or missing BenchFlow helpers."""
    # Order matters. A clean verifier reward of 0.0 is a scoreable benchmark
    # failure; runner/verifier errors mean the run itself was not cleanly scored.
    if passed:
        return OUTCOME_PASSED
    if error:
        return OUTCOME_ERRORED
    if verifier_error:
        return OUTCOME_VERIFIER_ERRORED
    if reward is not None:
        return OUTCOME_FAILED
    return OUTCOME_UNSCORED


def _classify_rollout_result(result: Any) -> str:
    """Return BenchFlow's outcome string for a rollout result.

    BenchFlow is only importable after the CLI has re-executed in the
    SkillsBench project, so the dependency stays lazy and confined here.
    """
    from benchflow._utils.scoring import classify_result_outcome

    outcome = classify_result_outcome(
        {
            "rewards": result.rewards,
            "error": result.error,
            "verifier_error": result.verifier_error,
        }
    )
    return str(outcome)


def _condition_result_from_rollout(
    *,
    condition: str,
    rollout: Any,
    agent_return_code: int,
    activated_skills: list[str] | None,
) -> ConditionResult:
    # Keep BenchFlow result objects at the boundary. The persisted summary stays
    # a small dataclass shape that can be loaded without importing BenchFlow.
    result = rollout.result or rollout._build_result()
    reward = (result.rewards or {}).get("reward") if result.rewards else None
    outcome = _classify_rollout_result(result)
    return ConditionResult(
        condition=condition,
        rollout_dir=str(rollout._rollout_dir) if rollout._rollout_dir else "",
        passed=outcome == OUTCOME_PASSED,
        reward=reward,
        error=result.error,
        verifier_error=result.verifier_error,
        agent_return_code=agent_return_code,
        outcome=outcome,
        activated_skills=activated_skills,
    )


def _condition_result_from_dict(payload: dict[str, Any]) -> ConditionResult:
    # Resume supports summaries produced before outcome was added. Infer the
    # closest equivalent from the older public fields instead of rejecting them.
    passed = bool(payload.get("passed", False))
    reward = payload.get("reward")
    error = payload.get("error")
    verifier_error = payload.get("verifier_error")
    outcome = payload.get("outcome")
    if not isinstance(outcome, str) or not outcome:
        outcome = _infer_outcome(
            passed=passed,
            reward=reward,
            error=error,
            verifier_error=verifier_error,
        )
    return ConditionResult(
        condition=str(payload.get("condition", "")),
        rollout_dir=str(payload.get("rollout_dir", "")),
        passed=passed,
        reward=reward,
        error=error,
        verifier_error=verifier_error,
        agent_return_code=int(payload.get("agent_return_code", 1)),
        outcome=outcome,
        activated_skills=payload.get("activated_skills"),
        skipped=bool(payload.get("skipped", False)),
    )


def _load_existing_results(jobs_dir: Path, job_name: str) -> dict[str, ConditionResult]:
    summary_path = jobs_dir / job_name / "summary.json"
    if not summary_path.is_file():
        return {}
    try:
        payload = json.loads(summary_path.read_text())
    except json.JSONDecodeError:
        return {}
    results = payload.get("results")
    if not isinstance(results, list):
        return {}
    loaded: dict[str, ConditionResult] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        result = _condition_result_from_dict(item)
        if result.condition:
            loaded[result.condition] = result
    return loaded


def _summary_payload(
    *,
    job_name: str,
    results: list[ConditionResult],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    # Keep JSON machine-friendly and close to ConditionResult. Markdown rendering
    # is separate so artifact formatting changes do not alter the data contract.
    return {
        "job_name": job_name,
        "manifest": manifest or {},
        "results": [asdict(result) for result in results],
    }


def _summary_markdown_lines(
    *,
    results: list[ConditionResult],
    manifest: dict[str, Any] | None,
) -> list[str]:
    # Keep the Markdown intentionally flat: one manifest block followed by one
    # compact condition block, matching the existing experiment-doc style.
    lines = ["# NOOA SkillsBench One-Task Summary", ""]
    if manifest:
        lines.extend(
            [
                "## Manifest",
                f"- task: {manifest['task']}",
                f"- model: {manifest['model']}",
                f"- sandbox: {manifest['sandbox']}",
                f"- repo_commit: {manifest['repo_commit']}",
                "",
            ]
        )
    for result in results:
        lines.append(f"## {result.condition}")
        if result.skipped:
            lines.append("- skipped: True")
        lines.append(f"- outcome: {result.outcome}")
        lines.append(f"- passed: {result.passed}")
        lines.append(f"- reward: {result.reward}")
        lines.append(f"- rollout_dir: {result.rollout_dir}")
        lines.append(f"- agent_return_code: {result.agent_return_code}")
        if result.activated_skills is not None:
            lines.append(f"- activated_skills: {result.activated_skills}")
        if result.error:
            lines.append(f"- error: {result.error}")
        if result.verifier_error:
            lines.append(f"- verifier_error: {result.verifier_error}")
        lines.append("")
    return lines


def _write_summary(
    jobs_dir: Path,
    job_name: str,
    results: list[ConditionResult],
    manifest: dict[str, Any] | None = None,
) -> None:
    job_dir = jobs_dir / job_name
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = _summary_payload(job_name=job_name, results=results, manifest=manifest)
    (job_dir / "summary.json").write_text(json.dumps(payload, indent=2))
    lines = _summary_markdown_lines(results=results, manifest=manifest)
    (job_dir / "summary.md").write_text("\n".join(lines))
