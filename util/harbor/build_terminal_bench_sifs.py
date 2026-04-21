#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build task-specific Apptainer SIF files for Terminal Bench smoke-test tasks.

Reads each task's Dockerfile, translates it to an Apptainer .def, builds with
sudo on top of the already-cached base SIFs, and updates task.toml so Harbor
uses the pre-built SIF instead of re-pulling the base image.

Every generated SIF has curl pre-installed so the Terminal Bench verifier
test scripts can install uv at runtime (the scripts run apt-get install curl
first, which fails without root, but then call curl directly — pre-installing
curl in the image makes that call succeed).

Usage (from the worktree root):
    python util/harbor/build_terminal_bench_sifs.py [--dry-run] [task_name ...]

Flags:
    --dry-run   Print generated .def files without building.
    task_name   Build only the named task(s). Default: all tasks.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_DIR = WORKTREE_ROOT / "util/harbor/tasks/terminal_bench"
SIF_CACHE = Path.home() / "3p/sif_cache"

# Cached base SIFs pulled in previous Harbor runs.
# Keys are Docker image references (as they appear in FROM lines).
_BASE_SIFS: dict[str, Path] = {
    "ghcr.io/laude-institute/t-bench/ubuntu-24-04:latest": SIF_CACHE
    / "ghcr.io_laude-institute_t-bench_ubuntu-24-04_latest.sif",
    "ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624": SIF_CACHE
    / "ghcr.io_laude-institute_t-bench_ubuntu-24-04_20250624.sif",
    "ghcr.io/laude-institute/t-bench/python-3-13:20250620": SIF_CACHE
    / "ghcr.io_laude-institute_t-bench_python-3-13_20250620.sif",
    # python-3-13:latest — treat as the same as the versioned SIF if present
    "ghcr.io/laude-institute/t-bench/python-3-13:latest": SIF_CACHE
    / "ghcr.io_laude-institute_t-bench_python-3-13_latest.sif",
}


# ---------------------------------------------------------------------------
# Dockerfile parser
# ---------------------------------------------------------------------------


def _join_continuations(lines: list[str]) -> list[str]:
    """Merge backslash-continuation lines into single logical lines."""
    out: list[str] = []
    buf = ""
    for raw in lines:
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1]
        else:
            buf += stripped
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def parse_dockerfile(path: Path):
    """Return (from_image, run_cmds, file_copies, env_vars, workdirs, copy_from_images).

    copy_from_images: external registry images referenced via COPY --from=<image>.
    COPY --from= lines are excluded from file_copies (can't be resolved on host).
    """
    lines = _join_continuations(path.read_text().splitlines())
    from_image = None
    run_cmds: list[str] = []
    file_copies: list[tuple[str, str]] = []  # (src_rel_to_task_dir, dst_in_container)
    env_vars: list[str] = []
    workdirs: list[str] = []
    copy_from_images: list[str] = []  # external images from COPY --from=

    for line in lines:
        if not line or line.startswith("#"):
            continue
        keyword = line.split()[0].upper()
        rest = line.split(None, 1)[1] if len(line.split()) > 1 else ""
        if keyword == "FROM":
            # Strip optional --platform=... flag and "AS <name>" alias.
            parts = rest.split()
            non_flags = [p for p in parts if not p.startswith("--")]
            from_image = non_flags[0] if non_flags else parts[0]
        elif keyword == "RUN":
            run_cmds.append(rest)
        elif keyword == "COPY":
            tokens = rest.split()
            if tokens and tokens[0].startswith("--from="):
                # Multi-stage COPY — can't copy from host. Track external images.
                image_ref = tokens[0][7:]
                if "/" in image_ref or ":" in image_ref:
                    copy_from_images.append(image_ref)
                # Skip: internal stage refs (e.g. --from=builder) have no host path.
            else:
                parts = rest.split(None, 1)
                if len(parts) == 2:
                    file_copies.append((parts[0], parts[1]))
        elif keyword == "ENV":
            env_vars.append(rest.replace(" ", "=", 1) if "=" not in rest else rest)
        elif keyword == "WORKDIR":
            workdirs.append(rest)

    return from_image, run_cmds, file_copies, env_vars, workdirs, copy_from_images


# ---------------------------------------------------------------------------
# .def generator
# ---------------------------------------------------------------------------


def generate_def(
    task_name: str,
    from_image: str,
    run_cmds: list[str],
    file_copies: list[tuple[str, str]],
    env_vars: list[str],
    workdirs: list[str],
    env_dir: Path,
    copy_from_images: list[str] | None = None,
) -> str:
    """Translate parsed Dockerfile fields into an Apptainer definition file."""
    base_sif = _BASE_SIFS.get(from_image)
    if base_sif and base_sif.exists():
        header = f"Bootstrap: localimage\nFrom: {base_sif}"
    else:
        # Fall back to pulling from registry (slower, needs network).
        print(
            f"  WARNING: no cached SIF for {from_image!r}; falling back to docker://",
            file=sys.stderr,
        )
        header = f"Bootstrap: docker\nFrom: {from_image}"

    sections: list[str] = [header, ""]

    # %setup — runs on the HOST with container FS at $SINGULARITY_ROOTFS,
    # before %files.  Use it to pre-create directories that COPY needs.
    # (%post runs after %files so mkdir there is too late for the copy.)
    setup: list[str] = []
    for _, dst in file_copies:
        parent = str(Path(dst).parent)
        if parent not in ("/", "", ".", ".."):
            setup.append(f'    mkdir -p "${{SINGULARITY_ROOTFS}}{parent}"')
    if setup:
        sections.append("%setup")
        sections.extend(setup)
        sections.append("")

    # %files — source paths are absolute so they work regardless of CWD.
    # Source files may live in env_dir (environment/) or task_dir (e.g. tests/).
    if file_copies:
        sections.append("%files")
        for src, dst in file_copies:
            abs_src = env_dir / src
            if not abs_src.exists():
                # Also check task_dir/src (e.g. tests/foo.py → task_dir/tests/foo.py)
                alt = env_dir.parent / src
                if alt.exists():
                    abs_src = alt
            sections.append(f"    {abs_src} {dst}")
        sections.append("")

    # %post — always pre-install curl so verifier scripts can call it even
    # though their own `apt-get install curl` fails (no root at runtime).
    # Then apply the Dockerfile's RUN commands, then WORKDIR mkdirs.
    post: list[str] = [
        "    apt-get update -qq && apt-get install -y --no-install-recommends curl"
        " && rm -rf /var/lib/apt/lists/*",
    ]

    # If the Dockerfile used COPY --from=ghcr.io/astral-sh/uv, install uv now.
    if copy_from_images and any("astral-sh/uv" in img for img in copy_from_images):
        post.append(
            "    # Install uv (replaces COPY --from=ghcr.io/astral-sh/uv in Dockerfile)\n"
            "    curl -LsSf https://astral.sh/uv/install.sh | env HOME=/root sh"
            " && cp /root/.local/bin/uv /bin/uv && cp /root/.local/bin/uvx /bin/uvx"
        )

    for cmd in run_cmds:
        # Normalize bare `mkdir` (no -p) to `mkdir -p` so %setup-pre-created
        # directories don't cause "File exists" failures in %post.
        import re as _re

        cmd = _re.sub(r"\bmkdir(?!\s+-)", "mkdir -p", cmd)
        post.append(f"    {cmd}")

    for wd in workdirs:
        post.append(f"    mkdir -p {wd}")

    # Redirect apt and temp I/O to /staging at runtime so neither apt-get update
    # nor uv installer fills the writable-tmpfs overlay.  /staging is a real
    # bind-mounted host directory.  These lines are added AFTER all build-time
    # apt calls so they don't interfere with %post package installs.
    post.extend(
        [
            "    # Redirect apt lists + TMPDIR to /staging at runtime to avoid ENOSPC",
            "    mkdir -p /etc/apt/apt.conf.d",
            # APT conf: store package lists on /staging; pre-invoke creates the dirs.
            r"""    printf '%s\n' \
        'Dir::State::Lists "/staging/apt-lists";' \
        'APT::Update::Pre-Invoke {"mkdir -p /staging/apt-lists/partial /staging/tmp";};' \
        > /etc/apt/apt.conf.d/99-tbench-staging""",
        ]
    )

    if post:
        sections.append("%post")
        sections.extend(post)
        sections.append("")

    # %environment — ENV directives + WORKDIR + staging redirects.
    env_section: list[str] = []
    for ev in env_vars:
        env_section.append(f"    export {ev}")
    if workdirs:
        env_section.append(f"    cd {workdirs[-1]} 2>/dev/null || true")
    # Redirect temp dir and HOME to /staging (real bind-mounted storage) so uv
    # installer, Python extract, and other tools don't fill the writable-tmpfs.
    # HOME=/staging/home redirects ~/.cache/uv and ~/.local/bin off tmpfs.
    env_section.append("    export TMPDIR=/staging/tmp")
    env_section.append("    export HOME=/staging/home")

    sections.append("%environment")
    sections.extend(env_section)
    sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_sif(
    task_name: str, def_content: str, dry_run: bool, skip_built: bool = False
) -> Path | None:
    sif_path = SIF_CACHE / f"terminal_bench_{task_name}.sif"

    print(f"\n{'=' * 60}")
    print(f"Task: {task_name}")
    print(f"{'=' * 60}")
    print(def_content)

    if dry_run:
        print(f"[dry-run] would build → {sif_path}")
        return None

    if skip_built and sif_path.exists():
        print(f"[skip-built] SIF already exists: {sif_path}")
        return sif_path

    with tempfile.NamedTemporaryFile(suffix=".def", mode="w", delete=False, dir="/tmp") as fh:
        fh.write(def_content)
        def_path = Path(fh.name)

    try:
        result = subprocess.run(
            [
                "sudo",
                "apptainer",
                "build",
                "--force",
                str(sif_path),
                str(def_path),
            ],
            cwd=WORKTREE_ROOT,
        )
    finally:
        def_path.unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"ERROR: apptainer build failed for {task_name}", file=sys.stderr)
        return None

    print(f"Built: {sif_path}")
    return sif_path


def update_task_toml(task_dir: Path, sif_path: Path) -> None:
    toml_path = task_dir / "task.toml"
    content = toml_path.read_text()

    if "docker_image" in content:
        print("  task.toml already has docker_image — skipping")
        return

    rel = sif_path.relative_to(WORKTREE_ROOT)
    updated = content.replace(
        "[environment]\n",
        f'[environment]\ndocker_image = "{rel}"\n',
        1,
    )
    toml_path.write_text(updated)
    print(f"  task.toml updated: docker_image = {rel}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-built",
        action="store_true",
        help="Skip tasks whose SIF already exists in 3p/sif_cache/.",
    )
    parser.add_argument("tasks", nargs="*", help="Task names to build (default: all)")
    args = parser.parse_args()

    task_dirs = sorted(TASKS_DIR.iterdir()) if TASKS_DIR.exists() else []
    if args.tasks:
        task_dirs = [TASKS_DIR / t for t in args.tasks]

    SIF_CACHE.mkdir(parents=True, exist_ok=True)

    for task_dir in task_dirs:
        if not task_dir.is_dir():
            continue
        dockerfile = task_dir / "environment" / "Dockerfile"
        if not dockerfile.exists():
            print(f"Skipping {task_dir.name}: no Dockerfile")
            continue

        from_image, run_cmds, file_copies, env_vars, workdirs, copy_from_images = parse_dockerfile(
            dockerfile
        )
        if not from_image:
            print(f"Skipping {task_dir.name}: no FROM in Dockerfile")
            continue

        def_content = generate_def(
            task_dir.name,
            from_image,
            run_cmds,
            file_copies,
            env_vars,
            workdirs,
            task_dir / "environment",
            copy_from_images=copy_from_images,
        )

        sif_path = build_sif(task_dir.name, def_content, args.dry_run, args.skip_built)

        if sif_path and not args.dry_run:
            update_task_toml(task_dir, sif_path)


if __name__ == "__main__":
    main()
