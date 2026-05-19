"""Test that BashSession and Actor locks survive event loop changes (gl-212)."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_bash_session_lock_survives_loop_change():
    """BashSession._lock recreates transparently when the event loop changes."""
    from nemo_oo_agents.tools._bash_session import BashSession

    session = BashSession(cwd="/tmp")

    # First access binds to current loop
    lock1 = session._lock
    assert isinstance(lock1, asyncio.Lock)

    # Simulate loop change by running in a new loop
    def _get_lock_on_new_loop():
        async def _inner():
            return session._lock
        return asyncio.run(_inner())

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        lock2 = await asyncio.get_running_loop().run_in_executor(pool, _get_lock_on_new_loop)

    # Lock was recreated for the new loop
    assert lock2 is not lock1


@pytest.mark.asyncio
async def test_actor_generation_lock_survives_loop_change():
    """ActorRuntime._generation_lock recreates transparently when the event loop changes."""
    from unittest.mock import MagicMock

    from nemo_oo_agents.runtime.actor import ActorRuntime

    agent = MagicMock()
    agent._truncation = MagicMock()
    actor = ActorRuntime(agent)

    # First access binds to current loop
    lock1 = actor._generation_lock
    assert isinstance(lock1, asyncio.Lock)

    # Simulate loop change
    def _get_lock_on_new_loop():
        async def _inner():
            return actor._generation_lock
        return asyncio.run(_inner())

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        lock2 = await asyncio.get_running_loop().run_in_executor(pool, _get_lock_on_new_loop)

    # Lock was recreated for the new loop
    assert lock2 is not lock1


@pytest.mark.asyncio
async def test_bash_session_lock_same_loop_reuses():
    """On the same loop, the lock is reused (not recreated every access)."""
    from nemo_oo_agents.tools._bash_session import BashSession

    session = BashSession(cwd="/tmp")
    lock1 = session._lock
    lock2 = session._lock
    assert lock1 is lock2
