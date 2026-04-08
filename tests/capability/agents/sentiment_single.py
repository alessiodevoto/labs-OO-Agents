"""Single sentiment classification agent."""

from typing import Annotated, Literal

from agent006 import Agent


class SentimentSingleAgent(Agent):
    """You are specialist for sentiment classification."""

    async def classify(
        self, text: Annotated[str, "The text to classify"]
    ) -> Literal["positive", "negative", "neutral"]:
        """Classify the sentiment of the text."""
        ...
