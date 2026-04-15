# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
LoCoMo (long-context memory) agent stub for nemo-oo-agents-benchmarks.

TODO (gl-14): Port long_memory_agent.py from agent006 history.  Retrieve it
with:
  git show 9a4f888~1:experiments/evaluation-ablations/agents/long_memory_agent.py
on the agent006 repository, then adapt it here.
"""

from __future__ import annotations

from typing import Any


class LoCoMoAgent:
    """Stub — not yet implemented.  See gl-14."""

    def __init__(self, llm: Any = None, **kwargs: Any) -> None:
        pass

    async def _run_evaluation(self, task_input: dict) -> dict:
        raise NotImplementedError(
            "LoCoMoAgent not yet implemented. "
            "See gl-14: port long_memory_agent.py from agent006 "
            "(git show 9a4f888~1:experiments/evaluation-ablations/agents/long_memory_agent.py)."
        )
