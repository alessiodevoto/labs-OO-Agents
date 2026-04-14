# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configuration loading for NeMo OO Agents TUI.

Hydra-like config: structured dataclass with Config.load(**overrides).

Resolution order (last wins):
    1. Dataclass defaults
    2. Environment variables (_ENV map)
    3. Keyword overrides from CLI args (_OVERRIDES map)

Usage:
    # From argparse
    config = Config.load(**vars(parse_args()))

    # From click
    config = Config.load(model="gpt-4o", orchestrator=True)

    # Programmatic
    config = Config.load()
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

if TYPE_CHECKING:
    from unifiedllm import CompletionClient

logger = logging.getLogger(__name__)

# Default model - Claude Sonnet 4.5
DEFAULT_MODEL = "aws/anthropic/bedrock-claude-sonnet-4-5-v1"


@dataclass
class SummarizationConfig:
    """Configuration for history summarization."""

    policy: Literal["token_budget", "sliding_window", "none"] = "token_budget"
    max_tokens: int = 100_000
    window_size: int = 50
    preserve_recent: int = 10


@dataclass
class AgentConfig:
    """Configuration for the TUI agent's behavior."""

    # History summarization settings
    summarization: SummarizationConfig = field(default_factory=SummarizationConfig)

    # Orchestrator mode (multi-phase workflow)
    orchestrator: bool = False

    # Working directory for bash commands
    working_dir: str = "."


@dataclass
class TUIConfig:
    """Configuration for the TUI presentation layer."""

    # MCP servers from .mcp.json
    mcp_file: Path = Path(".mcp.json")

    # Directories to search for skills and user-invocable commands.
    # Includes both project-local and user-global Claude/Cursor conventions.
    skills_dirs: list[Path] = field(
        default_factory=lambda: [
            Path(".cursor/skills"),
            Path(".claude/skills"),
            Path(".claude/commands"),
            Path("tui/skills"),
            Path.home() / ".claude" / "skills",
            Path.home() / ".claude" / "commands",
        ]
    )

    # Default LLM model (from unifiedllm registry)
    default_model: str = DEFAULT_MODEL

    # Trace output directory (None = OTLP auto-probe only; set via --trace to write files)
    trace_dir: Path | None = None

    # Vi keybindings in prompt_toolkit input
    vi_mode: bool = False

    # Custom agent spec: "module.path:ClassName" or "./file.py:ClassName"
    agent_spec: str | None = None

    # Show agent Python code execution panels (off by default)
    show_python: bool = False


@dataclass
class Config:
    """Top-level configuration. Single source of truth.

    Usage:
        config = Config.load(**vars(args))   # from argparse
        config = Config.load(model="gpt-4o") # programmatic
        config = Config.load()               # pure defaults + env
    """

    tui: TUIConfig = field(default_factory=TUIConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    # Runtime flags (not persisted)
    no_splash: bool = False
    no_trace: bool = False

    # ── Declarative mapping: kwarg name → dotted config path ──────────
    # Tuple form: (path, transform_fn) for type coercion.
    # String form: path only, value passed through as-is.
    _OVERRIDES: ClassVar[dict] = {
        "model": "tui.default_model",
        "mcp_file": ("tui.mcp_file", Path),
        "trace": ("tui.trace_dir", Path),
        "context_limit": "agent.summarization.max_tokens",
        "orchestrator": "agent.orchestrator",
        "working_dir": "agent.working_dir",
        "no_splash": "no_splash",
        "no_trace": "no_trace",
        "vi": "tui.vi_mode",
        "agent": "tui.agent_spec",
        "python": "tui.show_python",
    }

    # Fields that are argparse store_true flags (skip False values to avoid overwriting config)
    _STORE_TRUE_FLAGS: ClassVar[set[str]] = {"no_splash", "no_trace", "vi", "python"}

    @classmethod
    def load(cls, **overrides) -> "Config":
        """Build config: defaults → config file → overrides.

        Config file: .nemo_oo/config.toml (project-local, optional).
        Accepts any keyword argument matching _OVERRIDES keys.
        Unknown keys are silently ignored, so you can pass ``**vars(args)``
        from argparse directly.
        """
        cfg = cls()

        # Layer 2: project-local config file (.nemo_oo/config.toml)
        for key, val in _load_config_file().items():
            if key in cls._OVERRIDES:
                _set_nested(cfg, *_unpack_target(cls._OVERRIDES[key], val))

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

        # --skills-dir appends to existing list
        extra_skills = overrides.get("skills_dir")
        if extra_skills:
            dirs = extra_skills if isinstance(extra_skills, list) else list(extra_skills)
            for d in dirs:
                p = Path(d)
                if p not in cfg.tui.skills_dirs:
                    cfg.tui.skills_dirs.append(p)

        # ── Post-processing ───────────────────────────────────────────
        # Filter skills dirs to existing directories
        cfg.tui.skills_dirs = [d for d in cfg.tui.skills_dirs if d.exists()]

        return cfg


# ── Helpers ───────────────────────────────────────────────────────────────


def _load_config_file() -> dict:
    """Load .nemo_oo/config.toml and return its [tui] section as a flat dict."""
    import tomllib

    from nemo_oo_agents_cli._common import PROJECT_DIR_NAME, find_project_root

    config_path = find_project_root() / PROJECT_DIR_NAME / "config.toml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("tui", {})
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Failed to load config file %s: %s", config_path, e)
        return {}


def _unpack_target(target, value):
    """Unpack a target spec into (path, transformed_value)."""
    if isinstance(target, tuple):
        path, transform = target
        return path, transform(value)
    return target, value


def _set_nested(obj, path: str, value):
    """Set a dotted attribute path on a nested dataclass."""
    parts = path.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


# ── LLM helpers ───────────────────────────────────────────────────────────


def get_llm(config: TUIConfig | Config) -> "CompletionClient":
    """Get the LLM client based on configuration.

    Accepts either a TUIConfig or the top-level Config.
    """
    from unifiedllm import MODELS, CompletionClient, get_llm_client

    tui = config.tui if isinstance(config, Config) else config
    model_name = tui.default_model

    if model_name in MODELS:
        return get_llm_client(model_name)

    return CompletionClient(model=model_name)


def list_models() -> list[str]:
    """List available models from the unifiedllm registry."""
    from unifiedllm import MODELS

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
