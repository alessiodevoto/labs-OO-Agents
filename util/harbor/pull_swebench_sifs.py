#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pre-pull Apptainer SIF files for SWE-bench Verified Harbor tasks.

Reads each task directory's task.toml to find the required SIF path, then
calls `apptainer pull` to download the Docker Hub image if the SIF is missing.

Usage (from the worktree root):
    # Pull SIFs for all tasks under a directory:
    python util/harbor/pull_swebench_sifs.py util/harbor/tasks/swebench_smoke

    # Dry-run — print what would be pulled without actually pulling:
    python util/harbor/pull_swebench_sifs.py --dry-run util/harbor/tasks/swebench_smoke

    # Pull a single Docker image explicitly:
    python util/harbor/pull_swebench_sifs.py --image docker://swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest

Notes:
  - SIFs are written to ~/3p/sif_cache/ by default.
  - Each SWE-bench SIF is ~300–700 MB; budget ~500 MB/task.
  - Docker Hub must be reachable (no proxy issues on this machine).
  - Does not require sudo (SWE-bench images are user-runnable without fakeroot).
  - Re-run safely: already-present SIFs are skipped.

SIF naming convention (matches Harbor's Apptainer environment):
  docker://swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
  -> swebench_sweb.eval.x86_64.astropy_1776_astropy-12907_latest.sif
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

def _sif_cache_dir() -> Path:
    """Return ~/3p/sif_cache, respecting SUDO_USER if running as root."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        for prefix in ("/localhome", "/home"):
            candidate = Path(prefix) / sudo_user / "3p/sif_cache"
            if candidate.parent.parent.exists():
                return candidate
    return Path.home() / "3p/sif_cache"


def docker_uri_to_sif_name(docker_uri: str) -> str:
    """Convert a docker:// URI to the SIF filename Harbor expects.

    Examples:
      docker://swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
      -> swebench_sweb.eval.x86_64.astropy_1776_astropy-12907_latest.sif
    """
    # Strip docker:// prefix
    uri = docker_uri.removeprefix("docker://")
    # Replace : with _ (tag separator)
    uri = uri.replace(":", "_")
    # Replace / with _ (namespace separator)
    uri = uri.replace("/", "_")
    return f"{uri}.sif"


def task_toml_to_docker_uri(task_toml: Path) -> str | None:
    """Extract the docker_image field from a task.toml.

    Returns None if the field is not a docker:// URI (e.g. already a SIF path).
    Returns the docker URI if the field starts with 'swebench/' (Docker Hub).
    """
    if tomllib is None:
        raise RuntimeError("tomllib/tomli not available — install tomli: `uv add tomli`")
    data = tomllib.loads(task_toml.read_text())
    docker_image = data.get("environment", {}).get("docker_image", "")
    if docker_image.startswith("swebench/"):
        return f"docker://{docker_image}"
    # Already a SIF path or a non-swebench image
    return None


def collect_tasks(tasks_dir: Path) -> list[tuple[Path, str]]:
    """Return [(task_dir, docker_uri), ...] for tasks that need a SIF pull."""
    results = []
    for task_toml in sorted(tasks_dir.rglob("task.toml")):
        task_dir = task_toml.parent
        try:
            uri = task_toml_to_docker_uri(task_toml)
        except Exception as exc:
            print(f"[WARN] Could not parse {task_toml}: {exc}")
            continue
        if uri is not None:
            results.append((task_dir, uri))
    return results


def pull_sif(docker_uri: str, sif_cache: Path, *, dry_run: bool = False) -> bool:
    """Pull a SIF from Docker Hub if not already present.  Returns True on success."""
    sif_name = docker_uri_to_sif_name(docker_uri)
    sif_path = sif_cache / sif_name

    if sif_path.exists():
        print(f"[SKIP] {sif_name} (already cached)")
        return True

    if dry_run:
        print(f"[DRY]  apptainer pull {sif_cache}/{sif_name} {docker_uri}")
        return True

    print(f"[PULL] {docker_uri}")
    sif_cache.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, 4):
        result = subprocess.run(
            ["apptainer", "pull", "--dir", str(sif_cache), sif_name, docker_uri],
            check=False,
        )
        if result.returncode == 0:
            print(f"[OK]   {sif_name}")
            return True
        print(f"[WARN] attempt {attempt}/3 failed for {docker_uri}")

    print(f"[FAIL] {docker_uri}")
    return False


def update_task_toml(task_toml: Path, sif_path: Path) -> None:
    """Rewrite task.toml so docker_image points to the local SIF path."""
    text = task_toml.read_text()
    # Find and replace the docker_image line
    new_text = re.sub(
        r'(docker_image\s*=\s*")[^"]*(")',
        rf"\g<1>{sif_path}\g<2>",
        text,
    )
    if new_text != text:
        task_toml.write_text(new_text)
        print(f"[TOML] Updated {task_toml.parent.name}/task.toml -> {sif_path.name}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "tasks_dir", nargs="?", type=Path, help="Directory containing Harbor task subdirectories"
    )
    ap.add_argument("--image", type=str, help="Pull a single docker:// URI explicitly")
    ap.add_argument(
        "--sif-cache", type=Path, default=None, help="SIF cache directory (default: ~/3p/sif_cache)"
    )
    ap.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    ap.add_argument(
        "--update-toml",
        action="store_true",
        default=True,
        help="Update task.toml docker_image to point to local SIF (default: True)",
    )
    ap.add_argument("--no-update-toml", dest="update_toml", action="store_false")
    args = ap.parse_args()

    sif_cache = args.sif_cache or _sif_cache_dir()
    print(f"SIF cache: {sif_cache}")

    if args.image:
        success = pull_sif(args.image, sif_cache, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    if not args.tasks_dir:
        ap.error("Provide a tasks_dir or --image")

    tasks_dir = Path(args.tasks_dir)
    if not tasks_dir.exists():
        print(f"ERROR: tasks_dir does not exist: {tasks_dir}", file=sys.stderr)
        sys.exit(1)

    tasks = collect_tasks(tasks_dir)
    if not tasks:
        print(f"No swebench tasks found under {tasks_dir}")
        sys.exit(0)

    print(f"Found {len(tasks)} task(s) needing SIF pulls")

    ok, fail = 0, 0
    for task_dir, docker_uri in tasks:
        success = pull_sif(docker_uri, sif_cache, dry_run=args.dry_run)
        if success:
            ok += 1
            if args.update_toml and not args.dry_run:
                sif_name = docker_uri_to_sif_name(docker_uri)
                sif_path = sif_cache / sif_name
                task_toml = task_dir / "task.toml"
                update_task_toml(task_toml, sif_path)
        else:
            fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
