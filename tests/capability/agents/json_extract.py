"""JSON extraction agent - should read the json and extract."""

import json  # noqa: F401 — for LLM exec_globals
from typing import Any

from agent006 import Agent


class JsonExtractAgent(Agent):
    """Agent for returning data from JSON strings."""

    async def extract(self, json_str: str, path: str) -> Any:
        """
        Return the value from the JSON string at the given path.

        The path uses dot notation (e.g., "users.0.name" for nested access).
        Return None if path doesn't exist.

        Args:
            json_str: JSON string input
            path: Dot-notation path to return (e.g., "users.0.name")

        Returns:
            The value at the specified path, or None if not found
        """
        ...
