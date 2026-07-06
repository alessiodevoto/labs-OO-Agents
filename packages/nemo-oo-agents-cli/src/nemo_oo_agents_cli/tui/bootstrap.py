# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared bootstrap for the NeMo OO Agents TUI.

``main.py`` calls ``bootstrap()`` to create the agent, storage, session
manager, tracing, and registry.  The PTY web terminal (``web/pty_server.py``,
``nemo oo term``) runs this same TUI inside a pseudo-terminal, so it inherits
the identical setup; the frontend is plugged in afterwards.

This makes it structurally impossible for a feature to exist in one run mode
but not the other: if it's in bootstrap, both get it.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .output import Output

logger = logging.getLogger(__name__)

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
    restored: bool
    session_id: str | None
    # Messages accumulated during bootstrap (errors, warnings, info).
    # The caller renders them through its frontend after bootstrap returns.
    messages: list[Output] = field(default_factory=list)


def _scaffold_settings(config: "Config") -> None:
    """Write a commented user-level ``settings.yaml`` on first run.

    Only scaffolds when *no* ``settings.yaml`` exists in any layer
    (user / project / ``NEMO_OO_SETTINGS``), so it never clobbers a file
    the user already has. The scaffold is fully commented, so reading it
    back yields the same defaults.
    """
    from nemo_oo_agents.paths import get_user_dir

    from .settings import SETTINGS_FILENAME, render_settings_template, settings_present

    if settings_present():
        return

    target = get_user_dir(SETTINGS_FILENAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_settings_template(config))


def tui_agent_memory_key(agent: "Agent", config: "Config") -> str:
    """Stable config key for an agent's TUI memory preference."""
    if config.tui.agent_spec:
        return config.tui.agent_spec
    return f"{type(agent).__module__}:{type(agent).__qualname__}"


def resolve_tui_memory_scope(agent: "Agent", config: "Config") -> str:
    """Return the effective TUI memory scope for *agent*."""
    key = tui_agent_memory_key(agent, config)
    return config.tui.memory_agents.get(key, config.tui.memory)


def configure_tui_memory(
    agent: "Agent",
    config: "Config",
    *,
    agent_db,
    session_id: str | None,
) -> None:
    """Install/reinstall the TUI memory skill according to effective config."""
    from pathlib import Path

    key = tui_agent_memory_key(agent, config)
    agent._tui_memory_key = key
    scope = resolve_tui_memory_scope(agent, config)

    existing = getattr(agent, "memory", None)
    if existing is not None and hasattr(existing, "detach"):
        try:
            existing.detach()
        except Exception:
            pass

    if scope == "off":
        if hasattr(agent, "skills"):
            try:
                agent.skills.deactivate(["nemo.memory"])
            except Exception:
                pass
        if hasattr(agent, "memory"):
            try:
                delattr(agent, "memory")
            except Exception:
                pass
        return

    from nemo_oo_agents.memory import MemoryConfig
    from nemo_oo_agents.memory.memory_skill import MemorySkill
    from nemo_oo_agents.paths import get_project_dir

    if scope != "session":
        raise ValueError(f"Unsupported TUI memory scope {scope!r}; use 'off' or 'session'.")

    project_dir = get_project_dir()
    if config.tui.memory_path is not None:
        if config.tui.memory_path.is_absolute():
            raise ValueError("tui.memory_path must be relative to the project directory")
        memory_path = (project_dir / config.tui.memory_path).resolve()
        if (
            project_dir.resolve() not in memory_path.parents
            and memory_path != project_dir.resolve()
        ):
            raise ValueError("tui.memory_path must stay under the project directory")
    else:
        if session_id is None:
            raise RuntimeError("session-scoped memory requires a session id")
        memory_path = Path(agent_db).with_name(f"{session_id}-memory.db")

    memory_config = MemoryConfig(enabled=True, path=str(memory_path))
    agent.skills.register("nemo.memory", MemorySkill(memory_config))
    agent.skills.activate(["nemo.memory"])


_CONFIG_TOML_TEMPLATE = """# NeMo OO Agents project config
[agent]
model = "{default_model}"
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
            restored=False,
            session_id=None,
            messages=[],
        )

    # ------------------------------------------------------------------
    # Settings — scaffold ~/.config/nemo_oo/settings.yaml on first run
    # ------------------------------------------------------------------
    _scaffold_settings(config)

    # ------------------------------------------------------------------
    # LLM registry — populate from project + user + NEMO_OO_LLM_CONFIG.
    # Eagerly explicit (rather than relying on the lazy auto-load in
    # get_llm_client) so the registry is hot before the health check
    # runs and so MODELS readers (TUI /model commands, completer) see
    # populated state immediately.
    #
    # Wrapped: a malformed YAML or unreadable file shouldn't prevent
    # startup. The existing LLM-init fallback further down already
    # handles "no registry config" by passing the model string straight
    # to litellm.
    # ------------------------------------------------------------------
    try:
        from nemo_oo_agents.llm_config import llm_config_chain
        from nemo_oo_agents.secrets import load_secrets_into_env
        from nemo_oo_agents.unifiedllm import reload_registry

        # Push secrets.yaml env vars before the registry loads so the
        # health check sees the right API key. Non-clobbering: an env var
        # already set in the shell wins.
        load_secrets_into_env()
        reload_registry(*llm_config_chain())
    except Exception as e:
        from .output import TextOutput

        messages.append(TextOutput(f"Failed to load LLM registry config: {e}", "warning"))

    # ------------------------------------------------------------------
    # Tracing
    # ------------------------------------------------------------------
    _set_trace_session = None  # deferred: called once _session_id is known
    if not config.no_trace:
        try:
            from nemo_oo_agents.paths import find_project_root, get_project_dir
            from nemo_oo_agents.tracing import (
                enable_tracing,
                exporters,
                set_session,
            )

            trace_dir = config.tui.trace_dir
            if trace_dir is not None:
                if str(trace_dir) == ":project:":
                    trace_dir = get_project_dir("traces")
                elif not trace_dir.is_absolute():
                    trace_dir = find_project_root() / trace_dir
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
        from nemo_oo_agents.unifiedllm import FakeLLMClient

        llm = FakeLLMClient()

    # ------------------------------------------------------------------
    # Health check — verify endpoint is reachable and API key is valid
    # ------------------------------------------------------------------
    from nemo_oo_agents.unifiedllm import FakeLLMClient as _FakeLLMCheck

    if not isinstance(llm, _FakeLLMCheck):
        from .health_check import probe_llm

        _health = await probe_llm(llm)
        if not _health.ok:
            messages.append(TextOutput(f"⚠️  {_health.error_message}", "error"))
            if _health.fix_hint:
                messages.append(TextOutput(_health.fix_hint, "info"))

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
        agent_storage = SQLiteStorageManager(agent_db, check_same_thread=False)
    except SessionAlreadyActiveError as e:
        # Another process owns this session — start a fresh one instead.
        detail = f" (pid {e.owner_pid})" if e.owner_pid is not None else ""
        messages.append(
            TextOutput(
                f"Session {_session_id[:8]!r} is already active in another process{detail}, "
                "starting new.",
                "warning",
            )
        )
        _session_id = str(_uuid.uuid4())
        _resumed = False
        agent_db = SESSIONS_DIR / f"{_session_id}.db"
        agent_storage = SQLiteStorageManager(agent_db, check_same_thread=False)
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
    _actually_restored = False
    if _resumed and agent_storage is not None:
        try:
            _actually_restored = agent_storage.restore_latest_snapshot(agent)
            if not _actually_restored:
                messages.append(TextOutput("No agent snapshot found in session.", "warning"))
        except Exception as e:
            messages.append(TextOutput(f"Could not restore agent state: {e}", "warning"))

    # NOTE: the ``TuiSessionResumed`` event is emitted later, in ``build_registry``
    # — AFTER library skills (e.g. agent_mesh) are attached and have subscribed.
    # Emitting here (before skill attach) would fire into the void: no subscriber
    # exists yet, so a skill's resume handler (e.g. mesh auto-reconnect) would
    # never see it. The ``restored`` flag is carried on BootstrapResult.

    # ------------------------------------------------------------------
    # Rich content replay (nemo oo term, session resume only)
    # ------------------------------------------------------------------
    # When resuming inside a web terminal, replay stored RichOutput events
    # so the browser panel is restored.  Uses the public event_manager.filter()
    # interface — no direct storage access needed.
    import os as _os

    if _resumed and _os.environ.get("NEMO_OO_RICH_URL"):
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
    # Long-term memory
    # ------------------------------------------------------------------
    try:
        configure_tui_memory(agent, config, agent_db=agent_db, session_id=_session_id)
    except Exception as _e:
        messages.append(TextOutput(f"Could not enable memory: {_e}", "warning"))

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
        restored=_actually_restored,
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
        from nemo_oo_agents.paths import get_project_dir

        _td = config.tui.trace_dir
        trace_dir_str = str(get_project_dir("traces") if str(_td) == ":project:" else _td)

    return StartupInfo(
        model=config.tui.default_model,
        short_model=short_model,
        working_dir=str(config.agent.working_dir),
        vi_mode=config.tui.vi_mode,
        history_policy=history_policy,
        history_limit=history_limit,
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

    # Propagate skills_dirs to the agent so Doer subagents can discover them
    result.agent._skills_dirs = result.config.tui.skills_dirs  # type: ignore[attr-defined]

    # Discover library skill packages from configured libs_dirs
    for libs_dir in result.config.tui.libs_dirs:
        if libs_dir.exists():
            result.agent.skills.discover_libs(libs_dir)
    # Activate all discovered library skills (local.* from project libs, plus any prefixed ones)
    discovered = result.agent.skills.discovered()
    lib_patterns = {
        n.split(".")[0] + ".*"
        for n in discovered
        if n not in result.agent.skills.loaded() and n != "nemo.memory"
    }
    if lib_patterns:
        result.agent.skills.activate(list(lib_patterns))

    # Emit TuiSessionResumed now that library skills (e.g. agent_mesh) are
    # attached and have subscribed — emitting earlier (in bootstrap, before skill
    # attach) would fire into the void so a skill's resume handler never sees it.
    if result.session_id is not None:
        try:
            from nemo_oo_agents.events import TuiSessionResumed

            result.agent.event_manager.register_event_type(TuiSessionResumed)
            result.agent.event_manager.add(
                TuiSessionResumed(session_id=result.session_id, restored=result.restored)
            )
        except Exception:
            logger.debug("Failed to emit TuiSessionResumed", exc_info=True)

    # Agent-facing MCP registry (self.mcp). Holds connection/activation state and
    # wraps the stateless MCPManager factory. Registered through the agent's
    # SkillRegistry so doc(self.mcp) and the <mcp> context block are visible.
    from .mcp_registry import MCPRegistry

    result.agent.skills.register(
        "nemo.mcp",
        MCPRegistry(
            mcp_file=result.config.tui.mcp_file,
            servers=result.config.tui.mcp_servers,
        ),
    )
    result.agent.skills.activate(["nemo.mcp"])

    registry = CommandRegistry(
        config=result.config.tui,
        agent=result.agent,
        frontend=frontend,
        skills_dirs=result.config.tui.skills_dirs,
        mcp_file=result.config.tui.mcp_file,
        session_manager=result.session_manager,
        root_config=result.config,
    )
    # Expose to agent so LibraryManager can trigger slash-command hot-reload.
    result.agent._command_registry = registry  # type: ignore[attr-defined]
    return registry


def build_session(
    result: BootstrapResult,
    frontend: "Frontend",
    registry: "CommandRegistry",
    initial_outputs: list[Output] | None = None,
) -> "Session":
    """Build the Session from bootstrap results + frontend + registry."""
    from .session import Session

    return Session(
        frontend=frontend,
        agent=result.agent,
        config=result.config,
        registry=registry,
        session_manager=result.session_manager,
        initial_outputs=initial_outputs,
    )
