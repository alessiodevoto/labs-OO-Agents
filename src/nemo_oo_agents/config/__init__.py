# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``nemo_oo_agents.config`` — the single front door for all configuration.

Two flavours live here, both as typed objects:

- **In-code configs** you construct and pass to agents/strategies:
  ``CodeActConfig``, ``PredictConfig``, ``TruncationConfig``, ``BashConfig``, …
- **File-based configs** resolved from the layered YAML files
  (``settings.yaml`` / ``secrets.yaml`` / ``llm_config.yaml``): ``ModelConfig``,
  ``Secrets``, and :func:`resolved_config` (the programmatic ``config show``),
  plus the loader helpers ``load_layered_yaml`` / ``layered_paths`` /
  ``load_secrets_into_env``.

The unifiedllm registry is touched only lazily inside ``resolved_config()`` /
``get_model_config()`` (to avoid an import cycle and module-load cost here);
note that importing the ``nemo_oo_agents`` package itself already loads
litellm via ``strategies``.
"""

from nemo_oo_agents.config.execution_config import ExecutionConfig
from nemo_oo_agents.config.model_config import ModelConfig
from nemo_oo_agents.config.resolved import (
    ResolvedConfig,
    Secrets,
    get_model_config,
    resolved_config,
)
from nemo_oo_agents.config.strategy_config import (
    CodeActConfig,
    PredictConfig,
    ReflexionConfig,
)
from nemo_oo_agents.config.summarizer_config import MethodSummarizerConfig, TokenBudgetConfig
from nemo_oo_agents.config.tool_configs import BashConfig
from nemo_oo_agents.config.truncation_config import (
    CaptureConfig,
    FormatConfig,
    MediaCaptureConfig,
    TruncationConfig,
)
from nemo_oo_agents.layered_config import layered_paths, load_layered_yaml
from nemo_oo_agents.secrets import load_secrets_into_env

__all__ = [
    # In-code / strategy configs
    "ExecutionConfig",
    "CodeActConfig",
    "PredictConfig",
    "ReflexionConfig",
    "MethodSummarizerConfig",
    "TokenBudgetConfig",
    "BashConfig",
    "TruncationConfig",
    "CaptureConfig",
    "MediaCaptureConfig",
    "FormatConfig",
    # File-based configs (typed) + loaders
    "ModelConfig",
    "Secrets",
    "ResolvedConfig",
    "resolved_config",
    "get_model_config",
    "load_layered_yaml",
    "layered_paths",
    "load_secrets_into_env",
]
