"""Batch sentiment classification agent."""

from typing import Annotated, Literal

from agent006 import Agent
from agent006.tools.method_writing_lib import MethodWriting


class SentimentBatchAgent(Agent):
    """You are an agent that classifies sentiment of multiple texts."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.method_writing = MethodWriting()

    async def classify(
        self, texts: Annotated[list[str], "The texts to classify"]
    ) -> list[Literal["positive", "negative", "neutral"]]:
        """Classify the sentiment of multiple texts."""
        ...
