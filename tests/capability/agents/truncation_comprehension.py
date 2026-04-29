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

from nemo_oo_agents import Agent
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.strategies import PredictStrategy


class TruncationComprehensionAgent(Agent):
    """You read rendered Python output (lists, dicts, captured streams) and answer
    questions about it. Some output is partial — you must distinguish what is
    actually shown from what is missing, and not invent missing content.
    """

    @strategy(PredictStrategy())
    async def answer(
        self,
        context: Annotated[str, "The rendered Python output the question is about"],
        question: Annotated[str, "A question whose answer is an integer or 'cannot determine'"],
    ) -> int | None:
        """
        Based on the `context`, answer the `question`.
        Return an integer if the answer can be determined from the data shown.
        Return null if the answer cannot be determined.
        """
        ...
