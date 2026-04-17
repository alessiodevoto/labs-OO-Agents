"""
Run DABStep tasks via eval_pipeline.

Harbor is the canonical way to run benchmark evaluations.  This script
runs the same agent code against the same benchmark tasks without a container,
making it faster to iterate on agent changes locally.

Agents:
  baseline  — CodeAct REPL, no benchmark-specific logic (default: openai/gpt-4o)
  dabstep   — 3-phase pipeline (RulesLawyer → compute_answer → SolutionVerifier)
              (default: nvidia_nim/deepseek-ai/deepseek-v3.2)
  both      — run baseline then dabstep and print a comparison table

Usage:
    uv run python util/harbor/run_dabstep.py [--tasks 10] [--agent both]

See gl-27: Smoke test: run 5 DABStep tasks via Harbor
"""

from __future__ import annotations

import argparse
import asyncio
import math
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
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages/nemo-oo-agents-benchmarks/src"))
sys.path.insert(0, str(REPO_ROOT / "util/eval_pipeline/src"))

from difflib import SequenceMatcher  # noqa: E402

from datasets import load_dataset  # type: ignore[import]  # noqa: E402

from eval_pipeline import Evaluator, ScoreResult, ScoringContext  # noqa: E402
from unifiedllm import CompletionClient  # noqa: E402

# ---------------------------------------------------------------------------
# DABStep fuzzy scorer (adapted from harbor's scorer.py)
# ---------------------------------------------------------------------------


def _is_numeric_with_commas(value: str) -> bool:
    v = value.strip()
    pattern = r"""
      ^\$?
      (?:
         \d{1,3}(?:,\d{3})+(?:\.\d+)?
       | \d+[.,]\d+
      )
      $
    """
    return bool(re.match(pattern, v, re.VERBOSE))


def _extract_numeric(value: str) -> float | None:
    value = value.replace(",", "").replace("$", "")
    match = re.search(r"(\d*\.\d+|\d+\.?\d*)%?", value)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _compare_numeric(num1: float, num2: float) -> bool:
    if num1 == num2:
        return True
    if num1 < 1 and num2 < 1:
        return math.isclose(num1, num2, rel_tol=1e-4, abs_tol=1e-4)
    dec_places1 = len(str(num1).split(".")[-1]) if "." in str(num1) else 0
    dec_places2 = len(str(num2).split(".")[-1]) if "." in str(num2) else 0
    round_to = min(dec_places1, dec_places2)
    rounded1 = round(num1, round_to)
    rounded2 = round(num2, round_to)
    if rounded1 == rounded2:
        return True
    return math.isclose(num1, num2, rel_tol=1e-4, abs_tol=1e-4)


def _compare_strings(str1: str, str2: str) -> bool:
    clean1 = re.sub(r"[^\w]", "", str1)
    clean2 = re.sub(r"[^\w]", "", str2)
    if clean1.lower() == clean2.lower():
        return True
    ratio = SequenceMatcher(None, str1.lower(), str2.lower()).ratio()
    return ratio > 0.9


def _parse_list(s: str) -> list[str] | None:
    items = re.findall(r"\b[\w.]+\b", s)
    return items if len(items) > 1 else None


def _compare_lists(list1: str, list2: str) -> bool:
    items1 = sorted(re.findall(r"\b[\w.]+\b", list1), key=str.lower)
    items2 = sorted(re.findall(r"\b[\w.]+\b", list2), key=str.lower)
    if len(items1) != len(items2):
        return False
    return all(i1.lower() == i2.lower() for i1, i2 in zip(items1, items2, strict=False))


def dabstep_score(prediction: str, answer: str) -> bool:
    """DABStep fuzzy comparison (from harbor scorer.py)."""
    pred = str(prediction).strip()
    ans = str(answer).strip()
    if pred.lower() == ans.lower():
        return True
    # Numeric comparison
    if _is_numeric_with_commas(pred) or _is_numeric_with_commas(ans):
        num1 = _extract_numeric(pred)
        num2 = _extract_numeric(ans)
        if num1 is not None and num2 is not None:
            return _compare_numeric(num1, num2)
    pred_num = _extract_numeric(pred)
    ans_num = _extract_numeric(ans)
    if pred_num is not None and ans_num is not None:
        return _compare_numeric(pred_num, ans_num)
    # List comparison
    if _parse_list(pred) and _parse_list(ans):
        return _compare_lists(pred, ans)
    # String fuzzy
    return _compare_strings(pred, ans)


class DABStepScorer:
    """Fuzzy DABStep scorer for eval_pipeline."""

    def score(self, ctx: ScoringContext) -> ScoreResult:
        expected = str(ctx.expected or "")
        actual = str(ctx.actual or "")

        # Extract just the response from the result dict if needed
        if isinstance(ctx.actual, dict):
            actual = str(ctx.actual.get("response") or ctx.actual.get("answer") or "")

        passed = dabstep_score(actual, expected)
        return ScoreResult(
            score=1.0 if passed else 0.0,
            reasoning=f"Expected: {expected!r} | Got: {actual!r}",
        )


# ---------------------------------------------------------------------------
# Build task instruction (Harbor instruction.md format)
# ---------------------------------------------------------------------------


def make_instruction(question: str, guidelines: str, data_dir: str) -> str:
    """Create task instruction mirroring the Harbor instruction.md template."""
    return f"""\
You are an expert data analyst and you will answer factoid questions by referencing files in the data directory: `{data_dir}`
Don't forget to reference any documentation in the data dir before answering a question.

Here is the question you need to answer: {question}

Here are the guidelines you MUST follow when answering the question above: {guidelines}

Before answering the question, reference any documentation in the data dir and leverage its information in your reasoning / planning.

When you have computed the final answer, use `return_result(answer)` where answer is ONLY the final answer string (e.g. if the answer is 42, call `return_result("42")`).
"""


# ---------------------------------------------------------------------------
# Per-run helper
# ---------------------------------------------------------------------------


def _extract_answer(output: object) -> str:
    if isinstance(output, dict):
        return str(output.get("response") or output.get("answer") or "")
    return str(output or "")


def _print_run(label: str, model: str, results: object) -> None:
    print(f"\n{'=' * 60}")
    print(f"{label}  ({model})")
    print(f"{'=' * 60}")
    for r in results.results:
        actual = _extract_answer(r.output)
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] got={actual!r}  expected={r.expected!r}")
    print(f"\n  Score: {results.passed}/{results.total} = {results.pass_rate:.0f}%")


async def _run_agent(
    agent_class: type,
    model_id: str,
    llm_client: object,
    eval_data: list[dict],
    n_tasks: int,
    label: str,
) -> object:
    evaluator = Evaluator(
        models={model_id: llm_client},
        output_dir=str(REPO_ROOT / ".development/docs/evaluation"),
        name=f"dabstep_{label}_gl27",
    )
    evaluator._model_metadata = {
        model_id: {"id": model_id, "model_name": getattr(llm_client, "model", model_id)},
    }
    evaluator.add_test(
        name=f"dabstep_{label}_{n_tasks}tasks",
        agent_class=agent_class,
        method="_run_evaluation",
        data=eval_data,
        scorers=[DABStepScorer()],
    )
    return await evaluator.run(models=[model_id])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(
    n_tasks: int = 10,
    baseline_model: str = "openai/gpt-4o",
    dabstep_model: str = "openai/gpt-4o",
    agent_type: str = "both",
) -> None:
    from nemo_oo_agents_benchmarks.agents.baseline import BaselineAgent
    from nemo_oo_agents_benchmarks.agents.dabstep import DABStepAgent

    # Load DABStep dev tasks
    ds = load_dataset("adyen/DABstep", name="tasks", split="dev")
    tasks = [dict(row) for row in ds][:n_tasks]

    data_dir = str(Path(os.path.expanduser("~/.cache/dabstep/data/context")))
    if not Path(data_dir).exists():
        print(f"ERROR: DABStep data not found at {data_dir}")
        sys.exit(1)

    # Build eval data — data_dir is passed explicitly so DABStepAgent can use it
    # without needing to parse it from the instruction text.
    eval_data = [
        {
            "kwargs": {
                "task_input": {
                    "user_message": make_instruction(t["question"], t["guidelines"], data_dir),
                    "data_dir": data_dir,
                }
            },
            "expected": t["answer"],
            "metadata": {
                "task_id": t["task_id"],
                "level": t["level"],
                "question": t["question"],
            },
        }
        for t in tasks
    ]

    print(f"\nDABStep smoke test — {n_tasks} tasks from dev split")
    print(f"Data dir: {data_dir}\n")

    results_baseline = None
    results_dabstep = None

    if agent_type in ("baseline", "both"):
        print(f"[1/2] BaselineAgent  model={baseline_model}")
        baseline_client = CompletionClient(model=baseline_model)
        results_baseline = await _run_agent(
            BaselineAgent, "baseline", baseline_client, eval_data, n_tasks, "baseline"
        )
        _print_run("BaselineAgent", baseline_model, results_baseline)

    if agent_type in ("dabstep", "both"):
        print(f"\n[2/2] DABStepAgent   model={dabstep_model}")
        dabstep_client = CompletionClient(model=dabstep_model)
        results_dabstep = await _run_agent(
            DABStepAgent, "dabstep", dabstep_client, eval_data, n_tasks, "dabstep"
        )
        _print_run("DABStepAgent", dabstep_model, results_dabstep)

    # Comparison table
    print(f"\n{'=' * 60}")
    print("COMPARISON")
    print(f"{'=' * 60}")
    print(f"{'Agent':<20} {'Model':<40} {'Score'}")
    print(f"{'-' * 20} {'-' * 40} {'-' * 10}")
    if results_baseline:
        pct = f"{results_baseline.passed}/{results_baseline.total} = {results_baseline.pass_rate:.0f}%"
        print(f"{'BaselineAgent':<20} {baseline_model:<40} {pct}")
    if results_dabstep:
        pct = f"{results_dabstep.passed}/{results_dabstep.total} = {results_dabstep.pass_rate:.0f}%"
        print(f"{'DABStepAgent':<20} {dabstep_model:<40} {pct}")
    print(f"{'agent006 DABStepAgent':<20} {'Claude 3.5/4 (historical)':<40} 70–80%")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run DABStep smoke test via eval_pipeline")
    parser.add_argument("--tasks", type=int, default=10, help="Number of tasks (default: 10)")
    parser.add_argument(
        "--model",
        default="openai/gpt-4o",
        help="Model for BaselineAgent (default: openai/gpt-4o)",
    )
    parser.add_argument(
        "--dabstep-model",
        default="openai/gpt-4o",
        help="Model for DABStepAgent (default: openai/gpt-4o)",
    )
    parser.add_argument(
        "--agent",
        default="both",
        choices=["baseline", "dabstep", "both"],
        help="Which agent(s) to run (default: both)",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            n_tasks=args.tasks,
            baseline_model=args.model,
            dabstep_model=args.dabstep_model,
            agent_type=args.agent,
        )
    )
