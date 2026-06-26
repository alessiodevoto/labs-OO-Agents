# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration-ish tests for the per-turn TUI respond() dispatcher.

These don't spin up a real LLM — they verify the plumbing:
- ``TUIAgent`` has ``_user_messages_in`` (hidden InputQueue) and
  ``user_messages`` (LLM-facing OutputQueue facade) both wired up.
- ``TUIApplication.submit_message`` pushes onto the InputQueue and
  lazy-starts the dispatcher; subsequent pushes flow through without
  restarting.
- The dispatcher reads ``RespondResult.kind`` and dispatches accordingly
  All non-STOP kinds race all channels; DispatcherExit terminates the loop.
- State survives across turns via ``self.v`` (snapshot-backed vars).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from nemo_oo_agents_cli.tui.agent import TUIAgent
from nemo_oo_agents_cli.tui.keep_going import KeepGoingDecision
from nemo_oo_agents_cli.tui.tui_application import DispatcherExit, TUIApplication

from nemo_oo_agents.runtime.channels import Channel, _ChannelReader


def _fresh_agent() -> TUIAgent:
    """TUIAgent with a fake LLM so we don't need API keys."""
    from nemo_oo_agents.unifiedllm import FakeLLMClient

    return TUIAgent(llm=FakeLLMClient())


def test_tui_agent_installs_static_python_tools_context():
    """Verify TUIAgent registers ShellTools and RepoTools docs together."""
    agent = _fresh_agent()
    raw = dict(agent.context_manager._raw_items())

    assert "python_tools" in raw
    assert raw["python_tools"].expr == "doc(RepoTools, ShellTools)"
    assert agent.context_manager.is_static("python_tools") is True
    assert "self.shell" not in raw


def test_tui_agent_wires_repo_to_shell_session():
    """TUIAgent gives RepoTools the same root/session as ShellTools."""
    agent = _fresh_agent()

    assert agent.repo._root == agent.shell.cwd
    assert agent.repo._session is agent.shell._session


def test_doer_agent_installs_static_shell_doc_context():
    """Verify DoerAgent registers the live shell doc as a static expr block."""
    agent = _fresh_agent()
    doer = agent.make_doer()
    raw = dict(doer.context_manager._raw_items())

    assert "self.shell" in raw
    assert raw["self.shell"].expr == "doc(type(self.shell))"
    assert doer.context_manager.is_static("self.shell") is True


# ---------------------------------------------------------------------------
# Agent-side: queue declaration
# ---------------------------------------------------------------------------


def test_tui_agent_has_input_and_output_queue_for_user_messages():
    agent = _fresh_agent()
    # Hidden InputQueue with the full producer/dispatcher API.
    assert isinstance(agent._user_messages_in, Channel)
    assert agent._user_messages_in.name == "user_messages"
    # LLM-facing OutputQueue facade — same name, just .get() / .name.
    assert isinstance(agent.user_messages, _ChannelReader)
    assert agent.user_messages.name == "user_messages"


def test_self_v_proxy_round_trips_through_vars_dict():
    """``self.v.foo = "bar"`` lands in ``self.vars["foo"]`` and reads
    back via the proxy. Same shape as ``Todo.v`` / ``TodoVars``."""
    import pytest as _pytest

    agent = _fresh_agent()
    agent.v.spec = "bug-fix-plan"
    agent.v.cursor = 7

    assert agent.vars["spec"] == "bug-fix-plan"
    assert agent.vars["cursor"] == 7
    assert agent.v.spec == "bug-fix-plan"
    assert "spec" in agent.v
    del agent.v.spec
    assert "spec" not in agent.vars
    with _pytest.raises(AttributeError, match="nonexistent"):
        _ = agent.v.nonexistent


def test_self_v_visible_to_doc():
    """``self.v`` / ``self.vars`` is part of the LLM-facing surface so
    the agent knows where to put persistent variables."""
    from nemo_oo_agents.agentdoc import doc

    agent = _fresh_agent()
    api = doc(agent)
    assert "vars" in api or "self.v" in api


def test_tuiagent_docstring_explains_self_v_persistence():
    """The agent's docstring documents the persistence ladder so the
    LLM knows REPL locals (cleared every turn) vs ``self.v``
    (snapshot-backed, survives sessions) vs ``self.todo.<t>.v``
    (per-todo). Locks the prompt language."""
    agent = _fresh_agent()
    docstring = type(agent).__doc__ or ""
    assert "self.v" in docstring


def test_tuiagent_docstring_encourages_cell_intent_comments():
    """The agent's docstring asks for a ``# Doing X next`` comment at
    the top of each ``execute_python`` cell — the TUI surfaces these
    so the user can follow what the agent is doing live."""
    agent = _fresh_agent()
    docstring = type(agent).__doc__ or ""
    assert "Doing" in docstring or "doing next" in docstring.lower()


def test_user_messages_queue_is_hidden_from_doc():
    """The hidden InputQueue must NOT appear in the LLM's API listing."""
    from nemo_oo_agents.agentdoc import doc

    agent = _fresh_agent()
    api = doc(agent)
    assert "_user_messages_in" not in api
    # The OutputQueue facade IS visible — that's the LLM-facing handle.
    assert "user_messages" in api


def test_tui_agent_uses_cached_block_formatter_by_default():
    """``RenderConfig`` defaults to ``CachedBlockFormatter`` so static
    blocks (system prompt, doc(self), anything registered via
    ``set_static()``) land in a stable SYSTEM prefix the provider
    can cache across turns.
    """
    from nemo_oo_agents.context_blocks.renderers import CachedBlockFormatter

    agent = _fresh_agent()
    assert isinstance(agent.render_config.block_formatter, CachedBlockFormatter)


def test_tui_agent_caller_can_override_render_config():
    """Explicit ``render_config=`` wins over the CachedBlockFormatter default."""
    from nemo_oo_agents.context_blocks.formatter import XMLBlockFormatter
    from nemo_oo_agents.context_blocks.render_config import RenderConfig
    from nemo_oo_agents.unifiedllm import FakeLLMClient

    explicit = RenderConfig(block_formatter=XMLBlockFormatter())
    agent = TUIAgent(llm=FakeLLMClient(), render_config=explicit)
    assert isinstance(agent.render_config.block_formatter, XMLBlockFormatter)


# ---------------------------------------------------------------------------
# Queue status is surfaced through a dynamic context block
# ---------------------------------------------------------------------------


def test_queues_dynamic_block_is_registered():
    """``BaseTUIAgent`` registers ``queues`` as a dynamic context block
    so the LLM sees pending counts every turn, composed across all
    queue-mode channels in the manager."""
    from nemo_oo_agents.context_blocks.models import DynamicContext

    agent = _fresh_agent()
    entry = dict(agent.context_manager._raw_items()).get("queues")
    assert isinstance(entry, DynamicContext)
    assert entry.expr == "self.queue_manager.status()"


def test_queues_status_empty_when_all_channels_drained():
    agent = _fresh_agent()
    # No pending items → empty string (keeps the rendered block quiet).
    assert agent.queue_manager.status() == ""


def test_queues_status_lists_pending_per_channel():
    agent = _fresh_agent()
    agent._user_messages_in.put("a")
    agent._user_messages_in.put("b")
    # Add a second queue-mode channel to verify multi-channel formatting.
    jobs = agent.queue_manager.queue("job_outputs")
    jobs.put({"id": 1})

    status = agent.queue_manager.status()
    # One line per non-empty channel, stable enough to assert substrings.
    assert "user_messages: 2 pending" in status
    assert "job_outputs: 1 pending" in status


def test_queues_status_skips_empty_channels():
    agent = _fresh_agent()
    agent.queue_manager.queue("job_outputs")  # registered but empty
    agent._user_messages_in.put("only-this-one")

    status = agent.queue_manager.status()
    assert "user_messages" in status
    assert "job_outputs" not in status


def test_queues_status_composes_each_non_empty_channel():
    """``QueueManager.status()`` delegates the per-channel rendering
    to each channel's ``status()`` and joins the non-empty ones.
    Detailed formatting (numbered items, overflow, newline flattening,
    non-str preview) is covered in test_channels.py — this test just
    verifies composition."""
    agent = _fresh_agent()
    agent._user_messages_in.put("hi")
    jobs = agent.queue_manager.queue("job_outputs")
    jobs.put({"id": 1})
    agent.queue_manager.queue("idle")  # stays empty → dropped

    status = agent.queue_manager.status(max_items=3, max_chars=80)
    assert agent._user_messages_in.status(max_items=3, max_chars=80) in status
    assert jobs.status(max_items=3, max_chars=80) in status
    assert "idle" not in status


def test_tui_agent_framework_blocks_split_class_vs_instance_state():
    """``self`` (``doc(type(self))``) is class-level docs — genuinely stable,
    stays in the cached prefix. ``state`` (``pformat(self, ...)``) is
    instance state — skills attach at runtime, so it must be volatile.
    """
    from nemo_oo_agents.context_blocks import DynamicContext
    from nemo_oo_agents.strategies import CodeActStrategy

    agent = _fresh_agent()
    cm = agent.context_manager

    # Framework blocks should be registered as protected
    assert "self" in cm.protected_keys
    assert "state" in cm.protected_keys
    assert "system_prompt" in cm.protected_keys

    # Class-level doc — static, cacheable.
    self_block = cm._blocks["self"]
    assert isinstance(self_block, DynamicContext)
    assert cm.is_static("self") is True, (
        "doc(type(self)) is class-level and must go in the cached prefix"
    )
    assert self_block.expr == "doc(type(self))"

    # Instance state — dynamic, picks up runtime-attached skills.
    state_block = cm._blocks["state"]
    assert isinstance(state_block, DynamicContext)
    assert cm.is_static("state") is False
    assert "pformat(self" in state_block.expr

    # system_prompt is static — stable by construction.
    sp_block = cm._blocks["system_prompt"]
    assert isinstance(sp_block, DynamicContext)
    assert cm.is_static("system_prompt") is True

    # Strategy override ``strategy_prompt`` — stock CodeActStrategy
    # provides a strategy_prompt block.
    overrides = CodeActStrategy().get_block_overrides()
    assert "strategy_prompt" in overrides


# ---------------------------------------------------------------------------
# OutputQueue facade
# ---------------------------------------------------------------------------


def test_output_queue_exposes_only_get_and_name():
    """OutputQueue's public surface is intentionally tiny."""
    agent = _fresh_agent()
    out = agent.user_messages
    public = {n for n in dir(out) if not n.startswith("_")}
    assert "get" in public
    assert "name" in public
    # Producer / dispatcher methods stay on the InputQueue, not here.
    for forbidden in ("put", "snapshot", "qsize", "pop_last", "has_waiters"):
        assert forbidden not in public


async def _await_get_via_facade():
    agent = _fresh_agent()
    agent._user_messages_in.put("hi")
    msg = await agent.user_messages.get()
    return msg


def test_output_queue_get_delegates_to_input_queue():
    assert asyncio.run(_await_get_via_facade()) == "hi"


# ---------------------------------------------------------------------------
# TUIApplication dispatcher integration (no actual run_async)
# ---------------------------------------------------------------------------


class _DispatchResult:
    """Minimal duck of ``RespondResult`` — the dispatcher reads .kind."""

    def __init__(self, kind: str, explanation: str = "") -> None:
        self.kind = kind
        self.explanation = explanation


def test_submit_message_pushes_to_queue_and_starts_dispatcher():
    """``submit_message`` pushes onto the agent's InputQueue; the
    dispatcher lazy-starts, consumes the message, and calls
    ``respond((name, item))``."""

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    calls: list[dict] = []

    async def _respond(notification):
        calls.append(notification)
        # Immediately STOP so the dispatcher exits cleanly after one turn.
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    assert agent._user_messages_in.qsize() == 0
    assert app._agent_task is None

    async def _run():
        app.submit_message("hello")
        assert agent._user_messages_in.snapshot() == ["hello"]
        assert app._agent_task is not None
        await app._agent_task
        assert len(calls) == 1
        assert calls[0] == {"user_messages": ["hello"]}

    asyncio.run(_run())


def test_second_message_does_not_spawn_second_dispatcher():
    """While the dispatcher task is alive, additional pushes just go
    onto the queue — no parallel respond() task."""

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def _respond(notification):
        started.set()
        await proceed.wait()
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(started.wait(), timeout=0.5)
        first_task = app._agent_task
        assert first_task is not None and not first_task.done()

        app.submit_message("second")
        assert agent._user_messages_in.snapshot() == ["second"]
        assert app._agent_task is first_task

        proceed.set()
        await first_task

    asyncio.run(_run())


def test_tui_agent_has_system_messages_queue():
    """Base TUI agents expose system_messages for TUI-owned internal prompts."""

    agent = _fresh_agent()

    assert isinstance(agent._system_messages_in, Channel)
    assert agent._system_messages_in.name == "system_messages"
    assert isinstance(agent.system_messages, _ChannelReader)
    assert agent.system_messages.name == "system_messages"
    assert "system_messages" in agent.queue_manager.names()


def test_dispatcher_keep_going_queues_assistant_message_before_waiting(monkeypatch):
    """When /keep-going is on, a DONE audit prompt arrives via system_messages."""

    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()
    config = MagicMock()
    config.tui = MagicMock()
    config.tui.goal_mode = False
    config.tui.keep_going = True
    config.tui.keep_going_model = "test-keep-going-model"
    app = TUIApplication(agent=agent, config=config)

    calls: list[dict] = []

    async def fake_build_keep_going_prompt(agent_arg, result, *, model):
        assert agent_arg is agent
        assert result.kind == "DONE"
        assert model == "test-keep-going-model"
        return SimpleNamespace(prompt="[keep-going] continue", display_reason="open todo remains")

    monkeypatch.setattr(keep_going_mod, "build_keep_going_prompt", fake_build_keep_going_prompt)

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="DONE")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(app._agent_task, timeout=1.0)
        assert calls[0] == {"user_messages": ["first"]}
        assert calls[1] == {"system_messages": ["[keep-going] continue"]}

    asyncio.run(_run())


def test_dispatcher_keep_going_uses_agent_sticky_var(monkeypatch):
    """A restored agent sticky var can enable keep-going even when config default is off."""

    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()
    agent.vars["tui_keep_going"] = True
    agent.vars["tui_keep_going_model"] = "sticky-model"
    config = MagicMock()
    config.tui = MagicMock()
    config.tui.goal_mode = False
    config.tui.keep_going = False
    app = TUIApplication(agent=agent, config=config)

    calls: list[dict] = []

    async def fake_build_keep_going_prompt(agent_arg, result, *, model):
        assert model == "sticky-model"
        return SimpleNamespace(
            prompt="[keep-going] sticky continue", display_reason="sticky work remains"
        )

    monkeypatch.setattr(keep_going_mod, "build_keep_going_prompt", fake_build_keep_going_prompt)

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="DONE")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(app._agent_task, timeout=1.0)
        assert calls[0] == {"user_messages": ["first"]}
        assert calls[1] == {"system_messages": ["[keep-going] sticky continue"]}

    asyncio.run(_run())


def test_dispatcher_keep_going_audit_does_not_block_next_user_message(monkeypatch):
    """A slow keep-going audit is background work; the next user message is handled immediately."""

    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()
    config = MagicMock()
    config.tui = MagicMock()
    config.tui.goal_mode = False
    config.tui.keep_going = True
    config.tui.keep_going_model = "slow-audit-model"
    app = TUIApplication(agent=agent, config=config)

    calls: list[dict] = []
    audit_started = asyncio.Event()
    release_audit = asyncio.Event()

    async def fake_build_keep_going_prompt(agent_arg, result, *, model):
        assert model == "slow-audit-model"
        audit_started.set()
        await release_audit.wait()
        return SimpleNamespace(
            prompt="[keep-going] delayed continue", display_reason="delayed audit"
        )

    monkeypatch.setattr(keep_going_mod, "build_keep_going_prompt", fake_build_keep_going_prompt)

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="DONE")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(audit_started.wait(), timeout=0.5)
        app.submit_message("second")
        await asyncio.wait_for(app._agent_task, timeout=1.0)
        assert calls == [{"user_messages": ["first"]}, {"user_messages": ["second"]}]
        release_audit.set()
        for task in list(app._keep_going_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())


def test_dispatcher_discards_stale_keep_going_audit_after_new_user_message(monkeypatch):
    """A previous DONE audit must not enqueue a stale system prompt after a newer user turn."""

    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()
    config = MagicMock()
    config.tui = MagicMock()
    config.tui.goal_mode = False
    config.tui.keep_going = True
    config.tui.keep_going_model = "slow-audit-model"
    app = TUIApplication(agent=agent, config=config)

    calls: list[dict] = []
    audit_started = asyncio.Event()
    release_audit = asyncio.Event()

    async def fake_build_keep_going_prompt(agent_arg, result, *, model):
        audit_started.set()
        await release_audit.wait()
        return SimpleNamespace(prompt="[keep-going] stale", display_reason="stale audit")

    monkeypatch.setattr(keep_going_mod, "build_keep_going_prompt", fake_build_keep_going_prompt)

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="DONE")
        if len(calls) == 2:
            return _DispatchResult(kind="WAIT")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(audit_started.wait(), timeout=0.5)
        app.submit_message("second")
        for _ in range(50):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0)
        assert calls == [{"user_messages": ["first"]}, {"user_messages": ["second"]}]
        release_audit.set()
        await asyncio.sleep(0.05)
        assert calls == [{"user_messages": ["first"]}, {"user_messages": ["second"]}]
        app._agent_task.cancel()
        try:
            await app._agent_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_keep_going_judge_output_type_is_minimal():
    """The judge output stays simple so small models are less likely to emit invalid JSON."""

    assert set(KeepGoingDecision.model_fields) == {"should_reprompt", "reason", "next_action"}


def test_build_keep_going_prompt_shapes_reason_and_next_action(monkeypatch):
    """Prompt builder turns a minimal judge decision into system prompt + one-line reason."""

    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()

    async def fake_judge_keep_going(**kwargs):
        assert kwargs["model"] == "audit-model"
        return KeepGoingDecision(
            should_reprompt=True,
            reason="open todos remain",
            next_action="Finish the open todos.",
        )

    monkeypatch.setattr(keep_going_mod, "judge_keep_going", fake_judge_keep_going)

    async def _run():
        prompt = await keep_going_mod.build_keep_going_prompt(
            agent, _DispatchResult(kind="DONE"), model="audit-model"
        )
        assert prompt is not None
        assert prompt.display_reason == "open todos remain"
        assert "Reason: open todos remain" in prompt.prompt
        assert "Next action: Finish the open todos." in prompt.prompt

    asyncio.run(_run())


def test_build_keep_going_prompt_skips_false_decision(monkeypatch):
    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()

    async def fake_judge_keep_going(**kwargs):
        return KeepGoingDecision(should_reprompt=False)

    monkeypatch.setattr(keep_going_mod, "judge_keep_going", fake_judge_keep_going)

    async def _run():
        assert (
            await keep_going_mod.build_keep_going_prompt(agent, _DispatchResult(kind="DONE"))
            is None
        )

    asyncio.run(_run())


def test_build_keep_going_prompt_skips_non_done_result():
    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    async def _run():
        assert (
            await keep_going_mod.build_keep_going_prompt(
                _fresh_agent(), _DispatchResult(kind="NEED_INPUT")
            )
            is None
        )

    asyncio.run(_run())


def test_dispatcher_keep_going_audit_failure_is_user_visible(monkeypatch):
    """If structured-output parsing fails, show a one-line keep-going failure instead of only logging."""

    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()
    config = MagicMock()
    config.tui = MagicMock()
    config.tui.goal_mode = False
    config.tui.keep_going = True
    config.tui.keep_going_model = "broken-audit-model"
    outputs: list[Any] = []
    app = TUIApplication(agent=agent, config=config, on_output=outputs.append)

    async def fake_build_keep_going_prompt(agent_arg, result, *, model):
        raise RuntimeError("Structured output validation failed after 2 attempts.")

    monkeypatch.setattr(keep_going_mod, "build_keep_going_prompt", fake_build_keep_going_prompt)

    async def _respond(notification):
        if len(outputs) == 0:
            return _DispatchResult(kind="DONE")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        for _ in range(50):
            await asyncio.sleep(0)
            if len(outputs) >= 2:
                break
        assert any(
            getattr(output, "display_text", lambda: "")()
            == "∴ keep going: judge failed: Structured output validation failed after 2 attempts.; use /keep-going off or /keep-going model <model-id>"
            for output in outputs
        )
        app._agent_task.cancel()
        try:
            await app._agent_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_dispatcher_keep_going_enabled_without_model_does_not_audit(monkeypatch):
    """Layered config can enable keep-going with null model; dispatcher should not call model 'None'."""

    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()
    config = MagicMock()
    config.tui = MagicMock()
    config.tui.goal_mode = False
    config.tui.keep_going = True
    config.tui.keep_going_model = None
    outputs: list[Any] = []
    app = TUIApplication(agent=agent, config=config, on_output=outputs.append)

    async def fake_build_keep_going_prompt(agent_arg, result, *, model):
        raise AssertionError("keep-going audit should not run without a configured model")

    monkeypatch.setattr(keep_going_mod, "build_keep_going_prompt", fake_build_keep_going_prompt)

    async def _respond(notification):
        return _DispatchResult(kind="DONE")

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        for _ in range(50):
            await asyncio.sleep(0)
            if outputs:
                break
        assert any(
            getattr(output, "display_text", lambda: "")()
            == "∴ keep going: disabled: configure a model with /keep-going model <model-id>"
            for output in outputs
        )
        app._agent_task.cancel()
        try:
            await app._agent_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_dispatcher_keep_going_off_does_not_audit(monkeypatch):
    """When /keep-going is off, DONE proceeds to the unified queue race."""

    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui import keep_going as keep_going_mod

    agent = _fresh_agent()
    agent._jobs_in = agent.queue_manager.queue("job_outputs")
    agent.job_outputs = agent._jobs_in.reader
    config = MagicMock()
    config.tui = MagicMock()
    config.tui.goal_mode = False
    config.tui.keep_going = False
    app = TUIApplication(agent=agent, config=config)

    calls: list[dict] = []

    async def fake_build_keep_going_prompt(agent_arg, result, *, model):
        raise AssertionError("keep-going audit should not run when disabled")

    monkeypatch.setattr(keep_going_mod, "build_keep_going_prompt", fake_build_keep_going_prompt)

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="DONE")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        for _ in range(5):
            await asyncio.sleep(0)
        agent._jobs_in.put({"id": 7})
        await asyncio.wait_for(app._agent_task, timeout=1.0)
        assert calls[0] == {"user_messages": ["first"]}
        assert calls[1] == {"job_outputs": [{"id": 7}]}

    asyncio.run(_run())


def test_dispatcher_done_wakes_on_dynamically_registered_queue():
    """Dynamically registered queues participate in the unified wake path."""

    agent = _fresh_agent()
    agent._internal_prompts_in = agent.queue_manager.queue("internal_prompts")
    agent.internal_prompts = agent._internal_prompts_in.reader
    app = TUIApplication(agent=agent)

    calls: list[dict] = []

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="DONE")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        for _ in range(5):
            await asyncio.sleep(0)
        agent._internal_prompts_in.put("continue internally")
        await asyncio.wait_for(app._agent_task, timeout=1.0)
        assert calls[0] == {"user_messages": ["first"]}
        assert calls[1] == {"internal_prompts": ["continue internally"]}

    asyncio.run(_run())


def test_dispatcher_need_input_wakes_on_dynamically_registered_queue():
    """NEED_INPUT uses the same queue race path as every other stop reason."""

    agent = _fresh_agent()
    agent._internal_prompts_in = agent.queue_manager.queue("internal_prompts")
    agent.internal_prompts = agent._internal_prompts_in.reader
    app = TUIApplication(agent=agent)

    calls: list[dict] = []

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="NEED_INPUT")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        for _ in range(5):
            await asyncio.sleep(0)
        agent._internal_prompts_in.put("clarify internally")
        await asyncio.wait_for(app._agent_task, timeout=1.0)
        assert calls[0] == {"user_messages": ["first"]}
        assert calls[1] == {"internal_prompts": ["clarify internally"]}

    asyncio.run(_run())


def test_dispatcher_done_wakes_on_job_queue_via_unified_race_path():
    """DONE uses the same QueueManager.race wake path as other stop reasons."""

    agent = _fresh_agent()
    agent._jobs_in = agent.queue_manager.queue("job_outputs")
    agent.job_outputs = agent._jobs_in.reader
    app = TUIApplication(agent=agent)

    calls: list[dict] = []

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="DONE")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        for _ in range(5):
            await asyncio.sleep(0)
        agent._jobs_in.put({"id": 7})
        await asyncio.wait_for(app._agent_task, timeout=1.0)
        assert calls[0] == {"user_messages": ["first"]}
        assert calls[1] == {"job_outputs": [{"id": 7}]}

    asyncio.run(_run())


def test_dispatcher_emits_stop_reason_as_structured_output():
    """When handle() returns an explanation, the dispatcher emits a
    StopReasonOutput through the structured output callback.
    """

    agent = _fresh_agent()
    outputs: list[Any] = []
    app = TUIApplication(agent=agent, on_output=outputs.append)
    blocks: list[str] = []
    app.emit_block = blocks.append  # type: ignore[method-assign]

    async def _respond(notification):
        return _DispatchResult(
            kind="NEED_INPUT",
            explanation="need the target branch before pushing the MR",
        )

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("hello")
        for _ in range(50):
            await asyncio.sleep(0)
            if outputs:
                break
        assert len(outputs) == 1
        assert outputs[0].to_json() == {
            "type": "stop_reason",
            "kind": "NEED_INPUT",
            "label": "need input",
            "explanation": "need the target branch before pushing the MR",
            "text": "∴ need input: need the target branch before pushing the MR",
        }
        assert blocks == []
        app._agent_task.cancel()
        try:
            await app._agent_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_dispatcher_awaits_async_stop_reason_output_callback():
    """Async structured output callbacks complete before the dispatcher waits."""

    agent = _fresh_agent()
    outputs: list[Any] = []
    callback_finished = asyncio.Event()

    async def _on_output(output):
        await asyncio.sleep(0)
        outputs.append(output)
        callback_finished.set()

    app = TUIApplication(agent=agent, on_output=_on_output)

    async def _respond(notification):
        return _DispatchResult(kind="NEED_INPUT", explanation="need user approval")

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(callback_finished.wait(), timeout=0.5)
        assert outputs[0].display_text() == "∴ need input: need user approval"
        for _ in range(20):
            if agent._user_messages_in.has_waiters():
                break
            await asyncio.sleep(0)
        assert agent._user_messages_in.has_waiters()
        app._agent_task.cancel()
        try:
            await app._agent_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_dispatcher_done_uses_unified_race_path_for_job_queues():
    """DONE is a stop reason, not a special user-message-only wait path."""

    agent = _fresh_agent()
    agent._jobs_in = agent.queue_manager.queue("job_outputs")
    agent.job_outputs = agent._jobs_in.reader
    app = TUIApplication(agent=agent)

    calls: list[dict] = []

    async def _respond(notification):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="DONE", explanation="finished the request")
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        for _ in range(20):
            await asyncio.sleep(0)
        agent._jobs_in.put({"id": 7})
        await asyncio.wait_for(app._agent_task, timeout=1.0)
        assert calls[1] == {"job_outputs": [{"id": 7}]}

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# submit_message merge semantics (no events — just deque shape)
# ---------------------------------------------------------------------------


def test_three_consecutive_submits_while_busy_merge_into_one_item():
    """Three Enters typed while the dispatcher is busy compose a single
    multi-line queue item, separated by ``\\n``.
    """
    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    proceed = asyncio.Event()

    async def _respond(notification):
        # Block forever so the dispatcher stays inside respond() and
        # subsequent submit_messages all hit the slow-merge path.
        await proceed.wait()
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("one")
        # Let the dispatcher start and consume "one" so we KNOW the
        # subsequent submits land on a fresh queue (not the fast path).
        for _ in range(20):
            await asyncio.sleep(0)
            if app.is_thinking():
                break
        # Three more Enters while respond is blocked → all merge.
        app.submit_message("two")
        app.submit_message("three")
        app.submit_message("four")
        assert agent._user_messages_in.snapshot() == ["two\nthree\nfour"]
        proceed.set()
        await app._agent_task

    asyncio.run(_run())


def test_is_thinking_false_when_dispatcher_blocked_on_queue():
    """Between turns the dispatcher is awaiting ``_user_messages_in.get()``.
    During that await ``has_waiters()`` is True and ``is_thinking()``
    returns False — the agent is idle, not generating."""

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    async def _respond(notification):
        return _DispatchResult(kind="GET_USER_INPUT")

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("hello")
        for _ in range(20):
            await asyncio.sleep(0)
            if agent._user_messages_in.has_waiters():
                break
        assert agent._user_messages_in.has_waiters()
        assert app.is_thinking() is False
        app._agent_task.cancel()
        try:
            await app._agent_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_is_thinking_true_during_mid_turn_drain():
    """When the agent's ``respond()`` does ``await self.user_messages.get()``
    mid-turn (clarification flow), the queue has a waiter — but the
    agent is still thinking, not idle. The dispatcher's ``_in_respond``
    flag must report True for the duration.

    Regression guard for the bug where ``is_thinking()`` consulted
    ``has_waiters()`` and returned False during mid-turn drain, dropping
    the spinner while the agent was genuinely working.
    """

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    in_drain = asyncio.Event()
    proceed = asyncio.Event()

    async def _respond(notification):
        # Mid-turn drain: agent waits for the next user message.
        in_drain.set()
        await proceed.wait()
        # Pull from the LLM-facing facade — same path the agent would
        # use in ``execute_python``.
        await agent.user_messages.get()
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(in_drain.wait(), timeout=0.5)
        # Inside respond, before the mid-turn get() is even reached.
        assert app.is_thinking() is True
        # Now release respond — it'll await user_messages.get(), which
        # registers a waiter on the queue. Submit a second message and
        # confirm is_thinking stays True throughout.
        proceed.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if agent._user_messages_in.has_waiters():
                break
        assert agent._user_messages_in.has_waiters(), (
            "agent should be suspended on user_messages.get()"
        )
        # The bug under regression: previously is_thinking() returned
        # False here because has_waiters() was True.
        assert app.is_thinking() is True
        app.submit_message("answer")
        await app._agent_task

    asyncio.run(_run())


def test_dispatcher_does_not_call_on_get_hook_directly():
    """Locks the docstring-stated invariant: the dispatcher loop does
    NOT call the InputQueue's ``on_get`` hook itself; it relies on the
    hook firing from inside ``get()``. A future refactor that "helps"
    by also calling the hook from the loop would double-fire the
    user-bar render and the ``TUIUserInput`` log.
    """

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    fires: list[str] = []
    agent._user_messages_in.set_on_get(fires.append)

    async def _respond(notification):
        raise DispatcherExit()

    object.__setattr__(agent, "handle", _respond)  # bypass guard for test mock

    async def _run():
        app.submit_message("once")
        await app._agent_task
        # Exactly one fire — through get(), not double-fired by the loop.
        assert fires == ["once"]

    asyncio.run(_run())


def test_coalesce_string_into_queue_non_string_tail_preserves_both():
    """When the trailing queued item is non-string,
    ``_coalesce_string_into_queue`` must not merge — it pushes the
    non-string tail back ahead of the new string. Tests the helper
    directly (no dispatcher start) so we exercise the non-string
    branch without needing an event loop.
    """
    from nemo_oo_agents_cli.tui.tui_application import _coalesce_string_into_queue

    from nemo_oo_agents.runtime.channels import Channel

    inq: Channel[Any] = Channel("user_messages", "queue")
    inq.put({"job_id": 7})
    _coalesce_string_into_queue(inq, "hi")

    assert inq.snapshot() == [{"job_id": 7}, "hi"]


def test_coalesce_string_into_queue_string_tail_merges():
    """String tail → coalesce with newline separator (the typical UX
    case: user types, Enter, types more, Enter while busy)."""
    from nemo_oo_agents_cli.tui.tui_application import _coalesce_string_into_queue

    from nemo_oo_agents.runtime.channels import Channel

    inq: Channel[str] = Channel("user_messages", "queue")
    inq.put("first line")
    _coalesce_string_into_queue(inq, "second line")

    assert inq.snapshot() == ["first line\nsecond line"]


def test_coalesce_string_into_queue_empty_queue():
    """Empty queue → just put the new string."""
    from nemo_oo_agents_cli.tui.tui_application import _coalesce_string_into_queue

    from nemo_oo_agents.runtime.channels import Channel

    inq: Channel[str] = Channel("user_messages", "queue")
    _coalesce_string_into_queue(inq, "alone")
    assert inq.snapshot() == ["alone"]


def test_submit_message_dropped_when_agent_lacks_queue():
    """Defensive branch when the agent has no ``_user_messages_in``:
    emit a diagnostic block and start no dispatcher. Locks the
    diagnostic message so refactors don't silently drop it.
    """

    class _BareAgent:
        pass

    app = TUIApplication(agent=_BareAgent())
    blocks: list[str] = []
    app.emit_block = blocks.append  # type: ignore[method-assign]

    app.submit_message("hi")

    assert any("submit_message dropped" in b for b in blocks)
    assert app._agent_task is None
