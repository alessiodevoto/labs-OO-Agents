# ruff: noqa: F403,F405
"""Quickstart 10: Skills — inject curated context into an agent.

uv run python examples/quickstart/10_skills.py
"""

from pathlib import Path

from agent006 import SkillManager, TextSkill
from agent006.util.quickstart import *

ASSETS = Path(__file__).parent.parent / "assets"


class FrontendAgent(Agent, llm=llm):
    """Agent with a single file-based skill."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frontend_design = TextSkill(path=ASSETS / "frontend-design")

    async def respond(self, prompt: str) -> str:
        """Respond to a user message."""
        ...


class GenericAgent(Agent, llm=llm):
    """Agent that loads all skills from a directory."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._skills = SkillManager.install(self, skills_dir=ASSETS)

    async def respond(self, prompt: str) -> str:
        """Respond to a user message."""
        ...


@autorun
async def main():
    agent = FrontendAgent()
    result = await agent.respond("Can you build a responsive layout for a landing page?")
    print(result)
    agent = GenericAgent()
    result = await agent.respond("Can you build a responsive layout for a landing page?")
    print(result)
