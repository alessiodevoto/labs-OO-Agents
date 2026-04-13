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
import re
import shlex
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

        result = CommandResult.ok(
            ClearScreen(),
            _RichReplayPayload(payload={"kind": "clear"}),
            TextOutput("Started new session. Previous session saved.", "success"),
        )
        result.new_session_manager = new_sm
        return result


class ModelCommand(Command):
    @property
    def name(self) -> str:
        return "model"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/model": "Show current model"}

    async def execute(self, args: list[str]) -> "CommandResult":
        return CommandResult.ok(TextOutput(f"Current model: {self.config.default_model}", "info"))

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) > 0:
            return False, "Usage: /model  (to switch, use /switch)"
        return True, None


class ModelsCommand(Command):
    @property
    def name(self) -> str:
        return "models"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/models": "List available models from registry"}

    async def execute(self, args: list[str]) -> "CommandResult":
        from unifiedllm import MODELS

        rows: list[list[str]] = []
        for model_name in sorted(MODELS.keys()):
            marker = "\u2192" if model_name == self.config.default_model else ""
            rows.append([model_name, marker])

        return CommandResult.ok(
            TableOutput(
                title="Available Models",
                columns=["Model", ""],
                rows=rows,
                footer="Use /switch to change",
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
        from unifiedllm import MODELS, get_llm_client

        selected = args[0]
        if selected not in MODELS:
            return CommandResult.err(
                f"Model `{selected}` not found. Use /models to see available options."
            )

        self.config.default_model = selected
        try:
            self.agent._llm = get_llm_client(selected)
        except Exception as e:
            return CommandResult.err(f"Failed to switch model: {e}")

        return CommandResult.ok(TextOutput(f"Switched to model: {selected}", "success"))


class ThemeCommand(Command):
    """Switch the color theme."""

    THEMES = ("mocha", "latte", "vsdark", "vslight")

    @property
    def name(self) -> str:
        return "theme"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/theme <mocha|latte|vsdark|vslight>": "Switch color theme"}

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) != 1:
            return False, f"Usage: /theme <{'|'.join(self.THEMES)}>"
        if args[0].lower() not in self.THEMES:
            return False, f"Theme must be one of: {', '.join(self.THEMES)}"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        from . import theme as theme_module

        name = args[0].lower()
        theme_module.set_theme(name)

        # Replace the base theme in Rich Console's ThemeStack
        # We can't just push - we need to replace the base entry
        if hasattr(self.frontend, "_console") and hasattr(self.frontend._console, "console"):
            console = self.frontend._console.console
            new_theme = theme_module.create_theme()

            # Directly replace the base theme in the stack
            # This is the only way to actually change colors since Theme snapshots
            # the COLORS dict at creation time
            console._theme_stack._entries[0] = new_theme.styles
            console._theme_stack.get = console._theme_stack._entries[-1].get

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
            from mcp_nemo_oo_agents import MCPManager
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
                        "/sandbox",
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
# Sandbox commands
# ---------------------------------------------------------------------------


class SandboxCommand(Command):
    required_capabilities: ClassVar[frozenset[str]] = frozenset({"bash"})

    @property
    def name(self) -> str:
        return "sandbox"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/sandbox status": "Show sandbox status",
            "/sandbox enable": "Enable sandbox for bash commands",
            "/sandbox disable": "Disable sandbox for bash commands",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /sandbox <status|enable|disable>"
        if args[0].lower() not in ("status", "enable", "disable"):
            return False, f"Unknown subcommand `{args[0]}`"
        return True, None

    async def execute(self, args: list[str]) -> "CommandResult":
        subcmd = args[0].lower()

        if subcmd == "status":
            available = self.agent.bash.sandbox_available
            enabled = self.agent.bash.use_sandbox
            note = ""
            if enabled and not available:
                note = "Warning: sandbox enabled but SRT not available \u2014 running unsandboxed"
            elif enabled:
                note = "Bash commands running in SRT sandbox"
            else:
                note = "Bash commands running without sandbox"

            rows = [
                ["SRT available", "Yes" if available else "No"],
                ["Sandbox enabled", "Yes" if enabled else "No"],
            ]
            outputs: list[Output] = [
                TableOutput(columns=["Field", "Value"], rows=rows, title="Sandbox Status")
            ]
            if note:
                outputs.append(TextOutput(note, "status"))
            return CommandResult.ok(*outputs)

        if subcmd == "enable":
            if not self.agent.bash.sandbox_available:
                return CommandResult.err("SRT not available. Install to use sandboxing.")
            if self.agent.bash.use_sandbox:
                return CommandResult.ok(TextOutput("Sandbox already enabled", "info"))
            self.agent.bash.use_sandbox = True
            return CommandResult.ok(TextOutput("Sandbox enabled", "success"))

        # disable
        if not self.agent.bash.use_sandbox:
            return CommandResult.ok(TextOutput("Sandbox already disabled", "info"))
        self.agent.bash.use_sandbox = False
        return CommandResult.ok(TextOutput("Sandbox disabled", "success"))


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
        summarizers = getattr(self.agent, "_summarizers", [])
        if summarizers:
            try:
                tokens_before = summarizers[0]._estimate_tokens()
            except Exception:
                pass

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
                self.agent.event_manager.clear()
                return CommandResult.ok(
                    TextOutput(
                        f"Summarization failed ({e}); cleared {events_before} events.", "warning"
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
# Edit command
# ---------------------------------------------------------------------------


class EditCommand(Command):
    """Open a file in $EDITOR (TUI) or Monaco (web) and show the diff on save."""

    @property
    def name(self) -> str:
        return "edit"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/edit <file>": "Open file in $EDITOR (TUI) or Monaco editor (web) \u2014 shows diff on save",
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

            result = CommandResult.ok(
                ClearScreen(),
                _RichReplayPayload(payload={"kind": "clear"}),
                TextOutput("Started new session. History cleared.", "success"),
            )
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


class CommandRegistry:
    """Registry of command instances."""

    _command_classes: dict[str, type[Command]] = {
        "help": HelpCommand,
        "exit": ExitCommand,
        "quit": ExitCommand,
        "clear": ClearCommand,
        "compact": CompactCommand,
        "edit": EditCommand,
        "model": ModelCommand,
        "models": ModelsCommand,
        "switch": SwitchCommand,
        "theme": ThemeCommand,
        "history": HistoryCommand,
        "mcp": MCPCommand,
        "skills": SkillsCommand,
        "sandbox": SandboxCommand,
        "python": PythonCommand,
        "session": SessionCommand,
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
                commands.update(cmd_cls.help_text())
        return commands

    def get_active_help(self) -> dict[str, str]:
        commands: dict[str, str] = {}
        seen: set[type[Command]] = set()
        for cmd in self._commands.values():
            cls = type(cmd)
            if cls not in seen:
                seen.add(cls)
                commands.update(cls.help_text())
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
