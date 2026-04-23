# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Layer 3: Adapter for executing NeMo OO Agents agents.

This adapter bridges between the generic Layer 2 TaskRunner and specific
NeMo OO Agents agent execution. It handles agent instantiation, execution,
tracing, and result capture.
"""

import asyncio
import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from nemo_oo_agents_benchmarks.evaluation.task_runner import EvaluationResult, EvaluationTask

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration for agent execution."""

    timeout_seconds: float = 300
    trace_dir: str = "traces/agent_execution"
    enable_tracing: bool = True


def import_class(class_path: str) -> type:
    """
    Dynamically import a class from a string path.

    Args:
        class_path: Full dotted path to class (e.g., "module.submodule.ClassName")

    Returns:
        The imported class
    """
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class NemoOOAgentsAdapter:
    """
    Adapter for executing NeMo OO Agents agents within the evaluation framework.

    This adapter:
    1. Manages agent lifecycle (instantiation, execution, cleanup)
    2. Handles different agent interfaces (run, execute, callable)
    3. Integrates with OTel tracing
    4. Captures output and errors
    5. Returns standardized EvaluationResult

    Usage:
        # Create adapter with agent factory
        adapter = NemoOOAgentsAdapter(
            agent_factory=lambda: MyAgent(),
            config=AgentConfig(timeout_seconds=60)
        )

        # Execute agent on a task
        task = EvaluationTask(task_id="task1", data={"prompt": "..."})
        result = await adapter.execute_task(task)

        # Or use with TaskRunner
        runner = TaskRunner()
        results = await runner.run_tasks(
            tasks=[task],
            task_fn=adapter.execute_task_by_id
        )
    """

    def __init__(
        self,
        agent_factory: Callable[[], Any] | None = None,
        config: AgentConfig | None = None,
        llm_client: Any = None,
    ):
        """
        Initialize the agent adapter.

        Args:
            agent_factory: Callable that creates agent instances.
                          If llm_client is provided, factory should accept llm_client kwarg.
            config: Agent execution configuration
            llm_client: Shared LLM client for all agents (optional)
        """
        self.agent_factory = agent_factory
        self.config = config or AgentConfig()
        self.llm_client = llm_client

        # Task registry for execute_task_by_id
        self._task_registry: dict[str, EvaluationTask] = {}

        # Ensure trace directory exists
        if self.config.enable_tracing:
            Path(self.config.trace_dir).mkdir(parents=True, exist_ok=True)

    def register_tasks(self, tasks: list[EvaluationTask]) -> None:
        """
        Register tasks for execution by ID.

        This allows execute_task_by_id to lookup tasks when called by TaskRunner.

        Args:
            tasks: List of tasks to register
        """
        for task in tasks:
            self._task_registry[task.task_id] = task

    async def execute_task_by_id(self, task_id: str) -> EvaluationResult:
        """
        Execute a task by ID (for use with TaskRunner).

        Args:
            task_id: ID of registered task

        Returns:
            EvaluationResult
        """
        task = self._task_registry.get(task_id)
        if not task:
            return EvaluationResult(
                task_id=task_id,
                success=False,
                output=None,
                error=f"Task {task_id} not registered",
            )

        return await self.execute_task(task)

    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        """
        Execute a single task with an agent.

        This is the main entry point for agent execution. It:
        1. Creates a fresh agent instance
        2. Sets up tracing
        3. Executes the agent with task data
        4. Captures output and errors
        5. Returns standardized result

        Args:
            task: Evaluation task to execute

        Returns:
            EvaluationResult with success, output, and trace info
        """
        # Generate trace path
        trace_path = self._get_trace_path(task.task_id) if self.config.enable_tracing else None

        try:
            # Create agent instance
            agent = self._create_agent(task)

            # Execute with tracing and timeout
            output = await self._execute_with_trace(agent, task.data, trace_path)

            # Determine success (let caller override via metadata)
            success = self._determine_success(output, task)

            return EvaluationResult(
                task_id=task.task_id,
                success=success,
                output=output,
                trace_path=trace_path,
                metadata=task.metadata,
            )

        except TimeoutError:
            logger.warning(f"Task {task.task_id} timed out after {self.config.timeout_seconds}s")
            return EvaluationResult(
                task_id=task.task_id,
                success=False,
                output=None,
                error=f"Execution timed out after {self.config.timeout_seconds}s",
                trace_path=trace_path,
                metadata=task.metadata,
            )

        except Exception as e:
            logger.error(f"Task {task.task_id} failed with exception: {e}")
            return EvaluationResult(
                task_id=task.task_id,
                success=False,
                output=None,
                error=str(e),
                trace_path=trace_path,
                metadata=task.metadata,
            )

    def _create_agent(self, task: EvaluationTask) -> Any:
        """
        Create an agent instance for task execution.

        Supports two modes:
        1. Task-level agent specification (agent_class in metadata)
        2. Factory-based agent creation (using self.agent_factory)

        Args:
            task: The task that may specify an agent class

        Returns:
            Agent instance
        """
        # Check for task-level agent specification
        agent_class_path = task.metadata.get("agent_class") if task.metadata else None

        if agent_class_path:
            # Task specifies its own agent class
            logger.debug(f"Using task-level agent: {agent_class_path}")
            agent_class = import_class(agent_class_path)

            if self.llm_client is not None:
                # Try both parameter names for compatibility
                try:
                    return agent_class(llm=self.llm_client)
                except TypeError:
                    try:
                        return agent_class(llm_client=self.llm_client)
                    except TypeError:
                        return agent_class()
            else:
                return agent_class()

        elif self.agent_factory:
            # Use factory
            if self.llm_client is not None:
                try:
                    return self.agent_factory(llm_client=self.llm_client)
                except TypeError:
                    # Factory doesn't accept llm_client
                    return self.agent_factory()
            else:
                return self.agent_factory()

        else:
            raise ValueError("No agent_factory provided and task has no agent_class")

    async def _execute_with_trace(
        self,
        agent: Any,
        agent_input: Any,
        trace_path: str | None,
    ) -> Any:
        """
        Execute agent with tracing enabled.

        Integrates with OTel tracing infrastructure by switching trace files
        for per-task traces.

        Args:
            agent: Agent instance
            agent_input: Input data for the agent
            trace_path: Path to write trace (optional)

        Returns:
            Agent output
        """
        # Set up tracing if enabled
        prev_session = None

        if trace_path and self.config.enable_tracing:
            Path(trace_path).parent.mkdir(parents=True, exist_ok=True)

            try:
                from nemo_oo_agents.tracing import get_session, set_session

                prev_session = get_session()
                tp = Path(trace_path)
                session_id = tp.name
                if session_id.endswith(".jsonl"):
                    session_id = session_id[: -len(".jsonl")]
                set_session(session_id)
            except ImportError:
                logger.debug("OTel instrumentation not available")

        try:
            # Configure agent tracing if supported (legacy)
            if hasattr(agent, "configure_tracing") and trace_path:
                agent.configure_tracing(trace_file=trace_path)

            # Execute with timeout
            output = await asyncio.wait_for(
                self._execute_agent(agent, agent_input),
                timeout=self.config.timeout_seconds,
            )

            return output

        finally:
            # Restore previous session (or clear if there was none)
            try:
                from nemo_oo_agents.tracing import set_session

                set_session(prev_session)
            except ImportError:
                pass

    async def _execute_agent(self, agent: Any, agent_input: Any) -> Any:
        """
        Execute the agent using its interface.

        Tries different agent interfaces in order:
        1. run() method
        2. execute() method
        3. Callable agent
        4. Method specified in input

        Args:
            agent: Agent instance
            agent_input: Input for the agent

        Returns:
            Agent output

        Raises:
            ValueError: If agent has no known execution interface
        """
        # Try run() method
        if hasattr(agent, "run"):
            method = agent.run
            if asyncio.iscoroutinefunction(method):
                return await method(agent_input)
            else:
                return method(agent_input)

        # Try execute() method
        elif hasattr(agent, "execute"):
            method = agent.execute
            if asyncio.iscoroutinefunction(method):
                return await method(agent_input)
            else:
                return method(agent_input)

        # Try callable
        elif callable(agent):
            if asyncio.iscoroutinefunction(agent):
                return await agent(agent_input)
            else:
                return agent(agent_input)

        # Try method from input
        elif isinstance(agent_input, dict):
            method_name = agent_input.get("method", "process")
            if hasattr(agent, method_name):
                method = getattr(agent, method_name)
                if asyncio.iscoroutinefunction(method):
                    return await method(agent_input)
                else:
                    return method(agent_input)

        raise ValueError(f"Agent {type(agent).__name__} has no known execution interface")

    def _determine_success(self, output: Any, task: EvaluationTask) -> bool:
        """
        Determine if execution was successful.

        Default logic checks for error indicators in output. Can be overridden
        by task metadata.

        Args:
            output: Agent output
            task: The task

        Returns:
            True if successful, False otherwise
        """
        # Check task metadata for explicit success flag
        if task.metadata and "expected_success" in task.metadata:
            return task.metadata["expected_success"]

        # Check output for error indicators
        if isinstance(output, dict):
            if "error" in output:
                return False
            if "success" in output:
                return bool(output["success"])

        # Default: execution completed without exception = success
        return True

    def _get_trace_path(self, task_id: str) -> str:
        """Generate trace file path for a task."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid4().hex[:8]
        filename = f"agent_{task_id}_{timestamp}_{unique_id}.jsonl"
        return str(Path(self.config.trace_dir) / filename)


# Convenience function for simple use cases
async def execute_agent_on_tasks(
    agent_factory: Callable[[], Any],
    tasks: list[EvaluationTask],
    max_concurrent: int = 10,
    timeout_seconds: float = 300,
) -> list[EvaluationResult]:
    """
    Convenience function for executing an agent on multiple tasks.

    Args:
        agent_factory: Callable that creates agent instances
        tasks: List of tasks to execute
        max_concurrent: Maximum concurrent executions
        timeout_seconds: Timeout per task

    Returns:
        List of EvaluationResult
    """
    from nemo_oo_agents_benchmarks.evaluation.task_runner import RunnerConfig, TaskRunner

    config = AgentConfig(timeout_seconds=timeout_seconds)
    adapter = NemoOOAgentsAdapter(agent_factory=agent_factory, config=config)

    # Register tasks
    adapter.register_tasks(tasks)

    # Create runner
    runner_config = RunnerConfig(
        max_concurrent=max_concurrent,
        analyze_traces=True,
    )
    runner = TaskRunner(config=runner_config)

    # Execute
    results = await runner.run_tasks(
        tasks=tasks,
        task_fn=adapter.execute_task_by_id,
    )

    return results
