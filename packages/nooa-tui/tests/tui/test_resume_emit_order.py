# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TuiSessionResumed must be emitted AFTER skills attach, so subscribers receive it.

Regression: bootstrap() emitted TuiSessionResumed before library skills (e.g.
agent_mesh) were attached in build_registry(). The event fired into the void —
no subscriber existed yet — so a skill's resume handler never ran. Moving the
emit into build_registry() (after skill activation) fixes the ordering.
"""

import pytest

from nooa.events import TuiSessionResumed


@pytest.mark.asyncio
async def test_emit_happens_after_skill_can_subscribe():
    """A handler subscribed during build_registry() receives the resume event."""
    from nooa_tui.tui.bootstrap import bootstrap, build_registry
    from nooa_tui.tui.config import Config

    result = await bootstrap(Config())

    # Subscribe BEFORE build_registry runs the emit — emulating a skill that
    # attaches during build_registry's library-skill activation.
    seen = []
    result.agent.event_manager.register_event_type(TuiSessionResumed)
    result.agent.event_manager.on("TuiSessionResumed", lambda e: seen.append(e))

    # build_registry needs a frontend; a minimal stub is enough for the emit path.
    from unittest.mock import MagicMock

    build_registry(result, MagicMock())

    assert len(seen) == 1
    assert seen[0].session_id == result.session_id


@pytest.mark.asyncio
async def test_bootstrap_does_not_emit_on_its_own():
    """bootstrap() must NOT emit — otherwise it fires before skills attach."""
    from nooa_tui.tui.bootstrap import bootstrap
    from nooa_tui.tui.config import Config

    from nooa.runtime.event_manager import EventManager

    captured = []
    real_add = EventManager.add

    def _tee(self, event, **kw):
        if isinstance(event, TuiSessionResumed):
            captured.append(event)
        return real_add(self, event, **kw)

    import pytest as _pytest  # noqa: F401

    EventManager.add = _tee
    try:
        await bootstrap(Config())
    finally:
        EventManager.add = real_add

    assert captured == []  # bootstrap itself emits nothing now


def test_bootstrap_result_carries_restored_flag():
    from nooa_tui.tui.bootstrap import BootstrapResult

    assert "restored" in BootstrapResult.__dataclass_fields__
