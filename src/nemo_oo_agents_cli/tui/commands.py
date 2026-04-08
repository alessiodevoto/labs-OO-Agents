# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Slash command parser and handlers for NeMo OO Agents TUI/Web.

Commands return structured ``CommandResult`` objects whose ``outputs`` list
is rendered by the active ``Frontend``.  No command calls the console or
frontend directly for its results — it only uses ``self.frontend`` for
interactive I/O that must happen *during* execution (spinners, prompts).
"""

from __future__ import annotations

import abc
import datetime
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from .output import (
    ClearScreen,
    DiffOutput,
    HelpOutput,
    HistoryReplay,
    HistoryTurn,
    Output,
    TableOutput,
    TextOutput,
)
from .session_manager import SessionManager

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
    new_session_manager: SessionManager | None = None
    # Set by CompactCommand to signal that auto-renaming should be retried.
    compact_done: bool = False

    # Convenience constructors -------------------------------------------

    @classmethod
    def ok(cls, *outputs: Output) -> CommandResult:
        return cls(success=True, outputs=list(outputs))

    @classmethod
    def err(cls, message: str) -> CommandResult:
        return cls(success=False, outputs=[TextOutput(message, "error")])

    @classmethod
    def bye(cls) -> CommandResult:
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
        frontend: Frontend,
        config: TUIConfig,
        agent: Agent,
        session_manager: SessionManager | None = None,
        **kwargs,
    ):
        if agent is None:
            raise ValueError("agent cannot be None.")
        self.frontend = frontend
        self.config = config
        self.agent: Any = agent
        self.session_manager = session_manager

    @abc.abstractmethod
    async def execute(self, args: list[str]) -> CommandResult:
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

    async def execute(self, args: list[str]) -> CommandResult:
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

    async def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.bye()


class ClearCommand(Command):
    @property
    def name(self) -> str:
        return "clear"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/clear": "Clear conversation history and terminal"}

    async def execute(self, args: list[str]) -> CommandResult:
        if hasattr(self.agent, "event_manager"):
            self.agent.event_manager.clear()
        return CommandResult.ok(ClearScreen())


class ModelCommand(Command):
    @property
    def name(self) -> str:
        return "model"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/model": "Show current model"}

    async def execute(self, args: list[str]) -> CommandResult:
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

    async def execute(self, args: list[str]) -> CommandResult:
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
        return {"/switch": "Interactive model switcher"}

    async def execute(self, args: list[str]) -> CommandResult:
        from unifiedllm import MODELS, get_llm_client

        models = sorted(MODELS.keys())
        try:
            selected = await self.frontend.get_input("Model: ", completions=models)
        except (KeyboardInterrupt, EOFError):
            return CommandResult.err("Model switch cancelled")

        if not selected:
            return CommandResult.err("No model selected")

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

    async def execute(self, args: list[str]) -> CommandResult:
        subcmd = args[0].lower()
        if subcmd == "status":
            return self._history_status()
        return self._history_tags()

    def _history_status(self) -> CommandResult:
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

    def _history_tags(self) -> CommandResult:
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

    async def execute(self, args: list[str]) -> CommandResult:
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
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /skills <list|activate|deactivate>"
        if args[0].lower() not in ("list", "activate", "deactivate"):
            return False, f"Unknown subcommand `{args[0]}`"
        if args[0].lower() in ("activate", "deactivate") and len(args) < 2:
            return False, f"Usage: /skills {args[0]} <skill_id>"
        return True, None

    async def execute(self, args: list[str]) -> CommandResult:
        try:
            from nemo_oo_agents import SkillManager
        except ImportError:
            return CommandResult.err("Skills not enabled. Run `uv sync --extra skills`.")

        subcmd = args[0].lower()
        subargs = args[1:]

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

    async def execute(self, args: list[str]) -> CommandResult:
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

    async def execute(self, args: list[str]) -> CommandResult:
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

    async def execute(self, args: list[str]) -> CommandResult:
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

    async def execute(self, args: list[str]) -> CommandResult:
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

    async def execute(self, args: list[str]) -> CommandResult:
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
            turns = SessionManager.load_turns(full_id)
            if not turns:
                return CommandResult.err(f"Session '{session_id}' is empty.")
            outputs: list[Output] = [
                HistoryReplay(
                    turns=[HistoryTurn(role=t.role, content=t.content) for t in turns],
                    session_id=full_id[:8],
                )
            ]

            # Open the old session's DB and swap it in as the active storage
            from nemo_oo_agents.storage import SQLiteStorageManager

            from .session_manager import SESSIONS_DIR

            session_db = SESSIONS_DIR / f"{full_id}.db"
            try:
                old_storage = SQLiteStorageManager(session_db)
                restored = old_storage.restore_latest_snapshot(self.agent)
                if restored:
                    outputs.append(
                        TextOutput(f"Agent state restored from session {full_id[:8]}.", "status")
                    )
                # Create a resumed SessionManager on the old DB
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
            if hasattr(self.agent, "event_manager"):
                self.agent.event_manager.clear()
            if hasattr(self.agent, "context"):
                try:
                    self.agent.context.clear()
                except Exception:
                    pass

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
                TextOutput("Started new session. History cleared.", "success"),
            )
            result.new_session_manager = new_sm
            return result

        return CommandResult.err(f"Unknown subcommand `{subcmd}`")


# ---------------------------------------------------------------------------
# Registry and handler
# ---------------------------------------------------------------------------


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
        "history": HistoryCommand,
        "mcp": MCPCommand,
        "skills": SkillsCommand,
        "sandbox": SandboxCommand,
        "python": PythonCommand,
        "session": SessionCommand,
    }

    def __init__(
        self,
        config: TUIConfig,
        agent: Agent,
        frontend: Frontend,
        skills_dirs: list[Path] | None = None,
        mcp_file: Path | None = None,
        session_manager: SessionManager | None = None,
    ):
        self.config = config
        self.agent = agent
        self.frontend = frontend
        self.skills_dirs = skills_dirs
        self.mcp_file = mcp_file
        self.session_manager = session_manager
        self._commands: dict[str, Command] = self._register()

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

    def get_command(self, name: str) -> Command | None:
        return self._commands.get(name.lower())

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
        return commands

    def get_completions(self) -> dict[str, str]:
        help_text = self.get_active_help()
        completions: dict[str, str] = {}
        for cmd, desc in help_text.items():
            clean = cmd.split("<")[0].strip()
            if clean and clean not in completions:
                completions[clean] = desc
        return dict(sorted(completions.items()))


class CommandHandler:
    """Parses slash-command input and dispatches to registered commands."""

    def __init__(self, registry: CommandRegistry, frontend: Frontend) -> None:
        self.registry = registry
        self.frontend = frontend

    async def handle(self, input_text: str) -> CommandResult:
        if not input_text.startswith("/"):
            return CommandResult(False)

        try:
            parts = shlex.split(input_text[1:])
        except ValueError:
            parts = input_text[1:].split()

        if not parts:
            return CommandResult.err("Empty command. Type /help for available commands.")

        cmd_name = parts[0].lower()
        args = parts[1:]

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
        for output in result.outputs:
            await self.frontend.render(output)
        return result
