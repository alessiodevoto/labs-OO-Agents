# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test agent with multiple imports."""

import json  # noqa: F401
from asyncio import sleep  # noqa: F401
from datetime import datetime  # noqa: F401

from nemo_oo_agents import Agent
from nemo_oo_agents.unifiedllm import FakeLLMClient


class AgentMultipleImports(Agent, llm=FakeLLMClient()):
    """Agent with multiple imports."""

    pass
