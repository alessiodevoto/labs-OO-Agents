"""
Core protocol definitions for benchmark-agnostic evaluation.

This module defines the abstract interfaces that all benchmark adapters must implement,
as well as the data structures used throughout the evaluation framework.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, TypeVar


class ErrorCategory(Enum):
    """Categorization of errors for analysis and improvement."""

    # Validation errors (caught before execution)
    SYNTAX_ERROR = "syntax_error"
    VALIDATION_ERROR = "validation_error"
    FORBIDDEN_OPERATION = "forbidden_operation"

    # Tool/function calling errors
    WRONG_TOOL = "wrong_tool"
    WRONG_ARGUMENTS = "wrong_arguments"
    MISSING_ARGUMENTS = "missing_arguments"
    EXTRA_ARGUMENTS = "extra_arguments"

    # Execution errors
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    ASSERTION_FAILED = "assertion_failed"

    # Logic errors
    WRONG_OUTPUT = "wrong_output"
    INCOMPLETE_SOLUTION = "incomplete_solution"
    POLICY_VIOLATION = "policy_violation"

    # Other
    UNKNOWN = "unknown"


@dataclass
class Task:
    """
    A single benchmark task.

    Attributes:
        id: Unique identifier for this task
        description: Human-readable description of what the task requires
        input_data: Benchmark-specific input (prompt, function specs, etc.)
        expected_output: Ground truth for evaluation
        metadata: Additional benchmark-specific information
    """

    id: str
    description: str
    input_data: dict[str, Any]
    expected_output: Any
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class EvalResult:
    """
    Result of evaluating a single task attempt.

    Attributes:
        task_id: ID of the task that was evaluated
        success: Whether the task was completed successfully
        score: Numeric score (0.0-1.0), benchmark-specific interpretation
        error_category: Categorized error type for analysis
        error_message: Detailed error description
        trace_path: Path to the JSONL trace file for this attempt
        metadata: Additional evaluation details (test results, etc.)
        timestamp: When this evaluation was performed
    """

    task_id: str
    success: bool
    score: float
    error_category: ErrorCategory | None = None
    error_message: str | None = None
    trace_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        # Normalize score to 0-1 range
        self.score = max(0.0, min(1.0, self.score))


@dataclass
class TaskResult:
    """
    Complete result of running a task through the self-improvement loop.

    Attributes:
        task: The original task
        iterations: Results from each improvement iteration
        final_success: Whether the task was ultimately solved
        improvement_curve: Score at each iteration
        total_iterations: Number of attempts made
        improvement_context_history: The refinement hints generated at each step
    """

    task: Task
    iterations: list[EvalResult]
    final_success: bool
    improvement_curve: list[float]
    total_iterations: int
    improvement_context_history: list[str] = field(default_factory=list)

    @property
    def first_attempt_success(self) -> bool:
        """Whether the first attempt succeeded (no improvement needed)."""
        return len(self.iterations) > 0 and self.iterations[0].success

    @property
    def improvement_delta(self) -> float:
        """Score improvement from first to last attempt."""
        if len(self.improvement_curve) < 2:
            return 0.0
        return self.improvement_curve[-1] - self.improvement_curve[0]

    @property
    def solved_after_improvement(self) -> bool:
        """Whether the task was solved after initially failing."""
        return self.final_success and not self.first_attempt_success


@dataclass
class BenchmarkReport:
    """
    Aggregate report for a complete benchmark run.

    Attributes:
        benchmark_name: Name of the benchmark
        task_results: Results for each task
        aggregate_metrics: Computed metrics across all tasks
        config: Configuration used for this run
        started_at: When the benchmark run started
        completed_at: When the benchmark run completed
    """

    benchmark_name: str
    task_results: list[TaskResult]
    aggregate_metrics: dict[str, float]
    config: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def duration_seconds(self) -> float | None:
        """Total duration of the benchmark run."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def num_tasks(self) -> int:
        return len(self.task_results)

    @property
    def num_successful(self) -> int:
        return sum(1 for r in self.task_results if r.final_success)

    @property
    def success_rate(self) -> float:
        if self.num_tasks == 0:
            return 0.0
        return self.num_successful / self.num_tasks


@dataclass
class StepResult:
    """
    Result of a single step in an interactive benchmark environment.

    Following the OpenAI Gym pattern, each step returns:
    - observation: What the agent observes after taking the action
    - reward: Numeric signal (typically 0.0 or 1.0 for success)
    - terminated: Whether the episode is complete
    - truncated: Always False (not used in current implementation)
    - info: Additional information (errors, metadata, etc.)
    """

    observation: str
    reward: float
    terminated: bool
    truncated: bool
    info: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.info is None:
            self.info = {}


class BenchmarkEnvironment(ABC):
    """
    Abstract base class for interactive benchmark environments.

    Follows the OpenAI Gym pattern for multi-step agent interaction.
    This is distinct from BenchmarkAdapter - adapters handle task loading
    and evaluation, while environments handle the actual execution context.

    Key differences from BenchmarkAdapter:
    - BenchmarkAdapter: Loads tasks, formats prompts, evaluates final outputs
    - BenchmarkEnvironment: Manages execution state, processes actions, returns observations

    Multi-step benchmarks (InterCode, tau-bench, SWE-bench) require environments
    that agents can interact with over multiple steps. These environments typically
    run in Docker containers for proper isolation.

    Usage:
        env = InterCodeEnvironment(task)
        obs = await env.reset(task)
        while not done:
            action = await agent.decide(obs)
            result = await env.step(action)
            obs = result.observation
            done = result.terminated
        await env.close()

    Implementers must provide:
    - reset(): Initialize environment for a task
    - step(): Execute an action and return result
    - close(): Clean up resources
    - get_tools(): Return tool classes for agent injection
    """

    @abstractmethod
    async def reset(self, task: Task) -> str:
        """
        Initialize the environment for a new task.

        This sets up any necessary state (database, filesystem, containers)
        and returns the initial observation the agent will see.

        Args:
            task: The task to set up the environment for

        Returns:
            Initial observation string
        """
        pass

    @abstractmethod
    async def step(self, action: str) -> StepResult:
        """
        Execute an action in the environment.

        Args:
            action: The action to execute (command, SQL query, etc.)

        Returns:
            StepResult containing observation, reward, done flag, and info
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Clean up environment resources.

        This should stop containers, close connections, and free resources.
        Always call this when done with the environment.
        """
        pass

    @abstractmethod
    def get_tools(self) -> dict[str, Any]:
        """
        Return tool classes that agents can use to interact with this environment.

        Tools should be classes that agents can instantiate and call methods on,
        following the agent006 tool pattern. For example:

            {"sql": SQLExecutor, "bash": BashExecutor}

        Returns:
            Dict mapping tool names to tool classes
        """
        pass

    @property
    def max_steps(self) -> int:
        """Maximum number of steps allowed before termination."""
        return 30

    @property
    def requires_docker(self) -> bool:
        """Whether this environment requires Docker to run."""
        return True

    def ensure_docker_available(self) -> None:
        """
        Check that Docker is available and running if required.

        Uses the Docker SDK to verify the daemon is accessible, not just
        that the docker binary is on PATH.

        Raises:
            RuntimeError: If Docker is required but not available/running
        """
        if not self.requires_docker:
            return

        try:
            import docker

            client = docker.from_env()
            client.ping()
        except ImportError as e:
            raise RuntimeError(
                f"{self.__class__.__name__} requires the Docker SDK. "
                "Install with: pip install docker"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Docker is not available for {self.__class__.__name__}: {e}\n"
                "Make sure Docker is installed and the daemon is running."
            ) from e


class BenchmarkAdapter(ABC):
    """
    Abstract base class for benchmark integrations.

    Each benchmark adapter translates between the benchmark's native format
    and the evaluation framework's common protocol. Adapters must implement:

    - get_tasks(): Load tasks from the benchmark
    - format_for_agent(): Convert a task to agent input format
    - evaluate(): Score the agent's output

    Optionally, adapters may override:
    - get_tools(): Provide benchmark-specific tools
    - setup(): Perform one-time initialization
    - teardown(): Clean up resources
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this benchmark."""
        pass

    @property
    def description(self) -> str:
        """Human-readable description of what this benchmark tests."""
        return ""

    @abstractmethod
    def get_tasks(self, split: str = "test", limit: int | None = None) -> list[Task]:
        """
        Load tasks from the benchmark.

        Args:
            split: Dataset split to use ("train", "test", "validation")
            limit: Maximum number of tasks to load (None for all)

        Returns:
            List of Task objects
        """
        pass

    @abstractmethod
    def format_for_agent(self, task: Task) -> dict[str, Any]:
        """
        Convert a task to the format expected by the agent.

        The returned dict should contain keys that the agent understands,
        typically including:
        - system_prompt: Instructions for the agent
        - user_message: The actual task/query
        - tools: Available tools (if applicable)

        Args:
            task: The task to format

        Returns:
            Dict with agent input fields
        """
        pass

    @abstractmethod
    def evaluate(self, task: Task, agent_output: Any, trace: dict[str, Any]) -> EvalResult:
        """
        Evaluate the agent's output for a task.

        Args:
            task: The original task
            agent_output: What the agent produced
            trace: Trace information including path to JSONL file

        Returns:
            EvalResult with success/score/errors
        """
        pass

    def get_tools(self) -> list[Any]:
        """
        Get benchmark-specific tools the agent can use.

        Returns:
            List of tool definitions/instances (empty by default)
        """
        return []

    def setup(self) -> None:  # noqa: B027
        """
        Perform one-time setup (e.g., download data, start containers).

        Called once before any tasks are run.
        """
        pass

    def teardown(self) -> None:  # noqa: B027
        """
        Clean up resources (e.g., stop containers, close connections).

        Called after all tasks are complete.
        """
        pass

    def create_repair_task(
        self, original_task: Task, failed_output: Any, error: EvalResult
    ) -> Task:
        """
        Create a follow-up task for self-repair scenarios.

        Some benchmarks (like LiveCodeBench) have explicit self-repair tasks.
        This method creates a new task that includes the previous failure.

        Args:
            original_task: The task that failed
            failed_output: What the agent produced
            error: The evaluation result with error details

        Returns:
            New Task with failure context included
        """
        return Task(
            id=f"{original_task.id}_repair_{len(original_task.metadata.get('repair_attempts', []))}",
            description=original_task.description,
            input_data={
                **original_task.input_data,
                "previous_attempt": failed_output,
                "error_category": error.error_category.value if error.error_category else None,
                "error_message": error.error_message,
            },
            expected_output=original_task.expected_output,
            metadata={
                **original_task.metadata,
                "is_repair": True,
                "original_task_id": original_task.id,
                "repair_attempts": original_task.metadata.get("repair_attempts", []) + [error],
            },
        )


# ===========================
# Layer 0/1/2 Protocols
# ===========================
# These protocols define the lower-level execution and analysis infrastructure
# that sits beneath the benchmark adapters.

R = TypeVar("R", covariant=True)


@dataclass
class EngineConfig:
    """Configuration for execution engines (Layer 0).

    Generic configuration that all execution engines understand.
    Engine-specific configs can extend this.
    """

    max_concurrent: int = 10
    timeout_seconds: float | None = None
    enable_checkpointing: bool = True


@dataclass
class TaskState:
    """Checkpoint state for a single task.

    Used by execution engines to resume from crashes or skip completed tasks.
    """

    task_id: str
    completed: bool
    result: Any | None = None
    error: str | None = None
    timestamp: float | None = None


class ExecutionEngine(Protocol):
    """Protocol for swappable execution engines (Layer 0).

    Different implementations provide different execution strategies:
    - AsyncIOEngine: Async I/O with semaphore (for I/O-bound tasks like LLM APIs)
    - MultiprocessEngine: Process pool (for CPU-bound tasks like local models)
    - RayEngine: Distributed execution across cluster (for massive scale)
    - NemoRunEngine: Slurm/cloud batch submission (for HPC clusters)

    All engines share the same interface, allowing Layer 2 (Runner) to be
    agnostic to the execution strategy.
    """

    async def run_tasks(
        self,
        task_ids: list[str],
        task_fn: Callable[[str], Awaitable[R]],
        config: EngineConfig,
        checkpoint_state: list[TaskState] | None = None,
        on_task_complete: Callable[[str, R | Exception], None] | None = None,
    ) -> list[R]:
        """Execute tasks using engine-specific strategy.

        Args:
            task_ids: List of task identifiers
            task_fn: Async function that takes task_id and returns result
            config: Engine configuration
            checkpoint_state: Previous run state for resume (optional)
            on_task_complete: Callback for each completed task (optional)

        Returns:
            List of results in same order as task_ids
        """
        ...


@dataclass
class ModelUsageStats:
    """Usage statistics for a single model."""

    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def p95_latency_ms(self) -> float | None:
        """95th percentile latency in milliseconds."""
        if not self.latencies_ms:
            return None
        sorted_latencies = sorted(self.latencies_ms)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[idx]


@dataclass
class TaskUsageStats:
    """Usage statistics extracted from a single task's trace."""

    task_id: str
    total_runtime_seconds: float
    models_used: list[ModelUsageStats] = field(default_factory=list)
    total_llm_calls: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens across all models."""
        return sum(m.total_tokens for m in self.models_used)

    @property
    def total_prompt_tokens(self) -> int:
        """Total prompt tokens across all models."""
        return sum(m.prompt_tokens for m in self.models_used)

    @property
    def total_completion_tokens(self) -> int:
        """Total completion tokens across all models."""
        return sum(m.completion_tokens for m in self.models_used)


@dataclass
class AggregateUsageStats:
    """Aggregate usage statistics across multiple tasks."""

    num_tasks: int
    per_task_stats: list[TaskUsageStats] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """Total tokens across all tasks."""
        return sum(s.total_tokens for s in self.per_task_stats)

    @property
    def total_runtime_seconds(self) -> float:
        """Total runtime across all tasks."""
        return sum(s.total_runtime_seconds for s in self.per_task_stats)

    @property
    def models_breakdown(self) -> dict[str, ModelUsageStats]:
        """Aggregate stats per model across all tasks."""
        by_model: dict[str, ModelUsageStats] = {}

        for task_stats in self.per_task_stats:
            for model_stats in task_stats.models_used:
                if model_stats.model_name not in by_model:
                    by_model[model_stats.model_name] = ModelUsageStats(
                        model_name=model_stats.model_name
                    )

                agg = by_model[model_stats.model_name]
                agg.prompt_tokens += model_stats.prompt_tokens
                agg.completion_tokens += model_stats.completion_tokens
                agg.total_tokens += model_stats.total_tokens
                agg.call_count += model_stats.call_count
                agg.latencies_ms.extend(model_stats.latencies_ms)

        return by_model


class TraceAnalyzer(Protocol):
    """Protocol for analyzing OTel traces to extract usage statistics.

    Implementations read .jsonl trace files and extract:
    - Token counts per model
    - LLM call latencies
    - Total runtime

    This avoids duplicating trace data in IntermediateSteps.
    """

    def analyze_trace(self, trace_path: str) -> TaskUsageStats:
        """Analyze a single trace file and extract usage statistics.

        Args:
            trace_path: Path to .jsonl trace file

        Returns:
            TaskUsageStats with extracted metrics
        """
        ...
