# NeMo OO Agents Reference

Detailed reference for all NeMo OO Agents features with complete examples.

## Strategies

### CodeActStrategy (Default)

The default strategy for all generation methods. Iteratively generates and executes Python code, calling methods on `self` as tools.

```python
from nemo_oo_agents import Agent, strategy
from nemo_oo_agents.strategies import CodeActStrategy
from nemo_oo_agents.config import CodeActConfig

class ResearchAgent(Agent, llm=llm):
    bash = BashTool()  # External tool

    # Default CodeAct -- unlimited iterations
    async def research(self, topic: str) -> str:
        """Research the topic thoroughly using available tools."""
        ...

    # Bounded CodeAct
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=5)))
    async def quick_research(self, topic: str) -> str:
        """Do a quick research pass (max 5 iterations)."""
        ...
```

CodeAct behavior:
1. LLM generates Python code
2. Code is executed in a sandboxed environment
3. LLM sees the output and decides next action
4. Repeats until the return value is produced or max_iterations reached

### PredictStrategy

Single-shot generation optimized for classification, extraction, and simple structured output. No code execution.

```python
from typing import Literal
from nemo_oo_agents.strategies import PredictStrategy

class Classifier(Agent, llm=llm):
    @strategy(PredictStrategy())
    async def classify(self, text: str) -> Literal["positive", "negative", "neutral"]:
        """Classify the sentiment of the text."""
        ...

    @strategy(PredictStrategy())
    async def extract(self, text: str) -> list[str]:
        """Extract all named entities from the text."""
        ...
```

Use PredictStrategy when:
- The task is straightforward (no multi-step reasoning needed)
- You want faster, cheaper inference
- You need deterministic structured output without code execution

## Per-Method LLM Override

Assign a different LLM to a specific method via the `llm=` kwarg on `@strategy(...)`. Useful for routing cheap single-shot calls to a small model while keeping the class default on a more capable one.

```python
fast_llm = get_llm_client("nvidia/nvidia/Nemotron-3-Nano-30B-A3B")
powerful_llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1")

class MyAgent(Agent, llm=powerful_llm):
    # Uses powerful_llm (class default)
    async def complex_task(self, input: str) -> str: ...

    # Uses fast_llm (method override)
    @strategy(PredictStrategy(), llm=fast_llm)
    async def simple_classify(self, text: str) -> str: ...
```

## Structured Output

### Pydantic Models

Declare a `BaseModel` subclass as the return type. The framework parses the LLM response into an instance and retries on validation failures. Use `Field(...)` for value ranges, descriptions, or length constraints; use `Literal[...]` for closed sets.

```python
from pydantic import BaseModel, Field
from typing import Literal

class ReviewAnalysis(BaseModel):
    sentiment: Literal["positive", "negative", "neutral", "mixed"]
    rating: int = Field(ge=1, le=5, description="Star rating")
    key_points: list[str] = Field(min_length=1, max_length=5)
    recommendation: bool

class Reviewer(Agent, llm=llm):
    @strategy(PredictStrategy())
    async def analyze_review(self, review: str) -> ReviewAnalysis:
        """Analyze the product review."""
        ...
```

Pydantic features that work: `Field` constraints, validators, nested models, optional fields, `Literal` types.

### Dataclasses

```python
from dataclasses import dataclass

@dataclass
class Summary:
    title: str
    bullet_points: list[str]
    word_count: int

class Summarizer(Agent, llm=llm):
    async def summarize(self, text: str) -> Summary:
        """Create a structured summary."""
        ...
```

### TypedDict

```python
from typing import TypedDict

class Result(TypedDict):
    answer: str
    confidence: float
    sources: list[str]

class QAAgent(Agent, llm=llm):
    async def answer(self, question: str) -> Result:
        """Answer the question with sources."""
        ...
```

### Annotated Strings

For open-ended string output with semantic hints:

```python
from typing import Annotated

class Writer(Agent, llm=llm):
    async def write(self, topic: str) -> Annotated[str, "A well-structured essay"]:
        """Write an essay on the given topic."""
        ...
```

## Methods as Tools

In CodeAct, any method defined on the agent class is automatically exposed to the LLM as a callable tool -- deterministic Python methods and other generation methods alike. No explicit registration or decorator is needed; the framework introspects `self` and renders the method signatures and docstrings into the prompt.

### Basic Pattern

```python
class InventoryAgent(Agent, llm=llm):
    def __init__(self):
        super().__init__()
        self.db = {"apple": 50, "banana": 30}

    # Deterministic helper -- auto-available as tool in CodeAct
    def check_stock(self, item: str) -> int:
        """Check stock level for an item."""
        return self.db.get(item, 0)

    def calculate_total(self, items: dict[str, int]) -> float:
        """Calculate order total."""
        prices = {"apple": 0.75, "banana": 0.50}
        return sum(prices.get(k, 0) * v for k, v in items.items())

    # Generation method -- LLM calls the helpers above
    async def fulfill_order(self, order: list[str]) -> str:
        """Check stock and calculate total for the order."""
        ...
```

### External Tools as Class Attributes

```python
from nemo_oo_agents.tools.bash_tool import BashTool

class DevAgent(Agent, llm=llm):
    bash = BashTool()  # LLM can run shell commands

    async def setup_project(self, name: str) -> str:
        """Create a new Python project with uv."""
        ...
```

### Mixing Generation and Deterministic Methods

```python
class Pipeline(Agent, llm=llm):
    # Deterministic orchestrator
    async def run(self, raw_data: str) -> str:
        extracted = await self.extract(raw_data)      # LLM
        validated = self.validate(extracted)            # Deterministic
        enriched = await self.enrich(validated)         # LLM
        return self.format_output(enriched)             # Deterministic

    async def extract(self, data: str) -> dict: ...    # Generation
    def validate(self, data: dict) -> dict:             # Deterministic
        assert "name" in data
        return data
    async def enrich(self, data: dict) -> dict: ...    # Generation
    def format_output(self, data: dict) -> str:         # Deterministic
        import json
        return json.dumps(data, indent=2)
```

## Dynamic Prompts

### Self-Attribute Injection

```python
class ConfigurableAgent(Agent, llm=llm):
    def __init__(self, language: str, tone: str):
        super().__init__()
        self.language = language
        self.tone = tone

    async def respond(self, input: str) -> str:
        """Respond in {self.language} with a {self.tone} tone."""
        ...

# Different configurations, same code
formal = ConfigurableAgent(language="French", tone="formal")
casual = ConfigurableAgent(language="Spanish", tone="casual")
```

### Parameter and Expression Templates

```python
class DataAgent(Agent, llm=llm):
    async def process(self, items: list[str]) -> str:
        """Process {len(items)} items: {items}."""
        ...

    async def greet(self, name: str) -> str:
        """Say hello to {name.upper()}."""
        ...
```

## LLM Configuration

### Model Registry

The UnifiedLLM registry provides pre-configured models. All NVIDIA models use `NVIDIA_INTERNAL_API_KEY` from `.env`:

```python
from unifiedllm import get_llm_client

# NVIDIA Inference Hub models
llm = get_llm_client("nvidia/nvidia/Nemotron-3-Nano-30B-A3B")
llm = get_llm_client("nvidia/qwen/qwen3-next-80b-a3b-instruct")

# Claude via AWS
llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1")
```

### Custom Endpoints

For any OpenAI-compatible endpoint (including self-hosted or custom inference):

```python
from unifiedllm import CompletionClient

llm = CompletionClient(
    model="your-model",
    base_url="https://your-endpoint.example.com/v1",
    api_key="your-key",
)
```

### Three Override Levels

```python
default_llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1")
fast_llm = get_llm_client("nvidia/nvidia/Nemotron-3-Nano-30B-A3B")

# Level 1: Class default
class MyAgent(Agent, llm=default_llm):

    # Level 2: Method override
    @strategy(PredictStrategy(), llm=fast_llm)
    async def quick_classify(self, text: str) -> str: ...

    async def deep_analysis(self, data: str) -> str: ...

# Level 3: Instance override
agent = MyAgent(llm=another_llm)
```

## Context API

### Static Blocks

Pin a value into the LLM's system prompt:

```python
agent.context["instructions"] = "Always respond in JSON format."
agent.context["domain"] = "You are working in the healthcare domain."
```

### Dynamic Blocks

Re-evaluated every turn -- the LLM always sees live state:

```python
agent.context.set_dynamic("current_data", "self.render_data()")
agent.context.set_dynamic("stats", "f'Processed {self.count} items'")
```

### Managing Blocks

```python
# Remove a block
agent.context.remove("instructions")

# The LLM can also manage blocks in CodeAct:
# self.context["plan"] = "Step 1: ..."
# self.context.remove("old_plan")
```

### Scoped Overrides

Temporarily override context for a specific call branch:

```python
from nemo_oo_agents import ScopedContext, EventQuery

# Override system blocks for one call
with ScopedContext({"focus": "Only answer about pricing"}):
    result = await agent.respond(question)

# Combine with event filtering
with ScopedContext({"task": "summarize"}, events=EventQuery.last_n(10)):
    summary = await agent.summarize()
```

Inner scopes inherit and override outer scopes.

## Visibility System

### @hidden Decorator

Hide methods from the LLM's view (they're still callable but won't appear as tools):

```python
from nemo_oo_agents import hidden

class MyAgent(Agent, llm=llm):
    @hidden
    async def _internal_helper(self, data: str) -> str:
        """Not visible to the LLM as a tool."""
        return data.strip().lower()

    async def process(self, input: str) -> str:
        """Process the input (can call _internal_helper internally)."""
        ...
```

### Hidden Attributes

```python
from typing import Annotated
from nemo_oo_agents import hidden

class MyAgent(Agent, llm=llm):
    # Hidden from LLM introspection
    _secret: Annotated[str, hidden] = "internal-value"
```

### Unhiding Parent Fields

Re-declare a hidden parent field without `hidden` to make it visible:

```python
class ChildAgent(ParentAgent, llm=llm):
    my_tool: MyTool  # re-declared without hidden -- now visible to LLM
```

For framework fields like `context` and `events`, use `spec()` in `__init__` to preserve other annotations:

```python
from nemo_oo_agents.agentdoc import spec

class MyAgent(Agent, llm=llm):
    def __init__(self):
        super().__init__()
        spec(self, "context", hidden=False)  # expose Context API
        spec(self, "events", hidden=False)   # expose Event API
```

## Media Types

Handle images, audio, and files in agent methods:

```python
from nemo_oo_agents import Image, Audio, File

class MediaAgent(Agent, llm=llm):
    async def describe_image(self, img: Image) -> str:
        """Describe the contents of this image."""
        ...

    async def transcribe(self, audio: Audio) -> str:
        """Transcribe the audio recording."""
        ...
```

## MCP Integration

Connect external tool servers via Model Context Protocol.

### Configuration (`.mcp.json`)

```json
{
  "mcpServers": {
    "my-server": {
      "url": "https://my-server.example.com/mcp",
      "transport": "streamable-http"
    }
  }
}
```

### Usage

```python
from mcp_nemo_oo_agents import MCPManager

class MyAgent(Agent, llm=llm):
    external = MCPManager.create_from_server("my-server")

    async def query(self, question: str) -> str:
        """Query external data using MCP tools."""
        ...
```

## Tracing

### Auto-Tracing (Default)

Tracing is **automatic** when the tracing package is installed. Every `Agent.__init__()` probes `localhost:5001` and, if the `nemo start-dev` viewer is running, sends spans via OTLP with no code changes required.

```bash
nemo start-dev              # Start viewer, then run your agent -- traces appear automatically
```

### Explicit Setup (JSONL or Custom Endpoints)

Only call `enable_tracing()` explicitly when you need JSONL file output or a custom OTLP endpoint:

```python
from nemo_oo_agents.tracing import enable_tracing, exporters

# JSONL file exporter -- `trace_dir` is a DIRECTORY, not a file path.
# Files are written as `{trace_dir}/{session_id}.jsonl`, one per session.
# Do NOT pass a path ending in `.jsonl` (e.g. `"./traces/run.jsonl"`) --
# it would be treated as a directory name.
enable_tracing(exporters=[exporters.jsonl(trace_dir="./traces")])

# OTLP exporter (for Phoenix, Jaeger, etc.)
enable_tracing(exporters=[exporters.otlp("http://localhost:6006")])

# Both
enable_tracing(exporters=[
    exporters.jsonl(trace_dir="./traces"),
    exporters.otlp("http://localhost:6006"),
])
```

If auto-tracing already activated, calling `enable_tracing(exporters=[...])` cleanly replaces the auto-configured exporter with your explicit one.

### Trace Format

Traces are saved as `.006trace.jsonl` files. Each line contains one OTLP span wrapped in a `resourceSpans` envelope. Spans capture:
- LLM calls (model, tokens, latency)
- Code execution (input, output, errors)
- Tool invocations
- Parent-child relationships between spans

### Development Server

```bash
nemo start-dev    # Launches viewer at http://localhost:5001
```

## Self-Extending Agents

In CodeAct, the LLM can define new methods at runtime to decompose complex tasks:

```python
class FlexibleAgent(Agent, llm=llm):
    async def solve(self, problem: str) -> str:
        """Solve this problem. Create helper methods if needed."""
        ...
```

The LLM might dynamically create `_parse_input()`, `_validate()`, `_format_output()` methods as needed.

## Event API

### Querying Events

```python
from nemo_oo_agents import EventQuery

# Get recent events
recent = agent.events.query(limit=20)

# Query specific event types
messages = agent.events.query(EventQuery(type="message"))

# Query by call ID, regex, or tag
tagged = agent.events["5"]  # tag-lookup
```

### Event Filtering Per Method

Control what history the LLM sees for a specific method:

```python
from nemo_oo_agents import EventQuery

class MyAgent(Agent, llm=llm):
    # Only sees events from the current call (no history leakage)
    @strategy(PredictStrategy(), events=EventQuery.current_call())
    async def classify(self, text: str) -> str: ...

    # Only sees last 5 events
    @strategy(CodeActStrategy(), events=EventQuery.last_n(5))
    async def quick_task(self, data: str) -> str: ...
```

Built-in factories: `current_call()`, `by_type()`, `last_n()`.

### Event Subscriptions

React to agent events in real time:

```python
agent.event_manager.on("Message", lambda event: print(f"Agent said: {event}"))
```

### Middleware / Interception

Intercept agent calls, LLM calls, or code execution:

```python
async def retry_on_failure(ctx, next_fn):
    for attempt in range(3):
        try:
            return await next_fn(ctx)
        except Exception:
            if attempt == 2:
                raise

agent.event_manager.intercept("llm_call", retry_on_failure)
```

### History Summarization

For long-running agents, auto-compress history:

```python
from nemo_oo_agents.agents import TokenBudgetSummarizer, MethodSummarizer
from nemo_oo_agents.config import TokenBudgetConfig

# Compress when token budget exceeded
TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(max_tokens=2000))

# Or compress after each method call
MethodSummarizer.install(agent)

# Size budget relative to the model's context window
from nemo_oo_agents.config import context_budget
config = TokenBudgetConfig(max_tokens=context_budget(llm, percent=0.6))
```

## Persistence & Snapshots

### SQLite Storage

Persist agent state to disk -- events, context blocks, LLM-defined methods, and user attributes are all serialized:

```python
from nemo_oo_agents.storage import SQLiteStorageManager

storage = SQLiteStorageManager("agent_state.db")

# First run: creates snapshot
agent = MyAgent(storage=storage)
await agent.process("some work")  # state saved automatically

# Later: resume from snapshot
agent = MyAgent(storage=storage)  # restores full state
```

### JSON Snapshots

For cross-process or cross-host transfer:

```python
from nemo_oo_agents import snapshot_to_json

# Serialize
data = snapshot_to_json(agent)

# Send to another process, save to S3, etc.
```

### Excluding Fields

```python
from typing import Annotated
from nemo_oo_agents import nosnapshot

class MyAgent(Agent, llm=llm):
    _cache: Annotated[dict, nosnapshot] = {}     # excluded from snapshots
    _conn: Annotated[Connection, nosnapshot] = None  # live connections excluded
```

## Configuration Reference

### CodeActConfig

```python
from nemo_oo_agents.config import CodeActConfig

CodeActConfig(
    max_iterations=15,       # max code-execute cycles
    max_retries=3,           # retries on validation failure
    cell_timeout=30,         # seconds per code cell execution
    max_tokens=4096,         # LLM output token limit
    temperature=0.0,         # sampling temperature
    top_p=1.0,               # nucleus sampling
    max_tool_calls=10,       # max tool calls per iteration
)
```

### TruncationConfig

Cap sizes to manage context window:

```python
from nemo_oo_agents.config import TruncationConfig

TruncationConfig(
    block_limit=2000,         # max chars per context block
    total_context_tokens=..., # overall context budget
    event_tokens=...,         # budget for event history
    stdout_max_length=5000,   # cap stdout from code execution
    pprint_max_elements=50,   # cap pprint output size
    pprint_max_depth=3,       # cap pprint nesting
)
```

### ExecutionConfig

```python
from nemo_oo_agents.config import ExecutionConfig

ExecutionConfig(
    max_nesting_depth=5,  # prevent runaway agent-in-agent chains
)
```

### RestrictionsConfig

Restrict what generated code can do:

```python
from nemo_oo_agents.config import RestrictionsConfig

RestrictionsConfig(
    denied_modules=["os", "subprocess"],             # block entire modules
    denied_functions={"os": ["system", "exec"]},     # block specific functions
)
```

### Global Strategy Override

Override the default strategy for the entire process (useful in eval pipelines):

```python
from nemo_oo_agents import set_default_strategy
from nemo_oo_agents.strategies import PredictStrategy

set_default_strategy(PredictStrategy())  # all agents use PredictStrategy unless overridden
```

### Layered Config

All config objects support `merge_with()` for layered defaults:

```python
base = CodeActConfig(max_iterations=10, temperature=0.5)
override = CodeActConfig(temperature=0.0)
final = base.merge_with(override)  # max_iterations=10, temperature=0.0
```

## Code Execution Sandbox

### Safety

CodeAct-generated code runs in an AST-checked sandbox that blocks:
- `exec()` / `eval()` calls
- Raw imports (must use pre-imported modules or methods on `self`)
- Dunder escapes (`__import__`, `__class__`, etc.)
- Blocking calls that would hang the event loop
- Bare class/function definitions (use `MethodWriting` skill for that)

### REPL State

Variables persist across cells within a generation session:

```python
# Cell 1 (generated by LLM):
data = self.fetch("users")
filtered = [u for u in data if u["active"]]

# Cell 2 (generated by LLM):
# `data` and `filtered` are still available
result = self.summarize(filtered)  # uses filtered from Cell 1
return_result(result)
```

Access previous cell outputs with `Out[n]` (Jupyter-style).

### Pre-Imported Utilities

Available in generated code without explicit imports:
- `pprint` -- for pretty-printing objects
- `logger` -- for logging
- `asyncio` -- for async operations
- Common typing constructs

Module-level imports from your agent file are also inherited.

### Showing Media Mid-Execution

Display images or files to the LLM during CodeAct:

```python
from nemo_oo_agents import show, Image

# Inside generated code:
show(Image.from_file("chart.png"))  # capped at 5 attachments per execution
```

## Testing with FakeLLMClient

### Scripted Responses

```python
from unifiedllm import FakeLLMClient

# Simple message response
fake = FakeLLMClient.simple_message("Hello, world!")

# Tool call response
fake = FakeLLMClient.with_tool_call("check_stock", {"item": "butter"})

# Reasoning response
fake = FakeLLMClient.with_reasoning("Let me think...", "The answer is 42.")

# CodeAct code responses (sequence of code cells)
fake = FakeLLMClient.with_code_responses([
    "data = self.fetch('users')",
    "return_result(len(data))",
])
```

### Inspecting Calls

```python
agent = MyAgent(llm=fake)
result = await agent.process("test")

assert fake.call_count == 2
print(fake.last_messages)  # what the agent sent
print(fake.last_tools)     # what tools were available
```

## Progressive Disclosure with doc() and spec()

LLMs can use `doc()` to explore unknown objects at runtime:

```python
from nemo_oo_agents.agentdoc import doc, spec

class MyAgent(Agent, llm=llm):
    def __init__(self):
        super().__init__()
        spec(self, "context", hidden=False)  # Expose Context API to LLM
        spec(self, "events", hidden=False)   # Expose Event API to LLM

    async def explore(self, obj: Any) -> str:
        """Use doc(obj) to discover the API, then extract useful info."""
        ...
```

### spec() Annotations

Control how types render to the LLM without modifying source:

```python
from nemo_oo_agents.agentdoc import spec

# Add description to a field (renders as inline comment)
class MyAgent(Agent, llm=llm):
    target: Annotated[str, "The analysis target"] = ""

# Show a _private method (normally hidden)
@spec(hidden=False)
def _compute_score(self, data: dict) -> float: ...

# Collapse a sub-type to a one-liner
@spec(expand=False)
class InternalConfig: ...

# Annotate types you don't own (no source change)
spec(ThirdPartyClass, "field_name", hidden=True)

# Per-instance annotation
spec(self, "field_name", hidden=False)  # only affects this instance
```

## Tips

- **Docstring is the prompt**: Keep it clear, specific, and actionable
- **Use type hints**: They define the contract between your code and the LLM
- **Start simple**: Use PredictStrategy first, then switch to CodeAct for complex tasks
- **Batch operations**: Process lists in single method calls to reduce API calls
- **Test incrementally**: Build and verify one method at a time
- **Use context blocks**: Pin important state into the system prompt rather than re-passing it
- **Choose models wisely**: Fast models (Nemotron Nano) for simple tasks, powerful models (Claude) for complex reasoning
