"""
DEBUG ONLY — Run LoCoMo tasks in-process via eval_pipeline (no container, no Harbor).

This is a local development shortcut for fast iteration on agent changes.
It bypasses Harbor entirely: no Docker/Apptainer container, no task.toml environment,
no Harbor orchestrator. Results may differ from canonical Harbor runs.

THE CANONICAL WAY TO RUN LOCOMO:
    harbor run --config util/harbor/locomo_baseline.yaml

Data is downloaded automatically from GitHub on first run (~24 MB) and cached at
~/.cache/locomo/locomo10.json. No manual setup needed.

Usage:
    uv run python util/harbor/run_locomo_debug.py [--tasks 5] [--model MODEL]
    uv run python util/harbor/run_locomo_debug.py --tasks 5 --question-types single-hop

See gl-37: Add Harbor support for MemBench (also covers LoCoMo)

# ---- Smoke test results (2026-04-20, gl-37) ----------------------------------
#
# eval_pipeline (debug, openai/gpt-4o, 5 single-hop tasks):
#   [PASS] F1=1.000  expected='Adoption agencies'
#   [PASS] F1=0.571  expected='Transgender woman'  (got full sentence, partial match)
#   [FAIL] F1=0.000  expected='Single'             (agent answered "I don't know")
#   [PASS] F1=1.000  expected='Sweden'
#   [PASS] F1=0.615  expected='counseling or mental health for Transgender people'
#   Score: 4/5 = 80%
#
# Harbor (Apptainer, aws/anthropic/bedrock-claude-sonnet-4-5-v1, 3/5 completed):
#   locomo-conv00-single-hop-0007: reward=1.0000
#   locomo-conv00-single-hop-0011: reward=1.0000
#   locomo-conv00-single-hop-0013: reward=0.6154
#   Mean F1 = 0.87 (3/3 pass at F1 >= 0.5 threshold)
#   2 tasks DNF (still bootstrapping container when job was killed)
#
# Key findings:
#   1. locomo10.json category field is int 1–5, not string. Adapter fix required
#      (_CATEGORY_MAP: 1=single-hop, 2=temporal, 3=open-domain, 4=multi-hop,
#      5=adversarial) and conversation stored as flat session_N keys, not a list.
#   2. Debug instruction must use return_result() not /app/answer.txt — no container.
#   3. Apptainer requires apptainer_fakeroot: false on this system (IPC namespace
#      unavailable without CAP_SYS_ADMIN). Default true causes RuntimeError.
#   4. Model for Harbor runs: aws/anthropic/bedrock-claude-sonnet-4-5-v1 via
#      NVIDIA_INTERNAL_API_KEY. OPENAI_API_KEY is a proxy token, not direct OpenAI.
# ------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import asyncio
import os
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
ADAPTER_DIR = REPO_ROOT / "3p/harbor-nemo/adapters/locomo"

sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages/nemo-oo-agents-benchmarks/src"))
sys.path.insert(0, str(REPO_ROOT / "util/eval_pipeline/src"))
sys.path.insert(0, str(ADAPTER_DIR))

from adapter import QUESTION_TYPES, LoCoMoAdapter  # noqa: E402

from eval_pipeline import Evaluator, ScoreResult, ScoringContext  # noqa: E402
from nemo_oo_agents.unifiedllm import CompletionClient  # noqa: E402

# ---------------------------------------------------------------------------
# LoCoMo token-level F1 scorer (matches harbor scorer.py logic)
# ---------------------------------------------------------------------------


def _compute_f1(prediction: str, reference: str) -> float:
    pred_tokens = set(prediction.lower().split())
    ref_tokens = set(reference.lower().split())
    if not pred_tokens or not ref_tokens:
        return 0.0
    common = pred_tokens & ref_tokens
    precision = len(common) / len(pred_tokens)
    recall = len(common) / len(ref_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


class LoCoMoScorer:
    """Token-level F1 scorer for LoCoMo (pass = F1 >= 0.5)."""

    def score(self, ctx: ScoringContext) -> ScoreResult:
        expected = str(ctx.expected or "")
        actual = str(ctx.actual or "")
        if isinstance(ctx.actual, dict):
            actual = str(ctx.actual.get("response") or ctx.actual.get("answer") or "")

        f1 = _compute_f1(actual, expected)
        return ScoreResult(
            score=f1,
            reasoning=f"F1={f1:.3f} | Expected: {expected!r} | Got: {actual!r}",
        )


# ---------------------------------------------------------------------------
# Build task instruction (mirrors Harbor instruction.md template)
# ---------------------------------------------------------------------------


def make_instruction(conversation_text: str, question_type: str, question: str) -> str:
    return f"""\
The following is a long-term conversation between two people, recorded across \
multiple sessions over time. Read the full conversation carefully, then answer \
the question based on what was discussed.

## Conversation History

{conversation_text}

---

## Question

**Type:** {question_type}

{question}

Be concise and answer only what is asked. If the question cannot be answered \
from the conversation, answer "I don't know."

When you have your answer, call `return_result(answer)` with ONLY the answer string.
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
        score_detail = next(iter(r.scores.values()), None)
        f1 = score_detail.score if score_detail else 0.0
        print(f"  [{status}] F1={f1:.3f}")
        print(f"         expected={r.expected!r}")
        print(f"         got     ={actual[:120]!r}")
        if r.error:
            print(f"         error   ={r.error!r}")
        if isinstance(r.output, dict) and not r.output.get("success", True):
            print(f"         agent_error={r.output.get('error', '?')!r}")
    print(f"\n  Score: {results.passed}/{results.total} = {results.pass_rate:.0f}%")


async def main(
    n_tasks: int = 5,
    model: str = "openai/gpt-4o",
    question_types: list[str] | None = None,
) -> None:
    from nemo_oo_agents_benchmarks.agents.baseline import BaselineAgent

    print(f"\nLoCoMo debug smoke test — {n_tasks} tasks")
    print(f"Model: {model}")
    print(f"Question types: {question_types or 'all'}")
    print("Downloading/loading LoCoMo data...")

    adapter = LoCoMoAdapter(
        task_dir=Path("/tmp/locomo_debug_tasks"),  # not used, but required
        question_types=question_types,
        limit=n_tasks,
    )

    if not adapter.tasks:
        print("ERROR: No tasks loaded. Check network access.")
        sys.exit(1)

    print(f"Loaded {len(adapter.tasks)} tasks.\n")

    eval_data = [
        {
            "kwargs": {
                "task_input": {
                    "user_message": make_instruction(
                        t.conversation_text, t.question_type, t.question
                    ),
                }
            },
            "expected": t.expected_answer,
            "metadata": {
                "task_id": t.id,
                "question_type": t.question_type,
                "conv_idx": t.conv_idx,
            },
        }
        for t in adapter.tasks
    ]

    llm_client = CompletionClient(model=model)
    evaluator = Evaluator(
        models={model: llm_client},
        output_dir=str(REPO_ROOT / ".development/docs/evaluation"),
        name="locomo_baseline_gl37",
    )
    evaluator._model_metadata = {
        model: {"id": model, "model_name": getattr(llm_client, "model", model)},
    }
    evaluator.add_test(
        name=f"locomo_baseline_{n_tasks}tasks",
        agent_class=BaselineAgent,
        method="_run_evaluation",
        data=eval_data,
        scorers=[LoCoMoScorer()],
    )

    results = await evaluator.run(models=[model])
    _print_results(model, results)

    print(f"\n{'=' * 70}")
    print("To run canonically via Harbor:")
    print(
        "  python 3p/harbor-nemo/adapters/locomo/run_adapter.py "
        "--task-dir util/harbor/tasks/locomo --limit 5"
    )
    print("  harbor run --config util/harbor/locomo_baseline.yaml")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run LoCoMo smoke test via eval_pipeline")
    parser.add_argument("--tasks", type=int, default=5, help="Number of tasks (default: 5)")
    parser.add_argument(
        "--model",
        default="openai/gpt-4o",
        help="Model to use (default: openai/gpt-4o)",
    )
    parser.add_argument(
        "--question-types",
        nargs="+",
        choices=QUESTION_TYPES,
        default=None,
        help="Question types to include (default: all)",
    )
    args = parser.parse_args()
    asyncio.run(main(n_tasks=args.tasks, model=args.model, question_types=args.question_types))
