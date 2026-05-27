#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Convert original Terminal Bench tasks to Harbor task format.

Reads each task from the cached terminal-bench repo and creates Harbor-format
task directories under util/harbor/tasks/terminal_bench/.

Usage (from the worktree root):
    python util/harbor/generate_terminal_bench_tasks.py [--dry-run] [task_name ...]

Flags:
    --dry-run   List tasks that would be generated without writing files.
    task_name   Generate only the named task(s). Default: all tasks.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

# Injected immediately after the shebang line so it fires on ANY exit, even
# when set -e is active (suffix-based capture is unreachable after set -e exits).
_REWARD_TRAP = (
    "trap '_ec=$?;"
    ' [ "$_ec" -eq 0 ] && echo 1 > /logs/verifier/reward.txt'
    " || echo 0 > /logs/verifier/reward.txt;"
    ' exit "$_ec"\' EXIT\n'
)

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_OUT_DIR = WORKTREE_ROOT / "util/harbor/tasks/terminal_bench"
ORIGINAL_TASKS = Path.home() / ".cache/terminal-bench/terminal-bench-head/original-tasks"

_TASK_TOML_TEMPLATE = """\
schema_version = "1.1"

[metadata]
author_name = {author_name!r}
author_email = {author_email!r}
difficulty = {difficulty!r}
category = {category!r}
tags = {tags}
{dur_line}expert_time_estimate_min = {expert_time!r}
junior_time_estimate_min = {junior_time!r}

[verifier]
timeout_sec = {verifier_timeout!r}

[agent]
timeout_sec = {agent_timeout!r}

[environment]
build_timeout_sec = 600.0
cpus = 1
memory_mb = 2048
storage_mb = 10240
gpus = 0
allow_internet = true
mcp_servers = []

[verifier.env]

[environment.env]

[solution.env]
"""


def _tags_toml(tags: list[str]) -> str:
    """Format a list of strings as a TOML array."""
    if not tags:
        return "[]"
    items = ", ".join(repr(t) for t in tags)
    return f"[{items}]"


def generate_task(src: Path, out_dir: Path, dry_run: bool) -> bool:
    """Convert one original task directory to Harbor format.

    Returns True if the task was created/updated, False if skipped.
    """
    yaml_path = src / "task.yaml"
    if not yaml_path.exists():
        print(f"  SKIP {src.name}: no task.yaml", file=sys.stderr)
        return False

    data: dict[str, Any] = yaml.safe_load(yaml_path.read_text())
    instruction: str = data.get("instruction", "").strip()
    if not instruction:
        print(f"  SKIP {src.name}: no instruction in task.yaml", file=sys.stderr)
        return False

    if dry_run:
        print(f"  [dry-run] would generate: {out_dir}")
        return True

    # ------------------------------------------------------------------ dirs
    out_dir.mkdir(parents=True, exist_ok=True)
    env_dir = out_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    sol_dir = out_dir / "solution"
    sol_dir.mkdir(exist_ok=True)

    # --------------------------------------------------------- instruction.md
    (out_dir / "instruction.md").write_text(instruction + "\n")

    # -------------------------------------------------------- environment dir
    dockerfile = src / "Dockerfile"
    if dockerfile.exists():
        shutil.copy2(dockerfile, env_dir / "Dockerfile")
    else:
        # Some tasks use a docker-compose.yaml that builds from a sub-context
        # (e.g. context: client). For these, write a Harbor-compatible
        # docker-compose.yaml that overrides the build context.
        orig_compose = src / "docker-compose.yaml"
        if orig_compose.exists():
            orig_compose_data: dict[str, Any] = yaml.safe_load(orig_compose.read_text())
            for svc_config in (orig_compose_data.get("services") or {}).values():
                build = svc_config.get("build") or {}
                if isinstance(build, dict):
                    context = build.get("context", "")
                    dockerfile_rel = build.get("dockerfile", "Dockerfile")
                    if context and (src / context / dockerfile_rel).exists():
                        harbor_compose = {
                            "services": {
                                "main": {
                                    "build": {
                                        "context": context,
                                        "dockerfile": dockerfile_rel,
                                    }
                                }
                            }
                        }
                        (env_dir / "docker-compose.yaml").write_text(
                            yaml.dump(harbor_compose, default_flow_style=False)
                        )
                        break

    # Copy all non-metadata, non-test files (data files referenced by COPY).
    skip_names = {
        "Dockerfile",
        "docker-compose.yaml",
        "run-tests.sh",
        "task.yaml",
        "solution.sh",
        ".gitkeep",
    }
    for item in src.iterdir():
        if item.name in skip_names or item.name == "tests":
            continue
        dest = env_dir / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # ----------------------------------------------------------- solution dir
    sol_sh = src / "solution.sh"
    if sol_sh.exists():
        shutil.copy2(sol_sh, sol_dir / "solution.sh")

    # --------------------------------------------------------------- tests dir
    tests_src = src / "tests"
    tests_dst = out_dir / "tests"
    if tests_src.exists():
        if tests_dst.exists():
            shutil.rmtree(tests_dst)
        shutil.copytree(tests_src, tests_dst)

    # -------------------------------------------------- tests/test.sh (Harbor requires this)
    # Harbor's TaskPaths.is_valid() checks for tests/test.sh.
    # Copy run-tests.sh → tests/test.sh, replace $TEST_DIR with /tests,
    # and inject a trap-based reward writer after the shebang so it fires on
    # ANY exit — including early exits caused by set -e when pytest fails.
    run_tests_sh = src / "run-tests.sh"
    if run_tests_sh.exists():
        tests_dst.mkdir(exist_ok=True)
        content = run_tests_sh.read_text()
        if not content.endswith("\n"):
            content += "\n"
        content = content.replace("$TEST_DIR", "/tests")
        # Insert trap after shebang line (or at the top if no shebang).
        first, _, rest = content.partition("\n")
        if first.startswith("#!"):
            content = first + "\n" + _REWARD_TRAP + rest
        else:
            content = _REWARD_TRAP + content
        test_sh = tests_dst / "test.sh"
        test_sh.write_text(content)
        test_sh.chmod(test_sh.stat().st_mode | 0o111)

    # ---------------------------------------------------------------- task.toml
    # Skip if task.toml already exists (preserves docker_image from SIF build).
    toml_path = out_dir / "task.toml"
    if not toml_path.exists():
        dur = data.get("estimated_duration_sec")
        dur_line = f"estimated_duration_sec = {float(dur)!r}\n" if dur else ""

        toml_content = _TASK_TOML_TEMPLATE.format(
            author_name=data.get("author_name", ""),
            author_email=data.get("author_email", ""),
            difficulty=data.get("difficulty", ""),
            category=data.get("category", ""),
            tags=_tags_toml(data.get("tags", [])),
            dur_line=dur_line,
            expert_time=float(data.get("expert_time_estimate_min") or 0),
            junior_time=float(data.get("junior_time_estimate_min") or 0),
            verifier_timeout=float(data.get("max_test_timeout_sec") or 180),
            agent_timeout=float(data.get("max_agent_timeout_sec") or 900),
        )
        toml_path.write_text(toml_content)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("tasks", nargs="*", help="Task names to generate (default: all)")
    args = parser.parse_args()

    if not ORIGINAL_TASKS.exists():
        print(f"ERROR: original tasks not found at {ORIGINAL_TASKS}", file=sys.stderr)
        sys.exit(1)

    if args.tasks:
        src_dirs = [ORIGINAL_TASKS / t for t in args.tasks]
    else:
        src_dirs = sorted(ORIGINAL_TASKS.iterdir())

    n_generated = 0
    n_skipped = 0
    n_existing = 0

    for src in src_dirs:
        if not src.is_dir():
            continue
        out_dir = TASKS_OUT_DIR / src.name
        if out_dir.exists() and (out_dir / "task.toml").exists():
            # Already generated; check if non-toml files need updating.
            # We still refresh env/tests but keep task.toml (has docker_image).
            if not args.dry_run:
                generate_task(src, out_dir, dry_run=False)
            n_existing += 1
            continue
        ok = generate_task(src, out_dir, dry_run=args.dry_run)
        if ok:
            n_generated += 1
        else:
            n_skipped += 1

    print(f"Generated: {n_generated}  Updated existing: {n_existing}  Skipped: {n_skipped}")


if __name__ == "__main__":
    main()
