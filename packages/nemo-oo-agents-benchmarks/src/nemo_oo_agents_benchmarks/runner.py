# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Harbor agent runner for nemo-oo-agents — executed inside the Harbor container.

Harbor invokes this as a CLI process::

    python -m nemo_oo_agents_benchmarks \\
        --instruction '...' \\
        --model 'anthropic/claude-opus-4-6' \\
        --agent-type basic

The runner:
1. Instantiates the nemo-oo-agents agent for the given task type.
2. Runs it on the instruction inside the container.
3. Emits OTel traces to ``/logs/artifacts/traces/`` (Harbor's artifact path).
4. Writes result metadata JSON to ``/logs/agent/``.

TODO (gl-8): Auto-detect and publish to OTLP endpoint when available.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import click

logger = logging.getLogger("nemo_oo_agents_benchmarks.runner")

# Harbor container path conventions
LOGS_DIR = Path("/logs/agent")
ARTIFACTS_DIR = Path("/logs/artifacts")
TRACES_DIR = ARTIFACTS_DIR / "traces"

# System prompt for SWEBench tasks
_SWEBENCH_SYSTEM_PROMPT = (
    "You are a software engineer working inside a pre-configured repository "
    "container.  Your task is to make changes to non-test files in order to "
    "fix the issue described in the problem statement in a way that is general "
    "and consistent with the codebase.\n\n"
    "The repository is checked out at /testbed.  A conda environment named "
    "'testbed' is pre-activated with all dependencies installed.  Use the "
    "available shell and file tools to navigate, understand, and fix the code."
)


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(str(LOGS_DIR / "nemo_oo_agents_benchmarks.log")))
    except OSError:
        # Outside a Harbor container /logs may not exist or be writable — stderr only.
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=handlers,
    )


def _setup_tracing() -> None:
    """Enable OTel tracing to the Harbor artifact directory.

    TODO (gl-8): Also detect a local OTLP endpoint and publish in real time
    when reachable (e.g. nemo-oo-agents trace viewer already running locally).
    Falls back to JSONL file export when no endpoint is reachable.
    """
    try:
        from openinference_instrumentation_nemo_oo_agents import enable_tracing
        from openinference_instrumentation_nemo_oo_agents import exporters as nemo_exporters

        TRACES_DIR.mkdir(parents=True, exist_ok=True)
        enable_tracing(exporters=[nemo_exporters.jsonl(TRACES_DIR)])
        logger.info("OTel tracing enabled → %s", TRACES_DIR)
    except ImportError:
        logger.warning("openinference_instrumentation_nemo_oo_agents not available, no tracing")


def _import_agent_class(agent_type: str) -> type:
    from nemo_oo_agents_benchmarks.agents import AGENT_CLASSES

    entry = AGENT_CLASSES.get(agent_type)
    if entry is None:
        raise ValueError(
            f"Unknown agent_type: {agent_type!r}.  Must be one of: {sorted(AGENT_CLASSES)}"
        )
    module_path, class_name = entry.rsplit(":", 1)
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def _write_result(result: dict[str, Any], model: str, agent_type: str) -> None:
    """Write result metadata to LOGS_DIR/result.json."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "agent_type": agent_type,
        "success": result.get("success", False),
        "response": result.get("response", ""),
        "error": result.get("error"),
    }
    out = LOGS_DIR / "result.json"
    out.write_text(json.dumps(payload, indent=2))
    logger.info("Result written → %s", out)


async def _run(instruction: str, model: str, agent_type: str, api_base: str | None) -> int:
    """Async main: instantiate, wire, run.  Returns exit code (0 = success)."""
    from nemo_oo_agents_benchmarks.tools import SWEBenchLocalTools
    from unifiedllm import get_llm_client

    # Build LLM client — honour env-var overrides for local vLLM deployments.
    llm_overrides: dict[str, str] = {}
    if api_base:
        llm_overrides["api_base"] = api_base
    elif base_url := os.environ.get("OPENAI_BASE_URL"):
        llm_overrides["api_base"] = base_url
    if api_key := os.environ.get("OPENAI_API_KEY"):
        llm_overrides["api_key"] = api_key

    llm_client = get_llm_client(model, **llm_overrides)

    # Instantiate agent and tools.
    AgentClass = _import_agent_class(agent_type)
    agent: Any = AgentClass(llm=llm_client)
    tools = SWEBenchLocalTools()
    agent.swebench = tools
    # opt1 creates a FeedbackAgent in _run_evaluation; pre-wire tools there too
    # if the agent already has a feedback attribute (e.g. from a previous run).
    if hasattr(agent, "feedback") and agent.feedback is not None:
        agent.feedback.swebench = tools

    task_input = {
        "system_prompt": _SWEBENCH_SYSTEM_PROMPT,
        "problem_statement": instruction,
        "response_format": "diff",
        "environment_tools": ["swebench"],
    }

    logger.info("Running agent %s on task (model=%s)...", agent_type, model)
    result = await agent._run_evaluation(task_input)

    _write_result(result, model, agent_type)

    if result.get("success"):
        logger.info("Agent completed successfully.")
        return 0
    else:
        logger.error("Agent reported failure: %s", result.get("error", "(no error message)"))
        return 1


@click.command()
@click.option("--instruction", required=True, help="Task instruction / problem statement")
@click.option("--model", required=True, help="Model name in litellm format")
@click.option("--agent-type", default="baseline", show_default=True, help="Agent variant to run")
@click.option("--api-base", default=None, help="Override API base URL")
def main(instruction: str, model: str, agent_type: str, api_base: str | None) -> None:
    """Run a nemo-oo-agents agent on a task inside a Harbor container."""
    _setup_logging()
    logger.info("nemo-oo-agents-benchmarks runner starting")
    logger.info("  model:      %s", model)
    logger.info("  agent_type: %s", agent_type)
    if api_base:
        logger.info("  api_base:   %s", api_base)

    # Validate agent_type early so we get a clean error before any heavy imports.
    from nemo_oo_agents_benchmarks.agents import AGENT_CLASSES

    if agent_type not in AGENT_CLASSES:
        logger.error(
            "Unknown agent_type: %r.  Must be one of: %s", agent_type, sorted(AGENT_CLASSES)
        )
        sys.exit(1)

    _setup_tracing()

    try:
        exit_code = asyncio.run(_run(instruction, model, agent_type, api_base))
    except Exception as e:
        logger.exception("Runner failed with unhandled exception: %s", e)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
