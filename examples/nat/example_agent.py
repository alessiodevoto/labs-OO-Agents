"""Example Agent006 agent for NAT integration.

This agent has NO LLM or tools defined at class level.
Everything is injected by NAT via the agent006_wrapper:
- LLM comes from the llms: section in YAML
- Tools come from the functions: section in YAML (injected as native Python objects)

Usage:
    nat run --config_file config_full.yml --input "What time is it right now?"
"""

from agent006 import Agent


class DemoAgent(Agent):
    """A demo agent that receives its LLM and tools from NAT."""

    async def chat(self, user_message: str) -> str:
        """Respond to the user's message.

        Use doc(self) to discover what tools are available, then use them.
        Return a helpful response as a string.
        """
        ...

    async def summarize(self, text: str) -> str:
        """Summarize the given text in 2-3 sentences.

        Return a concise summary capturing the key points.
        """
        ...
