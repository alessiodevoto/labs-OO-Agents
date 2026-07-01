# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Smoke test for the long-horizon memory benchmark example (oracle solver)."""

import subprocess
import sys
from pathlib import Path

import pytest

_BENCH_DIR = Path(__file__).resolve().parents[2] / "examples" / "memory_bench"
BENCH = _BENCH_DIR / "bench.py"
RECALL_QA = _BENCH_DIR / "recall_qa.py"
MEMORY_EFFECT = _BENCH_DIR / "memory_effect.py"
LOCOMO = _BENCH_DIR / "locomo.py"
LOCOMO_DATA = _BENCH_DIR / "data" / "locomo10.json"


def _run(*args: str, script: Path = BENCH) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.parametrize("backend", ["numpy", "sqlite_vec"])
def test_oracle_bench_runs_and_reports(backend):
    if backend == "sqlite_vec":
        pytest.importorskip("sqlite_vec")
    res = _run("--solver", "oracle", "--memory", "on", "--backend", backend)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "requirement-fulfillment (f2p): 100%" in out
    assert "regression-avoidance (p2p): 100%" in out
    # monitoring counters surfaced
    assert "memory usage: writes=" in out
    assert "reflections=" in out


def test_oracle_compare_mode_runs():
    res = _run("--solver", "oracle", "--compare", "--backend", "numpy")
    assert res.returncode == 0, res.stderr
    assert "comparison (memory ON vs OFF)" in res.stdout


def test_recall_qa_oracle_shows_full_gain():
    """The cross-session QA benchmark: memory is decisive (write/agent 100%, OFF 0%).

    recall_qa now reports a write-op ablation table, so assert the two decisive rows
    (no-memory baseline at 0%, agent-authored memory at 100%) rather than the old
    'gain from memory' summary line.
    """
    res = _run("--solver", "oracle", script=RECALL_QA)
    assert res.returncode == 0, res.stderr
    assert "0/8 (0%)" in res.stdout  # OFF baseline: no memory -> 0%
    assert "8/8 (100%)" in res.stdout  # agent-authored memory -> 100%


def test_memory_effect_oracle_shows_both_effects():
    """Memory is useful in one scenario and detrimental in the other."""
    res = _run("--solver", "oracle", script=MEMORY_EFFECT)
    assert res.returncode == 0, res.stderr
    assert "memory HELPED" in res.stdout  # recall scenario
    assert "memory HURT" in res.stdout  # stale scenario


def test_locomo_requires_llm_credentials():
    """LoCoMo is agent-authored (no raw/offline fallback): without creds it exits clearly.

    Only meaningful when ARC_LLM_* is absent (e.g. CI). Skips when creds are present
    (a dev box with .env), since there we'd kick off a real, paid benchmark run.
    """
    import os

    if os.environ.get("ARC_LLM_API_KEY") or (LOCOMO.parent / ".env").exists():
        pytest.skip(
            "LLM creds present (.env) — would start a real run; guard tested only without creds"
        )
    res = _run("--limit", "4", script=LOCOMO)
    assert res.returncode == 2
    assert "AGENT-AUTHORED" in (res.stdout + res.stderr)
