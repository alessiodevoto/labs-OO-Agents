# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Truncation-comprehension agent for capability testing.

Tests whether LLMs of various sizes correctly interpret rendered context
that contains truncation markers — both today's pformat output (`before`
fixtures) and the proposed truncation 3.0 markers (`after` fixtures with
explicit `<preview>...</preview>` and `<truncated>...</truncated>` wrappers).

The hypothesis: small LLMs (Haiku, Gemini Flash, Nemotron Nano, Qwen 35B)
should improve on the `after` fixtures relative to `before`. If they
don't, the proposed marker design isn't earning its complexity.

See docs/design/truncation-3.0.md.
"""

from typing import Annotated

from pydantic import BaseModel, Field

from nemo_oo_agents import Agent
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.strategies import PredictStrategy


class Answer(BaseModel):
    """Structured answer with reason trace.
    """

    answer: Annotated[int | None, Field(description="Integer answer, or None if cannot be determined")]
    reason: Annotated[str, Field(description="Why you picked that answer (one or two sentences)")]


class TruncationComprehensionAgent(Agent):
    """You read rendered Python output (lists, dicts, captured streams) and answer
    questions about it.
    """

    @strategy(PredictStrategy())
    async def answer(
        self,
        context: Annotated[str, "The rendered Python output the question is about"],
        question: Annotated[str, "A question to answer."],
    ) -> Answer:
        """
        Based on the `context`, answer the `question`.
        Return an integer if the answer can be determined from the data shown.
        Return None if the answer cannot be determined.
        Include a brief reason string explaining your choice.
        """
        ...


# A/B control for measuring the uplift from the Pydantic Answer(answer, reason)
# schema vs a bare int|None return. Class docstring matches the main agent so
# the system-prompt persona is identical between the two; only the return type
# differs.
class TruncationComprehensionAgentBare(Agent):
    """You read rendered Python output (lists, dicts, captured streams) and answer
    questions about it.
    """

    @strategy(PredictStrategy())
    async def answer(
        self,
        context: Annotated[str, "The rendered Python output the question is about"],
        question: Annotated[str, "A question to answer."],
    ) -> int | None:
        """
        Based on the `context`, answer the `question`.
        Return an integer if the answer can be determined from the data shown.
        Return None if the answer cannot be determined.
        """
        ...
