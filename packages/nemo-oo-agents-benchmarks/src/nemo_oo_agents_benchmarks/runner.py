# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Harbor agent runner for nemo-oo-agents — executed inside the Harbor container.

Harbor invokes this as a CLI process::

    nemo-harbor \\
        --instruction '...' \\
        --model 'anthropic/claude-opus-4-6' \\
        --agent-type baseline

The runner is benchmark-agnostic. Its only jobs are:

1. Instantiate the right agent class from ``--agent-type``.
2. Inject container tools if requested via ``--tools`` (e.g. ``swebench``).
3. Call ``agent._run_evaluation({"user_message": instruction})``.
4. Write ``result.json`` to ``/logs/agent/`` and answer text to ``/app/answer.txt``.

Benchmark-specific logic (system prompts, instruction parsing, data paths) lives
inside each agent class, not here.

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
ANSWER_FILE = Path("/app/answer.txt")


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


def _setup_tracing(model: str, agent_type: str) -> None:
    """Enable OTel tracing, publishing live to the viewer if reachable.

    Detection order:
    1. Probe ``OTLP_ENDPOINT`` env var, or ``http://localhost:5001`` by default.
    2. If reachable → stream spans via the journal exporter with ``eval.model``
       and ``eval.agent_type`` injected as resource attributes so the viewer can
       display them without a separate ``import-harbor`` step.
    3. If unreachable → fall back to JSONL files in the Harbor artifact
       directory (``/logs/artifacts/traces/``), importable later via
       ``nemo_oo_agents import-harbor``.

    Note: Apptainer containers share the host network namespace, so
    ``localhost:5001`` inside the container resolves to the developer's host.
    For Docker containers set ``OTLP_ENDPOINT=http://host.docker.internal:5001``.
    """
    try:
        from openinference_instrumentation_nemo_oo_agents import (
            enable_tracing,
            probe_otlp_endpoint,
        )
        from openinference_instrumentation_nemo_oo_agents import exporters as nemo_exporters
    except ImportError:
        logger.warning("openinference_instrumentation_nemo_oo_agents not available, no tracing")
        return

    endpoint = os.environ.get("OTLP_ENDPOINT", "http://localhost:5001/v1/traces")

    if probe_otlp_endpoint(endpoint):
        logger.info("OTLP endpoint reachable (%s) — streaming traces live", endpoint)
        enable_tracing(
            exporters=[nemo_exporters.journal(endpoint=endpoint)],
            extra_resource_attrs={"eval.model": model, "eval.agent_type": agent_type},
        )
        logger.info("OTel tracing enabled → %s", endpoint)
    else:
        TRACES_DIR.mkdir(parents=True, exist_ok=True)
        enable_tracing(exporters=[nemo_exporters.jsonl(TRACES_DIR)])
        logger.info("OTel tracing enabled → %s (OTLP not reachable)", TRACES_DIR)


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


def _write_answer(result: dict[str, Any]) -> None:
    """Write the agent's answer to /app/answer.txt for Harbor's verifier."""
    answer = result.get("answer") or result.get("response", "")
    if not answer:
        logger.warning("No answer to write to %s", ANSWER_FILE)
        return
    try:
        ANSWER_FILE.parent.mkdir(parents=True, exist_ok=True)
        ANSWER_FILE.write_text(str(answer))
        logger.info("Answer written → %s", ANSWER_FILE)
    except OSError as e:
        logger.warning("Could not write answer file %s: %s", ANSWER_FILE, e)


async def _run(
    instruction: str,
    model: str,
    agent_type: str,
    tools: frozenset[str],
    api_base: str | None,
) -> int:
    """Async main: instantiate, wire, run.  Returns exit code (0 = success)."""
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

    # Instantiate agent.
    AgentClass = _import_agent_class(agent_type)
    agent: Any = AgentClass(llm=llm_client)

    # Inject container tools if requested.
    if "swebench" in tools:
        from nemo_oo_agents_benchmarks.tools import SWEBenchLocalTools

        swebench_tools = SWEBenchLocalTools()
        agent.swebench = swebench_tools
        if hasattr(agent, "feedback") and agent.feedback is not None:
            agent.feedback.swebench = swebench_tools

    # All agents share the same interface: {"user_message": instruction}.
    # Benchmark-specific parsing (system prompts, data paths, etc.) happens
    # inside the agent's _run_evaluation method.
    logger.info("Running agent %s (model=%s)...", agent_type, model)
    result = await agent._run_evaluation({"user_message": instruction})
    _write_result(result, model, agent_type)
    _write_answer(result)

    if result.get("success"):
        logger.info("Agent completed successfully.")
        return 0
    else:
        logger.error("Agent reported failure.")
        return 1


@click.command()
@click.option("--instruction", required=True, help="Task instruction / problem statement")
@click.option("--model", required=True, help="Model name in litellm format")
@click.option("--agent-type", default="baseline", show_default=True, help="Agent variant to run")
@click.option(
    "--tools",
    default="",
    help="Comma-separated tool sets to inject (e.g. 'swebench')",
)
@click.option("--api-base", default=None, help="Override API base URL")
def main(instruction: str, model: str, agent_type: str, tools: str, api_base: str | None) -> None:
    """Run a nemo-oo-agents agent on a task inside a Harbor container."""
    _setup_logging()
    logger.info("nemo-oo-agents-benchmarks runner starting")
    logger.info("  model:      %s", model)
    logger.info("  agent_type: %s", agent_type)
    if tools:
        logger.info("  tools:      %s", tools)
    if api_base:
        logger.info("  api_base:   %s", api_base)

    # Validate agent_type early so we get a clean error before any heavy imports.
    from nemo_oo_agents_benchmarks.agents import AGENT_CLASSES

    if agent_type not in AGENT_CLASSES:
        logger.error(
            "Unknown agent_type: %r.  Must be one of: %s", agent_type, sorted(AGENT_CLASSES)
        )
        sys.exit(1)

    _setup_tracing(model=model, agent_type=agent_type)

    tool_set = frozenset(t.strip() for t in tools.split(",") if t.strip())

    try:
        exit_code = asyncio.run(_run(instruction, model, agent_type, tool_set, api_base))
    except Exception as e:
        logger.exception("Runner failed with unhandled exception: %s", e)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
