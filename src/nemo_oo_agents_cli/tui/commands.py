# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Slash command parser and handlers for NeMo OO Agents TUI.

Uses Catppuccin Mocha theme from https://catppuccin.com/palette/
"""

import abc
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from .theme import COLORS


def _to_attr_name(name: str) -> str:
    """Convert a hyphenated server/skill name to a valid Python attribute name."""
    return name.replace("-", "_")


if TYPE_CHECKING:
    from nemo_oo_agents import Agent

    from .config import TUIConfig
    from .console import TUIConsole


@dataclass
class CommandResult:
    """Result from a command execution."""

    success: bool
    message: str = ""
    exit: bool = False


# ============================================================================
# Command Pattern: Base Command Interface
# ============================================================================


class Command(abc.ABC):
    """Abstract base class for all commands following the Command pattern."""

    # Agent attributes that must be present for this command to be registered.
    # CommandRegistry filters out commands whose required capabilities are absent.
    # Use frozenset() (the default) to indicate the command works with any agent.
    required_capabilities: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        console: "TUIConsole",
        config: "TUIConfig",
        agent: "Agent",
        **kwargs,
    ):
        """Initialize command with dependencies.

        Args:
            console: TUI console for output
            config: Application configuration
            agent: Any chat agent subclassing NeMo OO Agents
            **kwargs: Extra arguments (e.g. skills_dirs, mcp_file)

        Raises:
            ValueError: If agent is None
        """
        if agent is None:
            raise ValueError(
                "agent cannot be None. Commands require a valid NeMo OO Agents instance."
            )
        self.console = console
        self.config = config
        self.agent: Any = agent  # duck-typed; capabilities checked via hasattr

    @abc.abstractmethod
    async def execute(self, args: list[str]) -> CommandResult:
        """Execute the command.

        Args:
            args: Command arguments

        Returns:
            CommandResult with success status and message
        """
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return the command name."""
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def help_text(cls) -> dict[str, str]:
        """Return help text for this command.

        Returns:
            Dict mapping command strings (e.g., "/command" or "/command subcmd")
            to their descriptions. For commands with subcommands, include all variants.
        """
        raise NotImplementedError

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        """Validate command arguments.

        Args:
            args: Command arguments

        Returns:
            Tuple of (is_valid, error_message). If is_valid is False, error_message
            should contain a helpful error message with expected usage. If is_valid is True,
            error_message should be None.
        """
        if len(args) > 0:
            return False, f"Usage: /{self.name}"
        return True, None


# ============================================================================
# Concrete Command Classes
# ============================================================================


class HelpCommand(Command):
    def __init__(
        self,
        console: "TUIConsole",
        config: "TUIConfig",
        agent: "Agent",
        **kwargs,
    ):
        super().__init__(console, config, agent, **kwargs)
        # Injected by CommandRegistry so help only shows commands available for this agent
        self._registry = kwargs.get("registry")

    @property
    def name(self) -> str:
        return "help"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/help": "Show this help message"}

    async def execute(self, args: list[str]) -> CommandResult:
        if self._registry is not None:
            commands_dict = self._registry.get_active_help()
        else:
            commands_dict = CommandRegistry.get_help()  # fallback (standalone use)
        self.console.print_help(commands_dict)
        return CommandResult(True)


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
        """Exit the TUI."""
        self.console.print_status("Goodbye! Stay vibing.")
        return CommandResult(True, exit=True)


class ClearCommand(Command):
    @property
    def name(self) -> str:
        return "clear"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/clear": "Clear conversation history and terminal"}

    async def execute(self, args: list[str]) -> CommandResult:
        # Clear event history when the agent supports it
        if hasattr(self.agent, "event_manager"):
            self.agent.event_manager.clear()
        self.console.console.clear()
        return CommandResult(True)


class ModelCommand(Command):
    @property
    def name(self) -> str:
        return "model"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/model": "Show current model"}

    async def execute(self, args: list[str]) -> CommandResult:
        self.console.print_info(
            f"Current model: [bold {COLORS['green']}]{self.config.default_model}[/]"
        )
        return CommandResult(True)

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if len(args) > 0:
            return (
                False,
                "Usage: /model. Note: To switch models, use the interactive /switch command.",
            )
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

        # Group models by provider
        providers: dict[str, list[str]] = {}
        for model_name in sorted(MODELS.keys()):
            parts = model_name.split("/")
            provider = parts[0] if parts else "unknown"
            if provider not in providers:
                providers[provider] = []
            providers[provider].append(model_name)

        self.console.console.print(f"\n[bold {COLORS['mauve']}]Available Models[/]\n")

        for provider, models in sorted(providers.items()):
            self.console.console.print(f"  [{COLORS['lavender']}]{provider}[/]:")
            for model in models:
                is_current = model == self.config.default_model
                marker = f"[{COLORS['green']}]→[/] " if is_current else "  "
                self.console.console.print(f"    {marker}[{COLORS['text']}]{model}[/]")
        self.console.console.print(f"\n  [{COLORS['subtext1']}]Use /switch to change[/]")
        self.console.console.print()
        return CommandResult(True)


class SwitchCommand(Command):
    # bash is the proxy for "full codeact TUIAgent whose respond() uses the LLM"
    required_capabilities: ClassVar[frozenset[str]] = frozenset({"bash"})

    @property
    def name(self) -> str:
        return "switch"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {"/switch": "Interactive model switcher"}

    async def execute(self, args: list[str]) -> CommandResult:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter

        from unifiedllm import MODELS, get_llm_client

        models = sorted(MODELS.keys())
        completer = WordCompleter(models, ignore_case=True)

        self.console.console.print(
            f"\n[{COLORS['subtext1']}]Type model name (Tab to complete, Enter to select, Ctrl+C to cancel)[/]"
        )
        self.console.console.print(
            f"[{COLORS['subtext1']}]Current: [{COLORS['green']}]{self.config.default_model}[/][/]\n"
        )

        try:
            session = PromptSession(completer=completer, complete_while_typing=True)
            selected = (await session.prompt_async("Model: ")).strip()
        except KeyboardInterrupt:
            return CommandResult(False, "Model switch cancelled")

        if not selected:
            return CommandResult(False, "No model selected")

        if selected not in MODELS:
            return CommandResult(
                False,
                f"Model `{selected}` not found in registry. Use /models to see available options.",
            )

        self.config.default_model = selected
        try:
            self.agent._llm = get_llm_client(selected)
        except Exception as e:
            return CommandResult(False, f"Failed to switch model: {e}")
        self.console.print_info(f"Switched to model: [bold {COLORS['green']}]{selected}[/]")
        return CommandResult(True)


# ============================================================================
# History Commands
# ============================================================================


class HistoryCommand(Command):
    # get_summarization_status is only on TUIAgent (PassthroughAgent has no summarizer)
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
        subcmd = args[0].lower()
        if subcmd not in ("status", "tags"):
            return False, f"Unknown subcommand: `{subcmd}`. Usage: /history <status|tags>"
        return True, None

    async def execute(self, args: list[str]) -> CommandResult:
        subcmd = args[0].lower()

        if subcmd == "status":
            return await self._history_status()
        elif subcmd == "tags":
            return await self._history_tags()
        else:
            return CommandResult(
                False, f"Unknown subcommand: `{subcmd}`. Usage: /history <status|tags>"
            )

    async def _history_status(self) -> CommandResult:
        status = self.agent.get_summarization_status()

        self.console.console.print(f"\n[bold {COLORS['mauve']}]History Status[/]\n")
        self.console.console.print(
            f"  Active events: [bold {COLORS['peach']}]{status['active_events']}[/]"
        )
        self.console.console.print(
            f"  Summarization policy: [bold {COLORS['sapphire']}]{status['policy']}[/]"
        )

        if status["has_summarizer"] and status["max_tokens"] > 0:
            current = status["current_tokens"]
            max_tokens = status["max_tokens"]
            pct = (current / max_tokens * 100) if max_tokens > 0 else 0
            if pct >= 80:
                color = COLORS["red"]
            elif pct >= 50:
                color = COLORS["yellow"]
            else:
                color = COLORS["green"]
            self.console.console.print(
                f"  Token usage: [bold {color}]{current:,}[/] / {max_tokens:,} ({pct:.1f}%)"
            )
            self.console.console.print(
                f"  Preserve recent: [bold]{status['preserve_recent']}[/] events"
            )

        self.console.console.print(
            f"  Summary count: [bold {COLORS['yellow']}]{status['summary_count']}[/]"
        )

        if status["summary_tags"]:
            tags_str = (
                f"[{COLORS['yellow']}]"
                + f"[/], [{COLORS['yellow']}]".join(status["summary_tags"])
                + "[/]"
            )
            self.console.console.print(f"  Summary tags: {tags_str}")

        self.console.console.print()
        return CommandResult(True)

    async def _history_tags(self) -> CommandResult:
        tags = self.agent.event_manager.keys()

        self.console.console.print(
            f"\n[bold {COLORS['mauve']}]Active History Tags[/] "
            f"([{COLORS['peach']}]{len(tags)}[/] total)\n"
        )

        for tag in tags[:20]:
            event = self.agent.event_manager[tag]
            event_type = event.event_type if hasattr(event, "event_type") else type(event).__name__
            is_summary = ".." in tag
            tag_color = COLORS["yellow"] if is_summary else COLORS["blue"]
            self.console.console.print(
                f"  [{tag_color}]{tag}[/]: [{COLORS['subtext1']}]{event_type}[/]"
            )

        if len(tags) > 20:
            self.console.console.print(f"  [{COLORS['overlay1']}]... and {len(tags) - 20} more[/]")

        self.console.console.print()
        return CommandResult(True)


# ============================================================================
# MCP Commands
# ============================================================================


class MCPCommand(Command):
    # MCP tools are only useful if the agent runs CodeAct and can invoke them.
    # bash is the proxy for "full codeact TUIAgent".
    required_capabilities: ClassVar[frozenset[str]] = frozenset({"bash"})

    def __init__(
        self,
        console: "TUIConsole",
        config: "TUIConfig",
        agent: "Agent",
        **kwargs,
    ):
        super().__init__(console, config, agent, **kwargs)
        self.mcp_file = kwargs.get("mcp_file")
        # Track active MCP connections explicitly to avoid false positives from hasattr()
        # Store in command instance to avoid attribute name clashes on the agent
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
        subcmd = args[0].lower()
        if subcmd not in ("list", "connect", "disconnect"):
            return False, f"Unknown subcommand: `{subcmd}`. Usage: /mcp <list|connect|disconnect>"
        if subcmd in ("connect", "disconnect"):
            if len(args) < 2:
                return False, f"Usage: /mcp {subcmd} <server_name>"
        return True, None

    async def execute(self, args: list[str]) -> CommandResult:
        subcmd = args[0].lower()
        subargs = args[1:] if len(args) > 1 else []

        # Lazy import to avoid module-level side effects and enable test mocking
        try:
            from mcp_nemo_oo_agents import MCPManager
        except ImportError:
            return CommandResult(
                False,
                "MCP is not enabled. To use MCP, run `uv sync --extra mcp` and restart the TUI.",
            )

        servers = MCPManager.list_servers(self.mcp_file)

        if subcmd == "list":
            # Use explicit connection tracking instead of hasattr() to avoid false positives
            self.console.print_table(
                "MCP Servers",
                ["Server", "Connected"],
                [[server, "✓" if server in self._mcp_connections else ""] for server in servers],
            )
            self.console.print_info(f"Using mcp file: {self.mcp_file}")
            return CommandResult(True)

        elif subcmd == "connect":
            server_name = subargs[0]
            if server_name not in servers:
                return CommandResult(
                    False,
                    f"MCP server `{server_name}` not found. Use /mcp list to see available servers.",
                )
            try:
                self.console.start_spinner(message=f"Connecting to MCP server `{server_name}`...")
                tool = MCPManager.create_from_server(server_name, mcp_file=self.mcp_file)
                setattr(self.agent, _to_attr_name(server_name), tool)
                # Track connection explicitly in command instance
                self._mcp_connections.add(server_name)
                return CommandResult(True, f"MCP server `{server_name}` connected")
            except Exception as e:
                return CommandResult(False, f"Failed to connect to MCP server `{server_name}`: {e}")
            finally:
                self.console.stop_spinner()

        elif subcmd == "disconnect":
            server_name = subargs[0]
            try:
                # Check explicit connection tracking instead of hasattr()
                if server_name not in self._mcp_connections:
                    return CommandResult(
                        False,
                        f"MCP server `{server_name}` not connected. Use /mcp list to see connected servers.",
                    )
                self._mcp_connections.discard(server_name)
                delattr(self.agent, _to_attr_name(server_name))
                return CommandResult(True, f"MCP server `{server_name}` disconnected")
            except Exception as e:
                return CommandResult(
                    False, f"Failed to disconnect from MCP server `{server_name}`: {e}"
                )

        else:
            return CommandResult(
                False, f"Unknown subcommand: `{subcmd}`. Usage: /mcp <list|connect|disconnect>"
            )


# ============================================================================
# Skills Commands
# ============================================================================


class SkillsCommand(Command):
    # Skills are only useful if the agent runs CodeAct and can invoke them.
    # bash is the proxy for "full codeact TUIAgent".
    required_capabilities: ClassVar[frozenset[str]] = frozenset({"bash"})

    def __init__(
        self,
        console: "TUIConsole",
        config: "TUIConfig",
        agent: "Agent",
        **kwargs,
    ):
        super().__init__(console, config, agent, **kwargs)
        self.skills_dirs = kwargs.get("skills_dirs")
        # Track active skills explicitly to avoid false positives from hasattr()
        # Store in command instance to avoid attribute name clashes on the agent
        self._active_skills: set[str] = set()

    @property
    def name(self) -> str:
        return "skills"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/skills list": "List available skills (skills are reloaded automatically on each invocation)",
            "/skills activate <id>": "Activate a skill",
            "/skills deactivate <id>": "Deactivate a skill",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /skills <list|activate|deactivate>"
        subcmd = args[0].lower()
        if subcmd not in ("list", "activate", "deactivate"):
            return (
                False,
                f"Unknown subcommand: `{subcmd}`. Usage: /skills <list|activate|deactivate>",
            )
        if subcmd in ("activate", "deactivate"):
            if len(args) < 2:
                return False, f"Usage: /skills {subcmd} <skill_id>"
        return True, None

    async def execute(self, args: list[str]) -> CommandResult:
        try:
            from nemo_oo_agents import SkillManager
        except ImportError:
            return CommandResult(
                False,
                "Skills are not enabled. To use skills, run `uv sync --extra skills` and restart the TUI.",
            )

        subcmd = args[0].lower()
        subargs = args[1:] if len(args) > 1 else []

        if subcmd == "list":
            if not self.skills_dirs:
                self.console.print_info("No skills directories configured")
                return CommandResult(True)

            skills_dict = SkillManager.discover(self.skills_dirs)

            if not skills_dict:
                self.console.print_info(
                    "No skills found. Add .md files to .cursor/skills/ or .claude/skills/"
                )
                return CommandResult(True)

            rows = [
                [
                    skill_id,
                    "✓" if skill_id in self._active_skills else "",
                    skill.description,  # type: ignore[attr-defined]
                ]
                for skill_id, skill in sorted(skills_dict.items(), key=lambda x: x[0])
            ]

            self.console.print_table("Skills", ["ID", "Active", "Description"], rows)
            self.console.print_info(f"Using skills directories: {self.skills_dirs}")
            return CommandResult(True)

        elif subcmd == "activate":
            skill_id = subargs[0]

            if not self.skills_dirs:
                return CommandResult(False, "No skills directories configured")

            # Check if skill exists first
            available = SkillManager.discover(self.skills_dirs)
            if skill_id not in available:
                error_message = (
                    f"Skill `{skill_id}` not found. Use /skills list to see available skills."
                )
                return CommandResult(False, error_message)

            # Then check if it's already active
            if skill_id in self._active_skills:
                error_message = f"Skill `{skill_id}` is already activated"
                return CommandResult(False, error_message)

            try:
                attr_name = _to_attr_name(skill_id)
                skill_obj = available[skill_id]
                setattr(self.agent, attr_name, skill_obj)
                # Track activation explicitly in command instance
                self._active_skills.add(skill_id)
                return CommandResult(True, f"Skill `{skill_id}` activated")
            except Exception as e:
                return CommandResult(False, f"Failed to activate skill `{skill_id}`: {e}")

        elif subcmd == "deactivate":
            skill_id = subargs[0]

            try:
                # Check explicit connection tracking instead of hasattr()
                if skill_id not in self._active_skills:
                    return CommandResult(
                        False,
                        f"Skill `{skill_id}` not active. Use /skills list to see active skills.",
                    )

                delattr(self.agent, _to_attr_name(skill_id))
                self._active_skills.discard(skill_id)
                return CommandResult(True, f"Skill `{skill_id}` deactivated")
            except Exception as e:
                return CommandResult(False, f"Failed to deactivate skill `{skill_id}`: {e}")

        else:
            self.console.print_error(
                f"Unknown subcommand: <{subcmd}>. Usage: /skills <list|activate|deactivate>"
            )
            return CommandResult(False, f"Unknown subcommand: `{subcmd}`")


# ============================================================================
# Sandbox Commands
# ============================================================================


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
        subcmd = args[0].lower()
        if subcmd not in ("status", "enable", "disable"):
            return (
                False,
                f"Unknown subcommand: `{subcmd}`. Usage: /sandbox <status|enable|disable>",
            )
        return True, None

    async def execute(self, args: list[str]) -> CommandResult:
        subcmd = args[0].lower()

        if subcmd == "status":
            return await self._sandbox_status()
        elif subcmd == "enable":
            return await self._sandbox_enable()
        elif subcmd == "disable":
            return await self._sandbox_disable()
        else:
            return CommandResult(
                False,
                f"Unknown subcommand: `{subcmd}`. Usage: /sandbox <status|enable|disable>",
            )

    async def _sandbox_status(self) -> CommandResult:
        available = self.agent.bash.sandbox_available
        enabled = self.agent.bash.use_sandbox

        self.console.console.print(f"\n[bold {COLORS['mauve']}]Sandbox Status[/]\n")
        self.console.console.print(
            f"  SRT available: [bold {COLORS['green'] if available else COLORS['red']}]{'Yes' if available else 'No'}[/]"
        )
        self.console.console.print(
            f"  Sandbox enabled: [bold {COLORS['green'] if enabled else COLORS['yellow']}]{'Yes' if enabled else 'No'}[/]"
        )

        if enabled and not available:
            self.console.console.print(
                f"  [{COLORS['yellow']}]Warning: Sandbox enabled but SRT not available. Commands will run unsandboxed.[/]"
            )
        elif enabled and available:
            self.console.console.print(
                f"  [{COLORS['green']}]Bash commands are running in SRT sandbox.[/]"
            )
        else:
            self.console.console.print(
                f"  [{COLORS['subtext1']}]Bash commands are running without sandbox.[/]"
            )

        self.console.console.print()
        return CommandResult(True)

    async def _sandbox_enable(self) -> CommandResult:
        if not self.agent.bash.sandbox_available:
            error_message = "SRT sandbox not available. Install SRT: npm install -g @anthropic-ai/sandbox-runtime"
            return CommandResult(False, error_message)

        if self.agent.bash.use_sandbox:
            return CommandResult(True, "Sandbox already enabled")

        self.agent.bash.use_sandbox = True
        return CommandResult(True, "Sandbox enabled for bash commands")

    async def _sandbox_disable(self) -> CommandResult:
        if not self.agent.bash.use_sandbox:
            return CommandResult(False, "Sandbox is already disabled")

        self.agent.bash.use_sandbox = False
        return CommandResult(True, "Sandbox disabled for bash commands")


# ============================================================================
# Python Output Commands
# ============================================================================


class PythonCommand(Command):
    """Toggle display of the Python execution panel (code + output)."""

    def __init__(
        self,
        console: "TUIConsole",
        config: "TUIConfig",
        agent: "Agent",
        **kwargs,
    ):
        super().__init__(console, config, agent, **kwargs)
        self._streaming_display = kwargs.get("streaming_display")

    @property
    def name(self) -> str:
        return "python"

    @classmethod
    def help_text(cls) -> dict[str, str]:
        return {
            "/python status": "Show whether Python execution display is on or off",
            "/python on": "Enable Python execution display (code + output panels)",
            "/python off": "Suppress Python execution display",
        }

    def validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        if not args:
            return False, "Usage: /python <status|on|off>"
        subcmd = args[0].lower()
        if subcmd not in ("status", "on", "off"):
            return False, f"Unknown subcommand: `{subcmd}`. Usage: /python <status|on|off>"
        return True, None

    async def execute(self, args: list[str]) -> CommandResult:
        subcmd = args[0].lower()

        if self._streaming_display is None:
            return CommandResult(False, "Streaming display not available.")

        if subcmd == "status":
            state = "on" if self._streaming_display.show_python else "off"
            self.console.print_info(f"Python execution display: [{COLORS['green']}]{state}[/]")
            return CommandResult(True)

        if subcmd == "on":
            if self._streaming_display.show_python:
                return CommandResult(True, "Python execution display is already on.")
            self._streaming_display.show_python = True
            return CommandResult(True, "Python execution display enabled.")

        # off
        if not self._streaming_display.show_python:
            return CommandResult(True, "Python execution display is already off.")
        self._streaming_display.show_python = False
        return CommandResult(True, "Python execution display suppressed.")


# ============================================================================
# Command Registry
# ============================================================================


class CommandRegistry:
    """Registry of command instances.

    This is the single source of truth for command registration and instantiation.
    """

    # Command classes (shared across all instances)
    _command_classes: dict[str, type[Command]] = {
        "help": HelpCommand,
        "exit": ExitCommand,
        "quit": ExitCommand,  # Alias
        "clear": ClearCommand,
        "model": ModelCommand,
        "models": ModelsCommand,
        "switch": SwitchCommand,
        "history": HistoryCommand,
        "mcp": MCPCommand,
        "skills": SkillsCommand,
        "sandbox": SandboxCommand,
        "python": PythonCommand,
    }

    def __init__(
        self,
        config: "TUIConfig",
        agent: "Agent",
        console: "TUIConsole",
        skills_dirs: "list[Path] | None" = None,
        mcp_file: "Path | None" = None,
        streaming_display=None,
    ):
        """Initialize registry with command instances.

        Args:
            config: Application configuration
            agent: Any chat agent subclassing NeMo OO Agents
            skills_dirs: Directories to search for skills
            mcp_file: Path to MCP config file
            streaming_display: StreamingDisplay instance (for /python command)
        """
        self.config = config
        self.agent = agent
        self.skills_dirs = skills_dirs
        self.mcp_file = mcp_file
        self.console = console
        self.streaming_display = streaming_display
        self._commands: dict[str, Command] = self.register_commands()

    def register_commands(self) -> dict[str, Command]:
        """Register commands supported by the current agent.

        Commands whose ``required_capabilities`` are not present on the agent
        are silently omitted, so ``/help`` only lists available commands.
        """
        commands: dict[str, Command] = {}
        kwargs = {
            "skills_dirs": self.skills_dirs,
            "mcp_file": self.mcp_file,
            "registry": self,  # injected into HelpCommand for accurate /help output
            "streaming_display": self.streaming_display,  # injected into PythonCommand
        }
        for cmd_name, cmd_class in self.get_all_command_classes().items():
            if not all(hasattr(self.agent, cap) for cap in cmd_class.required_capabilities):
                continue
            command = cmd_class(self.console, self.config, self.agent, **kwargs)
            commands[cmd_name] = command
        return commands

    def get_command(self, name: str) -> Command | None:
        """Get command instance by name.

        Args:
            name: Command name

        Returns:
            Command instance or None if not found
        """
        return self._commands.get(name.lower())

    @classmethod
    def get_all_command_classes(cls) -> dict[str, type[Command]]:
        """Get all registered command classes.

        Returns:
            Dict mapping command names to command classes
        """
        return cls._command_classes.copy()

    @classmethod
    def get_help(cls) -> dict[str, str]:
        """Generate help text dict from all registered commands.

        This ensures the help text and command registry stay in sync.
        Each command class provides its help text via the help_text() classmethod.

        Returns:
            Dict mapping command strings to descriptions for help display
        """
        commands: dict[str, str] = {}
        # Use set to avoid processing the same class multiple times (e.g., ExitCommand for exit/quit)
        processed_classes: set[type[Command]] = set()
        for cmd_class in cls._command_classes.values():
            if cmd_class not in processed_classes:
                processed_classes.add(cmd_class)
                # Get help text from each command class
                help_dict = cmd_class.help_text()
                commands.update(help_dict)
        return commands

    def get_active_help(self) -> dict[str, str]:
        """Generate help text for currently registered (available) commands only.

        Unlike the classmethod ``get_help()``, this reflects what is actually
        usable with the current agent — commands filtered by ``required_capabilities``
        are omitted.
        """
        commands: dict[str, str] = {}
        processed_classes: set[type[Command]] = set()
        for command in self._commands.values():
            cmd_class = type(command)
            if cmd_class not in processed_classes:
                processed_classes.add(cmd_class)
                commands.update(cmd_class.help_text())
        return commands

    def get_completions(self) -> dict[str, str]:
        """Get completion options (commands without arg placeholders).

        Returns:
            Dict mapping command names (without args) to descriptions for completion
        """
        help_text = self.get_active_help()
        completions: dict[str, str] = {}
        for cmd, desc in help_text.items():
            # Strip argument placeholders like "<name>" for cleaner completion
            clean_cmd = cmd.split("<")[0].strip()
            if clean_cmd and clean_cmd not in completions:
                completions[clean_cmd] = desc
        return dict(sorted(completions.items()))


class CommandHandler:
    """Command invoker that parses input and executes commands using the Command pattern."""

    def __init__(
        self,
        registry: CommandRegistry,
        console: "TUIConsole",
    ) -> None:
        """Initialize command handler.

        Args:
            registry: Command registry
            console: TUI console for output
        """
        self.registry = registry
        self.console = console

    async def handle(self, input_text: str) -> CommandResult:
        """Parse and execute a slash command.

        Args:
            input_text: The full input starting with /

        Returns:
            CommandResult with success status and message
        """
        if not input_text.startswith("/"):
            return CommandResult(False, "Not a command")

        parts = shlex.split(input_text[1:])
        if not parts:
            return CommandResult(False, "Empty command. Type /help for available commands.")

        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        command = self.registry.get_command(cmd)
        if not command:
            all_classes = self.registry.get_all_command_classes()
            if cmd in all_classes:
                msg = f"Command /{cmd} is not available with this agent. Type /help for available commands."
            else:
                suggestions = [c for c in all_classes if c.startswith(cmd[:2])][:3]
                suffix = (
                    f" Did you mean: {', '.join(f'/{s}' for s in suggestions)}?"
                    if suggestions
                    else ""
                )
                msg = f"Unknown command: /{cmd}. Type /help for available commands.{suffix}"
            self.console.print_error(msg)
            return CommandResult(False, msg)

        is_valid, error_msg = command.validate_args(args)
        if not is_valid:
            self.console.print_error(error_msg or "Invalid arguments")
            return CommandResult(False, error_msg or "Invalid arguments")

        result = await command.execute(args)
        if result.message:
            if not result.success:
                self.console.print_error(result.message)
            else:
                self.console.print_success(result.message)
        return result
