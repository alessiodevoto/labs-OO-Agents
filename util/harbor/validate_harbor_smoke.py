#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate a Harbor smoke run before launching a full benchmark.

This is intentionally stricter than Harbor rc/result.json success. Some agent
failures are benchmark-invalid but look infra-clean to Harbor: the agent can
return ``success=true`` with ``response="done"`` and zero token usage after
swallowing an LLM-provider error, then the verifier simply scores reward 0.

Use this gate after every smoke run and before every full run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_BAD_LOG_PATTERNS = (
    "LLM Provider NOT provided",
    "Unknown LLM provider",
    "BadRequestError: litellm.BadRequestError",
    "Missing Anthropic API Key",
    "AuthenticationError: litellm.AuthenticationError",
    "OPENAI_API_KEY environment variable",
    "api_key client option must be set",
    "Unknown agent_type",
    "uv: command not found",
    "Cannot import 'hatchling.build'",
    "python3: command not found",
)
_DUMMY_RESPONSES = {"", "done", "ok", "success"}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="ignore"))
    except Exception:
        return {}


def _candidate_run_dirs(path: Path) -> list[Path]:
    if (path / "result.json").exists() and any(p.is_dir() for p in path.iterdir()):
        return [path]
    runs = [p for p in path.iterdir() if p.is_dir()] if path.is_dir() else []
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)


def _best_run_dir(path: Path) -> Path:
    runs = _candidate_run_dirs(path)
    if not runs:
        raise SystemExit(f"no Harbor run directories found under {path}")
    return max(runs, key=lambda p: sum(1 for _ in p.glob("*/result.json")))


def _trial_dirs(run_dir: Path) -> list[Path]:
    return sorted(p for p in run_dir.iterdir() if p.is_dir())


def _agent_result(trial_dir: Path) -> dict:
    direct = _read_json(trial_dir / "agent" / "result.json")
    if direct:
        return direct
    harbor = _read_json(trial_dir / "result.json")
    return harbor.get("agent_result") or {}


def _reward(trial_dir: Path) -> float | None:
    reward_txt = trial_dir / "verifier" / "reward.txt"
    if reward_txt.exists():
        try:
            return float(reward_txt.read_text(errors="ignore").strip())
        except Exception:
            return None
    result = _read_json(trial_dir / "result.json")
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    if "reward" in rewards:
        try:
            return float(rewards["reward"])
        except Exception:
            return None
    return None


def _exception_type(trial_dir: Path) -> str | None:
    result = _read_json(trial_dir / "result.json")
    exc = result.get("exception_info") or {}
    return exc.get("exception_type")


def _scan_logs(trial_dir: Path) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for rel in (
        "agent/nemo_oo_agents_benchmarks.log",
        "agent/command-0/stdout.txt",
        "agent/command-0/stderr.txt",
        "agent/setup/stdout.txt",
        "verifier/test-stdout.txt",
        "exception.txt",
    ):
        path = trial_dir / rel
        if not path.exists():
            continue
        text = path.read_text(errors="ignore")
        for pattern in _BAD_LOG_PATTERNS:
            if pattern in text:
                hits.append((rel, pattern))
    return hits


def validate(path: Path, *, min_completed: int, require_positive_tokens: bool) -> int:
    run_dir = _best_run_dir(path)
    trials = _trial_dirs(run_dir)
    completed = [t for t in trials if (t / "result.json").exists()]
    rewarded = [t for t in trials if _reward(t) is not None]
    exceptions = [t for t in completed if _exception_type(t)]

    failures: list[str] = []
    if len(completed) < min_completed:
        failures.append(
            f"only {len(completed)} completed trials; expected at least {min_completed}"
        )
    if exceptions:
        counts = Counter(_exception_type(t) for t in exceptions)
        failures.append(
            f"{len(exceptions)} completed trials have Harbor exceptions: {dict(counts)}"
        )

    tokenless: list[str] = []
    dummy: list[str] = []
    unsuccessful: list[str] = []
    log_hits: list[tuple[str, str, str]] = []
    token_values: list[tuple[int | None, int | None]] = []

    for trial in completed:
        agent = _agent_result(trial)
        n_in = agent.get("n_input_tokens")
        n_out = agent.get("n_output_tokens")
        token_values.append((n_in, n_out))
        if agent.get("success") is False:
            unsuccessful.append(trial.name)
        if (n_in in (None, 0)) and (n_out in (None, 0)):
            tokenless.append(trial.name)
        response = agent.get("response")
        if isinstance(response, str) and response.strip().lower() in _DUMMY_RESPONSES:
            dummy.append(trial.name)
        for rel, pattern in _scan_logs(trial):
            log_hits.append((trial.name, rel, pattern))

    if require_positive_tokens and completed and len(tokenless) == len(completed):
        failures.append("all completed trials have zero/missing agent token usage")
    if completed and len(dummy) == len(completed):
        failures.append("all completed trials have dummy agent responses (done/ok/success/empty)")
    if unsuccessful:
        failures.append(f"{len(unsuccessful)} trials report agent success=false")
    if log_hits:
        sample = log_hits[:5]
        failures.append(f"known bad log patterns found: {sample}")

    rewards = [_reward(t) for t in rewarded]
    reward_counts = Counter(rewards)

    print(f"run_dir={run_dir}")
    print(
        f"trials={len(trials)} completed={len(completed)} rewarded={len(rewarded)} exceptions={len(exceptions)}"
    )
    print(f"reward_counts={dict(reward_counts)}")
    print(f"tokenless={len(tokenless)} dummy_responses={len(dummy)} log_hits={len(log_hits)}")
    if token_values:
        print(f"token_sample={token_values[:10]}")

    if failures:
        print("SMOKE_GATE_FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("SMOKE_GATE_OK")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Harbor smoke/full run before scaling.")
    parser.add_argument("path", type=Path, help="Harbor run dir or jobs_dir")
    parser.add_argument("--min-completed", type=int, default=1)
    parser.add_argument(
        "--allow-zero-token-agents",
        action="store_true",
        help="Disable the positive-token gate for non-LLM baselines.",
    )
    args = parser.parse_args()
    raise SystemExit(
        validate(
            args.path,
            min_completed=args.min_completed,
            require_positive_tokens=not args.allow_zero_token_agents,
        )
    )


if __name__ == "__main__":
    main()
