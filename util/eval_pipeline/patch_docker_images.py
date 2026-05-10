#!/usr/bin/env python3
"""Patch task.toml with docker_image from Dockerfiles and fix TOML issues.

Harbor apptainer backend requires docker_image in task.toml.
This script extracts FROM from each task Dockerfile and adds it.
Also fixes common TOML issues (malformed tags, unquoted values).

Usage:
    python util/eval_pipeline/patch_docker_images.py [--tasks-dir DIR] [--sanitize]
"""

import argparse
import os
import re

DEFAULT_IMAGE = "ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624"


def patch_tasks(tasks_dir: str) -> None:
    """Add docker_image to task.toml from each task's Dockerfile FROM line."""
    fixed = 0
    failed = []

    for task in sorted(os.listdir(tasks_dir)):
        td = os.path.join(tasks_dir, task)
        if not os.path.isdir(td):
            continue

        toml_path = os.path.join(td, "task.toml")
        if not os.path.exists(toml_path):
            continue

        toml = open(toml_path).read()
        if "docker_image" in toml:
            fixed += 1
            continue

        dockerfile = os.path.join(td, "environment", "Dockerfile")
        docker_image = DEFAULT_IMAGE

        if os.path.exists(dockerfile):
            content = open(dockerfile).read()
            from_match = re.search(r"^FROM\s+(?:--\S+\s+)*([^\s]+)", content, re.MULTILINE)
            if from_match:
                candidate = from_match.group(1)
                if "$" in candidate or "{" in candidate:
                    arg_name = re.search(r"\$\{?(\w+)", candidate)
                    if arg_name:
                        arg_val = re.search(
                            r"^ARG\s+" + arg_name.group(1) + r"=([^\s]+)",
                            content,
                            re.MULTILINE,
                        )
                        if arg_val:
                            docker_image = arg_val.group(1).strip('"').strip("'")
                        else:
                            failed.append((task, f"unresolvable ARG: {candidate}"))
                    else:
                        failed.append((task, f"complex FROM: {candidate}"))
                else:
                    docker_image = candidate
            else:
                failed.append((task, "no FROM line in Dockerfile"))
        else:
            failed.append((task, "no Dockerfile, using default image"))

        toml = re.sub(r"^docker_image\s*=.*\n?", "", toml, flags=re.MULTILINE)
        if "[environment]" in toml:
            toml = toml.replace(
                "[environment]\n",
                f'[environment]\ndocker_image = "{docker_image}"\n',
                1,
            )
        else:
            toml += f'\n[environment]\ndocker_image = "{docker_image}"\n'

        open(toml_path, "w").write(toml)
        fixed += 1

    total = len([d for d in os.listdir(tasks_dir) if os.path.isdir(os.path.join(tasks_dir, d))])
    print(f"Patched: {fixed}/{total}")
    if failed:
        print(f"Warnings ({len(failed)} tasks used fallback image):")
        for t, r in failed:
            print(f"  {t}: {r}")


def sanitize_toml(tasks_dir: str) -> None:
    """Fix common TOML issues: malformed tags, unquoted docker_image values."""
    import tomllib

    fixed = 0
    for task in sorted(os.listdir(tasks_dir)):
        td = os.path.join(tasks_dir, task)
        if not os.path.isdir(td):
            continue
        tp = os.path.join(td, "task.toml")
        if not os.path.exists(tp):
            continue
        content = open(tp).read()
        try:
            tomllib.loads(content)
            continue
        except Exception:
            pass

        # Fix unquoted docker_image values
        content = re.sub(
            r'^(docker_image\s*=\s*)([^"\'\n][^\n]*)',
            lambda m: m.group(1) + '"' + m.group(2).strip() + '"',
            content,
            flags=re.MULTILINE,
        )
        # Fix empty/malformed tags: tags = [ ,] -> tags = []
        content = re.sub(r"tags\s*=\s*\[\s*,?\s*\]", "tags = []", content)
        # Fix trailing comma before ]
        content = re.sub(r",(\s*\])", r"\1", content)

        try:
            tomllib.loads(content)
            open(tp, "w").write(content)
            fixed += 1
        except Exception as e:
            print(f"  Cannot fix {task}: {e}")

    print(f"Sanitized: {fixed} files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", default="util/harbor/tasks/terminal_bench")
    parser.add_argument(
        "--sanitize",
        action="store_true",
        help="Also fix malformed TOML (tags, unquoted values)",
    )
    args = parser.parse_args()
    patch_tasks(args.tasks_dir)
    if args.sanitize:
        sanitize_toml(args.tasks_dir)
