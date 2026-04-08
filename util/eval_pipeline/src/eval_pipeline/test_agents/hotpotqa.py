"""HotpotQA multi-hop question answering agent.

This agent answers questions that require reasoning across multiple
context paragraphs. Some paragraphs are relevant, others are distractors.
"""

from nemo_oo_agents import Agent


class HotpotQAAgent(Agent):
    """You are an expert at multi-hop question answering.

    You answer questions by reasoning across multiple context paragraphs.
    Some paragraphs contain relevant information, others are distractors.
    Your job is to identify the relevant facts and chain them together
    to arrive at the correct answer.
    """

    async def answer(self, question: str, context: list[dict[str, str]]) -> str:
        """Answer a multi-hop question using the provided context.

        The question requires combining information from multiple paragraphs.
        Read the context carefully, identify the relevant facts, and reason
        step by step to find the answer.

        Args:
            question: The question to answer
            context: List of paragraphs, each with 'title' and 'sentences' keys.
                     Some paragraphs are relevant, others are distractors.

        Returns:
            The answer as a short string (typically a few words).
            Do NOT include explanations - just the answer itself.
        """
        ...
