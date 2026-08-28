# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run one SkillsBench task locally through NOOA in selected skill conditions.

This file is intentionally the thin CLI facade.  The runner has three separate
responsibilities behind it:

* ``skillsbench_conditions`` maps user-facing conditions to BenchFlow and NOOA
  runner settings.
* ``skillsbench_rollout`` owns sandbox/source staging and one-condition
  execution.
* ``skillsbench_artifacts`` owns the stable summary/result schema.

The private helper imports below are kept as a compatibility surface for the
existing tests and for any local debugging scripts that imported them from this
module while the runner was monolithic.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from nooa_bench.skillsbench_artifacts import (
    OUTCOME_ERRORED,
    OUTCOME_FAILED,
    OUTCOME_PASSED,
    OUTCOME_UNSCORED,
    OUTCOME_VERIFIER_ERRORED,
    ConditionResult,
    _condition_result_from_dict,
    _condition_result_from_rollout,
    _git_commit,
    _infer_outcome,
    _load_existing_results,
    _run_manifest,
    _summary_markdown_lines,
    _summary_payload,
    _write_summary,
)
from nooa_bench.skillsbench_conditions import (
    CONDITIONS,
    PAIRED_CONDITIONS,
    ConditionSettings,
    _condition_settings,
    _selected_conditions,
    _task_agent_timeout,
    _task_agent_timeout_from_frontmatter,
)
from nooa_bench.skillsbench_rollout import (
    SOURCE_COPY_IGNORED_NAMES,
    SOURCE_COPY_SECRET_SUFFIXES,
    _build_nooa_runner_args,
    _copy_nooa_source,
    _download_agent_logs,
    _execute_nooa_runner,
    _install_nooa,
    _install_nooa_command,
    _read_activated_skills,
    _read_task_instruction,
    _record_agent_run,
    _rollout_skills_dir,
    _run_condition,
    _skill_dirs,
    _translate_task_library_skills,
    _translated_library_skills_dir,
)

DEFAULT_MODEL = "openai/openai/openai/gpt-5.2"
DEFAULT_TASK = "citation-check"
DEFAULT_JOBS_DIR = Path("jobs/nooa-skillsbench")

__all__ = [
    "CONDITIONS",
    "DEFAULT_JOBS_DIR",
    "DEFAULT_MODEL",
    "DEFAULT_TASK",
    "OUTCOME_ERRORED",
    "OUTCOME_FAILED",
    "OUTCOME_PASSED",
    "OUTCOME_UNSCORED",
    "OUTCOME_VERIFIER_ERRORED",
    "PAIRED_CONDITIONS",
    "SOURCE_COPY_IGNORED_NAMES",
    "SOURCE_COPY_SECRET_SUFFIXES",
    "ConditionResult",
    "ConditionSettings",
    "_build_nooa_runner_args",
    "_condition_result_from_dict",
    "_condition_result_from_rollout",
    "_condition_settings",
    "_copy_nooa_source",
    "_credentials",
    "_download_agent_logs",
    "_ensure_benchflow_importable",
    "_execute_nooa_runner",
    "_git_commit",
    "_infer_outcome",
    "_install_nooa",
    "_install_nooa_command",
    "_load_env_file",
    "_load_existing_results",
    "_read_activated_skills",
    "_read_task_instruction",
    "_record_agent_run",
    "_repo_root",
    "_reexec_with_skillsbench_uv",
    "_rollout_skills_dir",
    "_run_condition",
    "_run_manifest",
    "_selected_conditions",
    "_skill_dirs",
    "_summary_markdown_lines",
    "_summary_payload",
    "_task_agent_timeout",
    "_task_agent_timeout_from_frontmatter",
    "_translate_task_library_skills",
    "_translated_library_skills_dir",
    "_write_summary",
    "build_parser",
    "main",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def _credentials(env_file: Path) -> dict[str, str]:
    file_values = _load_env_file(env_file)
    api_key = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("API_KEY")
        or file_values.get("API_KEY")
    )
    api_url = (
        os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("API_URL")
        or file_values.get("API_URL")
    )
    env: dict[str, str] = {}
    if api_key:
        env["OPENAI_API_KEY"] = api_key
    if api_url:
        env["OPENAI_BASE_URL"] = api_url
    return env


def _ensure_benchflow_importable(skillsbench_dir: Path) -> None:
    try:
        import benchflow  # noqa: F401

        return
    except ImportError:
        pass

    subprocess.run(["uv", "sync", "--locked"], cwd=skillsbench_dir, check=True)
    import benchflow  # noqa: F401


def _reexec_with_skillsbench_uv(skillsbench_dir: Path) -> int:
    """Restart inside the SkillsBench uv project when BenchFlow is not importable.

    Unit tests run from the NOOA checkout, but real rollouts need BenchFlow from
    the external SkillsBench repo.  Re-exec keeps the CLI command stable while
    switching only the dependency environment.
    """
    env = os.environ.copy()
    env["_NOOA_SKILLSBENCH_UV_REEXEC"] = "1"
    repo_root = _repo_root()
    pythonpath_roots = [
        repo_root / "src",
        repo_root / "packages" / "nooa-bench" / "src",
        repo_root / "packages" / "nooa-cli" / "src",
    ]
    existing_pythonpath = env.get("PYTHONPATH")
    pythonpath = os.pathsep.join(str(path) for path in pythonpath_roots)
    env["PYTHONPATH"] = pythonpath if not existing_pythonpath else f"{pythonpath}{os.pathsep}{existing_pythonpath}"
    cmd = [
        "uv",
        "run",
        "--project",
        str(skillsbench_dir),
        "python",
        "-m",
        "nooa_bench.skillsbench_runner",
        *sys.argv[1:],
    ]
    return subprocess.run(cmd, env=env).returncode


async def _amain(args: argparse.Namespace) -> int:
    skillsbench_dir = Path(args.skillsbench_dir).resolve()
    task_dir = skillsbench_dir / "tasks" / args.task
    if not task_dir.is_dir():
        raise FileNotFoundError(f"SkillsBench task not found: {task_dir}")

    # The first process runs from the NOOA repo.  If BenchFlow is absent, re-exec
    # in the SkillsBench uv environment and keep this checkout on PYTHONPATH.
    if not os.environ.get("_NOOA_SKILLSBENCH_UV_REEXEC"):
        try:
            import benchflow  # noqa: F401
        except ImportError:
            return _reexec_with_skillsbench_uv(skillsbench_dir)
    _ensure_benchflow_importable(skillsbench_dir)

    agent_env = _credentials(Path(args.env_file).resolve())
    if not agent_env.get("OPENAI_API_KEY"):
        print(
            "No API credentials found. Set OPENAI_API_KEY/API_KEY or provide --env-file.",
            flush=True,
        )
        return 2

    jobs_dir = Path(args.jobs_dir).resolve()
    job_name = args.job_name or f"{args.task}__nooa__{time.strftime('%Y-%m-%d__%H-%M-%S')}"
    conditions = _selected_conditions(args.condition)
    manifest = _run_manifest(
        task=args.task,
        model=args.model,
        sandbox=args.sandbox,
        conditions=conditions,
        skillsbench_dir=skillsbench_dir,
        jobs_dir=jobs_dir,
        repo_src=_repo_root(),
    )
    existing_results = _load_existing_results(jobs_dir, job_name) if args.resume else {}

    results: list[ConditionResult] = []
    for condition in conditions:
        if condition in existing_results:
            result = existing_results[condition]
            result.skipped = True
            results.append(result)
            print(
                f"{condition}: skipped=True outcome={result.outcome} "
                f"passed={result.passed} reward={result.reward} "
                f"rollout_dir={result.rollout_dir}",
                flush=True,
            )
            continue

        try:
            result = await _run_condition(
                task_dir=task_dir,
                jobs_dir=jobs_dir,
                job_name=job_name,
                condition=condition,
                model=args.model,
                sandbox=args.sandbox,
                agent_env=agent_env,
                repo_src=_repo_root(),
            )
        except Exception as exc:
            # Failures before a Rollout result exists are runner errors, not
            # scoreable benchmark failures.
            result = ConditionResult(
                condition=condition,
                rollout_dir="",
                passed=False,
                reward=None,
                error=str(exc),
                verifier_error=None,
                agent_return_code=1,
                outcome=OUTCOME_ERRORED,
            )

        results.append(result)
        print(
            f"{condition}: outcome={result.outcome} passed={result.passed} reward={result.reward} "
            f"rollout_dir={result.rollout_dir}",
            flush=True,
        )

    _write_summary(jobs_dir, job_name, results, manifest=manifest)
    return 0 if all(result.passed for result in results) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skillsbench-dir", default="skillsbench")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sandbox", default="docker")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--jobs-dir", default=str(DEFAULT_JOBS_DIR))
    parser.add_argument("--job-name", default=None)
    parser.add_argument("--condition", choices=("both", "all", *CONDITIONS), default="both")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing condition results from summary.json for this job name.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
