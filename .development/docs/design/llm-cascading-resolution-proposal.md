# LLM Cascading Resolution - Proposal

## The Problem

Current code uses `llm=None` as a special sentinel value, which is ambiguous and not self-documenting.

## The Solution

**Cascading resolution with INHERIT as the default.**

### Core Principle

When an agent is instantiated without an explicit LLM, cascade through multiple sources to find one:

1. **Instance-level**: `MyAgent(llm=explicit_llm)`
2. **Class-level**: `class MyAgent(Agent, llm=class_llm)`
3. **Python inheritance**: `class Child(Parent)` inherits parent's class-level LLM
4. **Runtime propagation**: Sub-agent instantiated in parent's generated code inherits parent's LLM
5. **Error**: No LLM found - provide helpful error message

### Key Constraints

**Invalid Parameters:**
- ❌ `llm=None` - Raises `ValueError`
- ❌ `llm=INHERIT` - Raises `ValueError` (INHERIT is internal only)
- ✅ Omit `llm=` parameter - Enables cascading (correct way)
- ✅ `llm=my_llm` - Explicit LLM (prevents cascading)

**Cascading Rules:**
- Method-level LLM (`@strategy(llm=...)`) does NOT cascade to sub-agents
- Sub-agents always cascade from agent-level LLM configuration
- Cascading only works for agent/class hierarchies, not method execution

### API Design

```python
from nemo_oo_agents import Agent  # Note: INHERIT is internal only, not exported
from unifiedllm import CompletionClient

my_llm = CompletionClient(model="gpt-4o")

# Most common: Omit llm= parameter (cascading is default)
class SubAgent(Agent):  # ✓ Will cascade to find LLM
    pass

# Explicit LLM (prevents cascading)
class TopLevelAgent(Agent, llm=my_llm):  # ✓ Always uses my_llm
    pass

# Python class inheritance
class ChildAgent(TopLevelAgent):  # ✓ Inherits my_llm via MRO
    pass

# Instance-level override
agent = SubAgent(llm=my_llm)  # ✓ Overrides any cascaded value

# ❌ INVALID: Both None and INHERIT are not allowed
class BadAgent1(Agent, llm=None):  # ❌ ValueError: llm=None is not allowed
    pass

class BadAgent2(Agent, llm=INHERIT):  # ❌ ValueError: llm=INHERIT is not allowed
    pass

# Note: To enable cascading, simply omit the llm= parameter
```

### Examples

**Example 1: Python Class Inheritance**
```python
parent_llm = CompletionClient(model="gpt-4o")

class ParentAgent(Agent, llm=parent_llm):
    pass

class ChildAgent(ParentAgent):  # Cascades to parent_llm via MRO
    pass

child = ChildAgent()  # Uses parent_llm
```

**Example 2: Runtime Propagation**
```python
class RouterAgent(Agent, llm=router_llm):
    async def process(self):
        # Generated code instantiates sub-agent
        analyzer = AnalyzerAgent()  # Cascades to router_llm via context var
        return await analyzer.analyze()

class AnalyzerAgent(Agent):  # No explicit LLM - will cascade
    async def analyze(self):
        ...
```

**Example 3: Mixed Hierarchy**
```python
# Top-level with explicit LLM
class AppAgent(Agent, llm=app_llm):
    pass

# Child inherits via Python class hierarchy
class WorkerAgent(AppAgent):
    pass

# Grandchild also inherits (walks MRO)
class SpecializedWorker(WorkerAgent):
    pass

# All use app_llm
app = AppAgent()              # Uses app_llm (class-level)
worker = WorkerAgent()        # Uses app_llm (inherited via MRO)
specialized = SpecializedWorker()  # Uses app_llm (inherited via MRO)
```

**Example 4: Instance Override**
```python
class FlexibleAgent(Agent):  # No class-level LLM
    pass

# Must provide at instantiation (no cascading source available)
try:
    agent = FlexibleAgent()  # ❌ ValueError: No LLM available
except ValueError:
    pass

# Provide at instantiation
agent = FlexibleAgent(llm=my_llm)  # ✓ Uses my_llm
```

**Example 5: Method-Level LLM Configuration**
```python
from nemo_oo_agents import Agent, strategy
from unifiedllm import CompletionClient

# Configure different LLMs for different use cases
fast_llm = CompletionClient(model="gpt-4o-mini")  # Quick, cheap
smart_llm = CompletionClient(model="gpt-4o")      # Slower, more capable

class MultiModelAgent(Agent, llm=fast_llm):
    """Agent that uses different LLMs for different methods."""

    async def quick_task(self):
        """Uses fast_llm (agent default)."""
        ...

    @strategy(llm=smart_llm)
    async def complex_task(self):
        """Uses smart_llm for this specific method."""
        ...

# Usage
agent = MultiModelAgent()
await agent.quick_task()      # Uses fast_llm
await agent.complex_task()    # Uses smart_llm
```

**Example 6: Mixed Class Inheritance and Runtime Propagation**
```python
# Base agent with LLM
base_llm = CompletionClient(model="gpt-4o")
class BaseAgent(Agent, llm=base_llm):
    """Base agent with default LLM."""
    pass

# Child inherits base_llm via Python class inheritance
class WorkerAgent(BaseAgent):
    """Inherits base_llm from BaseAgent via MRO."""

    async def process(self, data):
        # Generated code creates sub-agent during runtime
        # AnalyzerAgent cascades to base_llm via runtime propagation
        analyzer = AnalyzerAgent()
        return await analyzer.analyze(data)

# Sub-agent with no explicit LLM - will cascade
class AnalyzerAgent(Agent):  # Not inheriting from BaseAgent!
    """Will get LLM via runtime propagation from executing parent."""

    async def analyze(self, data):
        ...

# Usage
worker = WorkerAgent()  # Has base_llm (inherited from BaseAgent)
result = await worker.process(data)
# Inside process(): AnalyzerAgent() gets base_llm from worker via context variable
```

### Method-Level Resolution

In addition to agent-level cascading, LLM configuration can be overridden at the **method level** using the `@strategy` decorator:

```python
@strategy(llm=method_specific_llm)
async def my_method(self):
    ...
```

**Complete Resolution Order for Method Execution:**
1. **Call-level**: `agent.method(_llm=call_llm)` (highest priority)
2. **Method-level**: `@strategy(llm=method_llm)`
3. **Agent instance-level**: `MyAgent(llm=instance_llm)`
4. **Agent class-level**: `class MyAgent(Agent, llm=class_llm)`
5. **Python inheritance**: Parent class's LLM via MRO
6. **Runtime propagation**: Parent agent's LLM via context variable
7. **Error**: No LLM available (lowest priority)

**Important:** Method-level LLM (`@strategy(llm=...)`) **does NOT cascade** to sub-agents instantiated within that method. Sub-agents always cascade from the **agent-level** LLM configuration.

```python
# Example showing method-level LLM does NOT cascade
fast_llm = CompletionClient(model="gpt-4o-mini")
smart_llm = CompletionClient(model="gpt-4o")

class ParentAgent(Agent, llm=fast_llm):
    @strategy(llm=smart_llm)
    async def smart_method(self):
        # Method uses smart_llm, but sub-agents get fast_llm!
        sub = SubAgent()  # Gets fast_llm (agent-level), NOT smart_llm
        ...

class SubAgent(Agent):
    async def task(self):
        ...
```

This enables powerful use cases:
- **Cost optimization**: Use cheap models for simple tasks, expensive for complex ones
- **Model specialization**: Route tasks to models best suited for them (coding, reasoning, vision, etc.)
- **A/B testing**: Override LLM at call-time to compare models
- **Fallback strategies**: Try with fast model, retry with smart model on failure

## Resolution Algorithm

```python
def _resolve_llm(self, instance_llm: UnifiedLLM | None) -> UnifiedLLM:
    # 1. Instance-level explicit
    if instance_llm is not None:
        return instance_llm

    # 2. Class-level explicit
    class_llm = getattr(self.__class__, "_agent_llm", INHERIT)
    if class_llm is not INHERIT:
        return class_llm

    # 3. Python class inheritance (walk MRO)
    for base_class in self.__class__.__mro__[1:]:
        if not issubclass(base_class, Agent):
            continue
        base_llm = getattr(base_class, "_agent_llm", INHERIT)
        if base_llm is not INHERIT:
            return base_llm

    # 4. Runtime parent propagation
    parent = _parent_agent_var.get()
    if parent and hasattr(parent, "_llm"):
        return parent._llm

    # 5. No LLM found
    raise ValueError(f"No LLM available for {self.__class__.__name__}...")
```

## Benefits

1. **Natural default**: Most agents want cascading, so it's the default
2. **Less verbose**: `class MyAgent(Agent):` instead of `class MyAgent(Agent, llm=None):`
3. **Unified mechanism**: Single algorithm handles both inheritance types
4. **Explicit when needed**: Can specify `llm=my_llm` to prevent cascading
5. **Better errors**: Clear error messages explain the resolution order
6. **Type-safe**: `INHERIT` sentinel is distinct from `None` and `UnifiedLLM`
