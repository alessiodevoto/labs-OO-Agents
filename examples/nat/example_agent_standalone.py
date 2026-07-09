"""Example NeMo OO Agents agent with its own LLM.

This agent defines its own LLM at class level, so it works with
a minimal NAT config that doesn't specify an LLM.

Usage:
    nat run --config_file config_standalone.yml --input "Hello!"
"""

from nooa import Agent
from nooa.unifiedllm import CompletionClient

llm = CompletionClient(model="gpt-4o-mini")


class StandaloneAgent(Agent, llm=llm):
    """An agent that brings its own LLM.

    NAT wraps this agent but doesn't need to provide an LLM.
    """

    async def chat(self, user_message: str) -> str:
        """Respond to the user's message conversationally.

        Return a helpful response as a string.
        """
        ...
