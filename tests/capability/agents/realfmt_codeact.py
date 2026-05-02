# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CodeActStrategy variant of the realfmt format-comparison agent.

Class name `RealFmtAgent` and the method docstring are intentionally
identical to the ones in ``realfmt_predict.py``.

Under CodeAct the prefill renders ``data`` as a truncated marker via
``Wrapped.__repr__``, but the value passed at runtime is still a
``Wrapped`` instance with full container pass-through. So
``data[49]``, ``min(data)``, ``len(data)``, ``data.items()``, etc. all
work in ``execute_python`` against the real underlying value.
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field

from nemo_oo_agents import Agent
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.strategies import CodeActStrategy
from tests.capability.agents.truncation_formats import _patch_eval_pipeline_loader

_patch_eval_pipeline_loader()


class Answer(BaseModel):
    answer: Annotated[
        int | None, Field(description="Integer answer, or None if cannot be determined")
    ]
    reason: Annotated[str, Field(description="Why you picked that answer (one or two sentences)")]


class RealFmtAgent(Agent):
    """Answer questions about the data."""

    @strategy(CodeActStrategy())
    async def answer(
        self,
        data: Annotated[Any, "The data"],
        type_tag: Annotated[str, "Container shape"],
        fmt: Annotated[str, "Marker format"],
        question: Annotated[str, "The question"],
    ) -> Answer:
        """Answer the question based on the data."""
        ...
