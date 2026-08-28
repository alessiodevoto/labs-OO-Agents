# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""BenchFlow rollout execution for NOOA SkillsBench canaries.

This module is the boundary between local CLI orchestration and BenchFlow's
sandbox lifecycle.  It stages the current NOOA checkout, installs nooa-bench
inside the task sandbox, runs the in-container runner, downloads logs, and then
hands control to BenchFlow's verifier.
"""

from __future__ import annotations

import json
import shlex
import shutil
import tempfile
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nooa_bench.skillsbench_artifacts import (
    ConditionResult,
    _condition_result_from_rollout,
)
from nooa_bench.skillsbench_conditions import (
    ConditionSettings,
    _condition_settings,
    _task_agent_timeout,
)

SOURCE_COPY_IGNORED_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "jobs",
    "skillsbench",
}
SOURCE_COPY_SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx")


def _copy_nooa_source(src: Path) -> Path:
    """Stage a compact source tree for upload into the BenchFlow sandbox."""
    tmp = Path(tempfile.mkdtemp(prefix="nooa-bench-src-"))
    dst = tmp / "nooa-src"

    def ignore(_dir: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            lower = name.lower()
            if (
                name in SOURCE_COPY_IGNORED_NAMES
                or name == ".env"
                or name.startswith(".env.")
                or lower.endswith((".pyc", *SOURCE_COPY_SECRET_SUFFIXES))
            ):
                ignored.add(name)
        return ignored

    shutil.copytree(src, dst, ignore=ignore)
    return dst


def _read_task_instruction(task_dir: Path) -> str:
    from benchflow.task.document import TaskDocument

    task_md = task_dir / "task.md"
    if task_md.is_file():
        return TaskDocument.from_path(task_md).instruction.strip()
    instruction_md = task_dir / "instruction.md"
    if instruction_md.is_file():
        return instruction_md.read_text().strip()
    raise FileNotFoundError(f"Task missing task.md or instruction.md: {task_dir}")


def _skill_dirs(skills_root: Path) -> list[Path]:
    """Return immediate child TextSkill directories under a SkillsBench skills root."""
    skill_dirs: list[Path] = []
    if not skills_root.is_dir():
        return skill_dirs
    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / "SKILL.md").is_file() or (entry / "skill.md").is_file():
            skill_dirs.append(entry)
    return skill_dirs


def _translate_task_library_skills(task_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    """Translate task-bundled TextSkills into package-backed LibrarySkills."""
    from nooa.tools.slim_skill_translator import SlimTextSkillTranslator

    source_skills_dir = task_dir / "environment" / "skills"
    skill_dirs = _skill_dirs(source_skills_dir)
    if not skill_dirs:
        raise FileNotFoundError(f"no SKILL.md directories found in {source_skills_dir}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    translator = SlimTextSkillTranslator()
    summaries: list[dict[str, Any]] = []
    for skill_dir in skill_dirs:
        result = translator.translate(skill_dir, output_dir)
        report = translator.validate_package(result.package_dir)
        summary = {
            "translator": translator.__class__.__name__,
            "source_dir": str(skill_dir),
            "package_dir": str(result.package_dir),
            "package_name": result.package_name,
            "registry_name": result.registry_name,
            "class_name": result.class_name,
            "files_written": result.files_written,
            "omitted_scripts": [asdict(item) for item in result.omitted_scripts],
            "validation": {**asdict(report), "package_dir": str(report.package_dir)},
        }
        summaries.append(summary)
        if not report.ok:
            errors = "; ".join(report.errors) or "unknown validation failure"
            raise RuntimeError(f"translated LibrarySkill failed validation for {skill_dir}: {errors}")

    (output_dir / "translation_summary.json").write_text(json.dumps(summaries, indent=2))
    return summaries


def _build_nooa_runner_args(
    *,
    instruction: str,
    model: str,
    settings: ConditionSettings,
    agent_env: dict[str, str],
) -> list[str]:
    args = [
        "/opt/nooa-bench-venv/bin/python",
        "-m",
        "nooa_bench.runner",
        "--instruction",
        instruction,
        "--model",
        model,
        "--agent-type",
        "bench",
        "--working-dir",
        "/root",
        "--skill-mode",
        settings.runner_skill_mode,
    ]
    if settings.runner_skills_dir:
        args.extend(["--skills-dir", settings.runner_skills_dir])
    if agent_env.get("OPENAI_BASE_URL"):
        args.extend(["--api-base", agent_env["OPENAI_BASE_URL"]])
    return args


def _install_nooa_command(source_sandbox_dir: str) -> str:
    """Return the in-sandbox command that bootstraps uv and installs NOOA."""
    return (
        "set -eu; "
        "if ! command -v uv >/dev/null 2>&1; then "
        "if ! command -v curl >/dev/null 2>&1; then "
        "if command -v apt-get >/dev/null 2>&1; then "
        "apt-get update && apt-get install -y curl ca-certificates; "
        "else echo 'curl is required to install uv' >&2; exit 127; "
        "fi; "
        "fi; "
        "curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh; "
        "fi; "
        "export PATH=/root/.local/bin:$PATH; "
        f"cd {shlex.quote(source_sandbox_dir)}; "
        "UV_PROJECT_ENVIRONMENT=/opt/nooa-bench-venv "
        "uv sync --package nooa-bench"
    )


async def _install_nooa(env: Any, source_sandbox_dir: str) -> None:
    cmd = _install_nooa_command(source_sandbox_dir)
    result = await env.exec(cmd, user="root", timeout_sec=900)
    if result.return_code != 0:
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        raise RuntimeError(
            "NOOA install failed: "
            f"return_code={result.return_code} "
            f"stdout={stdout[-4000:]!r} stderr={stderr[-4000:]!r}"
        )


async def _download_agent_logs(env: Any, agent_dir: Path) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    try:
        await env.download_dir("/logs/agent", agent_dir)
    except Exception as exc:
        (agent_dir / "download_error.txt").write_text(str(exc))


async def _execute_nooa_runner(
    env: Any,
    *,
    command: str,
    agent_env: dict[str, str],
    timeout_sec: int,
) -> int:
    """Run the in-container NOOA runner and return its process exit code."""
    result = await env.exec(
        f"{command} > /logs/agent/nooa-run.log 2>&1",
        user="agent",
        env=agent_env,
        timeout_sec=timeout_sec,
    )
    return int(result.return_code)


def _read_activated_skills(agent_dir: Path) -> list[str] | None:
    result_path = agent_dir / "result.json"
    if not result_path.is_file():
        return None
    try:
        payload = json.loads(result_path.read_text())
    except json.JSONDecodeError:
        return None
    skills = payload.get("activated_skills")
    if not isinstance(skills, list) or not all(isinstance(item, str) for item in skills):
        return None
    return skills


def _translated_library_skills_dir(jobs_dir: Path, job_name: str) -> Path:
    return jobs_dir / job_name / "translated_library_skills"


def _rollout_skills_dir(
    *,
    task_dir: Path,
    jobs_dir: Path,
    job_name: str,
    condition: str,
    settings: ConditionSettings,
) -> Path | None:
    # TextSkill and no-skill conditions use BenchFlow's task assets directly.
    # LibrarySkill runs need a generated local package tree before sandbox setup.
    if condition != "library_skill":
        return settings.rollout_skills_dir
    output_dir = _translated_library_skills_dir(jobs_dir, job_name)
    _translate_task_library_skills(task_dir, output_dir)
    return output_dir


def _record_agent_run(
    rollout: Any,
    *,
    condition: str,
    agent_return_code: int,
) -> None:
    # Verification is still attempted after a completed agent process. A non-zero
    # runner exit is marked as rollout error so it cannot be confused with a
    # scoreable verifier failure.
    if agent_return_code != 0:
        rollout._error = (
            f"NOOA runner failed with exit code {agent_return_code}. "
            "See agent/nooa-run.log."
        )
    rollout._trajectory = [
        {
            "type": "nooa_runner",
            "condition": condition,
            "return_code": agent_return_code,
            "log_path": "/logs/agent/nooa-run.log",
        }
    ]
    rollout._agent_name = "nooa-bench"


async def _run_condition(
    *,
    task_dir: Path,
    jobs_dir: Path,
    job_name: str,
    condition: str,
    model: str,
    sandbox: str,
    agent_env: dict[str, str],
    repo_src: Path,
) -> ConditionResult:
    from benchflow.rollout import Rollout, RolloutConfig

    settings = _condition_settings(task_dir, condition)
    agent_timeout = _task_agent_timeout(task_dir)
    config = RolloutConfig(
        task_path=task_dir,
        environment=sandbox,
        sandbox_user="agent",
        jobs_dir=jobs_dir,
        job_name=job_name,
        rollout_name=f"{task_dir.name}__{condition}",
        agent="nooa-harbor",
        model=model,
        agent_env=agent_env,
        skill_mode=settings.rollout_skill_mode,
        skills_dir=_rollout_skills_dir(
            task_dir=task_dir,
            jobs_dir=jobs_dir,
            job_name=job_name,
            condition=condition,
            settings=settings,
        ),
        timeout=agent_timeout,
    )
    rollout = await Rollout.create(config)
    source_tmp: Path | None = None
    agent_return_code = 1
    activated_skills: list[str] | None = None
    try:
        # BenchFlow prepares the task sandbox. The current NOOA checkout is then
        # uploaded and installed so smoke runs validate this worktree exactly.
        await rollout.setup()
        await rollout.start()
        await rollout.install_agent()

        source_tmp = _copy_nooa_source(repo_src)
        await rollout._env.upload_dir(source_tmp, "/tmp/nooa-src")
        await _install_nooa(rollout._env, "/tmp/nooa-src")

        instruction = _read_task_instruction(task_dir)
        args = _build_nooa_runner_args(
            instruction=instruction,
            model=model,
            settings=settings,
            agent_env=agent_env,
        )

        command = " ".join(shlex.quote(arg) for arg in args)
        started = time.monotonic()
        # The in-container runner writes durable logs under /logs/agent; BenchFlow
        # bind-mounts that directory back into the local job artifact tree.
        agent_return_code = await _execute_nooa_runner(
            rollout._env,
            command=command,
            agent_env=agent_env,
            timeout_sec=agent_timeout,
        )
        rollout._timing["agent_execution"] = time.monotonic() - started
        await _download_agent_logs(rollout._env, rollout._rollout_paths.agent_dir)
        activated_skills = _read_activated_skills(rollout._rollout_paths.agent_dir)
        _record_agent_run(
            rollout,
            condition=condition,
            agent_return_code=agent_return_code,
        )
        await rollout.verify()
    except Exception as exc:
        # Setup, sandbox execution, log download, and verifier exceptions are
        # infrastructure failures. Preserve any earlier rollout error and append
        # the traceback for artifact-based debugging.
        trace = traceback.format_exc()
        if rollout._error:
            rollout._error = f"{rollout._error}; post-run error: {exc}\n{trace}"
        else:
            rollout._error = f"{exc}\n{trace}"
    finally:
        await rollout.cleanup()
        if source_tmp is not None:
            shutil.rmtree(source_tmp.parent, ignore_errors=True)

    return _condition_result_from_rollout(
        condition=condition,
        rollout=rollout,
        agent_return_code=agent_return_code,
        activated_skills=activated_skills,
    )
