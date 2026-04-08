"""Configuration loading for Agent006 TUI.

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
import os
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

    # Directories to search for skills
    skills_dirs: list[Path] = field(
        default_factory=lambda: [
            Path(".cursor/skills"),
            Path(".claude/skills"),
            Path("tui/skills"),
        ]
    )

    # Default LLM model (from unifiedllm registry)
    default_model: str = DEFAULT_MODEL

    # Trace output directory (None = OTLP auto-probe only; set via --trace for file output)
    trace_dir: Path | None = None


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
    }

    # Fields that are argparse store_true flags (skip False values to avoid overwriting config)
    _STORE_TRUE_FLAGS: ClassVar[set[str]] = {"no_splash", "no_trace"}

    # ── Environment variable mapping ──────────────────────────────────
    _ENV: ClassVar[dict] = {
        "AGENT006_MODEL": "tui.default_model",
        "AGENT006_TRACE_DIR": ("tui.trace_dir", Path),
    }

    @classmethod
    def load(cls, **overrides) -> "Config":
        """Build config: defaults → env vars → overrides.

        Accepts any keyword argument matching _OVERRIDES keys.
        Unknown keys are silently ignored, so you can pass ``**vars(args)``
        from argparse directly.
        """
        cfg = cls()

        # Layer 2: environment variables
        for env_key, target in cls._ENV.items():
            val = os.environ.get(env_key)
            if val is not None:
                _set_nested(cfg, *_unpack_target(target, val))

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
