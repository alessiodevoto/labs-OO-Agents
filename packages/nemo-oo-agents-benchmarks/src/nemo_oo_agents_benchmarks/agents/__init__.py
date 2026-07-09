# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Agent registry for nemo-oo-agents-benchmarks."""

from __future__ import annotations

# Maps --agent-type CLI values to dotted import paths: "module:ClassName"
#
# Naming convention: <benchmark>/<variant>
#   baseline            — General-purpose CodeAct agent (default, gl-22)
#   react-baseline      — ReAct (Thought/Action/Observation) baseline (gl-65–71)
#   swebench/basic      — SWE-bench Verified, single-pass CodeAct
#   swebench/opt1       — SWE-bench Verified, opt1 (feedback loop)
#   swebench/pro        — SWE-bench Pro, multi-language opt1 variant (gl-18)
#   swebench/todo       — SWE-bench Verified, todo-driven single agent with ShellTools
#   terminal-bench-1    — Terminal Bench 1 (stub, see gl-16)
#   terminal-bench-2    — Terminal Bench 2 (stub, see gl-15)
#   locomo              — LoCoMo / long-context memory (stub, see gl-14)
#   dabstep             — DABStep opt63 (ported from agent006)
#
# tau-bench is intentionally NOT registered: the TauBenchAgent renders
# ``doc(self.taubench)`` / ``self.taubench.policy`` every turn, but nothing
# provides ``self.taubench`` (no TauBenchTools class or injection site exists —
# the runner's --tools only covers "swebench"/"terminal"), so it would crash
# with AttributeError on the first context render. Re-register once Harbor
# multi-turn support (gl-23) and a TauBenchTools injection site land — matching
# the VendingBench disposition.
AGENT_CLASSES: dict[str, str] = {
    # General-purpose baselines (gl-22, gl-65–71)
    "baseline": "nemo_oo_agents_benchmarks.agents.baseline:BaselineAgent",
    "react-baseline": "nemo_oo_agents_benchmarks.agents.react_baseline:ReActBaselineAgent",
    # SWE-bench — implemented
    "swebench/basic": "nemo_oo_agents_benchmarks.agents.swebench_basic:SWEBenchBasicAgent",
    "swebench/opt1": "nemo_oo_agents_benchmarks.agents.swebench_opt1:SWEBenchOpt1Agent",
    "swebench/pro": "nemo_oo_agents_benchmarks.agents.swebench_pro:SWEBenchProAgent",
    "swebench/todo": "nemo_oo_agents_benchmarks.agents.swebench_todo:SWEBenchTodoAgent",
    # Terminal Bench — stubs
    "terminal-bench-1": "nemo_oo_agents_benchmarks.agents.terminal_bench1:TerminalBench1Agent",
    "terminal-bench-2": "nemo_oo_agents_benchmarks.agents.terminal_bench2:TerminalBench2Agent",
    # LoCoMo / memory — stub
    "locomo": "nemo_oo_agents_benchmarks.agents.locomo:LoCoMoAgent",
    # Tau Bench — ported but unregistered until self.taubench injection lands
    # (gl-23); see the note above.
    # DABStep — ported from agent006 rsc_dab_agent_hard_opt63
    "dabstep": "nemo_oo_agents_benchmarks.agents.dabstep:DABStepAgent",
    # Unified SWE-bench + Terminal-Bench agent
    "bench": "nemo_oo_agents_benchmarks.agents.bench_agent:BenchAgent",
}

__all__ = ["AGENT_CLASSES"]
