"""Sentiment classification agent."""

from nooa import Agent


class SentimentAgent(Agent):
    """Agent that classifies sentiment of text."""

    async def classify_single(self, text: str) -> str:
        """Classify the sentiment of a single text.

        Args:
            text: The text to classify

        Returns:
            One of: "positive", "negative", "neutral"
        """
        ...

    async def classify_batch(self, texts: list[str]) -> list[str]:
        """Classify the sentiment of multiple texts.

        Args:
            texts: List of texts to classify

        Returns:
            List of sentiment labels ("positive", "negative", "neutral")
        """
        ...
