"""
DEBUG ONLY — Run MemBench tasks in-process via eval_pipeline (no container, no Harbor).

This is a local development shortcut for fast iteration on agent changes.
It bypasses Harbor entirely: no Docker/Apptainer container, no task.toml environment,
no Harbor orchestrator. Results may differ from canonical Harbor runs.

THE CANONICAL WAY TO RUN MEMBENCH:
    harbor run --config util/harbor/membench_baseline.yaml

Prerequisites:
    Download MemBench data from Google Drive (one-time setup):
        https://drive.google.com/file/d/112Zraj4pTPH4Idph6i1uMOLA_LPFdGr0/view
    Unzip and point --data-dir at the result (must contain MemData/ or data2test/).

Usage:
    uv run python util/harbor/run_membench_debug.py \\
        --data-dir ~/.cache/membench/data \\
        [--tasks 5] [--model MODEL]

    uv run python util/harbor/run_membench_debug.py \\
        --data-dir ~/.cache/membench/data \\
        --tasks 5 --scenarios first --question-types simple

See gl-37: Add Harbor support for MemBench

# ---- Smoke test results (2026-04-20, gl-37) ----------------------------------
#
# Harbor smoke run not yet completed (MemBench data requires Google Drive download).
# Run manually once data is available:
#   python 3p/harbor-nemo/adapters/membench/run_adapter.py \
#       --data-dir ~/.cache/membench/data \
#       --task-dir /tmp/membench_smoke \
#       --scenarios first --question-types simple --limit 5
#   harbor run --config /tmp/membench_smoke.yaml   (see locomo_baseline.yaml for template)
#
# Key findings (from LoCoMo smoke, apply equally here):
#   1. Debug instruction must use return_result(letter) not /app/answer.txt — no container.
#   2. Apptainer requires apptainer_fakeroot: false on this system (IPC namespace
#      unavailable without CAP_SYS_ADMIN). Default true causes RuntimeError.
#   3. Model for Harbor runs: aws/anthropic/bedrock-claude-sonnet-4-5-v1 via
#      NVIDIA_INTERNAL_API_KEY. OPENAI_API_KEY is a proxy token, not direct OpenAI.
#   4. MemBench data is NOT on HuggingFace or GitHub. Download from Google Drive once
#      and cache locally. The --data-dir flag must point to the unzipped directory.
# ------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
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
ADAPTER_DIR = REPO_ROOT / "3p/harbor-nemo/adapters/membench"

sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages/nemo-oo-agents-benchmarks/src"))
sys.path.insert(0, str(REPO_ROOT / "util/eval_pipeline/src"))
sys.path.insert(0, str(ADAPTER_DIR))

from adapter import QUESTION_TYPES, SCENARIO_DIRS, MemBenchAdapter  # noqa: E402

from eval_pipeline import Evaluator, ScoreResult, ScoringContext  # noqa: E402
from unifiedllm import CompletionClient  # noqa: E402

# ---------------------------------------------------------------------------
# MemBench scorer — extract first A/B/C/D letter, exact match
# ---------------------------------------------------------------------------


def _extract_letter(text: str) -> str | None:
    """Extract first A/B/C/D answer letter from text."""
    text = text.strip()
    # Direct single letter
    if text.upper() in ("A", "B", "C", "D"):
        return text.upper()
    # Letter at start: "A.", "A)", "A:" or "A <rest>"
    m = re.match(r"^([ABCD])[.):,\s]", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # First occurrence anywhere
    m = re.search(r"\b([ABCD])\b", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


class MemBenchScorer:
    """Exact letter match scorer for MemBench MC questions."""

    def score(self, ctx: ScoringContext) -> ScoreResult:
        expected = str(ctx.expected or "").strip().upper()
        actual = str(ctx.actual or "")
        if isinstance(ctx.actual, dict):
            actual = str(ctx.actual.get("response") or ctx.actual.get("answer") or "")

        extracted = _extract_letter(actual)
        passed = extracted == expected
        return ScoreResult(
            score=1.0 if passed else 0.0,
            reasoning=f"Expected: {expected!r} | Extracted: {extracted!r} | Got: {actual[:80]!r}",
        )


# ---------------------------------------------------------------------------
# Build task instruction (mirrors Harbor instruction.md template)
# ---------------------------------------------------------------------------


def make_instruction(task: object) -> str:
    """Build instruction text matching the Harbor instruction.md template."""
    scenario_label = (
        "You are participating in the following conversation."
        if task.scenario == "first"
        else "You are observing the following conversation."
    )
    q_time = f"\n**Time:** {task.q_time}" if task.q_time else ""
    return f"""\
{scenario_label} Your task is to recall information from this history to answer \
a multiple-choice question.

## Conversation / Message History

{task.message_text}

---

## Question{q_time}

{task.question}

{task.choices_text}

Call `return_result(letter)` with ONLY the single letter of your answer (A, B, C, or D).
"""


# ---------------------------------------------------------------------------
# Per-run helper
# ---------------------------------------------------------------------------


def _extract_answer(output: object) -> str:
    if isinstance(output, dict):
        return str(output.get("response") or output.get("answer") or "")
    return str(output or "")


def _print_results(model: str, results: object) -> None:
    print(f"\n{'=' * 70}")
    print(f"BaselineAgent  ({model})")
    print(f"{'=' * 70}")
    for r in results.results:
        status = "PASS" if r.passed else "FAIL"
        actual = _extract_answer(r.output)
        extracted = _extract_letter(actual) or "?"
        print(f"  [{status}] extracted={extracted!r}  expected={r.expected!r}")
        if r.error:
            print(f"         error={r.error!r}")
    print(f"\n  Score: {results.passed}/{results.total} = {results.pass_rate:.0f}%")


async def main(
    data_dir: Path,
    n_tasks: int = 5,
    model: str = "openai/gpt-4o",
    scenarios: list[str] | None = None,
    question_types: list[str] | None = None,
    noise_length: int = 0,
) -> None:
    from nemo_oo_agents_benchmarks.agents.baseline import BaselineAgent

    print(f"\nMemBench debug smoke test — {n_tasks} tasks")
    print(f"Model: {model}")
    print(f"Data dir: {data_dir}")
    print(f"Scenarios: {scenarios or 'all'}")
    print(f"Question types: {question_types or 'all'}")
    print(f"Noise length: {noise_length}k\n")

    if not data_dir.exists():
        print(f"ERROR: --data-dir {data_dir} does not exist.")
        print(
            "Download MemBench data from: "
            "https://drive.google.com/file/d/112Zraj4pTPH4Idph6i1uMOLA_LPFdGr0/view"
        )
        sys.exit(1)

    adapter = MemBenchAdapter(
        task_dir=Path("/tmp/membench_debug_tasks"),  # not used
        data_dir=data_dir,
        scenarios=scenarios,
        question_types=question_types,
        noise_length=noise_length,
        limit=n_tasks,
    )

    if not adapter.tasks:
        print("ERROR: No tasks loaded. Check --data-dir contains MemData/ or data2test/ dirs.")
        sys.exit(1)

    print(f"Loaded {len(adapter.tasks)} tasks.\n")

    eval_data = [
        {
            "kwargs": {
                "task_input": {
                    "user_message": make_instruction(t),
                }
            },
            "expected": t.ground_truth,
            "metadata": {
                "task_id": t.id,
                "scenario": t.scenario,
                "question_type": t.question_type,
            },
        }
        for t in adapter.tasks
    ]

    llm_client = CompletionClient(model=model)
    evaluator = Evaluator(
        models={model: llm_client},
        output_dir=str(REPO_ROOT / ".development/docs/evaluation"),
        name="membench_baseline_gl37",
    )
    evaluator._model_metadata = {
        model: {"id": model, "model_name": getattr(llm_client, "model", model)},
    }
    evaluator.add_test(
        name=f"membench_baseline_{n_tasks}tasks",
        agent_class=BaselineAgent,
        method="_run_evaluation",
        data=eval_data,
        scorers=[MemBenchScorer()],
    )

    results = await evaluator.run(models=[model])
    _print_results(model, results)

    print(f"\n{'=' * 70}")
    print("To run canonically via Harbor:")
    print(
        f"  python 3p/harbor-nemo/adapters/membench/run_adapter.py "
        f"--data-dir {data_dir} --task-dir util/harbor/tasks/membench --limit 5"
    )
    print("  harbor run --config util/harbor/membench_baseline.yaml")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MemBench smoke test via eval_pipeline")
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Path to unzipped MemBench data (must contain MemData/ or data2test/)",
    )
    parser.add_argument("--tasks", type=int, default=5, help="Number of tasks (default: 5)")
    parser.add_argument(
        "--model",
        default="openai/gpt-4o",
        help="Model to use (default: openai/gpt-4o)",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=list(SCENARIO_DIRS),
        default=None,
        help="Scenarios to include (default: both)",
    )
    parser.add_argument(
        "--question-types",
        nargs="+",
        choices=QUESTION_TYPES,
        default=None,
        help="Question types to include (default: all)",
    )
    parser.add_argument(
        "--noise-length",
        type=int,
        default=0,
        choices=[0, 100],
        help="Context length variant: 0=short, 100=long (default: 0)",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            data_dir=args.data_dir,
            n_tasks=args.tasks,
            model=args.model,
            scenarios=args.scenarios,
            question_types=args.question_types,
            noise_length=args.noise_length,
        )
    )
