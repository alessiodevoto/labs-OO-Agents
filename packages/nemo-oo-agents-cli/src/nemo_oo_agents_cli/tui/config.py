# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configuration loading for NeMo OO Agents TUI.

Hydra-like config: structured Pydantic models with Config.load(**overrides).

Resolution order (last wins):
    1. Model defaults
    2. Layered ``settings.yaml`` (user → project → ``NEMO_OO_SETTINGS``),
       loaded via :mod:`nemo_oo_agents_cli.tui.settings`
    3. Keyword overrides from CLI args (_OVERRIDES map)

Usage:
    # From argparse
    config = Config.load(**vars(parse_args()))

    # From click
    config = Config.load(model="gpt-4o", orchestrator=True)

    # Programmatic
    config = Config.load()
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import CompletionClient

# Default model — direct litellm-supported name. Override via config or --model.
DEFAULT_MODEL = "claude-opus-4-8"


class SummarizationConfig(BaseModel):
    """Configuration for history summarization.

    ``max_tokens`` defaults to ``None`` meaning "80% of the LLM's context
    window, resolved at install time." The old 100K absolute was fine when
    models had ~200K context but fired at ~10% usage on 1M-context models
    like Opus 4.8, making summarization feel constant. Set an explicit
    integer to pin a specific threshold.
    """

    policy: Literal["token_budget", "sliding_window", "none"] = "token_budget"
    max_tokens: int | None = None
    window_size: int = 50
    preserve_recent: int = 10
    target_chars: int = 4000


class AgentConfig(BaseModel):
    """Configuration for the TUI agent's behavior."""

    # History summarization settings
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)

    # Orchestrator mode (multi-phase workflow)
    orchestrator: bool = False

    # Working directory for bash commands. Stored as a string (downstream
    # always str()s it); a Path is accepted and coerced for ergonomics.
    working_dir: str = "."

    @field_validator("working_dir", mode="before")
    @classmethod
    def _coerce_working_dir(cls, v: object) -> object:
        return str(v) if isinstance(v, Path) else v


class TUIConfig(BaseModel):
    """Configuration for the TUI presentation layer."""

    # MCP servers.  Inline ``mcp_servers`` in settings.yaml is the preferred single-file
    # configuration; ``mcp_file`` remains as a compatibility bridge for VS Code / Claude
    # style .mcp.json files.
    mcp_file: Path = Path(".mcp.json")
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mcp_auto_connect: list[str] = Field(default_factory=list)

    # Directories to search for skills and user-invocable commands.
    # Includes both project-local and user-global Claude/Cursor conventions.
    skills_dirs: list[Path] = Field(
        default_factory=lambda: [
            Path(".cursor/skills"),
            Path(".claude/skills"),
            Path(".claude/commands"),
            Path("tui/skills"),
            Path.home() / ".claude" / "skills",
            Path.home() / ".claude" / "commands",
        ]
    )

    # Extra directories containing library skill packages (each subdir is a skill).
    # Added via settings.yaml or --libs-dir CLI flag.
    libs_dirs: list[Path] = Field(default_factory=list)

    # Default LLM model (from unifiedllm registry)
    default_model: str = DEFAULT_MODEL

    # Trace output directory (None = OTLP auto-probe only; --trace writes files)
    trace_dir: Path | None = None

    # Vi keybindings in prompt_toolkit input
    vi_mode: bool = False

    # Custom agent spec: "module.path:ClassName" or "./file.py:ClassName"
    agent_spec: str | None = None

    # Show agent Python code execution panels (off by default)
    show_python: bool = False

    # Native scrollback with clear+rewrite transcript replay on resize.
    full_screen: bool = True
    # Long-term memory skill. Off by default; users can opt in per agent via
    # /config set memory session. memory_path is an explicit SQLite
    # path override.
    memory: Literal["off", "session"] = "off"
    memory_agents: dict[str, Literal["off", "session"]] = {}
    memory_path: Path | None = None

    # Goal mode: when on, unresolved todos auto-feed the agent after each turn
    goal_mode: bool = False

    # Keep-going mode: when on, audit DONE results and internally re-prompt if unfinished
    keep_going: bool = False

    # Model used by the keep-going stop-reason auditor. Required before /keep-going on.
    keep_going_model: str | None = None

    # Custom toolbar Python snippet (evaluated each render to produce label text).
    # Available vars: datetime, config, model, short_model, time, agent.
    toolbar_snippet: str | None = None


class Config(BaseModel):
    """Top-level configuration. Single source of truth.

    Usage:
        config = Config.load(**vars(args))   # from argparse
        config = Config.load(model="gpt-4o") # programmatic
        config = Config.load()               # pure defaults + env
    """

    tui: TUIConfig = Field(default_factory=TUIConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    # Runtime flags (not persisted)
    no_splash: bool = False
    no_trace: bool = False

    # ── Declarative mapping: kwarg name → dotted config path ──────────
    # Tuple form: (path, transform_fn) for type coercion.
    # String form: path only, value passed through as-is.
    _OVERRIDES: ClassVar[dict] = {
        "model": "tui.default_model",
        "mcp_file": ("tui.mcp_file", Path),
        "mcp_servers": "tui.mcp_servers",
        "mcp_auto_connect": (
            "tui.mcp_auto_connect",
            lambda v: [str(item) for item in v] if isinstance(v, (list, tuple, set)) else [str(v)],
        ),
        "trace": ("tui.trace_dir", Path),
        "context_limit": "agent.summarization.max_tokens",
        "orchestrator": "agent.orchestrator",
        "working_dir": "agent.working_dir",
        "no_splash": "no_splash",
        "no_trace": "no_trace",
        "vi": "tui.vi_mode",
        "agent": "tui.agent_spec",
        "python": "tui.show_python",
        "full_screen": "tui.full_screen",
        "libs_dirs": (
            "tui.libs_dirs",
            lambda v: [Path(p) for p in (v if isinstance(v, list) else [v])],
        ),
        "memory": "tui.memory",
        "memory_agents": "tui.memory_agents",
        "memory_path": ("tui.memory_path", Path),
    }

    # Fields that are argparse store_true flags (skip False values to avoid overwriting config)
    _STORE_TRUE_FLAGS: ClassVar[set[str]] = {
        "no_splash",
        "no_trace",
        "vi",
        "python",
        "full_screen",
    }

    @classmethod
    def load(cls, **overrides) -> "Config":
        """Build config: defaults → config file → overrides.

        Config file: layered ``settings.yaml`` (user → project →
        ``NEMO_OO_SETTINGS``), discovered through the shared layered-config
        helper. Accepts any keyword argument matching _OVERRIDES keys.
        Unknown keys are silently ignored, so you can pass ``**vars(args)``
        from argparse directly.
        """
        from .settings import load_settings

        # Layers 1-2: dataclass defaults, then layered settings.yaml.
        cfg = load_settings(cls())

        # Layer 3: explicit overrides (highest priority)
        for key, target in cls._OVERRIDES.items():
            val = overrides.get(key)
            if val is None:
                continue
            # Skip False values only for store_true flags (argparse store_true defaults are False)
            # This allows legitimate False overrides for other boolean fields like orchestrator
            if isinstance(val, bool) and not val and key in cls._STORE_TRUE_FLAGS:
                continue
            _set_nested(cfg, *_unpack_target(target, val))

        # ── Special-case overrides ────────────────────────────────────
        # --no-trace clears trace_dir
        if overrides.get("no_trace"):
            cfg.tui.trace_dir = None

        # ── Skills-dirs ordering ──────────────────────────────────────
        # Intentional precedence (first wins in SkillRegistry.discover_skills_dirs):
        #   1. --skills-dir from the CLI / env (user-explicit)
        #   2. Entry-point discovery (package-provided, e.g. wtf-issues)
        #   3. Default locations (~/.claude/commands, etc.) as fallback
        #
        # Before this ordering, ~/.claude/commands/wtf-status could shadow
        # a newer Python-based skill shipped by wtf-issues in its
        # skills-nemo/ directory — first-scan wins semantics meant the
        # user's explicit --skills-dir value lost to a defaulted
        # grab-bag.
        explicit: list[Path] = []
        entry_point: list[Path] = []

        extra_skills = overrides.get("skills_dir")
        if extra_skills:
            # Accept a single str/Path OR a list/tuple of them. The bare
            # fallback ``list(x)`` would iterate *characters* when given a
            # lone string, producing nonsense paths.
            if isinstance(extra_skills, (str, Path)):
                dirs: list = [extra_skills]
            elif isinstance(extra_skills, (list, tuple)):
                dirs = list(extra_skills)
            else:
                dirs = list(extra_skills)  # last-resort: any other iterable
            for d in dirs:
                p = Path(d)
                if p not in explicit:
                    explicit.append(p)

        try:
            from importlib.metadata import entry_points as _entry_points

            for _ep in _entry_points(group="nemo_oo_tui.skills_dirs"):
                try:
                    _d = Path(str(_ep.load()()))
                    if _d not in entry_point and _d not in explicit:
                        entry_point.append(_d)
                except Exception:
                    pass
        except Exception:
            pass

        defaults = [d for d in cfg.tui.skills_dirs if d not in explicit and d not in entry_point]
        cfg.tui.skills_dirs = explicit + entry_point + defaults

        # Python skill-library dirs shipped via entry points (group
        # "nemo_oo_tui.libs_dirs") — appended to libs_dirs so bootstrap's
        # discover_libs picks them up. Ships e.g. the inception lib.
        try:
            from importlib.metadata import entry_points as _entry_points

            for _ep in _entry_points(group="nemo_oo_tui.libs_dirs"):
                try:
                    _ld = Path(str(_ep.load()()))
                    if _ld not in cfg.tui.libs_dirs:
                        cfg.tui.libs_dirs.append(_ld)
                except Exception:
                    pass
        except Exception:
            pass

        # Filter skills and libs dirs to existing directories so a broken
        # entry-point path (e.g. an editable install pointing at a missing
        # dir) can't make discover_libs raise or silently fail.
        cfg.tui.skills_dirs = [d for d in cfg.tui.skills_dirs if d.exists()]
        cfg.tui.libs_dirs = [d for d in cfg.tui.libs_dirs if d.exists()]

        return cfg


# ── Helpers ───────────────────────────────────────────────────────────────


def _unpack_target(target, value):
    """Unpack a target spec into (path, transformed_value)."""
    if isinstance(target, tuple):
        path, transform = target
        return path, transform(value)
    return target, value


def _set_nested(obj, path: str, value):
    """Set a dotted attribute path on a nested config model."""
    parts = path.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


# ── LLM helpers ───────────────────────────────────────────────────────────


def get_llm(config: TUIConfig | Config) -> "CompletionClient":
    """Get the LLM client based on configuration.

    Accepts either a TUIConfig or the top-level Config.
    """
    from nemo_oo_agents.unifiedllm import MODELS, CompletionClient, get_llm_client

    tui = config.tui if isinstance(config, Config) else config
    model_name = tui.default_model

    if model_name in MODELS:
        return get_llm_client(model_name)

    return CompletionClient(model=model_name)


def list_models() -> list[str]:
    """List available models from the unifiedllm registry."""
    from nemo_oo_agents.unifiedllm import MODELS

    return sorted(MODELS.keys())


def load_agent_class(spec: str) -> type:
    """Load an agent class from a 'module:ClassName' or './file.py:ClassName' spec.

    Args:
        spec: Agent spec in the form ``module.path:ClassName`` or
              ``./path/to/file.py:ClassName`` (absolute paths also work).

    Returns:
        The agent class (uninstantiated).

    Raises:
        ValueError: If the spec format is invalid or the class is not an Agent subclass.
        FileNotFoundError: If a file-path spec points to a missing file.
        ImportError: If the module cannot be imported.
        AttributeError: If the class name is not found in the module.
    """
    import importlib
    import importlib.util
    import sys

    if ":" not in spec:
        raise ValueError(
            f"Invalid agent spec '{spec}'. "
            "Expected 'module.path:ClassName' or './path/to/file.py:ClassName'."
        )

    module_part, class_name = spec.rsplit(":", 1)
    class_name = class_name.strip()

    # File path: ends in .py OR contains a path separator OR starts with . / ~
    is_file = module_part.endswith(".py") or "/" in module_part or module_part.startswith(".")
    if is_file:
        file_path = Path(module_part).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Agent module file not found: {file_path}")

        parent_str = str(file_path.parent)
        inserted = False
        if parent_str not in sys.path:
            sys.path.insert(0, parent_str)
            inserted = True

        try:
            mod_spec = importlib.util.spec_from_file_location("_tui_custom_agent", file_path)
            if mod_spec is None or mod_spec.loader is None:
                raise ImportError(f"Cannot load module from {file_path}")
            module = importlib.util.module_from_spec(mod_spec)
            mod_spec.loader.exec_module(module)  # type: ignore[union-attr]
        finally:
            if inserted:
                sys.path.remove(parent_str)
    else:
        module = importlib.import_module(module_part)

    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(f"Class '{class_name}' not found in '{module_part}'.")

    # Validate it's an Agent subclass
    try:
        from nemo_oo_agents import Agent

        if not (isinstance(cls, type) and issubclass(cls, Agent)):
            raise ValueError(
                f"'{class_name}' is not a subclass of NeMo OO Agents Agent. "
                "Make sure your class inherits from Agent."
            )
    except ImportError:
        pass  # Can't validate without nemo_oo_agents; proceed anyway

    return cls
