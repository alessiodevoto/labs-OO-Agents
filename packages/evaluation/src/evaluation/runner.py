"""
Self-improvement benchmark runner.

This module provides the core runner that executes benchmarks with iterative
improvement loops, analyzing traces after each attempt and generating
refinement context for subsequent tries.
"""

import asyncio
import importlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tqdm import tqdm

from evaluation.protocol import (
    BenchmarkAdapter,
    BenchmarkReport,
    EvalResult,
    Task,
    TaskResult,
)
from evaluation.trace_analyzer import TraceAnalyzer

logger = logging.getLogger(__name__)


def import_class(class_path: str) -> type:
    """
    Dynamically import a class from a string path.

    Args:
        class_path: Full dotted path to class (e.g., "module.submodule.ClassName")

    Returns:
        The imported class

    Example:
        >>> SomeClass = import_class("my_package.my_module.SomeClass")
        >>> instance = SomeClass()
    """
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


@dataclass
class RunnerConfig:
    """Configuration for the benchmark runner."""

    # Model identification
    model: str = "unknown"  # Model name/ID used for evaluation

    # Improvement loop settings
    max_improvement_iterations: int = 5
    stop_on_success: bool = True

    # Tracing
    trace_dir: str = "traces/evaluation"
    results_dir: str = "results/evaluation"

    # Task limiting
    limit: int | None = None  # Maximum number of tasks to evaluate

    # Execution
    timeout_seconds: int = 300
    max_concurrent_tasks: int = 1

    # Reporting
    save_traces: bool = True
    save_intermediate_results: bool = True
    generate_html_report: bool = True

    # Callbacks for incremental updates
    on_task_complete: Callable[[TaskResult, int, int], None] | None = None
    """Called after each task completes: (result, completed_count, total_count)"""


@dataclass
class ImprovementIteration:
    """Record of a single improvement iteration."""

    iteration: int
    eval_result: EvalResult
    improvement_context: str
    trace_path: str
    timestamp: datetime = field(default_factory=datetime.now)


class SelfImprovementRunner:
    """
    Runs benchmark tasks with self-improvement loops.

    The runner:
    1. Executes a task with the agent
    2. Evaluates the result
    3. If failed, analyzes traces to generate improvement hints
    4. Repeats with improved context until success or max iterations

    This measures the agent's ability to learn from its failures.

    Usage:
        runner = SelfImprovementRunner(
            agent_factory=lambda: MyAgent(),
            benchmarks=["tau_bench", "bfcl"],
        )
        reports = await runner.run_all()
    """

    def __init__(
        self,
        agent_factory: Callable[[], Any],
        adapters: dict[str, BenchmarkAdapter] = None,
        config: RunnerConfig = None,
        trace_analyzer: TraceAnalyzer = None,
        llm_client: Any = None,
    ):
        """
        Initialize the runner.

        Args:
            agent_factory: Callable that creates a new agent instance.
                          If llm_client is provided, factory should accept llm_client kwarg:
                          agent_factory = lambda llm_client=None: MyAgent(llm_client=llm_client)
            adapters: Dict of name -> adapter
            config: Runner configuration
            trace_analyzer: Custom trace analyzer (optional)
            llm_client: Shared LLM client for all agents (e.g., BatchingLLMClient).
                       When provided, enables concurrent request handling and metrics.
        """
        self.agent_factory = agent_factory
        self.config = config or RunnerConfig()
        self.trace_analyzer = trace_analyzer or TraceAnalyzer()
        self.llm_client = llm_client
        self.adapters = adapters or {}

        # Ensure directories exist
        Path(self.config.trace_dir).mkdir(parents=True, exist_ok=True)
        Path(self.config.results_dir).mkdir(parents=True, exist_ok=True)

    async def run_all(self) -> dict[str, BenchmarkReport]:
        """
        Run all configured benchmarks.

        Returns:
            Dict mapping benchmark name to BenchmarkReport
        """
        reports = {}

        for name, adapter in self.adapters.items():
            logger.info(f"Starting benchmark: {name}")
            try:
                adapter.setup()
                report = await self.run_benchmark(name, adapter, task_limit=self.config.limit)
                reports[name] = report
            except Exception as e:
                logger.error(f"Benchmark {name} failed: {e}")
                reports[name] = BenchmarkReport(
                    benchmark_name=name,
                    task_results=[],
                    aggregate_metrics={"error": str(e)},
                )
            finally:
                adapter.teardown()

        # Save combined report
        if self.config.save_intermediate_results:
            self._save_combined_report(reports)

        return reports

    async def run_benchmark(
        self,
        name: str,
        adapter: BenchmarkAdapter,
        task_limit: int | None = None,
    ) -> BenchmarkReport:
        """
        Run a single benchmark.

        Args:
            name: Benchmark name
            adapter: Benchmark adapter
            task_limit: Maximum tasks to run (optional)

        Returns:
            BenchmarkReport with all results
        """
        started_at = datetime.now()
        tasks = adapter.get_tasks(limit=task_limit)

        logger.info(
            f"Running {len(tasks)} tasks for {name} "
            f"(max_concurrent: {self.config.max_concurrent_tasks})"
        )

        # Initialize experiment file with status="running"
        if self.config.save_intermediate_results:
            self._init_experiment_file(name, started_at)

        # Run tasks with bounded concurrency
        task_results = await self._run_tasks_concurrent(adapter, tasks, name)

        completed_at = datetime.now()

        # Compute aggregate metrics
        metrics = self._compute_aggregate_metrics(task_results)

        report = BenchmarkReport(
            benchmark_name=name,
            task_results=task_results,
            aggregate_metrics=metrics,
            config={
                "max_iterations": self.config.max_improvement_iterations,
                "pass_at_k": self.config.pass_at_k,
                "max_concurrent_tasks": self.config.max_concurrent_tasks,
            },
            started_at=started_at,
            completed_at=completed_at,
        )

        if self.config.save_intermediate_results:
            self._finalize_experiment(name, report, completed_at)

        return report

    async def _run_tasks_concurrent(
        self,
        adapter: BenchmarkAdapter,
        tasks: list[Task],
        benchmark_name: str,
    ) -> list[TaskResult]:
        """
        Run tasks with bounded concurrency.

        Uses asyncio.Semaphore to limit concurrent task execution to
        config.max_concurrent_tasks. Results are returned in the same
        order as the input tasks, regardless of completion order.

        Args:
            adapter: Benchmark adapter
            tasks: List of tasks to run
            benchmark_name: Name for progress display

        Returns:
            List of TaskResult in same order as input tasks
        """
        semaphore = asyncio.Semaphore(self.config.max_concurrent_tasks)
        collected: list[tuple[int, TaskResult]] = []

        async def run_task_with_semaphore(idx: int, task: Task) -> tuple[int, TaskResult]:
            """Run a single task with semaphore-based concurrency control."""
            async with semaphore:
                logger.debug(f"[{idx + 1}/{len(tasks)}] Starting task: {task.id}")

                try:
                    if self.config.pass_at_k > 1:
                        result = await self.run_task_pass_at_k(adapter, task)
                    else:
                        result = await self.run_task_with_improvement(adapter, task)
                except Exception as e:
                    logger.error(f"Task {task.id} failed with exception: {e}")
                    result = TaskResult(
                        task=task,
                        iterations=[],
                        final_success=False,
                        improvement_curve=[],
                        total_iterations=0,
                        improvement_context_history=[],
                    )

                # Append result to experiment file immediately (crash-safe)
                if self.config.save_intermediate_results:
                    self._append_task_result(benchmark_name, result)

                return idx, result

        # Create all coroutines
        coros = [run_task_with_semaphore(i, task) for i, task in enumerate(tasks)]

        # Use tqdm for progress bar with as_completed for real-time updates
        with tqdm(total=len(tasks), desc=f"Evaluating {benchmark_name}", unit="task") as pbar:
            for coro in asyncio.as_completed(coros):
                idx, result = await coro
                collected.append((idx, result))
                pbar.update(1)

                # Update progress bar with current success rate
                done = len(collected)
                success = sum(1 for _, r in collected if r.final_success)
                if done > 0:
                    pbar.set_postfix(success=f"{success / done:.0%}")

                # Call on_task_complete callback for incremental updates
                if self.config.on_task_complete:
                    self.config.on_task_complete(result, done, len(tasks))

        return [r for _, r in sorted(collected)]

    async def run_task_with_improvement(
        self,
        adapter: BenchmarkAdapter,
        task: Task,
    ) -> TaskResult:
        """
        Run a task with iterative improvement.

        This is the core self-improvement loop:
        1. Execute task
        2. Evaluate result
        3. If failed, analyze traces and generate improvement context
        4. Repeat with context until success or max iterations

        """
        iterations: list[ImprovementIteration] = []
        improvement_context = ""
        traces = []

        for i in range(self.config.max_improvement_iterations):
            logger.debug(f"Iteration {i + 1}/{self.config.max_improvement_iterations}")

            # Create fresh agent with improvement context
            # Support test-level agent specification (Option 5: Prompt-Opt pattern)
            agent_class_path = task.metadata.get("agent_class") if task.metadata else None

            if agent_class_path:
                # Test specifies its own agent class - dynamically import and instantiate
                logger.debug(f"Using test-level agent: {agent_class_path}")
                agent_class = import_class(agent_class_path)
                if self.llm_client is not None:
                    # Try both parameter names for compatibility
                    try:
                        agent = agent_class(llm=self.llm_client)
                    except TypeError:
                        agent = agent_class(llm_client=self.llm_client)
                else:
                    agent = agent_class()
            else:
                # Use legacy agent_factory (backward compatible)
                if self.llm_client is not None:
                    agent = self.agent_factory(llm_client=self.llm_client)
                else:
                    agent = self.agent_factory()

            # Format task for agent
            agent_input = adapter.format_for_agent(task)

            # Add improvement context to input
            if improvement_context:
                agent_input["improvement_context"] = improvement_context

            # Execute with tracing
            trace_path = self._get_trace_path(adapter.name, task.id, i)
            output = await self._execute_with_trace(agent, agent_input, trace_path)
            traces.append(trace_path)

            # Evaluate
            result = adapter.evaluate(task, output, {"path": trace_path})

            iterations.append(
                ImprovementIteration(
                    iteration=i + 1,
                    eval_result=result,
                    improvement_context=improvement_context,
                    trace_path=trace_path,
                )
            )

            if result.success and self.config.stop_on_success:
                logger.debug(f"Task succeeded on iteration {i + 1}")
                break

            # Analyze failure and generate improvement context
            improvement_context = await self.trace_analyzer.analyze_and_suggest(
                trace_path=trace_path,
                error_type=result.error_category,
                error_message=result.error_message,
                previous_context=improvement_context,
            )

        return TaskResult(
            task=task,
            iterations=[it.eval_result for it in iterations],
            final_success=iterations[-1].eval_result.success if iterations else False,
            improvement_curve=[it.eval_result.score for it in iterations],
            total_iterations=len(iterations),
            improvement_context_history=[it.improvement_context for it in iterations],
        )

    async def _execute_with_trace(
        self,
        agent: Any,
        agent_input: dict[str, Any],
        trace_path: str,
    ) -> Any:
        """
        Execute agent with tracing enabled.

        This integrates with your existing tracing infrastructure.
        Creates per-task trace files by switching the global exporter.
        """
        # Ensure trace directory exists
        Path(trace_path).parent.mkdir(parents=True, exist_ok=True)

        # Switch to per-task session for trace routing
        prev_session = None
        try:
            from openinference_instrumentation_nemo_oo_agents import get_session, set_session

            prev_session = get_session()
            # Derive session from trace path
            tp = Path(trace_path)
            session_id = tp.name
            if session_id.endswith(".jsonl"):
                session_id = session_id[: -len(".jsonl")]
            set_session(session_id)
        except ImportError:
            pass

        try:
            # Try to configure tracing if agent supports it (legacy support)
            if hasattr(agent, "configure_tracing"):
                agent.configure_tracing(trace_file=trace_path)

            # Execute based on agent interface with timeout
            async def _execute():
                if hasattr(agent, "run"):
                    # Agent has a run method
                    return await agent.run(agent_input)
                elif hasattr(agent, "execute"):
                    return await agent.execute(agent_input)
                elif callable(agent):
                    return await agent(agent_input)
                else:
                    # Try to call a method based on input
                    method_name = agent_input.get("method", "process")
                    if hasattr(agent, method_name):
                        method = getattr(agent, method_name)
                        if asyncio.iscoroutinefunction(method):
                            return await method(agent_input)
                        else:
                            return method(agent_input)
                    else:
                        raise ValueError(f"Don't know how to execute agent: {type(agent)}")

            # Apply timeout
            output = await asyncio.wait_for(_execute(), timeout=self.config.timeout_seconds)
            return output

        except TimeoutError:
            logger.warning(f"Agent execution timed out after {self.config.timeout_seconds}s")
            return {
                "error": "timeout",
                "message": f"Execution timed out after {self.config.timeout_seconds}s",
            }

        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            return {"error": "exception", "message": str(e)}

        finally:
            # Restore previous session
            if prev_session is not None:
                try:
                    from openinference_instrumentation_nemo_oo_agents import set_session

                    set_session(prev_session)
                except ImportError:
                    pass

    def _get_trace_path(self, benchmark: str, task_id: str, iteration: int) -> str:
        """Generate trace file path with .jsonl extension for trace-viewer discovery."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        filename = f"{benchmark}_{task_id}_iter{iteration}_{timestamp}_{unique_id}.jsonl"
        return str(Path(self.config.trace_dir) / benchmark / filename)

    def _compute_aggregate_metrics(self, results: list[TaskResult]) -> dict[str, float]:
        """Compute aggregate metrics across all task results."""
        if not results:
            return {}

        total = len(results)
        successful = sum(1 for r in results if r.final_success)
        first_try_success = sum(1 for r in results if r.first_attempt_success)
        improved = sum(1 for r in results if r.solved_after_improvement)

        # Average improvement
        improvements = [r.improvement_delta for r in results if len(r.improvement_curve) > 1]
        avg_improvement = sum(improvements) / len(improvements) if improvements else 0.0

        # Average iterations to success
        successful_iterations = [r.total_iterations for r in results if r.final_success]
        avg_iterations = (
            sum(successful_iterations) / len(successful_iterations)
            if successful_iterations
            else self.config.max_improvement_iterations
        )

        return {
            "total_tasks": total,
            "success_rate": successful / total if total > 0 else 0.0,
            "first_try_success_rate": first_try_success / total if total > 0 else 0.0,
            "improvement_rate": improved / (total - first_try_success)
            if (total - first_try_success) > 0
            else 0.0,
            "avg_improvement_delta": avg_improvement,
            "avg_iterations_to_success": avg_iterations,
            "tasks_improved_after_failure": improved,
        }

    def _get_experiment_path(self, benchmark: str) -> Path:
        """Get path to experiment file for a benchmark."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{benchmark}_{timestamp}.006eval.jsonl"
        return Path(self.config.results_dir) / filename

    def _init_experiment_file(self, benchmark: str, started_at: datetime) -> None:
        """Initialize experiment file with metadata and status='running'."""
        exp_path = self._get_experiment_path(benchmark)
        exp_path.parent.mkdir(parents=True, exist_ok=True)

        metadata = {
            "metadata": {
                "timestamp": started_at.isoformat(),
                "suite_name": benchmark,
                "models": [],  # Will be populated as tasks complete
                "config_file": benchmark,
                "status": "running",
            },
            "results": [],
        }

        with open(exp_path, "w") as f:
            json.dump(metadata, f, default=str)
            f.write("\n")

        # Store path for later appends
        self._current_experiment_path = exp_path
        logger.info(f"Initialized experiment file: {exp_path}")

    def _append_task_result(self, benchmark: str, result: TaskResult) -> None:
        """Append a task result to the experiment file."""
        if not hasattr(self, "_current_experiment_path"):
            logger.warning("No experiment file initialized, skipping append")
            return

        # Get final iteration
        final_iter = result.iterations[-1] if result.iterations else None

        # Determine judge pass/fail and reason
        judge_passed = result.final_success
        judge_score = final_iter.score if final_iter else 0.0
        if judge_passed:
            judge_reason = "Test passed all evaluation criteria"
        else:
            judge_reason = (
                final_iter.error_message
                if final_iter and final_iter.error_message
                else "Test failed evaluation"
            )

        # Build scores dict - support multi-scorer format if metadata contains scorer info
        scores = {}
        metadata = final_iter.metadata if final_iter and final_iter.metadata else {}

        # Check if this is a capability test with separate output/method correctness
        if "output_correct" in metadata and "method_correct" in metadata:
            # Multi-scorer format for capability tests
            scores["output_correctness"] = {
                "passed": metadata["output_correct"],
                "score": 1.0 if metadata["output_correct"] else 0.0,
                "reason": f"Output: {metadata.get('result', 'N/A')} (expected: {metadata.get('expected', 'N/A')})",
            }
            scores["method_correctness"] = {
                "passed": metadata["method_correct"],
                "score": 1.0 if metadata["method_correct"] else 0.0,
                "reason": f"Approach: {metadata.get('approach_used', 'unknown')}",
            }
        else:
            # Single evaluator format (default)
            scores["evaluator"] = {
                "passed": judge_passed,
                "score": judge_score,
                "reason": judge_reason,
            }

        # Build test result in standard .006eval format
        # See docs/evaluation-file-format.md for specification
        test_result = {
            # Required fields
            "test_id": result.task.id,
            "model": self.config.model,
            "variant": "v1_baseline",  # Mandatory even with single variant
            "passed": judge_passed,  # AND of all judges
            "scores": scores,
            # Optional fields
            "test_name": benchmark,
            "display_name": result.task.description or result.task.id,
            "input": result.task.input_data,
            "output": None,  # Could be populated if available
            "expected": result.task.expected_output,
            "metrics": {
                "iterations": len(result.iterations),
                "first_attempt_passed": result.first_attempt_success,
                "improvement_delta": result.improvement_delta,
                **(final_iter.metadata if final_iter and final_iter.metadata else {}),
            },
            "trace_file": final_iter.trace_path if final_iter else None,
            "error": final_iter.error_message if final_iter and not result.final_success else None,
        }

        # Append to file as JSONL (each line is a complete test result)
        with open(self._current_experiment_path, "a") as f:
            json.dump(test_result, f, default=str)
            f.write("\n")

    def _finalize_experiment(
        self, benchmark: str, report: BenchmarkReport, completed_at: datetime
    ) -> None:
        """Mark experiment as completed and write final metadata."""
        if not hasattr(self, "_current_experiment_path"):
            logger.warning("No experiment file to finalize")
            return

        # Read all results from file (now in standard format)
        results = []
        metadata = None

        with open(self._current_experiment_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if "metadata" in data:
                    # First line with metadata
                    metadata = data["metadata"]
                else:
                    # Subsequent lines are test results (no wrapper)
                    results.append(data)

        # Update metadata with completion
        if metadata:
            metadata["status"] = "completed"
            metadata["completed_at"] = completed_at.isoformat()
            metadata["duration_seconds"] = report.duration_seconds
            metadata["aggregate_metrics"] = report.aggregate_metrics

        # Rewrite file in JSONL format (line 1 = metadata, lines 2+ = results)
        # This matches ExperimentWriter.finalize() behavior
        with open(self._current_experiment_path, "w") as f:
            # Line 1: Metadata line (single-line JSON, no indent)
            metadata_line = {
                "metadata": metadata or {},
                "results": [],
            }
            json.dump(metadata_line, f, default=str)
            f.write("\n")

            # Lines 2+: Each result as single-line JSON
            for result in results:
                json.dump(result, f, default=str)
                f.write("\n")

        logger.info(
            f"Finalized experiment: {self._current_experiment_path} ({len(results)} results)"
        )

    def _save_combined_report(self, reports: dict[str, BenchmarkReport]) -> None:
        """Save combined report across all benchmarks (summary file, not for viewer)."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Use .006summary.json (not .006eval.json) - this is a summary, not evaluation results
        report_path = Path(self.config.results_dir) / f"combined_report_{timestamp}.006summary.json"

        data = {
            "timestamp": timestamp,
            "benchmarks": {
                name: {
                    "success_rate": report.success_rate,
                    "num_tasks": report.num_tasks,
                    "aggregate_metrics": report.aggregate_metrics,
                }
                for name, report in reports.items()
            },
        }

        with open(report_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved combined report to {report_path}")


# Convenience function for quick evaluation
async def evaluate_agent(
    agent_factory: Callable[[], Any],
    benchmarks: list[str],
    max_iterations: int = 5,
    task_limit: int | None = None,
) -> dict[str, BenchmarkReport]:
    """
    Quick evaluation of an agent on specified benchmarks.

    Args:
        agent_factory: Callable that creates agent instances
        benchmarks: List of benchmark names
        max_iterations: Max improvement iterations per task
        task_limit: Limit tasks per benchmark (for quick testing)

    Returns:
        Dict of benchmark reports
    """
    config = RunnerConfig(
        max_improvement_iterations=max_iterations,
    )

    runner = SelfImprovementRunner(
        agent_factory=agent_factory,
        benchmarks=benchmarks,
        config=config,
    )

    reports = {}
    for name in benchmarks:
        adapter = runner.adapters[name]
        reports[name] = await runner.run_benchmark(name, adapter, task_limit)

    return reports
