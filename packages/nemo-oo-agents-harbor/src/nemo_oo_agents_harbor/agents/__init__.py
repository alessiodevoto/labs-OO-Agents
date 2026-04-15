# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Agent registry for nemo-oo-agents-harbor."""

from __future__ import annotations

# Maps --agent-type CLI values to dotted import paths: "module:ClassName"
AGENT_CLASSES: dict[str, str] = {
    "basic": "nemo_oo_agents_harbor.agents.swebench_basic:SWEBenchBasicAgent",
    "opt1": "nemo_oo_agents_harbor.agents.swebench_opt1:SWEBenchOpt1Agent",
}

__all__ = ["AGENT_CLASSES"]
