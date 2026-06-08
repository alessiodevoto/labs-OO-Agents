# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""slash-inception: spawn a fresh debugging TUI agent that holds the *live*
current agent as ``self.agent`` and hot-swaps the running TUI onto it.

After ``/inception`` the user is talking to a brand-new ``InceptionAgent``
with a clean history. That new agent:

  * holds the previous agent as ``self.agent`` — a *live* Python reference
    (plain assignment, no rehydration). The field is declared
    ``Annotated[Any, nosnapshot]`` so the cyclic live graph never hits a
    snapshot.
  * is a full TUI agent (it has ``handle`` and the whole skill set).
  * knows how to find its debug subject's trace via ``self.trace_url()`` and
    can open it with ``self.trace_explorer_tools``.

How the swap works
------------------
The TUI dispatcher loop binds ``agent = app.agent`` once per loop and then
calls ``agent.handle()`` each turn, so a bare object-reference reassignment
does NOT redirect a running loop. The framework also forbids attaching a
callable to an agent instance (no ``old.handle = new.handle`` delegation).

The supported swap therefore goes through ``TUIApplication.swap_agent(new)``,
which re-points ``app.agent`` and restarts the dispatcher bound to the new
agent. The ``/inception`` skill (which runs on the UI loop and has no app
handle) does the *pure-data* part — build the new agent, share the old
agent's live channels + render callback — and returns a ``SwapAgentRequest``.
``Session._on_command`` (which holds the app) recognizes it, queues the seed
prompt, and calls ``app.swap_agent(new)`` on the agent loop.
"""

import os
from dataclasses import dataclass
from typing import Annotated, Any

from nemo_oo_agents.skill import Skill, slash_command
from nemo_oo_agents.storage.markers import nosnapshot

_SEED_PROMPT = (
    "hey, we ran into problems with this TUI agent, it's a member of you now "
    "(`self.agent`) so can you help debug it?\n\n"
    "You are a debugging agent. Your debug subject is the live agent at "
    "`self.agent` (agent_id `{subject_id}`). You can introspect it directly: "
    "`self.agent.events`, `self.agent.vars`, `self.agent.context`, "
    "`self.runtime.get_code(name)` / `self.runtime.list_methods()`, and its "
    "skills (`self.agent.shell`, `self.agent.repo`, ...).\n\n"
    "To look at traces: call `self.trace_url()` to resolve the subject's trace "
    "location, then load it with `self.trace_explorer_tools` "
    "(`await self.trace_explorer_tools.from_viewer(base_url, session_id)` for a "
    "live viewer, or `await self.trace_explorer_tools.from_file(path)` for the "
    "JSONL file) and use the returned `TraceExplorer` "
    "(`get_overview()`, `get_errors()`, `find_first_error()`, `search(...)`).\n\n"
    "From here, the user can bring you *whatever* they want to debug. Start by "
    "greeting them and confirming you can see the subject agent."
)


@dataclass
class SwapAgentRequest:
    """Sentinel returned by ``/inception`` asking the Session to swap agents.

    ``Session._on_command`` recognizes a ``SlashCommandResult`` whose
    ``.value`` is a ``SwapAgentRequest`` and performs the actual hot-swap via
    ``app.swap_agent(new_agent)`` (on the agent loop), after queuing
    ``seed_prompt`` onto the shared user-messages channel.
    """

    new_agent: Any
    seed_prompt: str
    subject_id: str

    def __str__(self) -> str:  # what the user sees in the TUI
        return (
            f"slash-inception: spawned InceptionAgent holding the previous "
            f"agent (`{self.subject_id}`) as `self.agent` (live, nosnapshot). "
            f"Swapping the TUI onto the debugging agent now…"
        )


def _resolve_inception_agent_cls():
    """Return the ``InceptionAgent`` subclass, defining it lazily.

    Defined lazily (not at import) so the ``TUIAgent`` base + its configured
    LLM are already importable and the class binds to the same default LLM.
    """
    from nemo_oo_agents_cli.tui.agent import _DEFAULT_LLM, TUIAgent

    class InceptionAgent(TUIAgent, llm=_DEFAULT_LLM):  # type: ignore[call-arg]
        """A debugging TUI agent that holds a previous agent as ``self.agent``.

        The held agent is a *live* reference — plain Python assignment, no
        rehydration. It is declared ``nosnapshot`` so the cyclic live graph is
        excluded from persistence (it lives purely in memory for the session).
        """

        # Live debug subject. nosnapshot => the snapshot extractor skips this
        # field entirely, so the cyclic agent-in-agent graph never serializes.
        agent: Annotated[Any, nosnapshot] = None

        def trace_url(self) -> dict:
            """Resolve the debug subject's trace location.

            Returns a dict with ``session_id`` (the subject's agent id), the
            live ``viewer_base_url`` if running under a viewer (``OTLP_ENDPOINT``
            / ``NEMO_RICH_URL``), and the likely ``jsonl_path`` on disk. Use the
            returned values with ``self.trace_explorer_tools``.
            """
            subject = self.agent
            session_id = getattr(subject, "agent_id", None) if subject else None

            # Prefer the live trace-session name when tracing is active (it is
            # what the viewer indexes on; correlated to the SQLite uuid prefix).
            try:
                from nemo_oo_agents.tracing import get_session

                trace_session = get_session() or session_id
            except Exception:  # noqa: BLE001
                trace_session = session_id

            endpoint = os.environ.get("OTLP_ENDPOINT") or os.environ.get("NEMO_RICH_URL")
            viewer_url = None
            if endpoint and trace_session:
                base = endpoint.rstrip("/")
                for suffix in ("/v1/traces", "/v1"):
                    if base.endswith(suffix):
                        base = base[: -len(suffix)]
                        break
                import urllib.parse

                viewer_url = f"{base}/traces/view?session_id={urllib.parse.quote(trace_session)}"

            jsonl_path = None
            if trace_session:
                trace_dir = os.environ.get("TRACE_DIR", "./traces")
                jsonl_path = os.path.join(trace_dir, f"{trace_session}.jsonl")

            return {
                "session_id": session_id,
                "trace_session": trace_session,
                "viewer_url": viewer_url,
                "jsonl_path": jsonl_path,
                "hint": (
                    "await self.trace_explorer_tools.from_viewer(<viewer base>, "
                    "trace_session)  OR  await self.trace_explorer_tools.from_file(jsonl_path)"
                ),
            }

    return InceptionAgent


def _share_runtime_wiring(new: Any, old: Any) -> None:
    """Point the new agent at the old agent's live channels + render callback.

    These are all ``nosnapshot`` fields on ``BaseTUIAgent``; reassigning the
    objects (not copying) keeps the running TUI's input routing + output
    rendering working unchanged when ``app.swap_agent`` restarts the
    dispatcher on the new agent.
    """
    new.queue_manager = old.queue_manager
    new._user_messages_in = old._user_messages_in
    new.user_messages = old.user_messages
    new._slash_commands_in = old._slash_commands_in
    new.slash_commands = old.slash_commands
    new._render_message = getattr(old, "_render_message", None)


class Inception(Skill):
    """slash-inception — spawn a debugging agent that holds the current agent.

    ``/inception`` creates a fresh ``InceptionAgent`` with a clean history,
    assigns the current (live) agent as its ``self.agent`` member, shares the
    running TUI's input channels + render callback, and returns a
    ``SwapAgentRequest`` that ``Session`` turns into a real hot-swap via
    ``app.swap_agent``. After the swap, the user is talking to the new agent.
    """

    @slash_command("inception", argument_hint="")
    async def inception(self, args: str) -> "SwapAgentRequest":
        """Spawn a debugging agent holding the current agent as ``self.agent``."""
        old = self._agent

        new = _resolve_inception_agent_cls()()
        new.agent = old  # live reference (nosnapshot field)
        _share_runtime_wiring(new, old)

        subject_id = getattr(old, "agent_id", "<unknown>")
        return SwapAgentRequest(
            new_agent=new,
            seed_prompt=_SEED_PROMPT.format(subject_id=subject_id),
            subject_id=subject_id,
        )
