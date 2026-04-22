#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pre-pull Apptainer SIF files for SWE-bench Pro Harbor tasks.

Reads each task directory's environment/Dockerfile to find the required Docker
image (the FROM line), then calls `apptainer pull` to download the Docker Hub
image if the SIF is missing.

Unlike SWE-bench Verified, SWE-bench Pro tasks do not store the image reference
in task.toml.  Instead, Harbor reads the FROM line from the Dockerfile and
converts it to a SIF on demand.  This script pre-populates the SIF cache so no
network pulls happen during a live harbor run.

Images are from the `jefzda/sweap-images` namespace on Docker Hub:
  jefzda/sweap-images:ansible.ansible-instance_ansible__ansible-xxxx
  -> jefzda_sweap-images_ansible.ansible-instance_ansible__ansible-xxxx.sif

The SIF filename follows Harbor's own convention (apptainer.py line 315-316):
  safe_name = docker_image.replace("/", "_").replace(":", "_")
  sif_path  = image_cache_dir / f"{safe_name}.sif"

Usage (from the worktree root):
    # Pull SIFs for all tasks under a directory:
    python util/harbor/pull_swebenchpro_sifs.py util/harbor/tasks/swebenchpro

    # Dry-run — print what would be pulled without actually pulling:
    python util/harbor/pull_swebenchpro_sifs.py --dry-run util/harbor/tasks/swebenchpro

    # Pull a single Docker image explicitly (no docker:// prefix needed):
    python util/harbor/pull_swebenchpro_sifs.py --image jefzda/sweap-images:some-tag

Notes:
  - SIFs are written to ~/3p/sif_cache/ by default.
  - Each SWE-bench Pro SIF is ~500 MB–2 GB; budget ~1 GB/task.
  - Docker Hub must be reachable (no proxy issues on this machine).
  - Does not require sudo (images are user-runnable without fakeroot at pull time).
  - Re-run safely: already-present SIFs are skipped.
  - The script does NOT update task.toml — Harbor reads the Dockerfile directly.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent


def _sif_cache_dir() -> Path:
    """Return ~/3p/sif_cache, respecting SUDO_USER if running as root."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        for prefix in ("/localhome", "/home"):
            candidate = Path(prefix) / sudo_user / "3p/sif_cache"
            if candidate.parent.parent.exists():
                return candidate
    return Path.home() / "3p/sif_cache"


def docker_image_to_sif_name(docker_image: str) -> str:
    """Convert a Docker image reference to the SIF filename Harbor expects.

    Matches Harbor's ApptainerEnvironment._convert_docker_to_sif() naming:
      safe_name = docker_image.replace("/", "_").replace(":", "_")
      sif_path  = cache_dir / f"{safe_name}.sif"

    Examples:
      jefzda/sweap-images:ansible.ansible-instance_ansible__ansible-xxxx
      -> jefzda_sweap-images_ansible.ansible-instance_ansible__ansible-xxxx.sif
    """
    safe_name = docker_image.replace("/", "_").replace(":", "_")
    return f"{safe_name}.sif"


def dockerfile_to_docker_image(dockerfile: Path) -> str | None:
    """Extract the first FROM image line from a Dockerfile.

    Returns the image reference (e.g. 'jefzda/sweap-images:some-tag') or None
    if no FROM line is found or it is a scratch/ARG-based stage.
    """
    try:
        for line in dockerfile.read_text().splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("FROM "):
                image = stripped.split(None, 1)[1].strip()
                # Skip multi-stage alias lines: FROM base AS builder
                parts = image.split()
                if len(parts) >= 3 and parts[1].upper() == "AS":
                    image = parts[0]
                # Skip scratch (no real image) or ARG references
                if image.lower() in ("scratch",) or image.startswith("$"):
                    return None
                return image
    except OSError:
        pass
    return None


def collect_tasks(tasks_dir: Path) -> list[tuple[Path, str]]:
    """Return [(task_dir, docker_image), ...] for tasks with a jefzda/sweap-images image."""
    results = []
    for dockerfile in sorted(tasks_dir.rglob("environment/Dockerfile")):
        task_dir = dockerfile.parent.parent
        image = dockerfile_to_docker_image(dockerfile)
        if image is None:
            continue
        # Only handle SWE-bench Pro images (jefzda/sweap-images namespace)
        if not image.startswith("jefzda/"):
            continue
        results.append((task_dir, image))
    return results


def pull_sif(docker_image: str, sif_cache: Path, *, dry_run: bool = False) -> bool:
    """Pull a SIF from Docker Hub if not already present.  Returns True on success."""
    sif_name = docker_image_to_sif_name(docker_image)
    sif_path = sif_cache / sif_name

    if sif_path.exists():
        print(f"[SKIP] {sif_name} (already cached)")
        return True

    docker_uri = f"docker://{docker_image}"

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


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "tasks_dir", nargs="?", type=Path, help="Directory containing Harbor task subdirectories"
    )
    ap.add_argument(
        "--image",
        type=str,
        help="Pull a single Docker image explicitly (e.g. jefzda/sweap-images:some-tag)",
    )
    ap.add_argument(
        "--sif-cache",
        type=Path,
        default=None,
        help="SIF cache directory (default: ~/3p/sif_cache)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    args = ap.parse_args()

    sif_cache = args.sif_cache or _sif_cache_dir()
    print(f"SIF cache: {sif_cache}")

    if args.image:
        # Strip docker:// prefix if user passed it
        image = args.image.removeprefix("docker://")
        success = pull_sif(image, sif_cache, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    if not args.tasks_dir:
        ap.error("Provide a tasks_dir or --image")

    tasks_dir = Path(args.tasks_dir)
    if not tasks_dir.exists():
        print(f"ERROR: tasks_dir does not exist: {tasks_dir}", file=sys.stderr)
        sys.exit(1)

    tasks = collect_tasks(tasks_dir)
    if not tasks:
        print(f"No SWE-bench Pro tasks (jefzda/sweap-images) found under {tasks_dir}")
        sys.exit(0)

    print(f"Found {len(tasks)} task(s) needing SIF pulls")

    ok, fail = 0, 0
    for _task_dir, docker_image in tasks:
        success = pull_sif(docker_image, sif_cache, dry_run=args.dry_run)
        if success:
            ok += 1
        else:
            fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
