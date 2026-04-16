"""
Run DABStep tasks via eval_pipeline using the baseline agent.

Harbor is the canonical way to run benchmark evaluations.  This script
runs the same agent code against the same benchmark tasks without a container,
making it faster to iterate on agent changes locally.

Usage:
    uv run python util/harbor/run_dabstep.py [--tasks 5] [--model MODEL]

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
# Build task instruction for the baseline agent
# ---------------------------------------------------------------------------


def make_instruction(question: str, guidelines: str, data_dir: str) -> str:
    """Create baseline-agent instruction mirroring the harbor instruction.md template."""
    return f"""\
You are an expert data analyst and you will answer factoid questions by referencing files in the data directory: `{data_dir}`
Don't forget to reference any documentation in the data dir before answering a question.

Here is the question you need to answer: {question}

Here are the guidelines you MUST follow when answering the question above: {guidelines}

Before answering the question, reference any documentation in the data dir and leverage its information in your reasoning / planning.

When you have computed the final answer, use `return_result(answer)` where answer is ONLY the final answer string (e.g. if the answer is 42, call `return_result("42")`).
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(n_tasks: int = 5, model_name: str = "openai/gpt-4o") -> None:
    from nemo_oo_agents_benchmarks.agents.baseline import BaselineAgent

    # Load DABStep dev tasks
    ds = load_dataset("adyen/DABstep", name="tasks", split="dev")
    tasks = [dict(row) for row in ds][:n_tasks]

    data_dir = str(Path(os.path.expanduser("~/.cache/dabstep/data/context")))
    if not Path(data_dir).exists():
        print(f"ERROR: DABStep data not found at {data_dir}")
        print('Run: python -c "from huggingface_hub import hf_hub_download; ..."')
        sys.exit(1)

    # Build eval data
    eval_data = [
        {
            "kwargs": {
                "task_input": {
                    "user_message": make_instruction(t["question"], t["guidelines"], data_dir)
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

    llm_client = CompletionClient(model=model_name)

    evaluator = Evaluator(
        models={"baseline": llm_client},
        output_dir=str(REPO_ROOT / ".development/docs/evaluation"),
        name="dabstep_baseline_gl27",
    )

    # Provide model metadata for proper traceability in output
    evaluator._model_metadata = {
        "baseline": {"id": "baseline", "model_name": model_name},
    }

    evaluator.add_test(
        name=f"dabstep_baseline_{n_tasks}tasks",
        agent_class=BaselineAgent,
        method="_run_evaluation",
        data=eval_data,
        scorers=[DABStepScorer()],
    )

    print(f"\nRunning {n_tasks} DABStep tasks with {model_name} (baseline agent)...")
    print(f"Data dir: {data_dir}\n")

    results = await evaluator.run(models=["baseline"])

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {results.summary()}")
    print(f"Output: {results.output_file}")
    print(f"{'=' * 60}\n")

    # Per-task breakdown
    for r in results.results:
        actual = r.output
        if isinstance(actual, dict):
            actual = actual.get("response", "")
        expected = r.expected
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] (got={actual!r} expected={expected!r})")

    # Summary
    print(f"\nTotal: {results.passed}/{results.total} = {results.pass_rate:.1f}%")
    print("\nPrevious agent006 DABStepAgent (Claude): 70-80% on full 10-task dev set")
    print("Note: BaselineAgent expected lower; this run uses", model_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run DABStep smoke test via eval_pipeline")
    parser.add_argument("--tasks", type=int, default=5, help="Number of tasks to run (default: 5)")
    parser.add_argument(
        "--model",
        default="openai/gpt-4o",
        help="Model name (default: openai/gpt-4o)",
    )
    args = parser.parse_args()
    asyncio.run(main(n_tasks=args.tasks, model_name=args.model))
