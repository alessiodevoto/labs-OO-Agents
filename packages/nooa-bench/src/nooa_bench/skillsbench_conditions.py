# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SkillsBench condition and task-configuration helpers.

The host runner has to speak two related APIs for every condition:
BenchFlow's rollout configuration and the in-container NOOA runner flags.  This
module keeps that mapping explicit so adding a condition does not require
reading the full sandbox lifecycle code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PAIRED_CONDITIONS = ("no_skill", "text_skill")
CONDITIONS = (*PAIRED_CONDITIONS, "library_skill")


@dataclass(frozen=True)
class ConditionSettings:
    """Condition-specific settings shared by BenchFlow and the NOOA runner."""

    rollout_skill_mode: str
    rollout_skills_dir: Path | None
    runner_skill_mode: str
    runner_skills_dir: str | None


def _selected_conditions(condition: str) -> tuple[str, ...]:
    if condition == "both":
        return PAIRED_CONDITIONS
    if condition == "all":
        return CONDITIONS
    return (condition,)


def _condition_settings(task_dir: Path, condition: str) -> ConditionSettings:
    if condition == "no_skill":
        return ConditionSettings(
            rollout_skill_mode="no-skill",
            rollout_skills_dir=None,
            runner_skill_mode="no_skill",
            runner_skills_dir=None,
        )

    if condition in {"text_skill", "library_skill"}:
        skills_dir = task_dir / "environment" / "skills"
        if not skills_dir.is_dir():
            raise FileNotFoundError(f"{condition} condition requires skills dir: {skills_dir}")
        return ConditionSettings(
            rollout_skill_mode="with-skill",
            rollout_skills_dir=skills_dir,
            runner_skill_mode=condition,
            runner_skills_dir="/skills",
        )

    raise ValueError(f"Unknown condition: {condition!r}")


def _task_agent_timeout(task_dir: Path, default: int = 900) -> int:
    try:
        from benchflow.task import Task
    except ImportError:
        timeout = _task_agent_timeout_from_frontmatter(task_dir / "task.md")
    else:
        timeout = Task(task_dir).config.agent.timeout_sec
    if timeout is None:
        return default
    return max(1, int(timeout))


def _task_agent_timeout_from_frontmatter(task_md: Path) -> float | None:
    """Read agent.timeout_sec without importing BenchFlow.

    Unit tests run from the NOOA repo where BenchFlow is intentionally not on
    sys.path.  The lightweight fallback keeps timeout behavior testable there;
    real CLI runs still use BenchFlow's Task parser after re-exec.
    """
    if not task_md.is_file():
        return None
    lines = task_md.read_text().splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    in_agent = False
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line and not line[0].isspace():
            in_agent = line.strip() == "agent:"
            continue
        if in_agent and line.strip().startswith("timeout_sec:"):
            value = line.split(":", 1)[1].strip().strip("'\"")
            try:
                return float(value)
            except ValueError:
                return None
    return None
