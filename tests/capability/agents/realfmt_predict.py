# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""PredictStrategy variant of the realfmt format-comparison agent.

Class name `RealFmtAgent` and the method docstring are intentionally
identical to the ones in ``realfmt_codeact.py``. Tests pick which one to
run via the ``module:`` / ``class:`` fields in ``config_truncation.yaml``.

The agent receives ``data`` as a ``Wrapped`` instance (see
``truncation_formats.Wrapped``) — the fixture supplies raw Python data
plus ``type_tag`` and ``fmt`` kwargs, and the loader monkey-patch
auto-wraps them. The framework then uses ``Wrapped.__repr__`` (i.e. our
chosen marker format) when rendering the parameter dump in the prefill.
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field

from nemo_oo_agents import Agent
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.strategies import PredictStrategy
from tests.capability.agents.truncation_formats import _patch_eval_pipeline_loader

_patch_eval_pipeline_loader()


class Answer(BaseModel):
    answer: Annotated[
        int | None, Field(description="Integer answer, or None if cannot be determined")
    ]
    reason: Annotated[str, Field(description="Why you picked that answer (one or two sentences)")]


class RealFmtAgent(Agent):
    """Answer questions about the data."""

    @strategy(PredictStrategy())
    async def answer(
        self,
        data: Annotated[Any, "The data"],
        type_tag: Annotated[str, "Container shape"],
        fmt: Annotated[str, "Marker format"],
        question: Annotated[str, "The question"],
    ) -> Answer:
        """Answer the question based on the data."""
        ...
