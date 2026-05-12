# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Slash command parser and handlers for NeMo OO Agents TUI/Web.

Commands return structured ``CommandResult`` objects whose ``outputs`` list
is rendered by the active ``Frontend``.  No command calls the console or
frontend directly for its results — it only uses ``self.frontend`` for
interactive I/O that must happen *during* execution (spinners, prompts).
"""

import abc
import datetime
import logging
import os
import re
import shlex
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

logger = logging.getLogger(__name__)

from .output import (  # noqa: E402
    ClearScreen,
    DiffOutput,
    HelpOutput,
    Output,
    TableOutput,
    TextOutput,
    _RichReplayPayload,
)
from .session_manager import SessionManager, build_resume_outputs  # noqa: E402

if TYPE_CHECKING:
    from nemo_oo_agents import Agent

    from .config import TUIConfig
    from .frontend import Frontend


def _to_attr_name(name: str) -> str:
    """Convert a hyphenated server/skill name to a valid Python attribute name."""
    return name.replace("-", "_")


def _detect_language(suffix: str) -> str:
    """Map a file extension to a language name for editor/diff rendering."""
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".sh": "bash",
        ".bash": "bash",
        ".toml": "toml",
        ".html": "html",
        ".css": "css",
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".cpp": "cpp",
        ".java": "java",
        ".rb": "ruby",
        ".sql": "sql",
    }.get(suffix.lower(), "plaintext")


# ---------------------------------------------------------------------------
# CommandResult
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    """Result from a command execution."""

    success: bool
    outputs: list[Output] = field(default_factory=list)
    exit: bool = False
    # When set, Session.run() replaces the active SessionManager with this one.
    new_session_manager: "SessionManager | None" = None
    # Set by CompactCommand to signal that auto-renaming should be retried.
    compact_done: bool = False
    # When set, Session.run() passes this as the user message for an agent turn.
    agent_message: str | None = None

    # Convenience constructors -------------------------------------------

    @classmethod
    def ok(cls, *outputs: "Output") -> "CommandResult":
        return cls(success=True, outputs=list(outputs))

    @classmethod
    def err(cls, message: str) -> "CommandResult":
        return cls(success=False, outputs=[TextOutput(message, "error")])

    @classmethod
    def bye(cls) -> "CommandResult":
        return cls(
            success=True,
            outputs=[TextOutput("Goodbye! Stay vibing.", "status")],
            exit=True,
        )


# ---------------------------------------------------------------------------
# Command base class
# ---------------------------------------------------------------------------


class Command(abc.ABC):
    """Abstract base class for all slash commands."""

    # Agent attributes that must be present for this command to be registered.
    required_capabilities: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        frontend: "Frontend",
        config: "TUIConfig",
        agent: "Agent",
        session_manager: "SessionManager | None" = None,
        **kwargs,
    ):
        if agent is None:
            raise ValueError("agent cannot be None.")
        self.frontend = frontend
        self.config = config
        self.agent: Any = agent
        self.session_manager = session_manager
        self._registry: Any = kwargs.get("registry")

    @abc.abstractmethod
    async def execute(self, args: list[str]) -> "CommandResult":
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def help_text(cls) -> dict[str, str]:
        raise NotImplementedError

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) > 0:
            return False, f"Usage: /{self.name}"
        return True, None


# ---------------------------------------------------------------------------
# Concrete commands
# ---------------------------------------------------------------------------


class HelpCommand(Command):
    def __init__(self, frontend, config, agent, **kwargs):
        super().__init__(frontend, config, agent, **kwargs)
        self._registry = kwargs.get("registry")

    @property
    def name(self) -> str:
        return "help"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/help": "Show this help message"}

    async def execute(self, args: list[str]) -> "CommandResult":
        commands_dict = (
            self._registry.get_active_help() if self._registry else CommandRegistry.get_help()
        )
        return CommandResult.ok(HelpOutput(commands_dict))


class ExitCommand(Command):
    @property
    def name(self) -> str:
        return "exit"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/exit": "Exit the TUI",
            "/quit": "Exit the TUI (alias for /exit)",
        }

    async def execute(self, args: list[str]) -> "CommandResult":
        return CommandResult.bye()


def _reset_agent_working_state(agent: "Agent") -> None:
    """Reset the agent's in-memory working state for a fresh ``/clear``.

    The event manager is already pointed at the new (empty) storage via
    ``_swap_session_manager`` — that handles conversation history. Here
    we clear the *snapshotable* fields that live on the agent instance
    itself and don't get reset by storage swap alone:

    - ``agent.todo`` — ``TodoManager``'s ``_todos`` / ``_order``
    - future fields should be added here as they're discovered

    Guarded by ``hasattr`` and a duck-typed ``clear`` check so agents
    without a todo skill keep working.
    """
    todo = getattr(agent, "todo", None)
    if todo is not None and hasattr(todo, "clear") and callable(todo.clear):
        try:
            todo.clear()
        except Exception:
            pass


class ClearCommand(Command):
    @property
    def name(self) -> str:
        return "clear"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/clear": "Start a new session (preserves old session history)"}

    async def execute(self, args: list[str]) -> "CommandResult":
        # Create a fresh SessionManager so subsequent turns go to a new file.
        # NOTE: do NOT call agent.event_manager.clear() here — that would
        # destroy the old session's SQLite data before _swap_session_manager()
        # can close and preserve it.  The new storage starts empty; the agent's
        # event_manager property will return the new backend after the swap.
        new_sm: SessionManager | None = None
        if self.session_manager is not None:
            try:
                import uuid as _uuid

                from nemo_oo_agents.storage import SQLiteStorageManager

                from .session_manager import SESSIONS_DIR

                _new_id = str(_uuid.uuid4())
                SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                _new_storage = SQLiteStorageManager(SESSIONS_DIR / f"{_new_id}.db")
                new_sm = SessionManager(
                    storage=_new_storage,
                    session_id=_new_id,
                    model=self.session_manager.model,
                    agent_cls=self.session_manager.agent_cls,
                    working_dir=self.session_manager.working_dir,
                )
            except Exception:
                pass

        # Cancel any background jobs spawned via queue_manager so they
        # don't keep running in the old session after /clear.
        # Fixes GitLab #172.
        qm = getattr(self.agent, "queue_manager", None)
        if qm is not None and hasattr(qm, "shutdown"):
            try:
                await qm.shutdown()
            except Exception:
                pass

        # Reset the agent's in-memory working state so /clear is truly a
        # fresh start — not just a storage swap. The old session's
        # snapshotable state (todos, context) is preserved on disk (via
        # ``Session.run``'s shutdown save_snapshot) and can be re-loaded
        # via ``/session <id>``.
        #
        # /session <id> has its own restore path (``restore_latest_snapshot``
        # replaces in-memory state wholesale); /clear has no snapshot to
        # restore, so we explicitly reset the known fresh-start fields.
        _reset_agent_working_state(self.agent)

        outputs: list[Output] = [
            ClearScreen(),
            _RichReplayPayload(payload={"kind": "clear"}),  # type: ignore[list-item]
        ]
        if self._registry and self._registry.startup_info:
            outputs.append(self._registry.startup_info)
        outputs.append(TextOutput("Started new session. Previous session saved.", "success"))
        result = CommandResult(success=True, outputs=outputs)
        result.new_session_manager = new_sm
        return result


class ModelCommand(Command):
    @property
    def name(self) -> str:
        return "model"

    def help_text(self) -> dict[str, str]:  # type: ignore[override]
        short = self.config.default_model.split("/")[-1] if hasattr(self, "config") else "?"
        return {
            "/model [name]": f"Switch model (currently {short})",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) > 1:
            return False, "Usage: /model [name]"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        if not args:
            return CommandResult.ok(
                TextOutput(f"Current model: {self.config.default_model}", "info")
            )

        from nemo_oo_agents.unifiedllm import get_llm_client

        selected = args[0]
        self.config.default_model = selected
        try:
            self.agent._llm = get_llm_client(selected)
        except Exception as e:
            return CommandResult.err(f"Failed to switch model: {e}")
        # Summarizer trigger and event-truncation cap both scale with the
        # model's context window. Swapping the LLM without also swapping
        # these budgets leaves a large-context session stranded on a
        # smaller model — e.g. 420K events on a 200K Sonnet window.
        from nemo_oo_agents_cli.tui.agent import apply_model_limits

        apply_model_limits(self.agent)
        return CommandResult.ok(TextOutput(f"Switched to model: {selected}", "success"))


class ModelsCommand(Command):
    @property
    def name(self) -> str:
        return "models"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/models": "List available models from registry"}

    async def execute(self, args: list[str]) -> "CommandResult":
        from nemo_oo_agents.unifiedllm import MODELS

        rows: list[list[str]] = []
        for model_name in sorted(MODELS.keys()):
            marker = "\u25c0" if model_name == self.config.default_model else ""
            rows.append([model_name, marker])

        return CommandResult.ok(
            TableOutput(
                title="Available Models",
                columns=["Model", ""],
                rows=rows,
                footer="Use /model <name> to switch. Any model supported by litellm works, not just these aliases.",
            )
        )


class SwitchCommand(Command):
    required_capabilities: ClassVar[frozenset[str]] = frozenset()

    @property
    def name(self) -> str:
        return "switch"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/switch <model>": "Switch the LLM model (Tab to autocomplete)"}

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) != 1:
            return False, "Usage: /switch <model>  (Tab to autocomplete)"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        from nemo_oo_agents.unifiedllm import get_llm_client

        selected = args[0]
        self.config.default_model = selected
        try:
            self.agent._llm = get_llm_client(selected)
        except Exception as e:
            return CommandResult.err(f"Failed to switch model: {e}")

        # Re-resolve summarizer trigger + truncation cap against the new
        # context window — see apply_model_limits for the math.
        from nemo_oo_agents_cli.tui.agent import apply_model_limits

        apply_model_limits(self.agent)

        return CommandResult.ok(TextOutput(f"Switched to model: {selected}", "success"))


class ThemeCommand(Command):
    """Switch the color theme."""

    THEMES = ("mocha", "latte", "vsdark", "vslight")

    @property
    def name(self) -> str:
        return "theme"

    def help_text(self) -> dict[str, str]:  # type: ignore[override]
        from . import theme as theme_module

        current = theme_module.get_theme() if hasattr(self, "config") else "?"
        return {
            "/theme [name]": f"Switch theme (currently {current})",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) > 1:
            return False, f"Usage: /theme [{'|'.join(self.THEMES)}]"
        if len(args) == 1 and args[0].lower() not in self.THEMES:
            return False, f"Theme must be one of: {', '.join(self.THEMES)}"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        from . import theme as theme_module

        if not args:
            current = theme_module.get_theme()
            others = ", ".join(t for t in self.THEMES if t != current)
            return CommandResult.ok(
                TextOutput(f"Current theme: {current}  (available: {others})", "info")
            )

        name = args[0].lower()
        theme_module.set_theme(name)

        # Replace the base theme in Rich Console's ThemeStack
        # We can't just push - we need to replace the base entry
        if hasattr(self.frontend, "_console") and hasattr(self.frontend._console, "console"):  # type: ignore[attr-defined]
            console = self.frontend._console.console  # type: ignore[attr-defined]
            new_theme = theme_module.create_theme()

            # Directly replace the base theme in the stack
            # This is the only way to actually change colors since Theme snapshots
            # the COLORS dict at creation time
            console._theme_stack._entries[0] = new_theme.styles
            console._theme_stack.get = console._theme_stack._entries[-1].get

        # Rebuild prompt_toolkit style for the new theme
        if hasattr(self.frontend, "_input_handler") and self.frontend._input_handler is not None:  # type: ignore[attr-defined]
            self.frontend._input_handler.refresh_style()  # type: ignore[attr-defined]

        return CommandResult.ok(TextOutput(f"Switched to {name} theme", "success"))


# ---------------------------------------------------------------------------
# History commands
# ---------------------------------------------------------------------------


class HistoryCommand(Command):
    required_capabilities: ClassVar[frozenset[str]] = frozenset({"get_summarization_status"})

    @property
    def name(self) -> str:
        return "history"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/history status": "Show history and summarization status",
            "/history tags": "Show active history tags",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /history <status|tags>"
        if args[0].lower() not in ("status", "tags"):
            return False, f"Unknown subcommand `{args[0]}`. Usage: /history <status|tags>"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        subcmd = args[0].lower()
        if subcmd == "status":
            return self._history_status()
        return self._history_tags()

    def _history_status(self) -> "CommandResult":
        s = self.agent.get_summarization_status()

        rows: list[list[str]] = [
            ["Active events", str(s["active_events"])],
            ["Policy", s["policy"]],
        ]

        if s.get("has_summarizer") and s.get("max_tokens", 0) > 0:
            cur = s["current_tokens"]
            mx = s["max_tokens"]
            pct = cur / mx * 100 if mx else 0
            rows.append([f"Token usage ({pct:.1f}%)", f"{cur:,} / {mx:,}"])
            rows.append(["Preserve recent", str(s.get("preserve_recent", 0))])

        rows.append(["Summary count", str(s.get("summary_count", 0))])

        outputs: list[Output] = [
            TableOutput(title="History Status", columns=["Field", "Value"], rows=rows)
        ]
        if s.get("summary_tags"):
            outputs.append(TextOutput("Tags: " + ", ".join(s["summary_tags"]), "status"))

        return CommandResult.ok(*outputs)

    def _history_tags(self) -> "CommandResult":
        if not hasattr(self.agent, "event_manager"):
            return CommandResult.err("Agent does not support event history.")
        tags = self.agent.event_manager.keys()
        rows: list[list[str]] = []
        for tag in tags[:50]:
            event = self.agent.event_manager[tag]
            etype = getattr(event, "event_type", type(event).__name__)
            rows.append([tag, etype])

        footer = f"\u2026 and {len(tags) - 50} more" if len(tags) > 50 else ""
        return CommandResult.ok(
            TableOutput(
                title=f"Active History Tags ({len(tags)} total)",
                columns=["Tag", "Type"],
                rows=rows,
                footer=footer,
            )
        )


# ---------------------------------------------------------------------------
# MCP commands
# ---------------------------------------------------------------------------


class MCPCommand(Command):
    required_capabilities: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, frontend, config, agent, **kwargs):
        super().__init__(frontend, config, agent, **kwargs)
        self.mcp_file = kwargs.get("mcp_file")
        self._mcp_connections: set[str] = set()

    @property
    def name(self) -> str:
        return "mcp"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/mcp list": "List configured MCP servers",
            "/mcp connect <server>": "Connect to an MCP server",
            "/mcp disconnect <server>": "Disconnect from an MCP server",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /mcp <list|connect|disconnect>"
        if args[0].lower() not in ("list", "connect", "disconnect"):
            return False, f"Unknown subcommand `{args[0]}`"
        if args[0].lower() in ("connect", "disconnect") and len(args) < 2:
            return False, f"Usage: /mcp {args[0]} <server_name>"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        try:
            from nemo_oo_agents.mcp import MCPManager
        except ImportError:
            return CommandResult.err("MCP not enabled. Run `uv sync --extra mcp` and restart.")

        subcmd = args[0].lower()
        subargs = args[1:]
        try:
            servers = MCPManager.list_servers(self.mcp_file)
        except Exception as exc:
            return CommandResult.err(f"Failed to read MCP config: {exc}")

        if subcmd == "list":
            rows = [[s, "\u2713" if s in self._mcp_connections else ""] for s in servers]
            return CommandResult.ok(
                TableOutput(columns=["Server", "Connected"], rows=rows, title="MCP Servers"),
                TextOutput(f"MCP file: {self.mcp_file}", "status"),
            )

        if subcmd == "connect":
            server_name = subargs[0]
            if server_name not in servers:
                return CommandResult.err(f"Server `{server_name}` not found. Use /mcp list.")
            try:
                await self.frontend.start_thinking(f"Connecting to `{server_name}`\u2026")
                tool = MCPManager.create_from_server(server_name, mcp_file=self.mcp_file)
                setattr(self.agent, _to_attr_name(server_name), tool)
                self._mcp_connections.add(server_name)
                return CommandResult.ok(
                    TextOutput(f"MCP server `{server_name}` connected", "success")
                )
            except Exception as e:
                return CommandResult.err(f"Failed to connect to `{server_name}`: {e}")
            finally:
                await self.frontend.stop_thinking()

        # disconnect
        server_name = subargs[0]
        if server_name not in self._mcp_connections:
            return CommandResult.err(f"`{server_name}` not connected. Use /mcp list.")
        try:
            self._mcp_connections.discard(server_name)
            delattr(self.agent, _to_attr_name(server_name))
            return CommandResult.ok(
                TextOutput(f"MCP server `{server_name}` disconnected", "success")
            )
        except Exception as e:
            return CommandResult.err(f"Failed to disconnect `{server_name}`: {e}")


# ---------------------------------------------------------------------------
# Skills commands
# ---------------------------------------------------------------------------


class SkillsCommand(Command):
    required_capabilities: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, frontend, config, agent, **kwargs):
        super().__init__(frontend, config, agent, **kwargs)
        self.skills_dirs = kwargs.get("skills_dirs")
        self._registry: CommandRegistry | None = kwargs.get("registry")
        self._active_skills: set[str] = set()

    @property
    def name(self) -> str:
        return "skills"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/skills list": "List available skills",
            "/skills activate <id>": "Activate a skill",
            "/skills deactivate <id>": "Deactivate a skill",
            "/skills commands": "Show auto-registered slash commands from skills",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /skills <list|activate|deactivate|commands|debug>"
        if args[0].lower() not in ("list", "activate", "deactivate", "commands", "debug"):
            return False, f"Unknown subcommand `{args[0]}`"
        if args[0].lower() in ("activate", "deactivate") and len(args) < 2:
            return False, f"Usage: /skills {args[0]} <skill_id>"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        try:
            from nemo_oo_agents import SkillManager
        except ImportError:
            return CommandResult.err("Skills not enabled. Run `uv sync --extra skills`.")

        subcmd = args[0].lower()
        subargs = args[1:]

        if subcmd == "debug":
            registry = self._registry
            outputs: list[Output] = []
            # 1. What's in _user_skills
            user_skills = registry._user_skills if registry else {}
            rows_us = [
                [name, skill.description[:60]] for name, skill in sorted(user_skills.items())
            ]
            outputs.append(
                TableOutput(
                    title=f"_user_skills ({len(user_skills)} entries)",
                    columns=["Name", "Description"],
                    rows=rows_us or [["(empty)", ""]],
                )
            )
            # 2. Raw scan trace — walk exactly what _discover_user_skills does
            scan_rows = []
            try:
                import re as _re

                import yaml as _yaml

                for sd in self.skills_dirs or []:
                    sd = Path(sd)
                    if not sd.is_dir():
                        scan_rows.append([str(sd), "(dir missing)", ""])
                        continue
                    found = sorted(sd.rglob("SKILL.md"))
                    if not found:
                        scan_rows.append([str(sd), "(no SKILL.md found)", ""])
                        continue
                    for sm in found:
                        try:
                            c = sm.read_text(encoding="utf-8")
                            if not c.startswith("---"):
                                scan_rows.append([str(sm), "SKIP: no frontmatter", ""])
                                continue
                            pts = c.split("---", 2)
                            if len(pts) < 3:
                                scan_rows.append([str(sm), "SKIP: unclosed frontmatter", ""])
                                continue
                            try:
                                m = _yaml.safe_load(pts[1]) or {}
                                if not isinstance(m, dict):
                                    raise ValueError
                                parse_mode = "yaml"
                            except Exception:
                                m = {}
                                for ln in pts[1].splitlines():
                                    mx = _re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*):\s*(.+)$", ln)
                                    if mx:
                                        rv = mx.group(2).strip()
                                        try:
                                            pv = _yaml.safe_load(rv)
                                            m[mx.group(1)] = str(pv) if isinstance(pv, list) else pv
                                        except Exception:
                                            m[mx.group(1)] = rv
                                parse_mode = "regex"
                            ia = m.get("install-as") == "command"
                            iv = m.get("user-invocable")
                            uv = ia or iv is True
                            nm = str(m.get("name", "")).strip()
                            reason = (
                                f"OK ia={ia} iv={iv} name={nm!r} parse={parse_mode}"
                                if uv
                                else f"SKIP ia={ia} iv={iv} name={nm!r} parse={parse_mode}"
                            )
                            scan_rows.append([str(sm.relative_to(sd)), reason, ""])
                        except Exception as ex:
                            scan_rows.append([str(sm), f"ERROR: {ex}", ""])
            except Exception as ex:
                scan_rows.append(["scan error", str(ex), ""])
            outputs.append(
                TableOutput(
                    title="Scan trace",
                    columns=["File", "Result", ""],
                    rows=scan_rows or [["(nothing scanned)", "", ""]],
                )
            )
            # 3. What get_completions() returns (the dict fed to Tab completion)
            if registry:
                completions = registry.get_completions()
                skill_completions = {
                    k: v
                    for k, v in completions.items()
                    if k
                    not in {
                        "/help",
                        "/exit",
                        "/quit",
                        "/clear",
                        "/compact",
                        "/edit",
                        "/model",
                        "/models",
                        "/switch",
                        "/theme",
                        "/history",
                        "/mcp",
                        "/skills",
                        "/python",
                        "/session",
                    }
                }
                rows_c = [[k, v[:60]] for k, v in sorted(skill_completions.items())]
                outputs.append(
                    TableOutput(
                        title=f"Skill completions in get_completions() ({len(skill_completions)} extra)",
                        columns=["Key", "Description"],
                        rows=rows_c or [["(none)", ""]],
                    )
                )
            # 4. Skills dirs
            outputs.append(TextOutput(f"skills_dirs: {self.skills_dirs}", "status"))
            return CommandResult.ok(*outputs)

        if subcmd == "commands":
            user_skills = self._registry._user_skills if self._registry else {}
            rows_cmd = [
                [f"/{name}", skill.argument_hint or "", skill.description]
                for name, skill in sorted(user_skills.items())
            ]
            if rows_cmd:
                return CommandResult.ok(
                    TableOutput(
                        columns=["Command", "Args", "Description"],
                        rows=rows_cmd,
                        title="Skill slash commands (install-as: command)",
                    ),
                    TextOutput(f"Searched: {self.skills_dirs}", "status"),
                )
            return CommandResult.ok(
                TextOutput("No skills with install-as: command found.", "info"),
                TextOutput(f"Searched: {self.skills_dirs}", "status"),
            )

        if subcmd == "list":
            if not self.skills_dirs:
                return CommandResult.ok(TextOutput("No skills directories configured", "info"))
            skills_dict = SkillManager.discover(self.skills_dirs)
            if not skills_dict:
                return CommandResult.ok(TextOutput("No skills found", "info"))
            rows = [
                [
                    sid,
                    "\u2713" if sid in self._active_skills else "",
                    getattr(skill, "description", ""),
                ]
                for sid, skill in sorted(skills_dict.items())
            ]
            return CommandResult.ok(
                TableOutput(columns=["ID", "Active", "Description"], rows=rows, title="Skills"),
                TextOutput(f"Dirs: {self.skills_dirs}", "status"),
            )

        if subcmd == "activate":
            skill_id = subargs[0]
            if not self.skills_dirs:
                return CommandResult.err("No skills directories configured")
            available = SkillManager.discover(self.skills_dirs)
            if skill_id not in available:
                return CommandResult.err(f"Skill `{skill_id}` not found. Use /skills list.")
            if skill_id in self._active_skills:
                return CommandResult.err(f"Skill `{skill_id}` already active")
            try:
                setattr(self.agent, _to_attr_name(skill_id), available[skill_id])
                self._active_skills.add(skill_id)
                return CommandResult.ok(TextOutput(f"Skill `{skill_id}` activated", "success"))
            except Exception as e:
                return CommandResult.err(f"Failed to activate `{skill_id}`: {e}")

        # deactivate
        skill_id = subargs[0]
        if skill_id not in self._active_skills:
            return CommandResult.err(f"`{skill_id}` not active. Use /skills list.")
        try:
            delattr(self.agent, _to_attr_name(skill_id))
            self._active_skills.discard(skill_id)
            return CommandResult.ok(TextOutput(f"Skill `{skill_id}` deactivated", "success"))
        except Exception as e:
            return CommandResult.err(f"Failed to deactivate `{skill_id}`: {e}")


# ---------------------------------------------------------------------------
# Todo commands
# ---------------------------------------------------------------------------


class TodoCommand(Command):
    required_capabilities: ClassVar[frozenset[str]] = frozenset({"todo"})

    @property
    def name(self) -> str:
        return "todo"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/todo": "Show all todos",
            "/todo <id>": "Show a single todo item",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) > 1:
            return False, "Usage: /todo [id]"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        todo_mgr = getattr(self.agent, "todo", None)
        if todo_mgr is None:
            return CommandResult.err("Agent has no todo manager.")

        if not args:
            # Show all todos
            todos = todo_mgr.list_todos()
            if not todos:
                return CommandResult.ok(TextOutput("(no todos)", "status"))
            rows = []
            icons = {"open": "○", "done": "✓", "blocked": "●"}
            for t in todos:
                effective = t.status
                if t.status == "open" and t.is_blocked(todo_mgr._todos):
                    effective = "blocked"
                icon = icons.get(effective, "?")
                deps = ", ".join(t.deps) if t.deps else ""
                rows.append([icon, t.id, t.title, effective, deps])
            done = sum(1 for t in todos if t.status == "done")
            return CommandResult.ok(
                TableOutput(
                    columns=["", "ID", "Title", "Status", "Deps"],
                    rows=rows,
                    title=f"Todos ({done}/{len(todos)} done)",
                )
            )

        # Show a single todo
        todo_id = args[0]
        t = todo_mgr.get(todo_id)
        if t is None:
            return CommandResult.err(f"Todo {todo_id!r} not found.")
        effective = t.status
        if t.status == "open" and t.is_blocked(todo_mgr._todos):
            effective = "blocked"
        lines = [
            f"**[{t.id}]** {t.title}",
            f"Status: {effective}",
        ]
        if t.deps:
            lines.append(f"Deps: {', '.join(t.deps)}")
        if t.vars:
            lines.append(f"Vars: {t.vars}")
        if t.notes:
            lines.append(f"Notes: {t.notes}")
        lines.append(f"Created: {t.created_at}")
        return CommandResult.ok(TextOutput("\n".join(lines), "info"))


# ---------------------------------------------------------------------------
# Context command
# ---------------------------------------------------------------------------


class ContextCommand(Command):
    """Show context window utilization stats."""

    @property
    def name(self) -> str:
        return "context"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/context": "Show context window utilization"}

    async def execute(self, args: list[str]) -> "CommandResult":
        stats = getattr(self.agent, "context_stats", None)
        if stats is None:
            return CommandResult.ok(
                TextOutput("No context stats yet — run a generation first.", "info")
            )
        return CommandResult.ok(TextOutput(stats.format(), "info"))


# ---------------------------------------------------------------------------
# Compact command
# ---------------------------------------------------------------------------


class CompactCommand(Command):
    """Summarize and compact conversation history."""

    @property
    def name(self) -> str:
        return "compact"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/compact": "Summarize conversation history into a compact block (frees tokens)"}

    async def execute(self, args: list[str]) -> "CommandResult":
        if not hasattr(self.agent, "event_manager"):
            return CommandResult.err("Agent does not support history management.")

        tags = self.agent.event_manager.keys()
        events_before = len(tags)
        if events_before == 0:
            return CommandResult.ok(
                TextOutput("Nothing to compact \u2014 history is already empty.", "info")
            )

        tokens_before = 0
        stats = self.agent.context_stats
        if stats:
            tokens_before = stats.total_tokens
        summarizers = getattr(self.agent, "_summarizers", [])

        if summarizers:
            summarizer = summarizers[0]
            start_tag = tags[0]
            end_tag = tags[-1]
            try:
                await self.frontend.start_thinking("Summarizing history\u2026")
                history_md = summarizer._render_range_to_markdown(start_tag, end_tag)
                target_chars = getattr(getattr(summarizer, "config", None), "target_chars", 2000)
                summary_text = await summarizer.summarize(history_md, target_chars)
                self.agent.event_manager.collapse(start_tag, end_tag, summary_text)
                events_after = len(self.agent.event_manager.keys())
                tok_sfx = f" (~{tokens_before:,} tokens freed)" if tokens_before else ""
                result = CommandResult.ok(
                    TextOutput(
                        f"Compacted {events_before} events \u2192 {events_after} (summary block){tok_sfx}.",
                        "success",
                    )
                )
                result.compact_done = True
                return result
            except Exception as e:
                return CommandResult.ok(
                    TextOutput(
                        f"Summarization failed ({e}); kept {events_before} events intact.",
                        "warning",
                    )
                )
            finally:
                await self.frontend.stop_thinking()

        self.agent.event_manager.clear()
        tok_sfx = f" (~{tokens_before:,} tokens freed)" if tokens_before else ""
        result = CommandResult.ok(
            TextOutput(f"Cleared {events_before} history events{tok_sfx}.", "success")
        )
        result.compact_done = True
        return result


# ---------------------------------------------------------------------------
# Python display toggle
# ---------------------------------------------------------------------------


class PythonCommand(Command):
    """Toggle display of the Python execution panel."""

    @property
    def name(self) -> str:
        return "python"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/python status": "Show whether Python execution display is on or off",
            "/python on": "Enable Python execution display",
            "/python off": "Suppress Python execution display",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /python <status|on|off>"
        if args[0].lower() not in ("status", "on", "off"):
            return False, f"Unknown subcommand `{args[0]}`"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        subcmd = args[0].lower()

        if subcmd == "status":
            state = "on" if self.config.show_python else "off"
            return CommandResult.ok(TextOutput(f"Python execution display: {state}", "info"))

        if subcmd == "on":
            self.config.show_python = True
            return CommandResult.ok(TextOutput("Python execution display enabled.", "success"))

        # off
        self.config.show_python = False
        return CommandResult.ok(TextOutput("Python execution display suppressed.", "success"))


# ---------------------------------------------------------------------------
# Goal Mode command
# ---------------------------------------------------------------------------


class GoalModeCommand(Command):
    """Toggle goal mode: auto-feed unresolved todos to the agent between turns."""

    @property
    def name(self) -> str:
        return "goal-mode"

    def help_text(self) -> dict[str, str]:  # type: ignore[override]
        state = "on" if self.config.goal_mode else "off"
        return {"/goal-mode [on|off]": f"Toggle goal mode (currently {state})"}

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if args and args[0].lower() not in ("on", "off"):
            return False, f"Unknown argument `{args[0]}`. Use on or off."
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        if not args:
            state = "on" if self.config.goal_mode else "off"
            return CommandResult.ok(TextOutput(f"Goal mode: {state}", "info"))

        subcmd = args[0].lower()

        if subcmd == "on":
            self.config.goal_mode = True
            # Build goal-mode trigger message if there are open todos
            agent_msg = None
            todo_mgr = getattr(self.agent, "todo", None)
            if todo_mgr is not None:
                open_todos = [
                    t
                    for t in todo_mgr.list_todos(status="open")
                    if not t.is_blocked(todo_mgr._todos)
                ]
                if open_todos:
                    t = open_todos[0]
                    agent_msg = f"You are in goal mode. Next open todo: [{t.id}] {t.title}"
                    if t.notes:
                        agent_msg += f"\nNotes: {t.notes}"
            result = CommandResult.ok(TextOutput("Goal mode enabled.", "success"))
            result.agent_message = agent_msg
            return result

        # off
        self.config.goal_mode = False
        return CommandResult.ok(TextOutput("Goal mode disabled.", "success"))


# ---------------------------------------------------------------------------
# Edit command
# ---------------------------------------------------------------------------


class EditCommand(Command):
    """Open a file in $EDITOR and show the diff on save."""

    @property
    def name(self) -> str:
        return "edit"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/edit <file>": "Open file in $EDITOR \u2014 shows diff on save",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /edit <file>"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        import difflib
        from pathlib import Path

        path = Path(args[0]).expanduser().resolve()
        original = path.read_text(errors="replace") if path.exists() else ""
        language = _detect_language(path.suffix)

        new_content = await self.frontend.open_editor(str(path), original, language)
        if new_content is None:
            return CommandResult.ok(TextOutput("Edit cancelled.", "info"))
        if new_content == original:
            return CommandResult.ok(TextOutput("No changes.", "info"))

        # Write the file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_content)

        # Compute unified diff
        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
                lineterm="\n",
            )
        )
        diff_text = "".join(diff_lines)

        outputs: list[Output] = [TextOutput(f"Saved {path}.", "success")]
        if diff_text:
            outputs.append(DiffOutput(diff=diff_text, filename=str(path)))
        return CommandResult.ok(*outputs)


# ---------------------------------------------------------------------------
# IPython command
# ---------------------------------------------------------------------------


class IPythonCommand(Command):
    """Drop into an embedded IPython shell with the agent in scope."""

    @property
    def name(self) -> str:
        return "ipython"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/ipython": "Drop into IPython with agent in scope \u2014 Ctrl+D to return",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        from .session import _handle_python_shell

        await _handle_python_shell(self.agent, self.frontend)
        return CommandResult.ok(TextOutput("Returned to TUI.", "info"))


# Session management command
# ---------------------------------------------------------------------------


class SessionCommand(Command):
    """List, resume, and manage conversation sessions."""

    @property
    def name(self) -> str:
        return "session"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/session list": "List recent sessions",
            "/session new": "Start a new session (current history cleared)",
            "/session resume <id>": "Resume a past session (injects history as context)",
            "/session delete <id>": "Delete a session",
            "/session export": "Export current session as Markdown",
            "/session rename <name>": "Rename the current session",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /session <list|new|resume|delete|export|rename>"
        if args[0].lower() not in ("list", "new", "resume", "delete", "export", "rename"):
            return False, f"Unknown subcommand `{args[0]}`"
        if args[0].lower() in ("resume", "delete") and len(args) < 2:
            return False, f"Usage: /session {args[0]} <session_id>"
        if args[0].lower() == "rename" and len(args) < 2:
            return False, "Usage: /session rename <name>"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        subcmd = args[0].lower()

        if subcmd == "list":
            sessions = SessionManager.list_sessions()
            if not sessions:
                return CommandResult.ok(TextOutput("No sessions found.", "info"))
            rows = []
            for s in sessions:
                dt = datetime.datetime.fromtimestamp(s.last_active).strftime("%m/%d %H:%M")
                name_display = s.name[:28] if s.name else ""
                rows.append(
                    [s.id[:8], dt, s.model.split("/")[-1][:20], str(s.turn_count), name_display]
                )
            return CommandResult.ok(
                TableOutput(
                    title="Recent Sessions",
                    columns=["ID", "Last Active", "Model", "Turns", "Name"],
                    rows=rows,
                )
            )

        if subcmd == "export":
            if self.session_manager is None:
                return CommandResult.err("No active session manager.")
            md = self.session_manager.as_markdown()
            fname = f"session-{self.session_manager.session_id[:8]}-{datetime.date.today()}.md"
            try:
                Path(fname).write_text(md)
                return CommandResult.ok(TextOutput(f"Session exported to {fname}", "success"))
            except Exception as e:
                return CommandResult.ok(TextOutput(f"Export failed: {e}\n\n{md[:500]}", "warning"))

        if subcmd == "resume":
            session_id = args[1]
            matches = SessionManager.find_by_prefix(session_id)
            if not matches:
                return CommandResult.err(f"Session '{session_id}' not found. Use /session list.")
            if len(matches) > 1:
                ids = ", ".join(m[:8] for m in matches)
                return CommandResult.err(f"Ambiguous session prefix '{session_id}' matches: {ids}")
            full_id = matches[0]

            import os as _os

            from .session_manager import SESSIONS_DIR as _SESSIONS_DIR

            _session_db_path = _SESSIONS_DIR / f"{full_id}.db"
            _in_nemo_term = bool(_os.environ.get("NEMO_RICH_URL"))

            outputs = build_resume_outputs(_session_db_path, full_id, in_nemo_term=_in_nemo_term)
            if not outputs:
                return CommandResult.err(f"Session '{session_id}' is empty.")

            # Open the old session's DB, restore agent state, swap session manager
            from nemo_oo_agents.storage import SQLiteStorageManager

            try:
                old_storage = SQLiteStorageManager(_session_db_path)
                restored = old_storage.restore_latest_snapshot(self.agent)
                if restored:
                    outputs.append(
                        TextOutput(f"Agent state restored from session {full_id[:8]}.", "status")
                    )
                new_sm = SessionManager(
                    storage=old_storage,
                    session_id=full_id,
                    model=self.session_manager.model if self.session_manager else "",
                    agent_cls=type(self.agent).__name__,
                    working_dir=self.session_manager.working_dir if self.session_manager else "",
                    resumed=True,
                )
                result = CommandResult.ok(*outputs)
                result.new_session_manager = new_sm
                return result
            except Exception as e:
                outputs.append(TextOutput(f"Could not restore session: {e}", "warning"))

            return CommandResult.ok(*outputs)

        if subcmd == "delete":
            session_id = args[1]
            matches = SessionManager.find_by_prefix(session_id)
            if not matches:
                return CommandResult.err(f"Session '{session_id}' not found.")
            if len(matches) > 1:
                ids = ", ".join(m[:8] for m in matches)
                return CommandResult.err(f"Ambiguous session prefix '{session_id}' matches: {ids}")
            SessionManager.delete_session(matches[0])
            return CommandResult.ok(TextOutput(f"Session {matches[0][:8]} deleted.", "success"))

        if subcmd == "rename":
            name = " ".join(args[1:]).strip()
            if self.session_manager is None:
                return CommandResult.err("No active session.")
            self.session_manager.rename(name, user_named=True)
            return CommandResult.ok(TextOutput(f"Session renamed to: {name}", "success"))

        if subcmd == "new":
            # NOTE: do NOT call agent.event_manager.clear() here — same
            # reasoning as ClearCommand: it would wipe the old session's
            # SQLite data before _swap_session_manager() preserves it.

            # Create a fresh SessionManager so subsequent turns go to a new file.
            new_sm: SessionManager | None = None
            if self.session_manager is not None:
                try:
                    import uuid as _uuid

                    from nemo_oo_agents.storage import SQLiteStorageManager

                    from .session_manager import SESSIONS_DIR

                    _new_id = str(_uuid.uuid4())
                    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                    _new_storage = SQLiteStorageManager(SESSIONS_DIR / f"{_new_id}.db")
                    new_sm = SessionManager(
                        storage=_new_storage,
                        session_id=_new_id,
                        model=self.session_manager.model,
                        agent_cls=self.session_manager.agent_cls,
                        working_dir=self.session_manager.working_dir,
                    )
                except Exception:
                    pass

            outputs: list[Output] = [
                ClearScreen(),
                _RichReplayPayload(payload={"kind": "clear"}),  # type: ignore[list-item]
            ]
            if self._registry and self._registry.startup_info:
                outputs.append(self._registry.startup_info)
            outputs.append(TextOutput("Started new session. History cleared.", "success"))
            result = CommandResult(success=True, outputs=outputs)
            result.new_session_manager = new_sm
            return result

        return CommandResult.err(f"Unknown subcommand `{subcmd}`")


# ---------------------------------------------------------------------------
# Registry and handler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _UserSkill:
    """Metadata for a user-invocable skill slash command."""

    name: str
    body: str
    description: str
    argument_hint: str | None = None

    def help_entry(self) -> tuple[str, str]:
        hint = self.argument_hint or ""
        key = f"/{self.name} {hint}".strip()
        return key, self.description

    def make_agent_message(self, args: list[str]) -> str:
        body = self.body
        if args:
            joined = " ".join(args)
            if "$ARGUMENTS" in body:
                return body.replace("$ARGUMENTS", joined)
            return f"{body}\n\nArguments: {joined}"
        return body


# ---------------------------------------------------------------------------
# Jobs command
# ---------------------------------------------------------------------------


class JobsCommand(Command):
    """Show background jobs, or inspect one by name."""

    @property
    def name(self) -> str:
        return "jobs"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/jobs [name]": "List background jobs, or inspect one by name",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        qm = getattr(self.agent, "queue_manager", None)
        if qm is None:
            return CommandResult.err("No queue manager available.")

        if args:
            return self._detail(qm, args[0])

        job_states = qm.jobs()
        if not job_states:
            return CommandResult.ok(TextOutput("No background jobs.", "info"))

        rows: list[list[str]] = []
        for channel_name, state in sorted(job_states.items()):
            handle = qm.job(channel_name)
            delivered = str(len(handle.values)) if handle and handle.values else "0"
            ch = qm._channels.get(channel_name)
            queued = str(ch.qsize()) if ch and hasattr(ch, "qsize") else "0"
            rows.append([channel_name, state, delivered, queued])

        return CommandResult.ok(
            TableOutput(
                title="Background Jobs",
                columns=["Channel", "State", "Delivered", "Queued"],
                rows=rows,
            )
        )

    @staticmethod
    def _detail(qm, name: str) -> "CommandResult":
        handle = qm.job(name)
        if handle is None:
            return CommandResult.err(f"No job named '{name}'.")

        outputs: list[Output] = []

        # Job state
        outputs.append(TextOutput(f"Job '{name}': {handle.state}", "info"))

        # Delivered values (ring-buffer history, last 20 shown)
        values = handle.values
        if values:
            total = len(values)
            shown = values[-20:]
            lines = "\n".join(str(v) for v in shown)
            header = f"Delivered ({total} items)"
            if total > 20:
                header += " — showing last 20"
            outputs.append(TextOutput(f"{header}:\n{lines}", "info"))
        else:
            outputs.append(TextOutput("Delivered: none yet", "info"))

        # Queue status
        ch = qm._channels.get(name)
        if ch is not None:
            pending = ch.qsize() if hasattr(ch, "qsize") else 0
            outputs.append(TextOutput(f"Queue pending: {pending}", "info"))

        return CommandResult.ok(*outputs)


# ---------------------------------------------------------------------------
# Trace URL command
# ---------------------------------------------------------------------------


class TraceUrlCommand(Command):
    """Print the full viewer URL for the current session's trace."""

    @property
    def name(self) -> str:
        return "trace-url"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/trace-url": "Print the full URL to the trace of the current session",
        }

    async def execute(self, args: list[str]) -> "CommandResult":
        try:
            from nemo_oo_agents.tracing import get_session
        except ImportError:
            return CommandResult.err("Tracing package not installed.")

        session_name = get_session()
        if not session_name:
            return CommandResult.err("No active trace session.")

        # Determine the viewer base URL from the OTLP endpoint
        endpoint = os.environ.get("OTLP_ENDPOINT", "http://localhost:5001/v1/traces")
        # Strip /v1/traces or /v1 suffix to get the viewer base
        base = endpoint.rstrip("/")
        for suffix in ("/v1/traces", "/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break

        url = f"{base}/traces/view?session_id={urllib.parse.quote(session_name)}"
        return CommandResult.ok(TextOutput(url, "info"))


# ---------------------------------------------------------------------------
# Show last Python command
# ---------------------------------------------------------------------------


class ShowLastPythonCommand(Command):
    """Show the code of the last execute_python block, syntax-highlighted."""

    @property
    def name(self) -> str:
        return "show-last-python"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/show-last-python": "Show the last execute_python code block"}

    async def execute(self, args: list[str]) -> "CommandResult":
        # Walk events backwards to find the most recent ToolCallEvent for execute_python
        em = getattr(self.agent, "event_manager", None)
        if em is None:
            return CommandResult.err("No event manager available.")

        code = None
        # Iterate active tags in reverse to find the last execute_python
        tags = em.keys()
        for tag in reversed(tags):
            event = em[tag]
            event_type = getattr(event, "event_type", type(event).__name__)
            if event_type == "ToolCallEvent":
                name = getattr(event, "name", "")
                if name == "execute_python":
                    arguments = getattr(event, "arguments", {})
                    code = arguments.get("code", "") if isinstance(arguments, dict) else ""
                    if code:
                        break

        if not code:
            return CommandResult.err("No execute_python block found in history.")

        from .output import AgentMessage

        return CommandResult.ok(
            AgentMessage(
                content=f"```python\n{code.strip()}\n```",
                show_rule=False,
            )
        )


class EventsCommand(Command):
    """Show event statistics or pretty-print a specific event by tag."""

    @property
    def name(self) -> str:
        return "events"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/events [tag]": "Show event statistics or pretty-print an event by tag"}

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) > 1:
            return False, "Usage: /events [tag]"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        em = getattr(self.agent, "event_manager", None)
        if em is None:
            return CommandResult.err("No event manager available.")

        tag = args[0] if args else ""

        if not tag:
            return self._format_stats(em)
        else:
            return self._format_event(em, tag)

    # ------------------------------------------------------------------
    # Stats view
    # ------------------------------------------------------------------

    def _format_stats(self, em) -> "CommandResult":
        """Generate event statistics summary."""
        all_tags = em.keys()
        if not all_tags:
            return CommandResult.ok(TextOutput("No events recorded.", "info"))

        type_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        first_ts = None
        last_ts = None

        for tag in all_tags:
            event = em.get(tag)
            if event is None:
                continue
            etype = getattr(event, "event_type", type(event).__name__)
            type_counts[etype] = type_counts.get(etype, 0) + 1

            if hasattr(event, "execution_status"):
                s = str(event.execution_status)
                label = s.rsplit(".", 1)[-1]
                status_counts[label] = status_counts.get(label, 0) + 1

            ts = getattr(event, "timestamp", None)
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

        total = sum(type_counts.values())
        sorted_types = sorted(type_counts.items(), key=lambda x: -x[1])

        icons = {
            "ToolCallEvent": "\U0001f527",
            "PythonOutput": "\U0001f4e4",
            "Task": "\U0001f4cb",
            "TUIUserInput": "\U0001f4ac",
            "TUISessionStart": "\U0001f680",
            "Summary": "\U0001f4dd",
            "Error": "\u274c",
        }

        rows: list[list[str]] = []
        for etype, count in sorted_types:
            pct = f"{count * 100 // total}%"
            icon = icons.get(etype, "\U0001f4cc")
            rows.append([f"{icon} {etype}", str(count), pct])

        outputs: list[Output] = []

        # Time range
        if first_ts and last_ts:
            duration = last_ts - first_ts
            mins = int(duration.total_seconds() // 60)
            secs = int(duration.total_seconds() % 60)
            ts_fmt = "%H:%M:%S"
            time_line = (
                f"Session: {first_ts.strftime(ts_fmt)} \u2192 "
                f"{last_ts.strftime(ts_fmt)} ({mins}m {secs}s)"
            )
            outputs.append(TextOutput(time_line, "info"))

        outputs.append(
            TextOutput(
                f"Total events: {total}  |  Tag range: {all_tags[0]} .. {all_tags[-1]}",
                "info",
            )
        )

        outputs.append(
            TableOutput(
                title="Event Types",
                columns=["Type", "Count", "%"],
                rows=rows,
            )
        )

        if status_counts:
            parts = []
            for label, count in sorted(status_counts.items(), key=lambda x: -x[1]):
                emoji = (
                    "\u2705"
                    if label == "complete"
                    else "\u274c"
                    if label == "error"
                    else "\u26a0\ufe0f"
                )
                parts.append(f"{emoji} {label}: {count}")
            outputs.append(TextOutput("Execution: " + "  |  ".join(parts), "info"))

        # Recent events
        recent_tags = all_tags[-10:]
        recent_rows: list[list[str]] = []
        for t in recent_tags:
            event = em.get(t)
            if event is None:
                continue
            etype = getattr(event, "event_type", type(event).__name__)
            icon = icons.get(etype, "\U0001f4cc")
            summary = self._one_line_summary(event, etype)
            recent_rows.append([t, f"{icon} {summary}"])

        outputs.append(
            TableOutput(
                title="Recent Events",
                columns=["Tag", "Event"],
                rows=recent_rows,
            )
        )

        return CommandResult(success=True, outputs=outputs)

    # ------------------------------------------------------------------
    # Single-event view
    # ------------------------------------------------------------------

    def _format_event(self, em, tag: str) -> "CommandResult":
        """Pretty-print a single event by tag."""
        event = em.get(tag)
        if event is None:
            return CommandResult.err(f"No event found with tag '{tag}'")

        etype = getattr(event, "event_type", type(event).__name__)

        if etype == "ToolCallEvent":
            return self._format_tool_call(event, tag)
        elif etype == "PythonOutput":
            return self._format_python_output(event, tag)
        else:
            return self._format_generic(event, tag, etype)

    def _format_tool_call(self, event, tag: str) -> "CommandResult":
        import json as _json

        name = getattr(event, "name", "?")
        ts = getattr(event, "timestamp", None)
        ts_str = ts.strftime("%H:%M:%S.%f")[:-3] if ts else "?"
        tool_call_id = getattr(event, "tool_call_id", "")
        short_id = tool_call_id[-8:] if tool_call_id else ""
        args = getattr(event, "arguments", {})
        result = getattr(event, "result", None)

        outputs: list[Output] = []

        header_rows = [["Tool", name], ["Time", ts_str]]
        if short_id:
            header_rows.append(["Call ID", f"...{short_id}"])
        outputs.append(
            TableOutput(
                title=f"\U0001f527 Tool Call [{tag}]",
                columns=["", ""],
                rows=header_rows,
            )
        )

        from .output import AgentMessage

        if name == "execute_python" and isinstance(args, dict) and "code" in args:
            outputs.append(
                AgentMessage(content=f"```python\n{args['code'].rstrip()}\n```", show_rule=False)
            )
        elif args:
            try:
                formatted = _json.dumps(args, indent=2, default=str)
            except (TypeError, ValueError):
                formatted = str(args)
            outputs.append(
                AgentMessage(content=f"**Arguments:**\n```json\n{formatted}\n```", show_rule=False)
            )

        if result is not None:
            result_status = getattr(result, "result_status", None)
            if result_status:
                s = str(result_status)
                status_str = s.rsplit(".", 1)[-1]
                icon = "\u2705" if status_str == "complete" else "\u274c"
                outputs.append(TextOutput(f"Result: {icon} {status_str}", "info"))

        return CommandResult(success=True, outputs=outputs)

    def _format_python_output(self, event, tag: str) -> "CommandResult":
        status = str(getattr(event, "execution_status", "?"))
        status_label = status.rsplit(".", 1)[-1]
        status_icon = "\u2705" if status_label == "complete" else "\u274c"

        ts = getattr(event, "timestamp", None)
        ts_str = ts.strftime("%H:%M:%S.%f")[:-3] if ts else "?"
        exec_count = getattr(event, "execution_count", None)
        tool_call_id = getattr(event, "tool_call_id", "")
        short_id = tool_call_id[-8:] if tool_call_id else ""

        outputs: list[Output] = []

        header_rows = [["Status", f"{status_icon} {status_label}"], ["Time", ts_str]]
        if exec_count is not None:
            header_rows.append(["Cell", f"In[{exec_count}]"])
        if short_id:
            header_rows.append(["Call ID", f"...{short_id}"])

        outputs.append(
            TableOutput(
                title=f"\U0001f4e4 Python Output [{tag}]  {status_icon}",
                columns=["", ""],
                rows=header_rows,
            )
        )

        from .output import AgentMessage

        stdout = getattr(event, "stdout", "") or ""
        if stdout.strip():
            outputs.append(
                AgentMessage(content=f"**Output:**\n```\n{stdout.rstrip()}\n```", show_rule=False)
            )

        stderr = getattr(event, "stderr", "") or ""
        if stderr.strip():
            outputs.append(
                AgentMessage(content=f"**Stderr:**\n```\n{stderr.rstrip()}\n```", show_rule=False)
            )

        error = getattr(event, "error", "") or ""
        if error.strip():
            outputs.append(
                AgentMessage(content=f"**Error:**\n```\n{error.rstrip()}\n```", show_rule=False)
            )

        return CommandResult(success=True, outputs=outputs)

    def _format_generic(self, event, tag: str, etype: str) -> "CommandResult":
        import json as _json

        ts = getattr(event, "timestamp", None)
        ts_str = ts.strftime("%H:%M:%S.%f")[:-3] if ts else "?"

        outputs: list[Output] = []
        outputs.append(TextOutput(f"\U0001f4cc {etype} [{tag}]  Time: {ts_str}", "info"))

        try:
            data = event.model_dump()
            for k in ("event_type", "id", "metadata", "status", "tag", "timestamp"):
                data.pop(k, None)
            if data:
                formatted = _json.dumps(data, indent=2, default=str)
                from .output import AgentMessage

                outputs.append(AgentMessage(content=f"```json\n{formatted}\n```", show_rule=False))
        except Exception:
            outputs.append(TextOutput(str(event), "info"))

        return CommandResult(success=True, outputs=outputs)

    @staticmethod
    def _one_line_summary(event, etype: str) -> str:
        """Generate a one-line summary for an event."""
        if etype == "ToolCallEvent":
            name = getattr(event, "name", "?")
            args = getattr(event, "arguments", {})
            if name == "execute_python" and isinstance(args, dict) and "code" in args:
                for line in args["code"].split("\n"):
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        return f"{name} \u2014 {stripped[:60]}"
            return name
        elif etype == "PythonOutput":
            status = str(getattr(event, "execution_status", "?"))
            label = status.rsplit(".", 1)[-1]
            icon = "\u2705" if label == "complete" else "\u274c"
            stdout = getattr(event, "stdout", "") or ""
            error = getattr(event, "error", "") or ""
            if label != "complete" and error:
                return f"{icon} {error.strip().splitlines()[-1][:60]}"
            elif stdout:
                return f"{icon} {stdout.strip().splitlines()[0][:60]}"
            return f"{icon} {label}"
        elif etype == "TUIUserInput":
            text = getattr(event, "text", "") or ""
            return text[:60] + ("..." if len(text) > 60 else "")
        elif etype == "TUISessionStart":
            return f"model={getattr(event, 'model', '?')}"
        elif etype == "Task":
            prompt = getattr(event, "prompt", "") or ""
            return prompt.strip().splitlines()[0][:60] if prompt.strip() else ""
        return str(event)[:60]


class TimeTravelCommand(Command):
    """Fork a session at a specific event tag, creating a new session truncated to that point."""

    @property
    def name(self) -> str:
        return "time-travel"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/time-travel <session-id> --at <tag>": (
                "Fork a session at a specific tag, creating a new session truncated to that point"
            ),
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) < 3:
            return False, "Usage: /time-travel <session-id> --at <tag>"
        if "--at" not in args:
            return False, "Usage: /time-travel <session-id> --at <tag>"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        import shutil
        import sqlite3
        import uuid as _uuid

        from nemo_oo_agents.storage import SQLiteStorageManager

        from .session_manager import SESSIONS_DIR

        # Parse arguments: /time-travel <session-id> --at <tag>
        at_index = args.index("--at")
        session_prefix = " ".join(args[:at_index])
        if at_index + 1 >= len(args):
            return CommandResult.err("Missing tag value after --at")
        target_tag = args[at_index + 1]

        # --- 1. Find the source session DB ---
        matches = SessionManager.find_by_prefix(session_prefix)
        if not matches:
            # Try matching by session name
            all_sessions = SessionManager.list_sessions(limit=100)
            matches = [
                s.id for s in all_sessions if s.name and session_prefix.lower() in s.name.lower()
            ]
        if not matches:
            return CommandResult.err(
                f"Session '{session_prefix}' not found. Use /session list to find session IDs."
            )
        if len(matches) > 1:
            ids = ", ".join(m[:8] for m in matches)
            return CommandResult.err(
                f"Ambiguous session identifier '{session_prefix}' matches: {ids}"
            )
        source_session_id = matches[0]
        source_db_path = SESSIONS_DIR / f"{source_session_id}.db"
        if not source_db_path.exists():
            return CommandResult.err(f"Session DB not found: {source_db_path}")

        # --- 2. Read original session name ---
        original_meta = SessionManager._read_meta(source_db_path)
        original_name = (
            original_meta.name if original_meta and original_meta.name else source_session_id[:8]
        )

        # --- 3. Verify the target tag exists in the source DB ---
        try:
            src_conn = sqlite3.connect(str(source_db_path))
            src_conn.row_factory = sqlite3.Row
            row = src_conn.execute(
                "SELECT insertion_order FROM events WHERE tag = ?", (target_tag,)
            ).fetchone()
            src_conn.close()
        except Exception as e:
            return CommandResult.err(f"Failed to read source session: {e}")

        if row is None:
            # Show available tags to help the user
            try:
                src_conn = sqlite3.connect(str(source_db_path))
                last_tags = src_conn.execute(
                    "SELECT tag, event_type FROM events ORDER BY insertion_order DESC LIMIT 10"
                ).fetchall()
                src_conn.close()
                hint_lines = [f"  tag={r[0]} ({r[1]})" for r in reversed(last_tags)]
                hint = "\nRecent tags:\n" + "\n".join(hint_lines)
            except Exception:
                hint = ""
            return CommandResult.err(
                f"Tag '{target_tag}' not found in session {source_session_id[:8]}.{hint}"
            )

        target_insertion_order = row["insertion_order"]

        # --- 4. Copy DB to new file with new UUID ---
        new_session_id = str(_uuid.uuid4())
        new_db_path = SESSIONS_DIR / f"{new_session_id}.db"
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(str(source_db_path), str(new_db_path))
        except Exception as e:
            return CommandResult.err(f"Failed to copy session DB: {e}")

        # --- 5. Truncate the new DB ---
        conn = None
        try:
            conn = sqlite3.connect(str(new_db_path))

            # Delete events after the target tag
            conn.execute(
                "DELETE FROM events WHERE insertion_order > ?",
                (target_insertion_order,),
            )

            # Clean active_tags: keep only tags that still exist in events
            conn.execute("DELETE FROM active_tags WHERE tag NOT IN (SELECT tag FROM events)")

            # Remove snapshots created after the target event's timestamp
            # Get the timestamp of the target event to compare against snapshot created_at
            target_row = conn.execute(
                "SELECT data FROM events WHERE tag = ?", (target_tag,)
            ).fetchone()
            if target_row:
                import json as _json

                try:
                    target_data = _json.loads(target_row[0])
                    target_ts = target_data.get("timestamp")
                    if target_ts:
                        conn.execute(
                            "DELETE FROM snapshots WHERE created_at > ?",
                            (target_ts,),
                        )
                    else:
                        # No timestamp — remove all snapshots to be safe
                        conn.execute("DELETE FROM snapshots")
                except Exception:
                    conn.execute("DELETE FROM snapshots")
            else:
                conn.execute("DELETE FROM snapshots")

            # --- 6. Rename the session by injecting a TUISessionRename event ---
            new_name = f"TimeTravel ({original_name}, tag {target_tag})"

            # Get max insertion_order in the truncated DB
            max_order_row = conn.execute("SELECT MAX(insertion_order) FROM events").fetchone()
            next_order = (max_order_row[0] or 0) + 1

            # Get max position in active_tags
            max_pos_row = conn.execute("SELECT MAX(position) FROM active_tags").fetchone()
            next_pos = (max_pos_row[0] or 0) + 1

            # Compute next tag number
            tag_num_row = conn.execute(
                """SELECT COALESCE(MAX(
                    CAST(
                        CASE WHEN instr(tag, '..') > 0
                             THEN substr(tag, instr(tag, '..') + 2)
                             ELSE tag
                        END AS INTEGER
                    )
                ), 0) FROM events"""
            ).fetchone()
            rename_tag = str((tag_num_row[0] or 0) + 1)

            import json as _json
            from datetime import UTC
            from datetime import datetime as _datetime

            rename_event_id = str(_uuid.uuid4())
            rename_ts = _datetime.now(UTC).isoformat()
            rename_data = _json.dumps(
                {
                    "event_type": "TUISessionRename",
                    "id": rename_event_id,
                    "status": "active",
                    "timestamp": rename_ts,
                    "metadata": {},
                    "name": new_name,
                    "user_named": False,
                }
            )
            conn.execute(
                "INSERT INTO events (tag, event_id, event_type, status, data, insertion_order) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    rename_tag,
                    rename_event_id,
                    "TUISessionRename",
                    "active",
                    rename_data,
                    next_order,
                ),
            )
            conn.execute(
                "INSERT INTO active_tags (position, tag) VALUES (?, ?)",
                (next_pos, rename_tag),
            )

            conn.commit()

            # Get final event count for the status message
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.close()
        except Exception as e:
            # Clean up the copied file on error
            if conn:
                conn.close()
            try:
                new_db_path.unlink()
            except Exception:
                pass
            return CommandResult.err(f"Failed to truncate session: {e}")

        # --- 7. Open the new session and switch into it ---
        try:
            new_storage = SQLiteStorageManager(new_db_path)
            new_sm = SessionManager(
                storage=new_storage,
                session_id=new_session_id,
                model=self.session_manager.model if self.session_manager else "",
                agent_cls=type(self.agent).__name__,
                working_dir=self.session_manager.working_dir if self.session_manager else "",
                resumed=True,
            )
        except Exception as e:
            return CommandResult.err(f"Failed to open time-travel session: {e}")

        # Build resume outputs to replay history
        import os as _os

        _in_nemo_term = bool(_os.environ.get("NEMO_RICH_URL"))
        outputs = build_resume_outputs(new_db_path, new_session_id, in_nemo_term=_in_nemo_term)

        outputs.append(
            TextOutput(
                f"\n⏳ Time-travel session created!\n"
                f"  Source: {source_session_id[:8]} ({original_name})\n"
                f"  Forked at tag: {target_tag}\n"
                f"  Events: {event_count}\n"
                f"  New session: {new_session_id[:8]}",
                "success",
            )
        )

        result = CommandResult(success=True, outputs=outputs)
        result.new_session_manager = new_sm
        return result


class CommandRegistry:
    """Registry of command instances."""

    _command_classes: dict[str, type[Command]] = {
        "help": HelpCommand,
        "exit": ExitCommand,
        "quit": ExitCommand,
        "clear": ClearCommand,
        "compact": CompactCommand,
        "context": ContextCommand,
        "edit": EditCommand,
        "ipython": IPythonCommand,
        "model": ModelCommand,
        "models": ModelsCommand,
        "switch": SwitchCommand,
        "theme": ThemeCommand,
        "history": HistoryCommand,
        "mcp": MCPCommand,
        "skills": SkillsCommand,
        "todo": TodoCommand,
        "python": PythonCommand,
        "goal-mode": GoalModeCommand,
        "session": SessionCommand,
        "jobs": JobsCommand,
        "show-last-python": ShowLastPythonCommand,
        "events": EventsCommand,
        "time-travel": TimeTravelCommand,
        "trace-url": TraceUrlCommand,
    }

    def __init__(
        self,
        config: "TUIConfig",
        agent: "Agent",
        frontend: "Frontend",
        skills_dirs: list[Path] | None = None,
        mcp_file: Path | None = None,
        session_manager: "SessionManager | None" = None,
    ):
        self.config = config
        self.agent = agent
        self.frontend = frontend
        self.skills_dirs = skills_dirs
        self.mcp_file = mcp_file
        self.session_manager = session_manager
        self.startup_info: Output | None = None  # set by main after bootstrap
        self._commands: dict[str, Command] = self._register()
        self._user_skills: dict[str, _UserSkill] = self._discover_user_skills()
        self._auto_install_skills()

    def _register(self) -> dict[str, Command]:
        commands: dict[str, Command] = {}
        kwargs: dict[str, Any] = {
            "skills_dirs": self.skills_dirs,
            "mcp_file": self.mcp_file,
            "registry": self,
            "session_manager": self.session_manager,
        }
        for name, cls in self.get_all_command_classes().items():
            if not all(hasattr(self.agent, cap) for cap in cls.required_capabilities):
                continue
            commands[name] = cls(self.frontend, self.config, self.agent, **kwargs)
        return commands

    def _discover_user_skills(self) -> "dict[str, _UserSkill]":
        """Scan skills dirs for install-as:command skills and register them as slash commands.

        Uses rglob to match SkillManager.discover() — finds skills at any depth.
        Parses SKILL.md frontmatter inline to avoid depending on private nemo_oo_agents
        internals that may not be present in older installed versions.
        """
        skills: dict[str, _UserSkill] = {}
        if not self.skills_dirs:
            return skills
        try:
            import yaml
        except ImportError:
            return skills
        for skills_dir in self.skills_dirs:
            skills_dir = Path(skills_dir)
            if not skills_dir.is_dir():
                continue
            for skill_md in sorted(skills_dir.rglob("SKILL.md")):
                entry = skill_md.parent
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    if not content.startswith("---"):
                        continue
                    parts = content.split("---", 2)
                    if len(parts) < 3:
                        continue
                    try:
                        meta = yaml.safe_load(parts[1]) or {}
                        if not isinstance(meta, dict):
                            raise ValueError("not a mapping")
                    except Exception:
                        # Fallback: line-by-line regex for invalid-YAML values like
                        # argument-hint: "<action>" [issue-id]  (Claude Code style).
                        # Parse each scalar individually so "false" → False (not "false").
                        import re

                        meta = {}
                        for line in parts[1].splitlines():
                            m = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*):\s*(.+)$", line)
                            if not m:
                                continue
                            raw = m.group(2).strip()
                            try:
                                parsed = yaml.safe_load(raw)
                                meta[m.group(1)] = (
                                    str(parsed) if isinstance(parsed, list) else parsed
                                )
                            except Exception:
                                meta[m.group(1)] = raw
                    if not isinstance(meta, dict):
                        continue
                    # CC convention: user-invocable defaults to true.
                    # Opt out with user-invocable: false.
                    # install-as: command is honored for backward compat.
                    if meta.get("user-invocable") is False:
                        continue
                    name = str(meta.get("name", "")).strip()
                    if not name or name in self._commands or name in skills:
                        continue
                    description = str(meta.get("description", "")).strip()
                    body = parts[2].strip()
                    hint = meta.get("argument-hint")
                    if isinstance(hint, list):
                        # YAML parses [label] as a list; reconstruct bracket notation
                        hint = "[" + ", ".join(str(x) for x in hint) + "]"
                    elif hint is not None:
                        hint = str(hint)
                    skills[name] = _UserSkill(
                        name=name,
                        body=body,
                        description=description,
                        argument_hint=hint,
                    )
                except Exception as e:
                    logger.warning("Failed to load skill from %s: %s", entry, e)
        return skills

    def _auto_install_skills(self) -> None:
        """Attach all discovered skills as agent attributes at startup."""
        if not self.skills_dirs:
            return
        try:
            from nemo_oo_agents import SkillManager

            SkillManager.install(self.agent, skills_dir=self.skills_dirs)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Failed to auto-install skills: %s", e)

    def get_command(self, name: str) -> "Command | None":
        return self._commands.get(name.lower())

    def commands(self) -> "list[Command]":
        """Return the list of registered ``Command`` instances.

        Public surface so callers (e.g. ``Session._swap_session_manager``)
        don't have to reach into ``_commands`` directly.
        """
        return list(self._commands.values())

    def get_user_skill(self, name: str) -> "_UserSkill | None":
        return self._user_skills.get(name.lower())

    @classmethod
    def get_all_command_classes(cls) -> dict[str, type[Command]]:
        return cls._command_classes.copy()

    @classmethod
    def get_help(cls) -> dict[str, str]:
        commands: dict[str, str] = {}
        seen: set[type[Command]] = set()
        for cmd_cls in cls._command_classes.values():
            if cmd_cls not in seen:
                seen.add(cmd_cls)
                try:
                    commands.update(cmd_cls.help_text())
                except TypeError:
                    # Instance-method help_text() overrides can't be called on the class
                    pass
        return commands

    def get_active_help(self) -> dict[str, str]:
        commands: dict[str, str] = {}
        seen: set[type[Command]] = set()
        for cmd in self._commands.values():
            cls = type(cmd)
            if cls not in seen:
                seen.add(cls)
                commands.update(cmd.help_text())
        for skill in self._user_skills.values():
            key, desc = skill.help_entry()
            commands[key] = desc
        return commands

    def get_completions(self) -> dict[str, str]:
        help_text = self.get_active_help()
        completions: dict[str, str] = {}
        for cmd, desc in help_text.items():
            # Strip both <action> and [label] style argument hints
            clean = re.split(r"\s+(?=[<\[])", cmd, maxsplit=1)[0].strip()
            if clean and clean not in completions:
                completions[clean] = desc
        return dict(sorted(completions.items()))


class CommandHandler:
    """Parses slash-command input and dispatches to registered commands."""

    def __init__(self, registry: "CommandRegistry", frontend: "Frontend") -> None:
        self.registry = registry
        self.frontend = frontend

    async def handle(self, input_text: str) -> "CommandResult":
        if not input_text.startswith("/"):
            return CommandResult(False)

        try:
            parts = shlex.split(input_text[1:])
        except ValueError:
            parts = input_text[1:].split()

        if not parts:
            result = CommandResult.err("Empty command. Type /help for available commands.")
            for output in result.outputs:
                await self.frontend.render(output)
            return result

        cmd_name = parts[0].lower()
        args = parts[1:]

        # Check user-invocable skills before falling through to unknown-command error
        skill = self.registry.get_user_skill(cmd_name)
        if skill is not None:
            return CommandResult(success=True, agent_message=skill.make_agent_message(args))

        command = self.registry.get_command(cmd_name)
        if not command:
            all_classes = self.registry.get_all_command_classes()
            if cmd_name in all_classes:
                msg = f"/{cmd_name} is not available with this agent."
            else:
                suggestions = [c for c in all_classes if c.startswith(cmd_name[:2])][:3]
                suffix = (
                    f" Did you mean: {', '.join(f'/{s}' for s in suggestions)}?"
                    if suggestions
                    else ""
                )
                msg = f"Unknown command: /{cmd_name}.{suffix} Type /help."
            result = CommandResult.err(msg)
            for output in result.outputs:
                await self.frontend.render(output)
            return result

        is_valid, error_msg = command.validate_args(args)
        if not is_valid:
            result = CommandResult.err(error_msg or "Invalid arguments")
            for output in result.outputs:
                await self.frontend.render(output)
            return result

        try:
            result = await command.execute(args)
        except Exception as exc:
            result = CommandResult.err(f"Command failed: {exc}")
        # Render outputs in order.  _RichReplayPayload sentinels are intercepted
        # here (not forwarded to the frontend) and POSTed to NEMO_RICH_URL so
        # plots appear at their correct inline position between history turns.
        import os as _os

        _rich_url = (
            _os.environ.get("NEMO_RICH_URL")
            if any(isinstance(o, _RichReplayPayload) for o in result.outputs)
            else None
        )
        for output in result.outputs:
            if isinstance(output, _RichReplayPayload):
                if _rich_url:
                    try:
                        import httpx as _httpx

                        # _replay=True tells the browser to skip blank-line
                        # reservation so replayed plots don't push down the prompt.
                        _httpx.post(
                            _rich_url, json={**output.payload, "_replay": True}, timeout=5.0
                        )
                    except Exception:
                        pass
            else:
                await self.frontend.render(output)
        return result
