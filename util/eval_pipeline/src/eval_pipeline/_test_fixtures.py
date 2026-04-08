"""Test fixtures for subprocess worker tests.

This module is importable by subprocess workers (unlike test files).
"""


class DummyAgent:
    """Plain Python agent for testing — no LLM, no agent006."""

    def __init__(self, llm=None):
        pass

    async def classify(self, text: str) -> str:
        words = set(text.lower().split())
        if words & {"great", "love", "excellent", "good"}:
            return "positive"
        if words & {"terrible", "hate", "bad", "awful"}:
            return "negative"
        return "neutral"
