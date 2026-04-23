#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate DABStep benchmark tasks in Harbor format.

Calls the harbor-nemo DABStep adapter to create per-task directories under
util/harbor/tasks/dabstep/.  Uses the pre-extracted ground truth answers from
util/harbor/dabstep_ground_truth.json so that HuggingFace access is not
required at generation time.

The ground truth file was extracted from the adyen/DABstep task_scores split
(197 K scored submissions → shortest correct answer per task_id).

Usage (from repo root):
    python util/harbor/generate_dabstep_tasks.py

Prerequisites:
    pip install datasets  # HuggingFace datasets library (for task metadata)
    uv pip install -e 3p/harbor-nemo   # Harbor runner
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ADAPTER = Path("/localhome/local-rcabral/3p/harbor/adapters/dabstep/run_adapter.py")
ANSWERS_FILE = REPO_ROOT / "util/harbor/dabstep_ground_truth.json"
OUTPUT_DIR = REPO_ROOT / "util/harbor/tasks/dabstep"


def main() -> None:
    if not ADAPTER.exists():
        print(f"ERROR: Harbor DABStep adapter not found at {ADAPTER}")
        print("Clone harbor-nemo to 3p/harbor first:")
        print(
            "  git clone git+ssh://git@gitlab-master.nvidia.com:12051/dl/JoC/competitive_evaluation/core_evals_frameworks/harbor.git 3p/harbor"
        )
        sys.exit(1)

    if not ANSWERS_FILE.exists():
        print(f"ERROR: Ground truth answers not found at {ANSWERS_FILE}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(ADAPTER),
        "--output-dir",
        str(OUTPUT_DIR),
        "--answers-file",
        str(ANSWERS_FILE),
        "--split",
        "default",
    ]

    print(f"Generating DABStep tasks → {OUTPUT_DIR}")
    print(f"Using answers from:       {ANSWERS_FILE}")
    result = subprocess.run(cmd, check=False)

    if result.returncode != 0:
        print(f"\nAdapter exited with code {result.returncode}")
        sys.exit(result.returncode)

    n = sum(1 for p in OUTPUT_DIR.iterdir() if p.is_dir() and p.name.startswith("dabstep-"))
    print(f"\nGenerated {n} DABStep task directories in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
