# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Task execution infrastructure for agent evaluation.

This package provides the layered execution stack:
- concurrency: pure async execution engines (Layer 0)
- protocol: execution-engine / trace-analysis protocols and usage-stats types
- trace_analyzer: extracts usage statistics from OTel traces
- task_runner: generic task runner with swappable execution engines (Layer 2)
- agent_adapter: adapts nemo_oo_agents agents into the runner

For LLM clients, use the nemo_oo_agents.unifiedllm subpackage:
    from nemo_oo_agents.unifiedllm import CompletionClient, RetryConfig

    client = CompletionClient(
        model="gpt-4o-mini",
        retry_config=RetryConfig(max_retries=3),
    )
"""

from nemo_oo_agents_benchmarks.evaluation.concurrency import (
    ConcurrencyConfig,
    ConcurrencyEngine,
    SubprocessEngine,
)
from nemo_oo_agents_benchmarks.evaluation.protocol import (
    AggregateUsageStats,
    EngineConfig,
    ExecutionEngine,
    ModelUsageStats,
    TaskState,
    TaskUsageStats,
)
from nemo_oo_agents_benchmarks.evaluation.task_runner import (
    EvaluationResult,
    EvaluationTask,
    RunnerConfig,
    TaskRunner,
    run_evaluation,
)
from nemo_oo_agents_benchmarks.evaluation.trace_analyzer import TraceAnalyzer

__all__ = [
    # Concurrency engines (Layer 0)
    "ConcurrencyConfig",
    "ConcurrencyEngine",
    "SubprocessEngine",
    # Protocols and usage-stats types
    "EngineConfig",
    "ExecutionEngine",
    "TaskState",
    "ModelUsageStats",
    "TaskUsageStats",
    "AggregateUsageStats",
    # Trace analysis
    "TraceAnalyzer",
    # Task runner (Layer 2)
    "EvaluationResult",
    "EvaluationTask",
    "RunnerConfig",
    "TaskRunner",
    "run_evaluation",
]
