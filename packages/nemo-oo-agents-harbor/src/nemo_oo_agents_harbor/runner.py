# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Harbor agent runner for nemo-oo-agents — executed inside the Harbor container.

Harbor invokes this as a CLI process:
    python -m nemo_oo_agents_harbor \\
        --instruction '...' \\
        --model 'anthropic/claude-opus-4-6' \\
        --agent-type basic

The runner is responsible for:
1. Instantiating the nemo-oo-agents agent for the given task type
2. Running it on the instruction inside the container
3. Emitting OTel traces to /logs/artifacts/traces/ (Harbor's artifact path)
4. Writing result metadata to /logs/agent/

TODO (gl-3): Implement agent instantiation and execution.
TODO (gl-8): Auto-detect and publish to OTLP endpoint when available.
"""

import logging
import sys
from pathlib import Path

import click

logger = logging.getLogger("nemo_oo_agents_harbor.runner")

# Harbor container conventions
LOGS_DIR = Path("/logs/agent")
ARTIFACTS_DIR = Path("/logs/artifacts")
TRACES_DIR = ARTIFACTS_DIR / "traces"


def _setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "nemo_oo_agents_harbor.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(str(log_file)),
        ],
    )


def _setup_tracing() -> None:
    """Enable OTel tracing to the Harbor artifact directory.

    TODO (gl-8): Also detect a local OTLP endpoint and publish in real time
    when reachable (e.g. nemo-oo-agents trace viewer already running locally).
    Falls back to JSONL file export if no endpoint is reachable.
    """
    try:
        from openinference_instrumentation_nemo_oo_agents import enable_tracing
        from openinference_instrumentation_nemo_oo_agents import exporters as nemo_exporters

        TRACES_DIR.mkdir(parents=True, exist_ok=True)
        enable_tracing(exporters=[nemo_exporters.jsonl(TRACES_DIR)])
        logger.info("OTel tracing enabled → %s", TRACES_DIR)
    except ImportError:
        logger.warning("openinference_instrumentation_nemo_oo_agents not available, no tracing")


@click.command()
@click.option("--instruction", required=True, help="Task instruction / problem statement")
@click.option("--model", required=True, help="Model name in litellm format")
@click.option("--agent-type", default="basic", show_default=True, help="Agent variant to run")
@click.option("--api-base", default=None, help="Override API base URL")
def main(instruction: str, model: str, agent_type: str, api_base: str | None) -> None:
    """Run a nemo-oo-agents agent on a task inside a Harbor container."""
    _setup_logging()
    logger.info("nemo-oo-agents-harbor runner starting")
    logger.info("  model:      %s", model)
    logger.info("  agent_type: %s", agent_type)
    if api_base:
        logger.info("  api_base:   %s", api_base)

    _setup_tracing()

    # TODO (gl-3): implement agent instantiation and execution
    raise NotImplementedError(
        "Harbor runner not yet implemented. "
        "See gl-3: Implement canonical Harbor runner in nemo-oo-agents-harbor."
    )


if __name__ == "__main__":
    main()
