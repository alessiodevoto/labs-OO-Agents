"""
DEBUG ONLY — Run Terminal Bench tasks in-process via eval_pipeline (no container, no Harbor).

This is a local development shortcut for fast iteration on agent changes.
It bypasses Harbor entirely: no Docker/Apptainer container, no task.toml environment,
no Harbor orchestrator. Results may differ from canonical Harbor runs.

Terminal Bench tasks require containers for full verification (the test scripts
run inside the sandbox and write reward.txt). Without containers, this script
runs the baseline agent against each task's instruction and reports whether the
agent completed without errors — NOT whether it solved the task correctly.

THE CANONICAL WAY TO RUN TERMINAL BENCH:
    harbor run --config util/harbor/terminal_bench_baseline.yaml

Usage:
    uv run python util/harbor/run_terminal_bench_debug.py [--tasks 5] [--model MODEL]

See gl-24: Smoke test: run 5 Terminal Bench tasks via Harbor
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

# ---- load .env from repo root (provides API keys) --------------------------
_env_file = Path(__file__).resolve().parents[2] / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ---- resolve workspace root so eval_pipeline imports work -----------------
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages/nemo-oo-agents-benchmarks/src"))
sys.path.insert(0, str(REPO_ROOT / "util/eval_pipeline/src"))

TERMINAL_BENCH_REPO = "https://github.com/laude-institute/terminal-bench.git"
CACHE_DIR = Path(os.path.expanduser("~/.cache/terminal-bench"))
TASKS_DIR = CACHE_DIR / "terminal-bench" / "tasks"
HARBOR_TASKS_DIR = REPO_ROOT / "util/harbor/tasks/terminal_bench"


def _ensure_tasks(n_tasks: int, task_names: list[str] | None = None) -> list[Path]:
    """Clone (or update) terminal-bench repo and return task dirs.

    If task_names is given, return exactly those tasks in that order.
    Otherwise return the first n_tasks alphabetically.
    """
    repo_dir = CACHE_DIR / "terminal-bench"

    if repo_dir.exists() and (repo_dir / ".git").exists():
        print(f"Using cached terminal-bench at {repo_dir}")
    else:
        print(f"Cloning terminal-bench (sparse) to {repo_dir} …")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--sparse",
                "--branch",
                "main",
                TERMINAL_BENCH_REPO,
                str(repo_dir),
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "sparse-checkout", "set", "original-tasks"],
            check=True,
        )

    tasks_dir = repo_dir / "original-tasks"
    if not tasks_dir.exists() or not any(tasks_dir.iterdir()):
        raise FileNotFoundError(
            f"Terminal Bench tasks not found at {tasks_dir}. "
            "Check the sparse checkout or clone the full repo."
        )

    if task_names:
        return [tasks_dir / name for name in task_names if (tasks_dir / name).is_dir()]

    task_dirs = [
        d for d in sorted(tasks_dir.iterdir()) if d.is_dir() and (d / "task.yaml").exists()
    ]
    return task_dirs[:n_tasks]


def _load_instruction(task_dir: Path) -> str | None:
    """Extract the instruction string from a task.yaml file."""
    import yaml

    yaml_path = task_dir / "task.yaml"
    if not yaml_path.exists():
        return None
    data = yaml.safe_load(yaml_path.read_text())
    return data.get("instruction", "")


# ---------------------------------------------------------------------------
# Scorer — runs the same pytest test_outputs.py that Harbor uses
# ---------------------------------------------------------------------------


class TerminalBenchScorer:
    """Scores Terminal Bench tasks by running the same pytest suite Harbor uses.

    ctx.expected must be the path to the Harbor task directory
    (util/harbor/tasks/terminal_bench/<task-name>/).  If the test file is
    missing or pytest itself can't run, falls back to agent-completion scoring.
    """

    def score(self, ctx) -> object:
        from eval_pipeline import ScoreResult

        task_dir = Path(ctx.expected) if ctx.expected else None
        test_file = task_dir / "tests" / "test_outputs.py" if task_dir else None

        if test_file and test_file.exists():
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short", "-q"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            passed = result.returncode == 0
            output = (result.stdout + result.stderr)[-1000:]
            return ScoreResult(
                score=1.0 if passed else 0.0,
                reasoning=output,
            )

        # Fallback: score by agent completion if no test file
        result = ctx.actual
        if isinstance(result, dict):
            success = result.get("success", False)
            error = result.get("error", "")
            passed = success and not error
        else:
            passed = bool(result)
        return ScoreResult(
            score=1.0 if passed else 0.0,
            reasoning="[no test file — completion-based] " + ("passed" if passed else "failed"),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(
    n_tasks: int = 5,
    model_name: str = "anthropic/claude-sonnet-4-6",
    task_names: list[str] | None = None,
) -> None:
    from nemo_oo_agents_benchmarks.agents.baseline import BaselineAgent

    from eval_pipeline import Evaluator
    from unifiedllm import CompletionClient
    from unifiedllm.registry import get_llm_client

    _REGISTRY_PREFIXES = ("aws/", "nvidia/", "azure/", "gcp/")

    def _make_client(model: str) -> CompletionClient:
        if any(model.startswith(p) for p in _REGISTRY_PREFIXES):
            return get_llm_client(model)
        return CompletionClient(model=model)

    task_dirs = _ensure_tasks(n_tasks, task_names=task_names)
    if not task_dirs:
        print("ERROR: No Terminal Bench tasks found.")
        sys.exit(1)

    print(f"\nLoaded {len(task_dirs)} Terminal Bench task(s):")
    for d in task_dirs:
        print(f"  {d.name}")

    eval_data = []
    task_names = []
    for task_dir in task_dirs:
        instruction = _load_instruction(task_dir)
        if not instruction:
            print(f"  WARNING: no instruction in {task_dir.name}, skipping")
            continue
        harbor_task_dir = HARBOR_TASKS_DIR / task_dir.name
        eval_data.append(
            {
                "kwargs": {
                    "task_input": {
                        "user_message": instruction,
                    }
                },
                "expected": str(harbor_task_dir) if harbor_task_dir.exists() else None,
            }
        )
        task_names.append(task_dir.name)

    if not eval_data:
        print("ERROR: No tasks with instructions found.")
        sys.exit(1)

    llm_client = _make_client(model_name)

    evaluator = Evaluator(
        models={"baseline": llm_client},
        output_dir=str(REPO_ROOT / ".development/docs/evaluation"),
        name="terminal_bench_baseline_gl24",
    )
    evaluator._model_metadata = {
        "baseline": {"id": "baseline", "model_name": model_name},
    }

    evaluator.add_test(
        name=f"terminal_bench_baseline_{len(eval_data)}tasks",
        agent_class=BaselineAgent,
        method="_run_evaluation",
        data=eval_data,
        scorers=[TerminalBenchScorer()],
    )

    print(f"\nRunning {len(eval_data)} Terminal Bench tasks with {model_name} (baseline agent)…")
    print(
        "Scoring: pytest test_outputs.py (same suite Harbor uses); fallback to completion if no test file.\n"
    )

    results = await evaluator.run(models=["baseline"])

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {results.summary()}")
    print(f"Output:  {results.output_file}")
    print(f"{'=' * 60}\n")

    for i, r in enumerate(results.results):
        task_name = task_names[i] if i < len(task_names) else "?"
        score = r.score if hasattr(r, "score") else (1.0 if r.passed else 0.0)
        status = f"{score:.1f}"
        reasoning = (r.reasoning or "")[-200:] if hasattr(r, "reasoning") else ""
        print(f"  [{status}] {task_name}")
        if reasoning:
            for line in reasoning.strip().splitlines()[-3:]:
                print(f"         {line}")

    print(f"\nTotal: {results.passed}/{results.total} = {results.pass_rate:.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Terminal Bench smoke test via eval_pipeline (no containers)"
    )
    parser.add_argument("--tasks", type=int, default=5, help="Number of tasks to run (default: 5)")
    parser.add_argument(
        "--task-names",
        nargs="+",
        metavar="TASK",
        help="Run specific named tasks (overrides --tasks)",
    )
    parser.add_argument(
        "--model",
        default="anthropic/claude-sonnet-4-6",
        help="Model name (default: anthropic/claude-sonnet-4-6)",
    )
    args = parser.parse_args()
    asyncio.run(main(n_tasks=args.tasks, model_name=args.model, task_names=args.task_names))
