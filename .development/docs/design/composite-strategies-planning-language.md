# Composite Strategies in Planning Language

## Problem

Composite strategies (Reflexion, PlanExecute, Ensemble) require ~250 LOC of boilerplate:

```python
# Current: manual history manipulation, prompt templates, event imports
class ReflexionStrategy(GenerationStrategy):
    async def execute(self, runtime, call):
        result = await runtime.execute_nested(self.base, call)

        # Build prompt manually
        reflection_prompt = REFLECTION_PROMPT.format(task=..., result=...)

        # Manipulate history directly
        runtime.history.add(FeedbackEvent(data=ContentData(content=reflection_prompt)))

        # Generate with output model
        response, _ = await runtime.generate(tools=[], output_model=ReflectionOutput)

        # Parse, format feedback, add to history...
```

Problems:
- Magic strings not optimizable
- Must understand events, history, hooks
- Doesn't compose with end-to-end optimization

## Developer Interface

Composite strategies extend `CompositeStrategy` and define `@plan` methods for sub-tasks:

```python
class ReflexionStrategy(CompositeStrategy):

    def __init__(self, base: GenerationStrategy | None = None, max_reflections: int = 3):
        self.base = base or PurePythonStrategy()
        self.max_reflections = max_reflections

    async def execute(self, agent: Agent, task: CurrentCall) -> Any:
        for _ in range(self.max_reflections):
            result = await self.run(agent, task)

            reflection = await self.reflect(agent, task.docstring, result)

            if reflection.is_satisfactory:
                return result

            await self.add_feedback(agent, reflection)

        return result

    @plan
    async def reflect(self, task: str, result: str) -> Reflection:
        """Critically evaluate if the result meets task requirements."""
        ...

    @plan
    async def add_feedback(self, reflection: Reflection) -> None:
        """Incorporate this feedback in the next attempt."""
        ...
```

Key points:
- `execute(agent, task)` - entry point, receives user's agent and task
- `self.run(agent, task)` - run task using base strategy
- `@plan` methods - sub-tasks (docstring = prompt, return type = output)
- Sub-tasks execute on `agent`, appear in its trace as if hand-written

### Usage

Users consume composite strategies exactly as today:

```python
@agent(llm=llm)
class MyAgent(Agent):
    @plan(strategy=ReflexionStrategy(max_reflections=3))
    async def analyze(self, data: str) -> Analysis:
        """Thoroughly analyze the data with self-critique."""
        ...
```

### More Examples

**Plan-and-Execute:**
```python
class PlanExecuteStrategy(CompositeStrategy):

    async def execute(self, agent: Agent, task: CurrentCall) -> Any:
        plan = await self.create_plan(agent, task.docstring)

        context = ""
        for i, step in enumerate(plan.steps):
            result = await self.execute_step(agent, step.description, context)
            context += f"\nStep {i+1}: {result}"

        return await self.synthesize(agent, task.docstring, context)

    @plan
    async def create_plan(self, task: str) -> Plan:
        """Break down the task into concrete executable steps."""
        ...

    @plan
    async def execute_step(self, step: str, context: str) -> str:
        """Execute this step given prior context."""
        ...

    @plan
    async def synthesize(self, task: str, results: str) -> str:
        """Synthesize final answer from step results."""
        ...
```

**Ensemble:**
```python
class EnsembleStrategy(CompositeStrategy):

    def __init__(self, base: GenerationStrategy | None = None, num_candidates: int = 3):
        self.base = base or PurePythonStrategy()
        self.num_candidates = num_candidates

    async def execute(self, agent: Agent, task: CurrentCall) -> Any:
        candidates = []
        for _ in range(self.num_candidates):
            result = await self.run(agent, task)
            candidates.append(result)

        vote = await self.select_best(agent, task.docstring, candidates)
        return candidates[vote.best_index]

    @plan
    async def select_best(self, task: str, candidates: list[str]) -> Vote:
        """Select the best candidate for the task."""
        ...
```

## Changes to Runtime

### 1. New signature for `GenerationStrategy.execute()`

```python
# Old
async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:

# New
async def execute(self, agent: Agent, task: CurrentCall) -> Any:
```

### 2. New `CompositeStrategy` base class

```python
class CompositeStrategy(GenerationStrategy):
    """Base class for strategies with @plan sub-tasks."""

    base: GenerationStrategy  # Subclasses set this

    async def run(self, agent: Agent, task: CurrentCall) -> Any:
        """Run task on agent using base strategy."""
        return await agent.runtime.execute_nested(self.base, task)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._wrap_plan_methods()

    @classmethod
    def _wrap_plan_methods(cls):
        """Wrap @plan methods to execute on agent."""
        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if getattr(method, '_agent_decorator', None) == 'plan':
                setattr(cls, name, cls._make_wrapper(method))

    @classmethod
    def _make_wrapper(cls, method):
        """Create wrapper that executes @plan method on agent."""
        @functools.wraps(method)
        async def wrapper(self, agent: Agent, *args, **kwargs):
            plan_call = CurrentCall.from_method(method, args, kwargs)
            strategy = PurePythonStrategy()
            return await agent.runtime.execute_nested(strategy, plan_call)
        return wrapper
```

### 3. Trace output

All sub-tasks appear in the agent's trace:

```
agent.analyze("some data")
├── [PurePython] generate initial result
├── reflect(task="...", result="...")
│   └── [PurePython] evaluate result
├── add_feedback(reflection=...)
│   └── [PurePython] format feedback
├── [PurePython] generate improved result
├── reflect(task="...", result="...")
│   └── [PurePython] evaluate result
└── return final result
```

## Benefits

| Aspect | Current | Proposed |
|--------|---------|----------|
| LOC for Reflexion | ~250 | ~40 |
| Sub-task definition | Magic strings | @plan methods |
| Trace | Opaque strategy internals | Full visibility |
| Optimization | Manual | End-to-end works |
| Testing | Mock RuntimeServices | Mock individual @plan methods |
