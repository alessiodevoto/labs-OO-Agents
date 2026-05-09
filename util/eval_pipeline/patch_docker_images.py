#!/usr/bin/env python3
"""Patch task.toml with docker_image from Dockerfiles.

Harbor apptainer backend requires docker_image in task.toml.
This script extracts FROM from each task Dockerfile and adds it.

Usage: python util/eval_pipeline/patch_docker_images.py [--tasks-dir DIR]
"""

import argparse
import os
import re

DEFAULT_IMAGE = "ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624"


def patch_tasks(tasks_dir: str) -> None:
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

        # Try to extract FROM from Dockerfile
        dockerfile = os.path.join(td, "environment", "Dockerfile")
        docker_image = DEFAULT_IMAGE  # fallback

        if os.path.exists(dockerfile):
            content = open(dockerfile).read()
            # Skip --platform and other flags after FROM
            from_match = re.search(r"^FROM\s+(?:--\S+\s+)*([^\s]+)", content, re.MULTILINE)
            if from_match:
                candidate = from_match.group(1)
                # Handle ARG-based FROM (e.g. FROM ${BASE_IMAGE})
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
                            docker_image = DEFAULT_IMAGE
                    else:
                        failed.append((task, f"complex FROM: {candidate}"))
                        docker_image = DEFAULT_IMAGE
                else:
                    docker_image = candidate
            else:
                failed.append((task, "no FROM line in Dockerfile"))
        else:
            failed.append((task, "no Dockerfile, using default image"))

        # Remove any existing (possibly malformed) docker_image line
        toml = re.sub(r"^docker_image\s*=.*\n?", "", toml, flags=re.MULTILINE)

        # Insert docker_image into [environment] section (always quoted)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", default="util/harbor/tasks/terminal_bench")
    args = parser.parse_args()
    patch_tasks(args.tasks_dir)
