"""Regression test: resumed sessions must not reuse tag numbers already in the backend.

Bug: After restoring from a snapshot, _next_tag_num was set from the snapshot's
recorded value. If events were added to the backend *after* the snapshot was
taken (e.g. session-close metadata), those events occupy higher tag numbers.
The restored agent would then start assigning from the snapshot value, colliding
with those later events.
"""

from __future__ import annotations

from nemo_oo_agents import Agent
from nemo_oo_agents.events import EventBase
from nemo_oo_agents.storage import SQLiteStorageManager
from unifiedllm import CompletionClient

_LLM = CompletionClient(model="openai/gpt-4o-mini", api_key="test")


class _SimpleAgent(Agent, llm=_LLM):
    value: int = 0


def test_next_tag_num_does_not_collide_after_resume(tmp_path):
    """_next_tag_num after restore must be > every tag already in the backend."""
    db = tmp_path / "session.db"

    # --- first session ---
    storage = SQLiteStorageManager(db)
    agent = _SimpleAgent(storage=storage)

    # Add some events so the tag counter advances
    agent.event_manager.add(EventBase())
    agent.event_manager.add(EventBase())
    agent.event_manager.add(EventBase())
    # tag counter is now at 4; snapshot records next_tag_num=4

    storage.save_snapshot(agent)

    # Simulate events added AFTER the snapshot (e.g. session-close metadata)
    agent.event_manager.add(EventBase())  # tag "4"
    agent.event_manager.add(EventBase())  # tag "5"
    storage.close()

    # --- resumed session ---
    storage2 = SQLiteStorageManager(db)
    agent2 = _SimpleAgent(storage=storage2)
    storage2.restore_latest_snapshot(agent2)

    # The next tag assigned must be strictly greater than 5 (the highest in the backend)
    next_tag = agent2.event_manager._next_tag_num
    assert next_tag > 5, (
        f"_next_tag_num={next_tag} will collide with existing tags up to 5"
    )

    storage2.close()
