"""Single calculation agent - interprets natural language math."""

from nemo_oo_agents import Agent


class CalculateSingleAgent(Agent):
    """You are an agent that performs a single calculation from natural language."""

    async def calculate(self, a: int | float, b: int | float, calculation: str) -> int | float:
        """Perform a calculation described in natural language.

        Args:
            a: First number
            b: Second number
            calculation: Natural language description of the calculation to perform.

        Returns:
            The computed result.
        """
        ...
