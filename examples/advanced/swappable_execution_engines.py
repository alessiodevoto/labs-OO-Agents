"""
Example: Swappable Execution Engines

This example demonstrates the ExecutionEngine protocol pattern. The protocol
(defined in evaluation/protocol.py) allows different execution strategies
to be swapped without changing higher-level code.

Current implementation:
- ConcurrencyEngine: Async I/O with semaphore (for LLM APIs)

Future implementations can be added by implementing the ExecutionEngine protocol:
- MultiprocessEngine: Process pool (for CPU-bound tasks like local models)
- RayEngine: Distributed execution across cluster
- NemoRunEngine: HPC cluster submission (Slurm, cloud)

Usage:
    uv run python examples/advanced/swappable_execution_engines.py --tasks 20 --concurrent 5
"""

import argparse
import asyncio
import logging

from evaluation.agent_adapter import Agent006Adapter, AgentConfig
from evaluation.concurrency import ConcurrencyEngine
from evaluation.task_runner import (
    EvaluationTask,
    RunnerConfig,
    TaskRunner,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Simple test agent
class TestAgent:
    """Simple agent for demonstrating execution engines."""

    async def run(self, input_data: dict) -> dict:
        """Execute the agent."""
        task_id = input_data.get("task_id")
        operation = input_data.get("operation", "compute")

        # Simulate work
        await asyncio.sleep(0.1)

        return {
            "task_id": task_id,
            "result": f"Completed {operation}",
            "status": "success",
        }


async def run_with_engine(
    num_tasks: int = 10,
    max_concurrent: int = 5,
):
    """
    Run evaluation with the ConcurrencyEngine.

    The ExecutionEngine protocol in evaluation/protocol.py defines the interface
    for swappable engines. When new engines are implemented (MultiprocessEngine,
    RayEngine, NemoRunEngine), they can be passed to TaskRunner without changing
    higher-level code.

    Args:
        num_tasks: Number of tasks to run
        max_concurrent: Maximum concurrent executions
    """
    logger.info("Creating AsyncIO execution engine (semaphore-based)")
    engine = ConcurrencyEngine()

    # Create tasks
    tasks = [
        EvaluationTask(
            task_id=f"task_{i:03d}",
            data={"task_id": f"task_{i:03d}", "operation": "compute"},
        )
        for i in range(num_tasks)
    ]

    logger.info(f"Created {num_tasks} tasks")

    # Create agent adapter
    agent_adapter = Agent006Adapter(
        agent_factory=lambda: TestAgent(),
        config=AgentConfig(timeout_seconds=30, enable_tracing=False),
    )
    agent_adapter.register_tasks(tasks)

    # Create runner with the specified engine
    runner_config = RunnerConfig(
        max_concurrent=max_concurrent,
        analyze_traces=False,
    )

    runner = TaskRunner(
        engine=engine,  # Swappable execution engine!
        config=runner_config,
    )

    logger.info(f"Running {num_tasks} tasks with asyncio engine...")
    logger.info(f"Max concurrent: {max_concurrent}")

    import time

    start_time = time.time()

    # Execute tasks - same API regardless of engine!
    results = await runner.run_tasks(
        tasks=tasks,
        task_fn=agent_adapter.execute_task_by_id,
    )

    duration = time.time() - start_time

    # Analyze results
    successful = sum(1 for r in results if r.success)
    failed = len(results) - successful

    logger.info("\nExecution complete!")
    logger.info("  Engine: asyncio (ConcurrencyEngine)")
    logger.info(f"  Duration: {duration:.2f}s")
    logger.info(f"  Tasks: {len(results)}")
    logger.info(f"  Successful: {successful}")
    logger.info(f"  Failed: {failed}")
    logger.info(f"  Throughput: {len(results) / duration:.1f} tasks/sec")

    return results


async def main():
    parser = argparse.ArgumentParser(description="Demonstrate swappable execution engines")

    parser.add_argument(
        "--tasks",
        "-t",
        type=int,
        default=10,
        help="Number of tasks to run",
    )

    parser.add_argument(
        "--concurrent",
        "-c",
        type=int,
        default=5,
        help="Maximum concurrent tasks",
    )

    args = parser.parse_args()

    await run_with_engine(
        num_tasks=args.tasks,
        max_concurrent=args.concurrent,
    )


if __name__ == "__main__":
    asyncio.run(main())
