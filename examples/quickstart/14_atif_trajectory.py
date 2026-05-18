# ruff: noqa: F403,F405
"""Quickstart 14: ATIF trajectory exporter — write trajectory.json from spans.

uv run python examples/quickstart/14_atif_trajectory.py
"""

from nemo_oo_agents.tracing import enable_tracing, exporters
from nemo_oo_agents.util.quickstart import *


class FeedbackAgent(Agent, llm=llm):
    """Agent for analyzing customer feedback."""

    async def analyze_feedback(self, text: str) -> str:
        """Analyze customer feedback in one sentence."""
        ...


@autorun
async def main():
    enable_tracing(
        exporters=[
            exporters.atif(
                "trajectory.json",
                session_id="quickstart-14",
                agent_name="feedback-agent",
                agent_version="0.1.0",
            ),
        ]
    )

    agent = FeedbackAgent()
    result = await agent.analyze_feedback("Great product, but shipping was slow")
    print(result)
