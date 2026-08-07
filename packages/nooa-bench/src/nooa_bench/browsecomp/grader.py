# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Answer graders for BrowseComp-Plus.

Upstream uses an LLM-as-judge with ``GRADER_TEMPLATE`` (vllm-hosted). We keep
that shape for compatibility, but ship a cheap heuristic grader too so smoke
tests can run without an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Protocol

# Kept verbatim from upstream (search_agent/prompts.py) so LLM-judge scores are
# directly comparable with the leaderboard.
GRADER_TEMPLATE = """
Judge whether the following [response] to [question] is correct or not based on the precise and unambiguous [correct_answer] below.

[question]: {question}

[response]: {response}

Your judgement must be in the format and criteria specified below:

extracted_final_answer: The final exact answer extracted from the [response]. Put the extracted answer as 'None' if there is no exact, final answer to extract from the response.

[correct_answer]: {correct_answer}

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_answer], focusing only on if there are meaningful differences between [correct_answer] and the extracted_final_answer. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_answer], focus only on whether the answers match.

correct: Answer 'yes' if extracted_final_answer matches the [correct_answer] given above, or is within a small margin of error for numerical problems. Answer 'no' otherwise, i.e. if there if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.


confidence: The extracted confidence score between 0|\\%| and 100|\\%| from [response]. Put 100 if there is no confidence score available.
""".strip()


@dataclass
class GradeResult:
    correct: bool
    extracted_answer: str
    reasoning: str = ""


class Grader(Protocol):
    def grade(self, question: str, response: str, correct_answer: str) -> GradeResult: ...


# ---------------------------------------------------------------------------
# Heuristic grader -- normalized substring match. Cheap, coarse, no LLM.
# ---------------------------------------------------------------------------


_ANSWER_RE = re.compile(r"exact\s*answer\s*:\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL)


def _extract_exact_answer(response: str) -> str:
    """Pull the ``Exact Answer:`` line out of a response, else return the whole thing."""
    m = _ANSWER_RE.search(response)
    return (m.group(1) if m else response).strip()


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


class HeuristicGrader:
    """Substring match on normalized text.

    Correct if the normalized ``correct_answer`` appears in the normalized
    extracted response (or vice versa for very short answers). Useful as a
    smoke check -- it will *over*-count vs. the LLM judge.
    """

    def grade(self, question: str, response: str, correct_answer: str) -> GradeResult:
        extracted = _extract_exact_answer(response)
        norm_r = _normalize(extracted)
        norm_a = _normalize(correct_answer)
        if not norm_a:
            return GradeResult(correct=False, extracted_answer=extracted, reasoning="empty answer")
        correct = norm_a in norm_r or (len(norm_a) <= 20 and norm_r in norm_a)
        return GradeResult(
            correct=correct,
            extracted_answer=extracted,
            reasoning="substring match" if correct else "no substring match",
        )


# ---------------------------------------------------------------------------
# LLM-judge grader -- upstream-compatible. Takes any ``judge(prompt) -> str``
# callable (e.g. wrapping OpenAI, Claude, or a local vllm server).
# ---------------------------------------------------------------------------


_JUDGE_ANSWER_RE = re.compile(
    r"extracted_final_answer\s*:\s*(.*?)(?=\n\s*(?:reasoning|correct|confidence)\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_JUDGE_CORRECT_RE = re.compile(r"correct\s*:\s*(yes|no)", re.IGNORECASE)
_JUDGE_REASON_RE = re.compile(
    r"reasoning\s*:\s*(.*?)(?=\n\s*(?:correct|confidence)\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)


class LLMJudgeGrader:
    """Delegates the correctness call to an LLM, matching upstream's rubric.

    Parameters
    ----------
    judge:
        Callable ``str -> str`` that runs the judge prompt through your LLM and
        returns its raw completion. Kept as a callable (not a UnifiedLLM
        subclass) so users can plug in any client -- OpenAI, Anthropic, vllm.
    """

    def __init__(self, judge: Callable[[str], str]) -> None:
        self._judge = judge

    def _build_prompt(self, question: str, response: str, correct_answer: str) -> str:
        return GRADER_TEMPLATE.format(
            question=question, response=response, correct_answer=correct_answer
        )

    def grade(self, question: str, response: str, correct_answer: str) -> GradeResult:
        prompt = self._build_prompt(question, response, correct_answer)
        raw = self._judge(prompt)

        m_ans = _JUDGE_ANSWER_RE.search(raw)
        m_ok = _JUDGE_CORRECT_RE.search(raw)
        m_reason = _JUDGE_REASON_RE.search(raw)
        return GradeResult(
            correct=bool(m_ok and m_ok.group(1).lower() == "yes"),
            extracted_answer=(m_ans.group(1).strip() if m_ans else ""),
            reasoning=(m_reason.group(1).strip() if m_reason else ""),
        )
