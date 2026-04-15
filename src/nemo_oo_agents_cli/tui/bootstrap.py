# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared bootstrap for terminal and web frontends.

Both ``main.py`` (terminal) and ``web/server.py`` call ``bootstrap()`` to
create the agent, storage, session manager, tracing, and registry.  The
frontend is plugged in afterwards — that's the only thing that differs.

This makes it structurally impossible for a feature to exist in one frontend
but not the other: if it's in bootstrap, both get it.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .output import Output

if TYPE_CHECKING:
    from nemo_oo_agents import Agent

    from .commands import CommandRegistry
    from .config import Config
    from .frontend import Frontend
    from .session import Session
    from .session_manager import SessionManager


@dataclass
class BootstrapResult:
    """Everything produced by bootstrap — ready to wire to any frontend."""

    config: "Config"
    agent: "Agent"
    session_manager: "SessionManager | None"
    tracing_enabled: bool
    resumed: bool
    session_id: str | None
    # Messages accumulated during bootstrap (errors, warnings, info).
    # The caller renders them through its frontend after bootstrap returns.
    messages: list[Output] = field(default_factory=list)


_CONFIG_TOML_TEMPLATE = """\
# NeMo OO Agents TUI — project-local configuration
# Place this file at .nemo_oo_agents/config.toml to override defaults.
# All keys are optional; omit any you don't want to change.

[tui]
# LLM model (from unifiedllm registry)
# model = "{default_model}"

# Write trace files to this directory (relative to project root).
# Omit or comment out to use OTLP auto-probe only (no files written).
# trace = ".nemo_oo_agents/traces"

# Show agent Python code execution panels
# python = false

# Vi keybindings in the prompt input
# vi = false
"""


def _scaffold_project_dir(config: "Config") -> None:
    """Create .nemo_oo_agents/ and write a config.toml template on first run."""
    from nemo_oo_agents.paths import get_project_dir

    project_dir = get_project_dir()
    project_dir.mkdir(exist_ok=True)

    config_path = project_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(_CONFIG_TOML_TEMPLATE.format(default_model=config.tui.default_model))


async def bootstrap(
    config: "Config",
    *,
    continue_last: bool = False,
    resume_session_id: str | None = None,
    agent: "Agent | None" = None,
) -> BootstrapResult:
    """Create agent, storage, session manager, and tracing.

    Args:
        config: Loaded Config instance.
        continue_last: Resume the most recent session with turns.
        resume_session_id: Resume a specific session by ID/prefix.
        agent: Optional pre-built agent (skips LLM/storage/tracing init).

    Returns:
        BootstrapResult with everything needed to create a Session.
    """
    from .output import TextOutput

    messages: list[Output] = []
    tracing_enabled = False
    agent_storage = None
    _session_id: str | None = None
    _resumed = False

    if agent is not None:
        # Agent provided externally — skip all init
        return BootstrapResult(
            config=config,
            agent=agent,
            session_manager=None,
            tracing_enabled=False,
            resumed=False,
            session_id=None,
            messages=[],
        )

    # ------------------------------------------------------------------
    # Project directory — scaffold .nemo_oo_agents/ on first run
    # ------------------------------------------------------------------
    _scaffold_project_dir(config)

    # ------------------------------------------------------------------
    # Tracing
    # ------------------------------------------------------------------
    _set_trace_session = None  # deferred: called once _session_id is known
    if not config.no_trace:
        try:
            from openinference_instrumentation_nemo_oo_agents import (
                enable_tracing,
                exporters,
                set_session,
            )

            trace_dir = config.tui.trace_dir
            if trace_dir is not None:
                trace_dir.mkdir(parents=True, exist_ok=True)
                enable_tracing(exporters=[exporters.jsonl(trace_dir), exporters.journal()])
            else:
                enable_tracing()
            tracing_enabled = True
            _set_trace_session = set_session
        except ImportError:
            messages.append(
                TextOutput(
                    "Tracing package not installed (openinference-instrumentation-nemo_oo_agents)",
                    "warning",
                )
            )
        except Exception as e:
            messages.append(TextOutput(f"Failed to enable tracing: {e}", "warning"))

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------
    from .config import get_llm

    try:
        llm = get_llm(config)
    except Exception as e:
        messages.append(TextOutput(f"Failed to initialize LLM: {e}", "error"))
        messages.append(TextOutput("Using fake LLM client for testing", "info"))
        from unifiedllm import FakeLLMClient

        llm = FakeLLMClient()

    # ------------------------------------------------------------------
    # Storage — per-session SQLite DB
    # ------------------------------------------------------------------
    import uuid as _uuid

    from nemo_oo_agents.storage import SQLiteStorageManager
    from nemo_oo_agents.storage.sqlite import SessionAlreadyActiveError

    from .session_manager import SESSIONS_DIR, SessionManager

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve which session to open
    if resume_session_id is not None:
        # Explicit session ID
        matches = SessionManager.find_by_prefix(resume_session_id)
        if matches:
            _session_id = matches[0]
            _resumed = True
        else:
            messages.append(
                TextOutput(f"Session '{resume_session_id}' not found, starting new.", "warning")
            )
    elif continue_last:
        # Find most recent session with turns
        all_recent = SessionManager.list_sessions(limit=20)
        for candidate in all_recent:
            if candidate.turn_count > 0:
                _session_id = candidate.id
                _resumed = True
                break

    if _session_id is None:
        _session_id = str(_uuid.uuid4())

    agent_db = SESSIONS_DIR / f"{_session_id}.db"

    try:
        agent_storage = SQLiteStorageManager(agent_db)
    except SessionAlreadyActiveError:
        # Another process owns this session — start a fresh one instead.
        messages.append(
            TextOutput(
                f"Session {_session_id[:8]!r} is already active in another process, starting new.",
                "warning",
            )
        )
        _session_id = str(_uuid.uuid4())
        _resumed = False
        agent_db = SESSIONS_DIR / f"{_session_id}.db"
        agent_storage = SQLiteStorageManager(agent_db)
    except Exception as e:
        messages.append(TextOutput(f"Agent storage unavailable: {e}", "warning"))

    # Name the initial trace session using the SQLite session UUID so the
    # trace file correlates directly to the storage file.
    if _set_trace_session is not None and _session_id is not None:
        from .session_manager import _make_trace_session_name

        _set_trace_session(_make_trace_session_name(_session_id))

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------
    from .agent import TUIAgent

    storage_kwargs = {"storage": agent_storage} if agent_storage is not None else {}

    if config.tui.agent_spec:
        from .config import load_agent_class
        from .theme import COLORS

        try:
            agent_cls = load_agent_class(config.tui.agent_spec)
            agent = agent_cls(llm=llm, **storage_kwargs)
            messages.append(
                TextOutput(
                    f"Loaded custom agent: [{COLORS['green']}]{agent_cls.__name__}[/] "
                    f"from {config.tui.agent_spec}",
                    "info",
                )
            )
        except Exception as e:
            messages.append(
                TextOutput(f"Failed to load agent '{config.tui.agent_spec}': {e}", "error")
            )
            messages.append(TextOutput("Falling back to default TUIAgent", "info"))
            agent = TUIAgent(llm=llm, config=config.agent, **storage_kwargs)
    else:
        agent = TUIAgent(llm=llm, config=config.agent, **storage_kwargs)

    assert agent is not None  # always assigned by one of the branches above

    # ------------------------------------------------------------------
    # Snapshot restore (when resuming)
    # ------------------------------------------------------------------
    if _resumed and agent_storage is not None:
        try:
            restored = agent_storage.restore_latest_snapshot(agent)
            if not restored:
                messages.append(TextOutput("No agent snapshot found in session.", "warning"))
        except Exception as e:
            messages.append(TextOutput(f"Could not restore agent state: {e}", "warning"))

    # ------------------------------------------------------------------
    # Rich content replay (nemo oo term, session resume only)
    # ------------------------------------------------------------------
    # When resuming inside a web terminal, replay stored RichOutput events
    # so the browser panel is restored.  Uses the public event_manager.filter()
    # interface — no direct storage access needed.
    import os as _os

    if _resumed and _os.environ.get("NEMO_RICH_URL"):
        try:
            from nemo_oo_agents.tools.web_publisher import RichOutput
            from nemo_oo_agents.tools.web_publisher import WebPublisher as _WP

            agent.event_manager.register_event_type(RichOutput)
            _rich_events = agent.event_manager.filter(type="RichOutput")
            if _rich_events:
                _replay_wp = _WP()  # no event_manager — replay only, don't re-store
                for _ev in _rich_events:
                    if isinstance(_ev, RichOutput):
                        _replay_wp._post(_ev.payload)
        except Exception as _e:
            messages.append(TextOutput(f"Could not replay rich content: {_e}", "warning"))

    # ------------------------------------------------------------------
    # Session manager
    # ------------------------------------------------------------------
    session_manager: SessionManager | None = None
    if agent_storage is not None:
        try:
            session_manager = SessionManager(
                storage=agent_storage,
                session_id=_session_id,
                model=config.tui.default_model,
                agent_cls=type(agent).__name__,
                working_dir=str(config.agent.working_dir),
                resumed=_resumed,
            )
        except Exception as exc:
            messages.append(TextOutput(f"Session persistence unavailable: {exc}", "warning"))

    return BootstrapResult(
        config=config,
        agent=agent,
        session_manager=session_manager,
        tracing_enabled=tracing_enabled,
        resumed=_resumed,
        session_id=_session_id,
        messages=messages,
    )


def build_startup_info(result: BootstrapResult) -> "Output":
    """Build the StartupInfo output from bootstrap results."""
    from .agent import TUIAgent
    from .output import StartupInfo
    from .session import _short_model_name

    config = result.config
    agent = result.agent

    short_model = _short_model_name(config.tui.default_model)

    sandbox_available: bool | None = None
    if hasattr(agent, "bash"):
        sandbox_available = agent.bash.sandbox_available  # type: ignore[union-attr]

    history_policy: str | None = None
    history_limit: int | None = None
    if isinstance(agent, TUIAgent):
        history_policy = config.agent.summarization.policy
        history_limit = config.agent.summarization.max_tokens

    custom_agent_name: str | None = None
    if config.tui.agent_spec and not isinstance(agent, TUIAgent):
        custom_agent_name = type(agent).__name__

    trace_dir_str: str | None = None
    if result.tracing_enabled and config.tui.trace_dir:
        trace_dir_str = str(config.tui.trace_dir)

    return StartupInfo(
        model=config.tui.default_model,
        short_model=short_model,
        working_dir=str(config.agent.working_dir),
        vi_mode=config.tui.vi_mode,
        history_policy=history_policy,
        history_limit=history_limit,
        sandbox_available=sandbox_available,
        tracing_enabled=result.tracing_enabled,
        trace_dir=trace_dir_str,
        custom_agent=custom_agent_name,
    )


def build_registry(
    result: BootstrapResult,
    frontend: "Frontend",
) -> "CommandRegistry":
    """Build the CommandRegistry from bootstrap results + frontend."""
    from .commands import CommandRegistry

    return CommandRegistry(
        config=result.config.tui,
        agent=result.agent,
        frontend=frontend,
        skills_dirs=result.config.tui.skills_dirs,
        mcp_file=result.config.tui.mcp_file,
        session_manager=result.session_manager,
    )


def build_session(
    result: BootstrapResult,
    frontend: "Frontend",
    registry: "CommandRegistry",
) -> "Session":
    """Build the Session from bootstrap results + frontend + registry."""
    from .session import Session

    return Session(
        frontend=frontend,
        agent=result.agent,
        config=result.config,
        registry=registry,
        session_manager=result.session_manager,
    )
