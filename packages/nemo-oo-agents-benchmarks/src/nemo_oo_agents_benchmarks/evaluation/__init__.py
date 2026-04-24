# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Benchmark-agnostic evaluation framework for measuring agent self-improvement.

This module provides:
- BenchmarkAdapter: Abstract base class for benchmark integrations
- BenchmarkEnvironment: Abstract base class for interactive execution environments
- TraceAnalyzer: Analyzes execution traces to identify failure patterns
- SelfImprovementRunner: Runs tasks with iterative improvement loops
- Metrics and reporting utilities

For LLM clients, use the unifiedllm package directly:
    from unifiedllm import CompletionClient, RetryConfig

    client = CompletionClient(
        model="gpt-4o-mini",
        retry_config=RetryConfig(max_retries=3),
    )
"""

from nemo_oo_agents_benchmarks.evaluation.metrics import ImprovementMetrics, MetricsCalculator
from nemo_oo_agents_benchmarks.evaluation.protocol import (
    BenchmarkAdapter,
    BenchmarkEnvironment,
    BenchmarkReport,
    EvalResult,
    StepResult,
    Task,
    TaskResult,
)
from nemo_oo_agents_benchmarks.evaluation.runner import SelfImprovementRunner
from nemo_oo_agents_benchmarks.evaluation.trace_analyzer import FailurePattern, TraceAnalyzer

__all__ = [
    # Core protocol
    "Task",
    "EvalResult",
    "TaskResult",
    "BenchmarkReport",
    "BenchmarkAdapter",
    "BenchmarkEnvironment",
    "StepResult",
    # Analysis
    "TraceAnalyzer",
    "FailurePattern",
    # Runner
    "SelfImprovementRunner",
    # Metrics
    "MetricsCalculator",
    "ImprovementMetrics",
]
