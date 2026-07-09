# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TUI Configuration — inspect and manage NeMo OO Agents TUI settings."""

from pathlib import Path
from typing import Any, Literal

from nemo_oo_agents.skill import Skill, slash_command

# Friendly ``/config set`` keys → settings paths.
# Keeps the short names users already know while the file uses field names.
_FRIENDLY_KEYS: dict[str, str] = {
    "model": "tui.default_model",
    "python": "tui.show_python",
    "vi": "tui.vi_mode",
    "trace": "tui.trace_dir",
    "libs_dirs": "tui.libs_dirs",
}


class TuiConfigurationSkill(Skill):
    """Inspect and manage TUI configuration: settings file, external skills, libs, models.

    The TUI reads configuration from layered ``settings.yaml`` files. This
    skill provides ``/config`` to view and edit settings, and documents the
    full schema so the agent can answer questions.

    ## Settings file

    Layered (last wins): user ``~/.config/nemo_oo/settings.yaml`` →
    project ``<project-root>/.nemo_oo/settings.yaml`` →
    ``$NEMO_OO_SETTINGS``. ``/config set`` writes the project-local file.

    ```yaml
    tui:
      default_model: claude-opus-4-8   # LLM model (litellm or unifiedllm alias)
      show_python: false               # Show Python execution panels
      vi_mode: false                   # Vi keybindings
      libs_dirs: ["/path/to/skills"]   # External library skill directories
      trace_dir: .nemo_oo/traces       # Trace output directory
      memory: "off"                    # global default: "off" | "session" | "project"
      # memory_path: memory.db         # explicit SQLite override (optional)
      # Per-agent sticky opt-in written by `/memory on|local|off`
      # (or `/config set memory session|project`):
      memory_agents:
        "nemo_oo_agents_cli.tui.agent:TUIAgent": "project"
      reflection: false                # idle-reflection default (/reflection on|off)
      reflection_agents:
        "nemo_oo_agents_cli.tui.agent:TUIAgent": true

      # MCP servers can be configured inline here. Use env vars for secrets
      # so tokens are not committed; ${VAR} is expanded when connecting.
      mcp_auto_connect: [maas]
      mcp_servers:
        maas:
          url: https://maas.stg.astra.nvidia.com/maas/confluence/mcp
          transport: streamable-http
          headers:
            Authorization: "Bearer ${MAAS_API_KEY}"
    ```

    ## External skills libraries (libs_dirs)

    ``libs_dirs`` is a YAML list of absolute paths. Each subdirectory with a
    ``pyproject.toml`` is loaded as a library skill (available as ``self.<name>``).

    Structure of a skills repo::

        my-skills/
        ├── gitlab_ci/
        │   ├── pyproject.toml   # [project.entry-points."nemo_oo_agents.skills"]
        │   ├── __init__.py      # exports a Skill subclass
        │   └── ...
        └── worktrees/
            ├── pyproject.toml
            └── __init__.py

    Each package's ``pyproject.toml`` declares its registry name::

        [project.entry-points."nemo_oo_agents.skills"]
        "nvzurich.gitlab_ci" = "gitlab_ci:GitLabCISkill"

    After adding ``libs_dirs``, restart the TUI. Skills appear as
    ``self.<lib_name>`` automatically. Use ``/skills`` to verify.

    ## MCP servers (mcp_servers)

    ``mcp_servers`` is the preferred single-file TUI MCP configuration. It uses
    the same per-server fields as ``.mcp.json``: ``command``/``args``/``env`` for
    stdio servers, or ``url``/``headers`` for HTTP servers. Set
    ``mcp_auto_connect = ["server-name"]`` to attach servers to the agent at TUI
    startup as ``self.<server_name>`` (hyphens become underscores). Environment
    variables in string values are expanded at connect time, e.g.
    ``Authorization = "Bearer ${MAAS_API_KEY}"``.

    ``mcp_file`` remains available for compatibility with VS Code / Claude-style
    ``.mcp.json`` files; inline ``mcp_servers`` override servers with the same
    name from the file.

    ## SKILL.md-based slash commands (skills_dirs)

    Separate from libs, the TUI scans these directories for Markdown-based
    user-invocable commands (SKILL.md with YAML frontmatter):

    - ``.cursor/skills/``, ``.claude/skills/``, ``.claude/commands/``
    - ``tui/skills/``, ``~/.claude/skills/``, ``~/.claude/commands/``

    ## CLI flags (override settings.yaml)

    ``--model``, ``--mcp-file``, ``--libs-dir``, ``--skills-dir``, ``--trace``,
    ``--no-trace``, ``--vi``, ``--python``, ``--agent <module:Class>``

    ## Environment variables

    - ``NEMO_OO_PROJECT_DIR`` — override project directory
    - ``NEMO_OO_USER_DIR`` — override user config dir

    ## Hot-reload

    After editing a library's source code::

        await self.libs.reload("library_name")
    """

    @slash_command(
        "config",
        argument_hint="<action> [key] [value]",
        completions=("show", "set", "save", "libs", "skills", "path"),
    )
    async def config_command(
        self,
        action: Literal["show", "set", "save", "libs", "skills", "path"] = "show",
        key: str = "",
        value: str = "",
    ) -> str:
        """Show or modify TUI configuration.

        /config          — show current effective configuration
        /config libs     — list libs_dirs and discovered libraries
        /config skills   — list skills_dirs and discovered skills
        /config set <k> <v> — update a key in settings.yaml
        /config save [project|user] [--dry-run] — save current runtime settings
        /config path     — show config file path
        """
        if action == "show":
            return self._show_config()
        elif action == "libs":
            return self._show_libs()
        elif action == "skills":
            return self._show_skills()
        elif action == "set":
            return self._set_config(f"{key} {value}".strip())
        elif action == "save":
            return self._save_config(f"{key} {value}".strip())
        elif action == "path":
            return f"Config file: {self._config_path()}"
        return "Unreachable"

    def _config_path(self, scope: Literal["project", "user"] = "project") -> Path:
        """Return the settings.yaml path for a writable scope."""
        from .settings import settings_path

        return settings_path(scope)

    def _load_raw(self) -> dict[str, Any]:
        """Load the merged, effective settings (all layers, last wins)."""
        from nemo_oo_agents.layered_config import load_layered_yaml

        from .settings import SETTINGS_ENV_VAR, SETTINGS_FILENAME

        return load_layered_yaml(SETTINGS_FILENAME, SETTINGS_ENV_VAR)

    def _show_config(self) -> str:
        """Format the current effective config for display."""
        import yaml

        from .settings import SETTINGS_ENV_VAR, SETTINGS_FILENAME, settings_present

        data = self._load_raw()
        if not data:
            return (
                f"No config file exists yet at `{self._config_path()}`. The TUI is running on defaults.\n\n"
                "To create one, edit the file directly or use `/config set <key> <value>`.\n"
                "Available keys: `model`, `python`, `vi`, `libs_dirs`, `trace`, `memory`, `memory_path`.\n\n"
                "Example: `/config set model claude-opus-4-8`"
            )
        content = yaml.safe_dump(data, sort_keys=False).rstrip()
        layers = "present" if settings_present() else "none"
        return (
            f"Effective TUI configuration (merged from {SETTINGS_FILENAME} layers; "
            f"{SETTINGS_ENV_VAR} override applies):\n\n"
            f"```yaml\n{content}\n```\n\n"
            f"`/config set` writes the project file (`{self._config_path()}`).\n"
            'To add an external skills library: `/config set libs_dirs ["/path/to/skills"]`\n'
            f"(layers: {layers}). Changes take effect on TUI restart."
        )

    def _show_libs(self) -> str:
        """List libs_dirs and what libraries are found in each."""
        data = self._load_raw()
        libs_dirs = data.get("tui", {}).get("libs_dirs", [])
        if not libs_dirs:
            return (
                "No external library directories are configured.\n\n"
                "To point the TUI at an external skills repo, add `libs_dirs` to "
                "`.nemo_oo/settings.yaml`:\n\n"
                '```yaml\ntui:\n  libs_dirs: ["/absolute/path/to/skills-repo"]\n```\n\n'
                "Each subdirectory with a `pyproject.toml` becomes a loadable skill "
                "(available as `self.<name>`). The `pyproject.toml` must declare an "
                'entry-point under `[project.entry-points."nemo_oo_agents.skills"]`.\n\n'
                "After editing settings.yaml, restart the TUI to pick up the new libraries."
            )

        lines = ["The following library directories are configured:\n"]
        for d in libs_dirs:
            p = Path(d)
            if not p.exists():
                lines.append(f"- `{d}` ⚠️ directory does not exist — check the path")
                continue
            libs = sorted(
                sub.name
                for sub in p.iterdir()
                if sub.is_dir()
                and not sub.name.startswith(".")
                and (sub / "pyproject.toml").exists()
            )
            if libs:
                lines.append(f"- `{d}` ({len(libs)} libraries)")
                for lib in libs:
                    lines.append(f"  - {lib}")
            else:
                lines.append(
                    f"- `{d}` (no valid library packages found — need pyproject.toml in subdirs)"
                )

        lines.append("")
        lines.append(
            "To add another directory: edit `.nemo_oo/settings.yaml` and append to the `libs_dirs` list."
        )
        lines.append('To reload a library after code changes: `await self.libs.reload("<name>")`')
        lines.append(
            'To activate a discovered but inactive library: `self.skills.activate(["<name>"])`'
        )
        return "\n".join(lines)

    def _show_skills(self) -> str:
        """List skills_dirs and discovered SKILL.md files."""
        from nemo_oo_agents.paths import find_project_root

        defaults = [
            Path(".cursor/skills"),
            Path(".claude/skills"),
            Path(".claude/commands"),
            Path("tui/skills"),
            Path.home() / ".claude" / "skills",
            Path.home() / ".claude" / "commands",
        ]
        root = find_project_root()
        lines = ["Discovered SKILL.md-based slash commands:\n"]
        found_any = False
        for d in defaults:
            resolved = d if d.is_absolute() else root / d
            if not resolved.exists():
                continue
            skills = sorted(
                sub.name
                for sub in resolved.iterdir()
                if sub.is_dir() and ((sub / "SKILL.md").exists() or (sub / "skill.md").exists())
            )
            if skills:
                found_any = True
                lines.append(f"- `{d}` ({len(skills)} skills)")
                for s in skills:
                    lines.append(f"  - /{s}")
        if not found_any:
            lines.append("No SKILL.md-based commands found in any default directory.")

        lines.append("")
        lines.append(
            "These are Markdown-based skills (not Python). Each is a directory with a SKILL.md file"
        )
        lines.append(
            "containing YAML frontmatter (`name`, `description`) and a body that becomes the prompt."
        )
        lines.append("")
        lines.append("To create a new one: make a directory in `.claude/commands/<name>/SKILL.md`.")
        lines.append(
            'To create a Python-based skill with methods and slash commands, use `self.libs.scaffold("<name>")`'
        )
        lines.append("and register it in `libs_dirs` (see `/config libs`).")
        return "\n".join(lines)

    def _set_config(self, args: str) -> str:
        """Set a key in the project settings.yaml.

        Reads the project file (only -- not the merged layers), updates one
        setting, and writes it back. Friendly aliases (``model``, ``python``,
        ``vi``, ``trace``) map to their ``tui.`` settings paths; dotted keys
        are treated as explicit nested paths.
        """
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            return "Usage: /config set <key> <value>\nExample: /config set model claude-opus-4-8"

        raw_key, raw_value = parts
        if raw_key == "memory":
            return self._set_agent_memory(raw_value)
        try:
            setting_path = self._setting_path(raw_key)
        except ValueError as exc:
            return f"Error: {exc}"
        value = self._parse_value(raw_value)
        path = self._config_path()

        from .settings import write_settings_updates

        path, _data = write_settings_updates({tuple(setting_path): value})
        dotted = ".".join(setting_path)
        return f"Set `{dotted} = {value!r}` in {path}\n⚠️ Restart TUI for changes to take effect."

    def _save_config(self, args: str) -> str:
        """Save safe current runtime TUI settings to a settings.yaml layer."""
        import yaml

        scope: Literal["project", "user"] = "project"
        dry_run = False
        for token in args.split():
            if token in {"project", "user"}:
                scope = token  # type: ignore[assignment]
            elif token == "--dry-run":
                dry_run = True
            else:
                return "Usage: /config save [project|user] [--dry-run]"

        runtime = self._runtime_tui_config()
        if runtime is None:
            return "Error: live TUI configuration is not available in this session."

        updates = self._runtime_tui_updates(runtime)
        from .settings import write_settings_updates

        path, data = write_settings_updates(updates, scope=scope, dry_run=dry_run)
        changed = ", ".join(".".join(setting_path) for setting_path in updates)
        rendered = yaml.safe_dump(data, sort_keys=False).rstrip()
        verb = "Would save" if dry_run else "Saved"
        return (
            f"{verb} current TUI settings to {scope} config: {path}\n"
            f"Keys: {changed}\n\n"
            f"```yaml\n{rendered}\n```"
        )

    def _runtime_tui_config(self) -> Any | None:
        agent = getattr(self, "_agent", None)
        registry = getattr(agent, "_command_registry", None)
        config = getattr(registry, "config", None)
        if config is not None:
            return config
        return getattr(agent, "_tui_config", None)

    def _runtime_tui_updates(self, config: Any) -> dict[tuple[str, ...], Any]:
        fields = (
            "default_model",
            "show_python",
            "goal_mode",
            "keep_going",
            "keep_going_model",
            "toolbar_snippet",
        )
        values = {name: getattr(config, name) for name in fields if hasattr(config, name)}

        vars_obj = getattr(getattr(self, "_agent", None), "vars", None)
        if vars_obj is not None:
            if "tui_keep_going" in vars_obj:
                values["keep_going"] = bool(vars_obj.get("tui_keep_going"))
            if "tui_keep_going_model" in vars_obj:
                values["keep_going_model"] = vars_obj.get("tui_keep_going_model")

        return {("tui", key): value for key, value in values.items() if value is not None}

    def _set_agent_memory(self, value: str) -> str:
        """Persist memory preference for the current agent, not globally."""
        value = value.strip().strip("\\\"'")
        if value not in {"off", "session", "project"}:
            return "Usage: /config set memory <off|session|project>"

        from .commands import _set_agent_memory_config

        path = _set_agent_memory_config(self._agent, value)
        key = getattr(self._agent, "_tui_memory_key", None)
        if key is None and self._agent is not None:
            key = f"{type(self._agent).__module__}:{type(self._agent).__qualname__}"
        return (
            f"Set memory `{value}` for agent `{key or 'default'}` in {path}\n"
            "⚠️ Restart TUI for changes to take effect."
        )

    @staticmethod
    def _setting_path(raw_key: str) -> list[str]:
        """Return the nested settings path for a user-facing key."""
        key = _FRIENDLY_KEYS.get(raw_key, raw_key)
        parts = key.split(".")
        if any(not part for part in parts):
            raise ValueError(f"Invalid config key: {raw_key!r}")
        if len(parts) == 1:
            parts.insert(0, "tui")
        return parts

    @staticmethod
    def _parse_value(raw: str) -> Any:
        """Parse a raw CLI string into a bool / int / list / str for YAML."""
        import yaml

        low = raw.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        try:
            return int(raw)
        except ValueError:
            pass
        # Allow inline YAML/JSON lists, e.g. ["a", "b"].
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = yaml.safe_load(raw)
                if isinstance(parsed, list):
                    return parsed
            except yaml.YAMLError:
                pass
        # Strip surrounding quotes if the user typed them.
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        return raw
