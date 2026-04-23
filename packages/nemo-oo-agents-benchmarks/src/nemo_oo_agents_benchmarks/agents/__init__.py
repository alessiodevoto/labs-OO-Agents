# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Agent registry for nemo-oo-agents-benchmarks."""

from __future__ import annotations

# Maps --agent-type CLI values to dotted import paths: "module:ClassName"
#
# Naming convention: <benchmark>/<variant>
#   baseline            — General-purpose CodeAct agent (default, gl-22)
#   swebench/basic      — SWE-bench Verified, single-pass CodeAct
#   swebench/opt1       — SWE-bench Verified, opt1 (feedback loop)
#   swebench/pro        — SWE-bench Pro, multi-language opt1 variant (gl-18)
#   terminal-bench-1    — Terminal Bench 1 (stub, see gl-16)
#   terminal-bench-2    — Terminal Bench 2 (stub, see gl-15)
#   locomo              — LoCoMo / long-context memory (stub, see gl-14)
#   tau-bench           — Tau Bench opt2 (ported; requires Harbor multi-turn gl-23)
#   dabstep             — DABStep opt63 (ported from agent006)
AGENT_CLASSES: dict[str, str] = {
    # General-purpose baseline (gl-22)
    "baseline": "nemo_oo_agents_benchmarks.agents.baseline:BaselineAgent",
    # SWE-bench — implemented
    "swebench/basic": "nemo_oo_agents_benchmarks.agents.swebench_basic:SWEBenchBasicAgent",
    "swebench/opt1": "nemo_oo_agents_benchmarks.agents.swebench_opt1:SWEBenchOpt1Agent",
    "swebench/pro": "nemo_oo_agents_benchmarks.agents.swebench_pro:SWEBenchProAgent",
    # Terminal Bench — stubs
    "terminal-bench-1": "nemo_oo_agents_benchmarks.agents.terminal_bench1:TerminalBench1Agent",
    "terminal-bench-2": "nemo_oo_agents_benchmarks.agents.terminal_bench2:TerminalBench2Agent",
    # LoCoMo / memory — stub
    "locomo": "nemo_oo_agents_benchmarks.agents.locomo:LoCoMoAgent",
    # Tau Bench — ported (also requires Harbor multi-turn support, gl-23)
    "tau-bench": "nemo_oo_agents_benchmarks.agents.tau_bench:TauBenchAgent",
    # DABStep — ported from agent006 rsc_dab_agent_hard_opt63
    "dabstep": "nemo_oo_agents_benchmarks.agents.dabstep:DABStepAgent",
}

__all__ = ["AGENT_CLASSES"]
