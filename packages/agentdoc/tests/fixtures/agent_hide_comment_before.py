"""Test agent with hide comment on line before import."""

import json  # noqa: F401

# agentdoc: hide
import os  # noqa: F401

from agent006 import Agent
from unifiedllm import FakeLLMClient


class AgentHideCommentBefore(Agent, llm=FakeLLMClient()):
    """Agent with comment before import."""

    def run(self):
        return json.dumps({"test": "data"})
