"""Integration tests for unified backend architecture.

Tests the complete stack:
- Layer 0: ConcurrencyEngine
- Layer 1: ExecutionEngine protocol
- Layer 2: TaskRunner with TraceAnalyzer
- Layer 3: Agent006Adapter

These tests verify that the layers integrate correctly and produce expected results.
"""

import asyncio
from pathlib import Path

import pytest

from evaluation.agent_adapter import Agent006Adapter, AgentConfig
from evaluation.concurrency import ConcurrencyConfig, ConcurrencyEngine
from evaluation.task_runner import EvaluationTask, RunnerConfig, TaskRunner


# Simple test agents for integration testing
class SimpleCalculatorAgent:
    """Test agent that performs basic arithmetic."""

    async def run(self, input_data: dict) -> dict:
        """Execute the agent."""
        operation = input_data.get("operation")
        a = input_data.get("a")
        b = input_data.get("b")

        if operation == "add":
            result = a + b
        elif operation == "multiply":
            result = a * b
        elif operation == "error":
            raise ValueError("Intentional error for testing")
        else:
            result = None

        return {"result": result, "operation": operation}


class MultiplyAgent:
    """Agent that multiplies two numbers."""

    async def run(self, input_data: dict) -> dict:
        a = input_data.get("a", 0)
        b = input_data.get("b", 0)
        return {"result": a * b}


class AddAgent:
    """Agent that adds two numbers."""

    async def run(self, input_data: dict) -> dict:
        a = input_data.get("a", 0)
        b = input_data.get("b", 0)
        return {"result": a + b}


@pytest.mark.asyncio
async def test_layer_0_concurrency_engine():
    """Test Layer 0: ConcurrencyEngine execution."""
    engine = ConcurrencyEngine()

    task_ids = ["task1", "task2", "task3"]

    async def task_fn(task_id: str) -> str:
        # Simulate work
        await asyncio.sleep(0.01)
        return f"result_{task_id}"

    config = ConcurrencyConfig(max_concurrent=2)
    results = await engine.run_tasks(
        task_ids=task_ids,
        task_fn=task_fn,
        config=config,
    )

    assert len(results) == 3
    assert results[0] == "result_task1"
    assert results[1] == "result_task2"
    assert results[2] == "result_task3"


@pytest.mark.asyncio
async def test_layer_2_task_runner():
    """Test Layer 2: TaskRunner with agent execution."""
    # Create tasks
    tasks = [
        EvaluationTask(task_id="calc1", data={"operation": "add", "a": 2, "b": 3}),
        EvaluationTask(task_id="calc2", data={"operation": "multiply", "a": 4, "b": 5}),
    ]

    # Create agent adapter
    agent_adapter = Agent006Adapter(
        agent_factory=lambda: SimpleCalculatorAgent(),
        config=AgentConfig(timeout_seconds=5, enable_tracing=False),
    )
    agent_adapter.register_tasks(tasks)

    # Create runner
    runner = TaskRunner(config=RunnerConfig(max_concurrent=2, analyze_traces=False))

    # Execute
    results = await runner.run_tasks(
        tasks=tasks,
        task_fn=agent_adapter.execute_task_by_id,
    )

    # Verify results
    assert len(results) == 2
    assert results[0].success is True
    assert results[0].output["result"] == 5  # 2 + 3
    assert results[1].success is True
    assert results[1].output["result"] == 20  # 4 * 5


@pytest.mark.asyncio
async def test_layer_3_agent_adapter():
    """Test Layer 3: Agent006Adapter with direct execution."""
    agent_adapter = Agent006Adapter(
        agent_factory=lambda: SimpleCalculatorAgent(),
        config=AgentConfig(timeout_seconds=5, enable_tracing=False),
    )

    task = EvaluationTask(
        task_id="test_task",
        data={"operation": "add", "a": 10, "b": 20},
    )

    result = await agent_adapter.execute_task(task)

    assert result.task_id == "test_task"
    assert result.success is True
    assert result.output["result"] == 30
    assert result.error is None


@pytest.mark.asyncio
async def test_error_handling():
    """Test that errors are properly captured."""
    agent_adapter = Agent006Adapter(
        agent_factory=lambda: SimpleCalculatorAgent(),
        config=AgentConfig(timeout_seconds=5, enable_tracing=False),
    )

    task = EvaluationTask(
        task_id="error_task",
        data={"operation": "error"},  # Will raise ValueError
    )

    result = await agent_adapter.execute_task(task)

    assert result.task_id == "error_task"
    assert result.success is False
    assert result.error is not None
    assert "Intentional error" in result.error


@pytest.mark.asyncio
async def test_timeout_handling():
    """Test that timeouts are enforced."""

    class SlowAgent:
        async def run(self, input_data: dict) -> dict:
            await asyncio.sleep(10)  # Sleep longer than timeout
            return {"done": True}

    agent_adapter = Agent006Adapter(
        agent_factory=lambda: SlowAgent(),
        config=AgentConfig(timeout_seconds=0.1, enable_tracing=False),
    )

    task = EvaluationTask(task_id="slow_task", data={})

    result = await agent_adapter.execute_task(task)

    assert result.success is False
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_concurrent_execution():
    """Test that multiple tasks execute concurrently."""
    tasks = [
        EvaluationTask(task_id=f"task{i}", data={"operation": "add", "a": i, "b": i})
        for i in range(10)
    ]

    agent_adapter = Agent006Adapter(
        agent_factory=lambda: SimpleCalculatorAgent(),
        config=AgentConfig(timeout_seconds=5, enable_tracing=False),
    )
    agent_adapter.register_tasks(tasks)

    runner = TaskRunner(config=RunnerConfig(max_concurrent=5, analyze_traces=False))

    import time

    start = time.time()
    results = await runner.run_tasks(
        tasks=tasks,
        task_fn=agent_adapter.execute_task_by_id,
    )
    duration = time.time() - start

    # Verify all results
    assert len(results) == 10
    for i, result in enumerate(results):
        assert result.success is True
        assert result.output["result"] == i + i  # i + i

    # With max_concurrent=5 and 10 tasks, should finish faster than sequential
    # (though without sleep, this is mainly a sanity check)
    assert duration < 5  # Should be very fast


@pytest.mark.asyncio
async def test_task_level_agent_specification():
    """Test that tasks can specify their own agent class."""
    # Task 1 uses MultiplyAgent
    task1 = EvaluationTask(
        task_id="task1",
        data={"a": 3, "b": 4},
        metadata={"agent_class": "tests.evaluation.test_unified_backend.MultiplyAgent"},
    )

    # Task 2 uses AddAgent
    task2 = EvaluationTask(
        task_id="task2",
        data={"a": 3, "b": 4},
        metadata={"agent_class": "tests.evaluation.test_unified_backend.AddAgent"},
    )

    agent_adapter = Agent006Adapter(
        agent_factory=lambda: SimpleCalculatorAgent(),  # Default
        config=AgentConfig(timeout_seconds=5, enable_tracing=False),
    )

    result1 = await agent_adapter.execute_task(task1)
    result2 = await agent_adapter.execute_task(task2)

    assert result1.output["result"] == 12  # 3 * 4
    assert result2.output["result"] == 7  # 3 + 4


@pytest.mark.asyncio
async def test_checkpoint_and_resume():
    """Test checkpoint/resume functionality."""
    import tempfile

    tasks = [
        EvaluationTask(task_id=f"task{i}", data={"operation": "add", "a": i, "b": 1})
        for i in range(5)
    ]

    agent_adapter = Agent006Adapter(
        agent_factory=lambda: SimpleCalculatorAgent(),
        config=AgentConfig(timeout_seconds=5, enable_tracing=False),
    )
    agent_adapter.register_tasks(tasks)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        checkpoint_file = Path(f.name)

    try:
        runner = TaskRunner(
            config=RunnerConfig(
                max_concurrent=2,
                enable_checkpointing=True,
                analyze_traces=False,
            )
        )

        # First run
        results = await runner.run_tasks(
            tasks=tasks,
            task_fn=agent_adapter.execute_task_by_id,
            checkpoint_file=checkpoint_file,
        )

        assert len(results) == 5
        assert checkpoint_file.exists()

        # Second run with same checkpoint (should skip completed tasks)
        runner2 = TaskRunner(
            config=RunnerConfig(
                max_concurrent=2,
                enable_checkpointing=True,
                analyze_traces=False,
            )
        )

        # Re-register tasks
        agent_adapter2 = Agent006Adapter(
            agent_factory=lambda: SimpleCalculatorAgent(),
            config=AgentConfig(timeout_seconds=5, enable_tracing=False),
        )
        agent_adapter2.register_tasks(tasks)

        results2 = await runner2.run_tasks(
            tasks=tasks,
            task_fn=agent_adapter2.execute_task_by_id,
            checkpoint_file=checkpoint_file,
        )

        # Should get same results (from checkpoint)
        assert len(results2) == 5

    finally:
        checkpoint_file.unlink(missing_ok=True)


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])
