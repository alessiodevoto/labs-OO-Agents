# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from nemo_oo_agents.unifiedllm.fake import FakeLLMClient
from nemo_oo_agents.unifiedllm.http_config import HttpConfig
from nemo_oo_agents.unifiedllm.registry import MODELS, get_llm_client, reload_registry
from nemo_oo_agents.unifiedllm.retry import (
    EmptyContentError,
    RetryingWrapper,
    sync_retry,
    with_retry,
)
from nemo_oo_agents.unifiedllm.retry_config import RetryConfig
from nemo_oo_agents.unifiedllm.unifiedllm import (
    CompletionClient,
    LLMResponse,
    OpenAIResponseClient,
    ReasoningCompletionClient,
    ResponsesClient,
    Tool,
    ToolCall,
    UnifiedLLM,
    create_tool_from_callable,
    extract_and_parse_json,
)

__all__ = [
    # Core classes
    "UnifiedLLM",
    "CompletionClient",
    "ReasoningCompletionClient",
    "ResponsesClient",
    "OpenAIResponseClient",
    # Model registry
    "get_llm_client",
    "reload_registry",
    "MODELS",
    # Tools
    "Tool",
    "ToolCall",
    "create_tool_from_callable",
    # Response types
    "LLMResponse",
    # HTTP config
    "HttpConfig",
    # Retry utilities
    "EmptyContentError",
    "RetryConfig",
    "RetryingWrapper",
    "with_retry",
    "sync_retry",
    # Testing
    "FakeLLMClient",
    # Utilities
    "extract_and_parse_json",
]
