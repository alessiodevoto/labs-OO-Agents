"""TUI backed by an agent with deterministic helper tools.

This example shows the next step beyond a pure chat agent: an Agent006 agent
that exposes real Python methods the LLM can call via ``execute_python()``.
When the agent calls a tool you'll see the Python execution panel appear in
the TUI (use ``/python on|off`` to toggle it).

The agent manages a simple in-memory note store.  Tools:

    agent.add_note(topic, content)   →  store a note under a topic
    agent.list_topics()              →  list all topics
    agent.get_notes(topic)           →  retrieve notes for a topic
    agent.delete_note(topic, index)  →  remove a note by index

Usage::

    python -m examples.tools_agent_tui.example
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from agent006 import Agent
from agent006_cli.tui.config import DEFAULT_MODEL, Config
from agent006_cli.tui.main import main
from unifiedllm import FakeLLMClient, get_llm_client

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

try:
    _llm = get_llm_client(DEFAULT_MODEL)
except Exception:
    _llm = FakeLLMClient()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class NotesAgent(Agent, llm=_llm):
    """An agent that manages a personal note store.

    The LLM has access to add, list, retrieve, and delete notes organised
    by topic.  Replies are sent with message().
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._notes: dict[str, list[str]] = defaultdict(list)

    # -- deterministic tools (visible to LLM via doc(self)) ------------------

    def add_note(self, topic: str, content: str) -> str:
        """Add a note under *topic*. Returns a confirmation string."""
        self._notes[topic].append(content)
        idx = len(self._notes[topic]) - 1
        return f"Note [{idx}] added under '{topic}'."

    def list_topics(self) -> list[str]:
        """Return all topics that have at least one note."""
        return sorted(self._notes.keys())

    def get_notes(self, topic: str) -> list[str]:
        """Return all notes stored under *topic* (empty list if none)."""
        return list(self._notes.get(topic, []))

    def delete_note(self, topic: str, index: int) -> str:
        """Delete note at *index* under *topic*. Returns a confirmation string."""
        notes = self._notes.get(topic)
        if not notes:
            return f"No notes found under '{topic}'."
        if index < 0 or index >= len(notes):
            return f"Index {index} out of range (topic '{topic}' has {len(notes)} note(s))."
        removed = notes.pop(index)
        if not notes:
            del self._notes[topic]
        return f"Deleted note [{index}] from '{topic}': {removed!r}"

    # -- generation method ----------------------------------------------------

    async def respond(self, user_input: str) -> None:
        """You are a helpful personal assistant managing the user's notes.

        Available tools (call via execute_python):
        {doc(self)}

        User: {user_input}

        Help the user manage their notes. Use the tools above to read or write
        notes as needed, then send a clear reply with message().
        """
        ...


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run() -> None:
    config = Config.load(no_trace=True)
    agent = NotesAgent(llm=_llm)
    await main(config=config, agent=agent)


if __name__ == "__main__":
    asyncio.run(run())
