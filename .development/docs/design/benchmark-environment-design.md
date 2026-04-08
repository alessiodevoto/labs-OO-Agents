# Benchmark Environment Design

> **Implementation Status: COMPLETE** (Dec 11, 2025)
>
> All components have been implemented:
> - ✅ Cleanup: Removed all `_get_builtin_tasks()` fallbacks from adapters
> - ✅ Cleanup: Removed simplified stubs from `intercode.py`
> - ✅ Protocol: Added `StepResult` and `BenchmarkEnvironment` ABC to `protocol.py`
> - ✅ Environment: `InterCodeEnvironment` wrapping InterCode Docker envs
> - ✅ Environment: `TauBenchEnvironment` with Docker isolation
> - ✅ Environment: `SWEBenchEnvironment` wrapping SWE-agent's SWEEnv
> - ✅ Environment: `SingleStepEnvironment` for one-shot benchmarks
> - ✅ Runner: Updated `SelfImprovementRunner` to use environments
>
> Files created/modified:
> - `evaluation/protocol.py` - Added StepResult, BenchmarkEnvironment ABC
> - `evaluation/environments/__init__.py` - New module
> - `evaluation/environments/intercode.py` - InterCodeEnvironment
> - `evaluation/environments/tau_bench.py` - TauBenchEnvironment
> - `evaluation/environments/swebench.py` - SWEBenchEnvironment
> - `evaluation/environments/single_step.py` - SingleStepEnvironment
> - `evaluation/runner.py` - Multi-step execution support
> - `evaluation/adapters/*.py` - Removed fallbacks from all adapters

## Overview

This document describes the design for a Gym-style `BenchmarkEnvironment` abstraction that provides a unified interface for both single-step and multi-step benchmarks. The design wraps existing benchmark environments (InterCode, tau-bench, SWE-agent) while exposing a consistent API.

**Key Principle: No Fallbacks or Stubs**

If the real benchmark environment cannot be run (missing dependencies, Docker not running, etc.), we **fail with a clear error** rather than providing fake/simplified results. This ensures evaluation results are always meaningful.

**Key Principle: Uniform Execution Path**

**All benchmarks go through an environment.** There is no branching between single-step and multi-step benchmarks at the runner level:

- **Multi-step benchmarks** (InterCode, tau-bench, SWE-bench): Use Docker-based environments with tool injection
- **Single-step benchmarks** (BFCL, LiveCodeBench, GAIA, etc.): Use `SingleStepEnvironment` wrapper

This provides:
1. **Simpler runner code** - no conditional paths
2. **Consistent API** - all benchmarks follow the same `reset() → run → close()` pattern
3. **Future extensibility** - easy to add tools or multi-step variants to "single-step" benchmarks
4. **Uniform observability** - trajectory tracking available for all benchmarks

## Cleanup: Remove Existing Fallbacks

Before implementing the new environment abstraction, remove all fallback/stub code from existing adapters:

### 1. Remove `_get_builtin_tasks()` from all adapters

These methods return fake example tasks when real data can't be loaded. Replace with errors:

| File | Change |
|------|--------|
| `swebench.py` | Remove `_get_builtin_tasks()`, raise error if HuggingFace fetch fails |
| `gaia.py` | Remove `_get_builtin_tasks()`, raise error if HuggingFace fetch fails |
| `dabstep.py` | Remove `_get_builtin_tasks()`, raise error if fetch fails |
| `bigcodebench.py` | Remove `_get_builtin_tasks()`, raise error if fetch fails |
| `bfcl.py` | Remove `_get_builtin_tasks()`, raise error if fetch fails |
| `livecodebench.py` | Remove `_get_builtin_tasks()`, raise error if fetch fails |

### 2. Remove simplified implementations from `intercode.py`

| Method | Action |
|--------|--------|
| `_execute_sql_step()` | Remove - real execution comes from `IntercodeEnv` |
| `_execute_bash_step()` | Remove - real execution comes from `IntercodeEnv` |
| `execute_step()` | Remove - replaced by `InterCodeEnvironment.step()` |
| `_evaluate_direct()` | Remove - if no trajectory, it's a failure |

### 3. Update error messages

All adapters should have clear error messages like:
```python
raise RuntimeError(
    "InterCode SQL environment requires Docker. "
    "Install: pip install intercode && docker pull intercode-sql"
)
```

## Key Insight: Follow OpenAI Gym Pattern

Both InterCode and tau-bench use the OpenAI Gym interface:
- `reset()` → initial observation
- `step(action)` → `(observation, reward, done, info)`

SWE-agent's `SWEEnv` uses a different but compatible model:
- `start()` / `reset()` - initialize/reset environment
- `communicate(input)` → output (execute command)
- `read_file(path)` / `write_file(path, content)` - file operations
- `close()` - shutdown

We can wrap all of these in a unified `BenchmarkEnvironment` interface.

## Protocol Design

### StepResult Dataclass

```python
@dataclass
class StepResult:
    """Result of a single environment step (Gym-style)."""
    observation: str          # stdout/response from action
    reward: float             # 0.0-1.0 correctness signal
    done: bool                # episode complete?
    info: dict[str, Any]      # auxiliary data (e.g., cwd, test results)
```

### BenchmarkEnvironment ABC

```python
class BenchmarkEnvironment(ABC):
    """
    Gym-style execution environment for benchmarks.

    Follows the classic OpenAI Gym interface:
    - reset(task) → initial observation
    - step(action) → StepResult(observation, reward, done, info)

    Plus agent006-specific:
    - get_tools() → tool class instances for agent to use as self.<name>
    """

    @abstractmethod
    async def reset(self, task: Task) -> str:
        """
        Initialize environment for a task.

        Returns:
            Initial observation (task description, context, etc.)
        """

    @abstractmethod
    async def step(self, action: str) -> StepResult:
        """
        Execute an action in the environment.

        Args:
            action: Code/command to execute

        Returns:
            StepResult with observation, reward, done flag, and info
        """

    @abstractmethod
    async def close(self) -> None:
        """Clean up environment resources."""

    @abstractmethod
    def get_tools(self) -> dict[str, Any]:
        """
        Return tool class instances for agent to use.

        Returns:
            Dict mapping tool names to tool instances.
            Agent accesses as self.<name> (e.g., self.sql.execute())
        """

    @property
    def max_steps(self) -> int:
        """Maximum interaction steps before timeout."""
        return 50
```

## Environment Implementations

### 1. InterCodeEnvironment (Wraps IntercodeEnv)

Wraps the actual InterCode Gym environment from `princeton-nlp/intercode`.

```python
class InterCodeEnvironment(BenchmarkEnvironment):
    """Wraps InterCode's IntercodeEnv Gym environment."""

    def __init__(self, env_type: str = "sql"):
        self.env_type = env_type
        self._env: IntercodeEnv | None = None  # The actual Gym env

    async def reset(self, task: Task) -> str:
        # Import and create the actual InterCode environment
        from intercode.envs import SqlEnv, BashEnv

        if self.env_type == "sql":
            self._env = SqlEnv(data_path=task.metadata.get("data_path"))
        else:
            self._env = BashEnv(data_path=task.metadata.get("data_path"))

        obs = self._env.reset(task.id)
        return obs

    async def step(self, action: str) -> StepResult:
        obs, reward, done, info = self._env.step(action)
        return StepResult(
            observation=obs,
            reward=reward,
            done=done,
            info=info
        )

    def get_tools(self) -> dict[str, Any]:
        """Return tool wrapper around environment."""
        return {"env": InterCodeTool(self)}

    async def close(self):
        if self._env:
            self._env.close()


class InterCodeTool:
    """Tool wrapper for InterCode environment."""

    def __init__(self, env: InterCodeEnvironment):
        self._env = env

    async def execute(self, command: str) -> str:
        """Execute a command and return the output."""
        result = await self._env.step(command)
        return result.observation
```

### 2. TauBenchEnvironment (Docker-isolated tau-bench)

Runs tau-bench in Docker containers for isolation during parallel execution.
Each task gets a fresh container with clean state - no contamination between tasks.

```python
class TauBenchEnvironment(BenchmarkEnvironment):
    """
    Runs tau-bench in Docker for parallel task isolation.

    Each task gets its own container with:
    - Fresh simulated database state
    - Independent LLM-powered user simulation
    - No shared state with other tasks
    """

    def __init__(self, domain: str = "retail", user_model: str = "gpt-4o-mini"):
        self.domain = domain
        self.user_model = user_model
        self._container = None
        self._env = None
        self._tools_instance = None

    async def reset(self, task: Task) -> str:
        # Start Docker container with tau-bench
        self._container = await self._start_container()

        # Create environment inside container
        # (tau-bench env runs in container, we communicate via API/socket)
        from tau_bench.envs import RetailEnv, AirlineEnv

        if self.domain == "retail":
            self._env = RetailEnv(user_model=self.user_model)
        else:
            self._env = AirlineEnv(user_model=self.user_model)

        obs = self._env.reset(task_id=task.id)
        self._tools_instance = TauBenchTools(self._env)
        return obs

    async def _start_container(self):
        """Start isolated Docker container for this task."""
        import docker
        client = docker.from_env()
        return client.containers.run(
            "tau-bench:latest",
            detach=True,
            remove=True,
            environment={"OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY")},
        )

    async def step(self, action: str) -> StepResult:
        obs, reward, done, info = self._env.step(action)
        return StepResult(
            observation=obs,
            reward=reward,
            done=done,
            info=info
        )

    def get_tools(self) -> dict[str, Any]:
        """Return domain-specific tools."""
        return {"api": self._tools_instance}

    async def close(self):
        if self._env:
            self._env.close()
        if self._container:
            self._container.stop()


class TauBenchTools:
    """Tool class wrapping tau-bench domain APIs."""

    def __init__(self, env):
        self._env = env

    # Retail domain tools
    async def find_user_id_by_email(self, email: str) -> str:
        return self._env.call_tool("find_user_id_by_email", {"email": email})

    async def get_order_details(self, order_id: str) -> str:
        return self._env.call_tool("get_order_details", {"order_id": order_id})

    # ... other tools mapped from tau-bench
```

### 3. SWEBenchEnvironment (Wraps SWE-agent's SWEEnv)

Wraps SWE-agent's `SWEEnv` in our unified interface.

```python
class SWEBenchEnvironment(BenchmarkEnvironment):
    """
    Wraps SWE-agent's SWEEnv for SWE-bench tasks.

    SWEEnv handles:
    - Docker container management
    - Repository checkout at specific commit
    - Command execution in isolated environment
    """

    def __init__(self):
        self._swe_env: SWEEnv | None = None
        self._shell_tool = None
        self._file_tool = None

    async def reset(self, task: Task) -> str:
        from sweagent.environment.swe_env import SWEEnv, EnvironmentConfig
        from swerex.deployment.config import DockerDeploymentConfig

        # Configure for this task's repository
        config = EnvironmentConfig(
            deployment=DockerDeploymentConfig(
                image="python:3.11",
            ),
            repo=RepoConfig(
                github_url=f"https://github.com/{task.input_data['repo']}",
                base_commit=task.input_data['base_commit'],
            ),
        )

        self._swe_env = SWEEnv.from_config(config)
        self._swe_env.start()

        # Create tool instances
        self._shell_tool = ShellTool(self._swe_env)
        self._file_tool = FileTool(self._swe_env)

        return f"Repository: {task.input_data['repo']}\n\nIssue:\n{task.input_data['problem_statement']}"

    async def step(self, action: str) -> StepResult:
        """Execute action via shell tool."""
        output = self._swe_env.communicate(action)

        # SWE-bench doesn't have per-step reward
        # Reward is computed at the end by running tests
        return StepResult(
            observation=output,
            reward=0.0,  # Computed at evaluation time
            done=False,  # Agent decides when done
            info={}
        )

    def get_tools(self) -> dict[str, Any]:
        return {
            "shell": self._shell_tool,
            "files": self._file_tool,
        }

    async def close(self):
        if self._swe_env:
            self._swe_env.close()


class ShellTool:
    """Shell command execution tool wrapping SWEEnv."""

    def __init__(self, swe_env: SWEEnv):
        self._env = swe_env

    def execute(self, command: str, timeout: int = 30) -> str:
        """Execute a shell command and return output."""
        return self._env.communicate(command, timeout=timeout)

    def interrupt(self) -> None:
        """Interrupt currently running command."""
        self._env.interrupt_session()


class FileTool:
    """File operations tool wrapping SWEEnv."""

    def __init__(self, swe_env: SWEEnv):
        self._env = swe_env

    def read(self, path: str) -> str:
        """Read file contents."""
        return self._env.read_file(path)

    def write(self, path: str, content: str) -> None:
        """Write content to file."""
        self._env.write_file(path, content)
```

### 4. SingleStepEnvironment (For BFCL, LiveCodeBench, etc.)

For benchmarks that don't need multi-step interaction.

```python
class SingleStepEnvironment(BenchmarkEnvironment):
    """
    Simple environment for one-shot benchmarks.

    - reset() returns the task prompt
    - step() executes once and returns done=True
    - get_tools() returns benchmark-specific tools or empty dict
    """

    def __init__(self, adapter: BenchmarkAdapter):
        self._adapter = adapter
        self._task: Task | None = None
        self._completed = False

    async def reset(self, task: Task) -> str:
        self._task = task
        self._completed = False
        formatted = self._adapter.format_for_agent(task)
        return formatted.get("user_message", task.description)

    async def step(self, action: str) -> StepResult:
        if self._completed:
            return StepResult(
                observation="Task already completed",
                reward=0.0,
                done=True,
                info={}
            )

        # Single step - evaluate immediately
        self._completed = True

        # Let adapter handle evaluation
        result = self._adapter.evaluate(
            self._task,
            action,
            trace={"path": ""}
        )

        return StepResult(
            observation="Task submitted for evaluation",
            reward=result.score,
            done=True,
            info={"eval_result": result}
        )

    def get_tools(self) -> dict[str, Any]:
        """Single-step benchmarks typically don't need tools."""
        return {}

    async def close(self):
        pass

    @property
    def max_steps(self) -> int:
        return 1
```

## Integration Pattern

### Agent Loop with Environment

```python
async def run_task_with_environment(
    agent: Agent,
    env: BenchmarkEnvironment,
    task: Task
) -> EvalResult:
    """Run agent in environment-controlled loop."""

    # Setup: Initialize environment and inject tools
    initial_obs = await env.reset(task)
    tools = env.get_tools()

    # Inject tools into agent (agent006 pattern)
    for name, tool in tools.items():
        setattr(agent, name, tool)

    # Agent's own loop handles interaction
    # For ITERATIVE strategy, agent calls tools via self.<name>
    result = await agent.solve(initial_obs)

    # Cleanup
    await env.close()

    return result
```

### Environment Factory

```python
def get_environment(adapter: BenchmarkAdapter) -> BenchmarkEnvironment:
    """Factory to get appropriate environment for adapter."""

    if isinstance(adapter, InterCodeAdapter):
        return InterCodeEnvironment(env_type=adapter.environment)
    elif isinstance(adapter, TauBenchAdapter):
        return TauBenchEnvironment(domain=adapter.domain)
    elif isinstance(adapter, SWEBenchAdapter):
        return SWEBenchEnvironment()
    else:
        # Default: wrap in single-step environment
        return SingleStepEnvironment(adapter)
```

## Summary Table

| Benchmark | Underlying Env | Our Wrapper | Docker? | Tools Provided |
|-----------|---------------|-------------|---------|----------------|
| InterCode | `IntercodeEnv` (Gym) | `InterCodeEnvironment` | Yes | `env.execute()` |
| tau-bench | tau-bench env (Gym) | `TauBenchEnvironment` | Yes | Domain API tools |
| SWE-bench | `SWEEnv` (SWE-agent) | `SWEBenchEnvironment` | Yes | `shell`, `files` |
| BFCL | None | `SingleStepEnvironment` | No | None |
| LiveCodeBench | None | `SingleStepEnvironment` | No | `python.run()` |
| DABStep | None | `SingleStepEnvironment` | No | None |
| GAIA | None | `SingleStepEnvironment` | No | Web search, files |

## Implementation Order

### Phase 0: Cleanup (Remove Fallbacks)

1. **Remove `_get_builtin_tasks()` from all adapters**:
   - `swebench.py` - raise error on fetch failure
   - `gaia.py` - raise error on fetch failure
   - `dabstep.py` - raise error on fetch failure
   - `bigcodebench.py` - raise error on fetch failure
   - `bfcl.py` - raise error on fetch failure
   - `livecodebench.py` - raise error on fetch failure

2. **Remove simplified implementations from `intercode.py`**:
   - Delete `_execute_sql_step()`, `_execute_bash_step()`, `execute_step()`
   - Delete `_evaluate_direct()` fallback

### Phase 1: Protocol

3. **protocol.py**: Add `StepResult` dataclass and `BenchmarkEnvironment` ABC

### Phase 2: Environment Implementations

4. **InterCodeEnvironment**: Wrap InterCode's `IntercodeEnv` (requires Docker)
5. **TauBenchEnvironment**: Wrap tau-bench's Gym env (Docker for parallel isolation)
6. **SWEBenchEnvironment**: Wrap SWE-agent's `SWEEnv` (requires Docker)
7. **SingleStepEnvironment**: Wrapper for one-shot benchmarks (BFCL, LiveCodeBench, etc.)

### Phase 3: Integration

8. **Runner integration**: Update evaluation runner to use environments
9. **Agent wiring**: Ensure agent006 agents receive environment tools

## Dependencies

All environments require their actual benchmark dependencies:

| Benchmark | Dependency | Notes |
|-----------|------------|-------|
| InterCode | `pip install intercode` + Docker | Containers for SQL/Bash execution |
| tau-bench | `pip install tau-bench` + Docker + LLM API | Containers for state isolation, LLM for simulated user |
| SWE-bench | `pip install sweagent` + Docker | Containers for repo checkout + execution |
| BFCL | Network access | Fetches from HuggingFace |
| LiveCodeBench | Network access | Fetches from HuggingFace |
| GAIA | `HF_TOKEN` env var | Gated dataset on HuggingFace |
| BigCodeBench | Network access | Fetches from HuggingFace |
| DABStep | Network access | Fetches from source |

**Docker required for multi-step benchmarks**: InterCode, tau-bench, and SWE-bench all run in Docker containers to ensure:
- Complete state isolation between parallel tasks
- Reproducible execution environment
- No contamination from host system

**No fallbacks**: If dependencies are missing, adapters raise clear errors explaining what to install.
