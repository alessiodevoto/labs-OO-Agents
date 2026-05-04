# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
General-purpose baseline agent for nemo-oo-agents-harbor.

Ported from agent006 history:
  git show 92d5ba90:experiments/evaluation-ablations/agents/agent006_tools.py

Baseline: simple CodeAct agent with data science imports pre-loaded and
dynamic environment tool injection. Ran against DABStep, MemoryBench, and
others — serves as the reference score for all benchmark comparisons.

Benchmark-specific logic removed from the original agent006_tools.py:

  - BigCodeBench: code_imports exec/module-namespace injection, _import_lock,
    matplotlib Agg backend, Django settings pre-configuration, and post-task
    namespace cleanup. Restore in a dedicated BigCodeBench agent if needed.

  - LoCoBench: response_format="text" category-based strategy injection
    (text-analysis vs code-writing paths), large-description extraction
    (_TASK_SEP / "## Task" section prepend). Restore in a dedicated
    LoCoBench agent if needed.

  - MemoryBench: response_format="answer"/"mc_answer"/"short_answer" QA path,
    constraint-hint extraction from task_input["constraints"]. Restore in the
    dedicated memory agent (see gl-14) and MemBench smoke test (see gl-28).
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Any

from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.tools import FileTool
from nemo_oo_agents.unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import UnifiedLLM

# Data science imports — pre-loaded so they're available in the REPL sandbox.
# Required for DABStep (financial data analysis benchmark).
try:
    import matplotlib
    import matplotlib.pyplot as plt
except ImportError:
    matplotlib = None  # type: ignore[assignment]
    plt = None  # type: ignore[assignment]
try:
    import numpy
    import numpy as np
except ImportError:
    numpy = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
try:
    import pandas
    import pandas as pd
except ImportError:
    pandas = None  # type: ignore[assignment]
    pd = None  # type: ignore[assignment]


class BaselineAgent(Agent, llm=FakeLLMClient()):
    """General-purpose baseline agent with data science imports pre-loaded.

    Supports any Harbor benchmark via dynamic environment tool injection.
    ``self.files`` (FileTool) is available when no environment tools are present.
    """

    files: FileTool | None = None

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner."""
        from nemo_oo_agents.agentdoc import doc

        parts = []

        if system_prompt := task_input.get("system_prompt"):
            parts.append(f"## Context\n{system_prompt}")

        if env_tools := task_input.get("environment_tools"):
            tools_docs = [
                "## Available Environment Tools",
                "Use `await self.<tool>.<method>()` to interact with the environment.\n",
            ]
            for tool_name in env_tools:
                tool = getattr(self, tool_name, None)
                if tool is not None:
                    tools_docs.append(f"### self.{tool_name}")
                    tools_docs.append(doc(tool))
            parts.append("\n".join(tools_docs))

        description = (
            task_input.get("user_prompt")
            or task_input.get("user_message")
            or task_input.get("description", "")
        )
        if description:
            parts.append(f"## Task\n{description}")

        full_description = "\n\n".join(parts) if parts else description
        response_format = task_input.get("response_format", "")

        try:
            self.context["problem_statement"] = full_description
            self.context["response_format"] = response_format
            result = await self.solve_task(full_description, response_format)
            result_str = str(result) if result is not None else ""
            if response_format == "code" and result_str:
                result_str = textwrap.dedent(result_str)
            return {"response": result_str, "success": True, "result": result}
        except Exception as e:
            return {"response": "", "success": False, "error": str(e)}

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100, max_retries=10)))
    async def solve_task(self, description: str, response_format: str = "") -> Any:
        """Solve the following task:

        {description}

        Expected output format: {response_format}

        Instructions:
        - For environment tasks: use ``await self.<tool>.<method>()`` directly in
          ``execute_python()`` blocks. Those run inside the environment.
          Direct commands run on the host.
        - Use the REPL to iterate and validate before returning.
        - ``return``/``return_result`` ends execution. Only use it for the final answer.
        - Check ``doc(self)`` to see available tools and methods.
        """
        ...
