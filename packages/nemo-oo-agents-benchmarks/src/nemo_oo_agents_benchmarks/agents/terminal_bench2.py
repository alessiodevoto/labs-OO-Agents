# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Terminal Bench 2 agent stub for nemo-oo-agents-benchmarks.

TODO (gl-15): Port Terminal Bench 2 agent from agent006 history into this
package.  Find the last Terminal Bench 2 agent in:
  git log --all -- 'experiments/evaluation-ablations/agents/terminal*'
on the agent006 repository, then adapt it here.
"""

from __future__ import annotations

from typing import Any


class TerminalBench2Agent:
    """Stub — not yet implemented.  See gl-15."""

    def __init__(self, llm: Any = None, **kwargs: Any) -> None:
        pass

    async def _run_evaluation(self, task_input: dict) -> dict:
        raise NotImplementedError(
            "TerminalBench2Agent not yet implemented. "
            "See gl-15: port Terminal Bench 2 agent from agent006 history."
        )
