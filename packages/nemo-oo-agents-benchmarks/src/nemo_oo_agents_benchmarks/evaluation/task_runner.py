# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Layer 2: Generic task execution runner with swappable execution engines.

This module provides the core infrastructure for running evaluation tasks
with pluggable execution strategies (async I/O, multiprocess, distributed, etc.).

This is distinct from runner.py which handles self-improvement loops. This module
provides the lower-level execution infrastructure that higher-level runners build on.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nemo_oo_agents_benchmarks.evaluation.concurrency import ConcurrencyEngine
from nemo_oo_agents_benchmarks.evaluation.protocol import (
    AggregateUsageStats,
    EngineConfig,
    ExecutionEngine,
    TaskState,
    TaskUsageStats,
)
from nemo_oo_agents_benchmarks.evaluation.protocol import (
    TraceAnalyzer as TraceAnalyzerProtocol,
)
from nemo_oo_agents_benchmarks.evaluation.trace_analyzer import TraceAnalyzer


@dataclass
class EvaluationTask:
    """Generic evaluation task for Layer 2 runner.

    This is the opaque task data that Layer 2 works with. The actual
    task execution is delegated to a task_fn provided by Layer 3.
    """

    task_id: str
    data: Any  # Opaque data passed to task_fn
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    """Result from executing a single evaluation task."""

    task_id: str
    success: bool
    output: Any  # Opaque output from task_fn
    error: str | None = None
    trace_path: str | None = None
    usage_stats: TaskUsageStats | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunnerConfig:
    """Configuration for the generic task runner."""

    # Execution engine
    max_concurrent: int = 10
    timeout_seconds: float | None = None
    enable_checkpointing: bool = True

    # Tracing and usage analysis
    trace_dir: str | None = None
    analyze_traces: bool = True  # Extract usage stats from traces

    # Output
    output_dir: str = "results"


class TaskRunner:
    """
    Generic task execution runner with swappable execution engines.

    This is Layer 2 infrastructure that:
    1. Uses ExecutionEngine protocol for pluggable execution strategies
    2. Runs tasks in parallel with concurrency control
    3. Analyzes traces for usage statistics
    4. Supports checkpointing and resume
    5. Provides progress tracking

    Usage:
        # Create runner with default async I/O engine
        runner = TaskRunner(engine=ConcurrencyEngine())

        # Define tasks and execution function
        tasks = [
            EvaluationTask(task_id="task1", data={"prompt": "..."}),
            EvaluationTask(task_id="task2", data={"prompt": "..."}),
        ]

        async def execute_task(task_id: str) -> EvaluationResult:
            task = task_map[task_id]
            # Execute agent, run tests, etc.
            return EvaluationResult(...)

        # Run
        results = await runner.run_tasks(tasks, execute_task)
        stats = runner.get_usage_stats()
    """

    def __init__(
        self,
        engine: ExecutionEngine | None = None,
        config: RunnerConfig | None = None,
        trace_analyzer: TraceAnalyzerProtocol | None = None,
    ):
        """
        Initialize task runner.

        Args:
            engine: Execution engine (defaults to ConcurrencyEngine)
            config: Runner configuration
            trace_analyzer: Trace analyzer for usage stats (optional)
        """
        self.engine = engine or ConcurrencyEngine()
        self.config = config or RunnerConfig()
        self.trace_analyzer = trace_analyzer or TraceAnalyzer()

        # State tracking
        self._results: list[EvaluationResult] = []
        self._checkpoint_state: list[TaskState] = []

    async def run_tasks(
        self,
        tasks: list[EvaluationTask],
        task_fn: Callable[[str], Any],
        checkpoint_file: Path | None = None,
    ) -> list[EvaluationResult]:
        """
        Run evaluation tasks using the configured execution engine.

        Args:
            tasks: List of evaluation tasks
            task_fn: Async function that takes task_id and returns EvaluationResult
            checkpoint_file: Path to checkpoint file for resume (optional)

        Returns:
            List of EvaluationResult in same order as tasks
        """
        # Load checkpoint if provided
        if checkpoint_file and checkpoint_file.exists():
            self._checkpoint_state = self._load_checkpoint(checkpoint_file)

        # Extract task IDs
        task_ids = [task.task_id for task in tasks]

        # Wrap task_fn to store results
        async def wrapped_task_fn(task_id: str) -> EvaluationResult:
            result = await task_fn(task_id)

            # Analyze trace if configured
            if (
                self.config.analyze_traces
                and result.trace_path
                and Path(result.trace_path).exists()
            ):
                result.usage_stats = self.trace_analyzer.analyze_trace(result.trace_path)

            return result

        # Convert config to EngineConfig
        engine_config = EngineConfig(
            max_concurrent=self.config.max_concurrent,
            timeout_seconds=self.config.timeout_seconds,
            enable_checkpointing=self.config.enable_checkpointing,
        )

        # Callback to collect results and update checkpoint
        def on_complete(task_id: str, result_or_error: EvaluationResult | Exception):
            if isinstance(result_or_error, EvaluationResult):
                self._results.append(result_or_error)

                # Update checkpoint state
                if self.config.enable_checkpointing:
                    state = TaskState(
                        task_id=task_id,
                        completed=True,
                        result=result_or_error,
                        timestamp=datetime.now().timestamp(),
                    )
                    self._checkpoint_state.append(state)

                    # Save checkpoint incrementally
                    if checkpoint_file:
                        self._save_checkpoint(checkpoint_file)

            else:
                # Handle exception
                error_result = EvaluationResult(
                    task_id=task_id,
                    success=False,
                    output=None,
                    error=str(result_or_error),
                )
                self._results.append(error_result)

        # Run tasks using execution engine
        results = await self.engine.run_tasks(
            task_ids=task_ids,
            task_fn=wrapped_task_fn,
            config=engine_config,
            checkpoint_state=self._checkpoint_state if self.config.enable_checkpointing else None,
            on_task_complete=on_complete,
        )

        return results

    def get_usage_stats(self) -> AggregateUsageStats:
        """
        Get aggregate usage statistics across all completed tasks.

        Returns:
            AggregateUsageStats with token counts, latencies, costs
        """
        per_task_stats = []

        for result in self._results:
            if result.usage_stats:
                per_task_stats.append(result.usage_stats)

        return AggregateUsageStats(
            num_tasks=len(self._results),
            per_task_stats=per_task_stats,
        )

    def _load_checkpoint(self, checkpoint_file: Path) -> list[TaskState]:
        """Load checkpoint state from file."""
        if not checkpoint_file.exists():
            return []

        states = []
        with open(checkpoint_file) as f:
            for line in f:
                data = json.loads(line)
                states.append(TaskState(**data))

        return states

    def _save_checkpoint(self, checkpoint_file: Path) -> None:
        """Save checkpoint state to file."""
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file then atomic replace
        temp_file = checkpoint_file.with_suffix(".tmp")

        with open(temp_file, "w") as f:
            for state in self._checkpoint_state:
                f.write(
                    json.dumps(
                        {
                            "task_id": state.task_id,
                            "completed": state.completed,
                            "error": state.error,
                            "timestamp": state.timestamp,
                        }
                    )
                    + "\n"
                )

        # Atomic replace
        temp_file.replace(checkpoint_file)


# Convenience function for simple use cases
async def run_evaluation(
    tasks: list[EvaluationTask],
    task_fn: Callable[[str], Any],
    max_concurrent: int = 10,
    analyze_traces: bool = True,
) -> tuple[list[EvaluationResult], AggregateUsageStats]:
    """
    Convenience function for running evaluation with default settings.

    Args:
        tasks: List of evaluation tasks
        task_fn: Async function that takes task_id and returns EvaluationResult
        max_concurrent: Maximum concurrent tasks
        analyze_traces: Whether to analyze traces for usage stats

    Returns:
        Tuple of (results, usage_stats)
    """
    config = RunnerConfig(
        max_concurrent=max_concurrent,
        analyze_traces=analyze_traces,
    )

    runner = TaskRunner(config=config)
    results = await runner.run_tasks(tasks, task_fn)
    stats = runner.get_usage_stats()

    return results, stats
