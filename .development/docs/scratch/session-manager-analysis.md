# SessionManager Deep Dive: Should We Adopt It?

## What is SessionManager For?

NAT's `SessionManager` is designed for **multi-user workflow services** (FastAPI servers, chat applications). It provides:

### 1. Per-User Workflow Isolation

```python
class SessionManager:
    def __init__(self, ...):
        # Per-user management
        self._per_user_builders: dict[str, PerUserBuilderInfo] = {}
        self._per_user_session_timeout = config.general.per_user_workflow_timeout
        self._is_workflow_per_user = workflow_registration.is_per_user
```

**Use case**: Multi-tenant SaaS where each user has their own workflow state.

**Example**:
- User A: Chat session with conversation history
- User B: Different chat session with different history
- Each user gets isolated workflow instance

**Do we need this for batch evaluation?** NO - we don't have users, just tasks.

### 2. Per-User Concurrency Control

```python
class PerUserBuilderInfo(BaseModel):
    semaphore: asyncio.Semaphore  # Per-user semaphore!

async def run(self, message, runtime_type):
    async with self._semaphore:  # Per-user rate limiting
        async with self._workflow.run(message, ...) as runner:
            yield runner
```

**Use case**: Rate limit each user independently.

**Example**:
- User A can have 5 concurrent requests
- User B can have 5 concurrent requests
- Total: 10 concurrent (not 5 shared)

**Do we need this for batch evaluation?** NO - we want global concurrency limit across all tasks.

### 3. Automatic Lifecycle Management

```python
async def _run_periodic_cleanup(self):
    """Run every N minutes"""
    while not self._shutdown_event.is_set():
        await asyncio.wait_for(self._shutdown_event.wait(), timeout=cleanup_interval)
        await self._cleanup_inactive_per_user_builders()

async def _cleanup_inactive_per_user_builders(self) -> int:
    """Clean up builders inactive for > timeout"""
    now = datetime.now()
    threshold = now - self._per_user_session_timeout

    async with self._per_user_builders_lock:
        for user_id, builder_info in list(self._per_user_builders.items()):
            if builder_info.ref_count == 0 and builder_info.last_activity < threshold:
                await builder_info.builder.__aexit__(None, None, None)
                del self._per_user_builders[user_id]
```

**Use case**: Long-running server that needs to garbage collect inactive user sessions.

**Example**:
- User chats, then disappears for 30 minutes
- SessionManager automatically cleans up their workflow
- Frees memory and resources

**Do we need this for batch evaluation?** NO - our evaluation runs are finite, we shut down when done.

### 4. Context Management (Reactive Event Streams)

```python
class Runner:
    async def __aenter__(self):
        # Create reactive event stream for this run
        self._context_state.event_stream.set(Subject())
        self._context_state.active_function.set(InvocationNode(...))
        self._runtime_type_token = self._context_state.runtime_type.set(self._runtime_type)
```

**Use case**: Collect intermediate steps (LLM calls, tool calls) during workflow execution.

**Pattern**: Reactive streams with callbacks

```python
def pull_intermediate() -> asyncio.Future[list[dict]]:
    """Subscribe to runner's event stream using callbacks"""
    future = asyncio.Future()
    intermediate_steps = []

    def on_next_cb(item: IntermediateStep):
        intermediate_steps.append(item.model_dump())

    def on_complete_cb():
        future.set_result(intermediate_steps)

    context.intermediate_step_manager.subscribe(
        on_next=on_next_cb,
        on_complete=on_complete_cb
    )

    return future
```

**Do we need this for batch evaluation?** YES - intermediate steps are valuable!

But do we need SessionManager for this? Let's explore...

---

## What SessionManager REALLY Provides

### The Good Parts

#### 1. Structured Intermediate Step Collection

```python
class IntermediateStep(BaseModel):
    event_type: IntermediateStepType  # LLM_START, LLM_END, TOOL_START, TOOL_END, ...
    event_timestamp: float
    event_category: IntermediateStepCategory  # LLM, TOOL, WORKFLOW, TASK, ...

    # Rich metadata
    llm_name: str | None
    token_usage: TokenUsageBaseModel
    trace_metadata: TraceMetadata  # Chat inputs/outputs, tool calls, etc.

    # Hierarchy tracking
    invocation_node: InvocationNode  # Parent/child relationships
```

**This is valuable!** It's like OpenTelemetry spans but with domain-specific structure.

**Benefits**:
- Track which LLM was called
- Capture token usage per call
- Record tool invocations with inputs/outputs
- Build call hierarchy (which tool called which LLM)
- Measure latency per operation

**Example usage stats** (from evaluate.py):

```python
def _compute_usage_stats(self, item: EvalInputItem):
    steps = item.trajectory
    usage_stats_per_llm = {}

    for step in steps:
        if step.event_type == "LLM_END":
            llm_name = step.llm_name
            usage_stats_per_llm[llm_name].prompt_tokens += step.token_usage.prompt_tokens
            usage_stats_per_llm[llm_name].completion_tokens += ...
            usage_stats_per_llm[llm_name].reasoning_tokens += ...

    # Calculate p95 LLM latency
    llm_latencies = [...]
    llm_latency = float(np.percentile(llm_latencies, 95))
```

**We get:**
- Token counts per LLM
- Latency percentiles
- Call hierarchy
- Error tracking

#### 2. Observability Integration

```python
class Runner:
    def __init__(self, ..., exporter_manager: ExporterManager):
        self._exporter_manager = exporter_manager
```

**ExporterManager handles**:
- OpenTelemetry trace export
- Weave logging
- Phoenix integration
- Langfuse integration

**Benefit**: Automatic instrumentation - just run workflow, get traces.

#### 3. Unified Context (ContextState)

```python
class ContextState:
    input_message: ContextVar[Any]
    event_stream: ContextVar[Subject]
    runtime_type: ContextVar[RuntimeTypeEnum]
    active_function: ContextVar[InvocationNode]
    # ... many more
```

**Pattern**: Python context variables for implicit state passing.

**Benefit**: Workflow code doesn't need to explicitly pass context around.

```python
# Instead of:
await my_function(context, input, trace_ctx, exporter, ...)

# Just:
await my_function(input)
# It gets context implicitly
```

---

## The Cost of SessionManager

### 1. Architectural Coupling

**To use SessionManager, you must**:

```python
# Create SessionManager
session_manager = await SessionManager.create(
    config=nat_config,  # NAT Config object!
    shared_builder=workflow_builder,  # NAT WorkflowBuilder!
    max_concurrency=10,
)

# Use SessionManager
async with session_manager.session(user_id="user123") as session:
    async with session.run(input_obj, runtime_type=RuntimeTypeEnum.EVALUATE) as runner:
        result = await runner.result()
        intermediate_steps = await pull_intermediate()
```

**Dependencies**:
- `Config` - NAT's config system
- `WorkflowBuilder` - NAT's builder pattern
- `Workflow` - NAT's workflow abstraction
- `Runner` - NAT's execution runtime
- `ContextState` - NAT's context management

**To integrate nemo_oo_agents**:

```python
# We'd need to wrap nemo_oo_agents as a NAT workflow
class Agent006Workflow:
    """Wrapper to make nemo_oo_agents look like a NAT workflow"""

    def __init__(self, agent_factory):
        self.agent_factory = agent_factory

    async def run(self, input_message):
        # Somehow translate input_message → agent method kwargs
        # Somehow capture intermediate steps and push to context
        # Somehow handle multi-turn interactions
        # ...
```

**This is the coupling we wanted to avoid!**

### 2. Paradigm Mismatch

**NAT's model**: "Workflows process messages"

```python
# User sends a message
input_message = {"query": "What is the weather?"}

# Workflow processes it
async with session.run(input_message) as runner:
    output = await runner.result()
```

**Agent006's model**: "Agents execute methods with arbitrary kwargs"

```python
# Different capability tests have different signatures
await agent.solve_math_problem(problem="2+2")
await agent.analyze_sentiment(text="I love this!", language="en")
await agent.interact_with_retail_environment(initial_state=state)
```

**Benchmarks are even more diverse**:
- BFCL: Call agent with function schemas, validate function calls
- InterCode: Multi-turn environment simulation with state
- TAU-Bench: Complex retail environment with human feedback
- LiveCodeBench: Code generation with execution and test validation

**SessionManager assumes** a uniform input→output pattern. Our benchmarks need flexibility.

### 3. Multi-User Features We Don't Need

**SessionManager's per-user features**:
- Per-user workflow isolation ❌ (batch evaluation has no users)
- Per-user concurrency limits ❌ (we want global limit)
- Inactive session cleanup ❌ (finite runs, not long-lived server)
- User authentication/authorization ❌ (local evaluation)
- Per-user metrics tracking ❌ (we track per-task, not per-user)

**We pay the complexity cost for features we don't use.**

---

## What We SHOULD Adopt

### 1. ✅ Structured Intermediate Steps

**Adopt the data model**, not SessionManager:

```python
# evaluation/intermediate_steps.py

from dataclasses import dataclass
from enum import Enum

class StepType(str, Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    AGENT_STEP = "agent_step"

@dataclass
class IntermediateStep:
    """Structured step for analysis (inspired by NAT)."""

    step_type: StepType
    timestamp: float

    # LLM call details
    model_name: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0

    # Tool call details
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: Any | None = None

    # Hierarchy
    parent_step_id: str | None = None

    # Error tracking
    error: str | None = None

class IntermediateStepCollector:
    """Collect steps during agent execution."""

    def __init__(self):
        self.steps: list[IntermediateStep] = []

    def record_llm_call(self, model: str, prompt_tokens: int, ...):
        self.steps.append(IntermediateStep(
            step_type=StepType.LLM_CALL,
            timestamp=time.time(),
            model_name=model,
            prompt_tokens=prompt_tokens,
            ...
        ))

    def compute_stats(self) -> dict:
        """Compute aggregate statistics (like NAT's usage stats)."""
        stats_per_model = {}

        for step in self.steps:
            if step.step_type == StepType.LLM_CALL:
                model = step.model_name
                if model not in stats_per_model:
                    stats_per_model[model] = {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "calls": 0,
                    }
                stats_per_model[model]["prompt_tokens"] += step.prompt_tokens
                stats_per_model[model]["completion_tokens"] += step.completion_tokens
                stats_per_model[model]["calls"] += 1

        # Compute p95 latency
        latencies = [s.timestamp for s in self.steps if s.step_type == StepType.LLM_CALL]
        p95_latency = np.percentile(latencies, 95) if latencies else 0

        return {
            "per_model": stats_per_model,
            "total_steps": len(self.steps),
            "p95_latency": p95_latency,
        }
```

**Usage in Agent006Adapter**:

```python
class Agent006Adapter(ExecutionAdapter):
    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        # Create step collector
        step_collector = IntermediateStepCollector()

        # Set up tracing (our existing OTel tracing)
        trace_file = self._setup_trace(task.task_id)

        # Optionally: monkey-patch LiteLLM to call step_collector
        # Or: wrap agent methods to intercept calls

        # Execute agent
        agent = self.agent_factory()
        method = getattr(agent, self.method_name)
        output = await method(**task.data["kwargs"])

        # Compute stats from steps
        usage_stats = step_collector.compute_stats()

        return EvaluationResult(
            task_id=task.task_id,
            success=True,
            output=output,
            metadata={
                "trace_file": str(trace_file),
                "usage_stats": usage_stats,  # NEW!
                "intermediate_steps": len(step_collector.steps),
            },
        )
```

**Benefits**:
- ✅ Token usage tracking per model
- ✅ Latency measurements
- ✅ Tool call tracking
- ✅ NO coupling to SessionManager
- ✅ Works with our clean architecture

### 2. ✅ Better Usage Stats Output

**Adopt NAT's output format**:

```python
# After evaluation completes
usage_stats = {
    "total_tasks": 100,
    "total_runtime_seconds": 450.2,
    "per_model": {
        "gpt-4o-mini": {
            "prompt_tokens": 125000,
            "completion_tokens": 45000,
            "reasoning_tokens": 0,
            "cached_tokens": 10000,
            "total_calls": 150,
            "p95_latency_ms": 850,
        },
        "claude-3-5-sonnet": {
            "prompt_tokens": 98000,
            "completion_tokens": 32000,
            "reasoning_tokens": 15000,
            "cached_tokens": 8000,
            "total_calls": 120,
            "p95_latency_ms": 720,
        },
    },
    "per_task_avg": {
        "tokens": 1700,
        "runtime_seconds": 4.5,
        "llm_calls": 2.7,
    },
}

# Write to file
with open(output_dir / "usage_stats.json", "w") as f:
    json.dump(usage_stats, f, indent=2)
```

**Benefits**:
- Compare models on cost/performance
- Identify expensive tasks
- Track caching effectiveness
- Measure latency characteristics

### 3. ✅ Integration with Existing OTel Tracing

**We already have OTel tracing!** We don't need NAT's ExporterManager.

**Current**: `openinference_instrumentation_nemo_oo_agents`

```python
from openinference_instrumentation_nemo_oo_agents import enable_tracing

exporter = enable_tracing(trace_dir="traces/eval")
# Auto-instruments LiteLLM, captures spans
```

**Enhancement**: Extract data from OTel spans for usage stats

```python
class UsageStatsFromOTel:
    """Extract usage stats from OTel trace files."""

    def extract_from_trace(self, trace_file: Path) -> dict:
        """Read .006trace.jsonl and compute stats."""
        steps = []

        for line in trace_file.read_text().splitlines():
            span = json.loads(line)

            if span.get("name") == "llm":
                steps.append({
                    "type": "llm_call",
                    "model": span["attributes"].get("llm.model"),
                    "prompt_tokens": span["attributes"].get("llm.token_count.prompt"),
                    "completion_tokens": span["attributes"].get("llm.token_count.completion"),
                    "start_time": span["start_time"],
                    "end_time": span["end_time"],
                })

        return self._compute_stats(steps)
```

**Benefits**:
- ✅ Use our existing tracing infrastructure
- ✅ No dependency on NAT's ExporterManager
- ✅ Extract usage stats from traces post-hoc

---

## Alternative: Lighter-Weight Context Pattern

**If we want implicit context** (like NAT's ContextState), we can use a simpler pattern:

```python
# evaluation/context.py

from contextvars import ContextVar

_current_task: ContextVar[str] = ContextVar("current_task", default=None)
_step_collector: ContextVar[IntermediateStepCollector] = ContextVar("step_collector", default=None)

class EvaluationContext:
    """Lightweight context for evaluation run."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.step_collector = IntermediateStepCollector()

    def __enter__(self):
        self._task_token = _current_task.set(self.task_id)
        self._collector_token = _step_collector.set(self.step_collector)
        return self

    def __exit__(self, *exc):
        _current_task.reset(self._task_token)
        _step_collector.reset(self._collector_token)

# Usage in adapter
class Agent006Adapter:
    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        with EvaluationContext(task.task_id) as ctx:
            # Now agent code can access context implicitly
            agent = self.agent_factory()
            output = await agent.solve(...)

            # Collector is available in context
            usage_stats = ctx.step_collector.compute_stats()
```

**Benefits**:
- ✅ Implicit context like NAT
- ✅ Much simpler than SessionManager
- ✅ No coupling to NAT framework
- ✅ Works with our clean architecture

---

## Recommendation

### ❌ Do NOT Adopt SessionManager

**Reasons**:
1. **Architectural coupling** - Requires adopting NAT's Config, WorkflowBuilder, Workflow, Runner
2. **Multi-user features we don't need** - Per-user isolation, cleanup, rate limiting
3. **Paradigm mismatch** - Workflow message pattern vs agent method pattern
4. **Violates clean separation** - Would leak NAT concepts into our runner

### ✅ Adopt These Patterns

1. **Structured intermediate steps**
   - Data model for LLM calls, tool calls, agent steps
   - Hierarchy tracking (parent/child)
   - Timestamp and error tracking

2. **Usage stats computation**
   - Token counts per model
   - Latency percentiles (p95)
   - Per-task breakdown
   - Cost analysis

3. **Better output structure**
   - Save usage stats JSON
   - Per-task metadata
   - Aggregate statistics

4. **Optional: Lightweight context**
   - ContextVars for implicit state
   - Much simpler than SessionManager
   - No framework coupling

### 🎯 Best of Both Worlds

**What we get**:
- ✅ Clean 5-layer architecture (no nemo_oo_agents leakage)
- ✅ Flexible adapter pattern (works with diverse benchmarks)
- ✅ Rich usage statistics (inspired by NAT)
- ✅ Existing OTel tracing (already working)
- ✅ Intermediate step tracking (for analysis)

**What we avoid**:
- ❌ SessionManager coupling
- ❌ NAT framework dependency
- ❌ Multi-user features we don't need
- ❌ Workflow paradigm mismatch

---

## Implementation Plan Update

Based on this analysis:

### Phase 1: Core Implementation (Current)
- ✅ Layer 0: Concurrency engine (DONE)
- ⏳ Layer 1: Protocol definitions (IN PROGRESS)
- ⏳ Layer 2: Evaluation runner (IN PROGRESS)
- ⏳ Layer 3: Adapters (IN PROGRESS)

### Phase 2: Enhanced Observability (Inspired by NAT)
- Add `IntermediateStep` data model
- Add `IntermediateStepCollector` for tracking
- Extract usage stats from OTel traces
- Add usage stats JSON output
- Add per-task metadata tracking

### Phase 3: Optional Context Enhancement
- Add lightweight `EvaluationContext` (ContextVars)
- Update adapters to use context
- Add helper utilities for common operations

---

## Conclusion

**SessionManager is excellent for what it does** - managing multi-user workflow services.

**But we're doing batch evaluation**, which has different requirements:
- No users, just tasks
- Finite runs, not long-lived servers
- Diverse execution patterns (not uniform workflows)
- Need clean separation (no framework coupling)

**We should**:
- ✅ Continue with our clean 5-layer architecture
- ✅ Adopt NAT's **patterns** (intermediate steps, usage stats)
- ✅ Use our existing **infrastructure** (OTel tracing)
- ✅ Keep our **flexibility** (adapter-based execution)

**This gives us the best of both worlds**: Clean architecture + Rich observability.
