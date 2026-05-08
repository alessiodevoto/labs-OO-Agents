---
name: nemo-oo-agents
description: "Guidance for building LLM-powered agents with the NeMo OO Agents Python framework. Use when building agents with nemo_oo_agents, implementing CodeAct or structured output strategies, or working with the unifiedllm library. Important: Contains specific installation instructions and development patterns -- read before installing or writing any agent code."
compatibility: Python >= 3.12, uv for dependency management, .env file for API keys (NVIDIA_INTERNAL_API_KEY).
metadata:
  skill_type: library
user-invocable: false
---

# NeMo OO Agents

NeMo OO Agents is a Python framework for building LLM-powered agents using familiar object-oriented programming. Define classes with async methods and docstrings -- the framework executes them via LLM at runtime. No new paradigms to learn: classes for scope, methods for capabilities, inheritance for composition.

**Key ideas:**
- Methods with `...` body are **generation methods** -- the LLM implements them based on the docstring
- The default strategy is **CodeAct**: iterative code execution that can call methods on `self`
- Any method on `self` is automatically available as a tool (no registration needed)
- Return types are validated: Pydantic models, dataclasses, TypedDict, and basic types all work
- Deterministic Python and LLM reasoning interleave seamlessly (SW1/SW3)

## Installation

```bash
uv add "nemo-oo-agents @ git+https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents.git@2d1816e7421ba35b8a70ebba7911d349c57ebee6"
```

### API Keys

Create a `.env` file in the project root:

```bash
NVIDIA_INTERNAL_API_KEY=your-key-here
```

Get your key at [inference.nvidia.com](https://inference.nvidia.com).

## LLM Configuration

Get a pre-configured LLM client from the UnifiedLLM registry:

```python
from unifiedllm import get_llm_client, CompletionClient

llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1")
# or create a CompletionClient with the url
llm = CompletionClient(
    model="your-model-name",
    base_url="https://your-endpoint.example.com/v1",
    api_key="your-key",
)
```

The registry reads your API key from `.env` automatically. Model IDs follow a `provider/org/model` format, e.g.:

| Model ID | Provider |
|---|---|
| `aws/anthropic/claude-haiku-4-5-v1` | Claude Haiku (via AWS) |
| `nvidia/nvidia/Nemotron-3-Nano-30B-A3B` | Nemotron Nano |
| `nvidia/qwen/qwen3-next-80b-a3b-instruct` | Qwen 3 |

To list every model in the curated registry (kept up to date as new models are added to `unifiedllm`):

```python
from unifiedllm import MODELS
print(sorted(MODELS.keys()))
```

All NVIDIA models route through `https://inference-api.nvidia.com/v1` using `NVIDIA_INTERNAL_API_KEY`.

**Broader discovery** (models litellm knows about, beyond the curated registry):

```python
import litellm
# All models litellm has pricing/context metadata for (~500+, incl. OpenAI, Anthropic, Cohere, Mistral, Gemini, ...)
print(sorted(litellm.model_cost.keys()))

# Runtime filter: only models usable given the env vars currently set (respects your credentials)
from litellm.utils import get_valid_models
print(get_valid_models())
```

Any litellm-supported model works when passed directly to `CompletionClient` with the right `base_url` and `api_key`.

You can also create a custom `CompletionClient` for any OpenAI-compatible endpoint:

```python
from unifiedllm import CompletionClient

llm = CompletionClient(
    model="your-model-name",
    base_url="https://your-endpoint.example.com/v1",
    api_key="your-key",
)
```

### LLM Override Levels

Set the LLM at three levels (later overrides earlier):

```python
# 1. Class level (default for all methods)
class MyAgent(Agent, llm=llm): ...

# 2. Method level (override for one method)
@strategy(PredictStrategy(), llm=fast_llm)
async def classify(self, text: str) -> str: ...

# 3. Instance level (override at creation)
agent = MyAgent(llm=different_llm)
```

## Guide: Building an Agent from Scratch

Start at Step 1 and only move to the next step when you hit the limitation it addresses.

### Step 1 — The simplest possible agent

One class, one method, free-form string output. No structure, no phases, no tools.
Use this as your starting point for any new agent.

```python
import asyncio
from nemo_oo_agents import Agent
from unifiedllm import get_llm_client
from nemo_oo_agents.tracing import enable_tracing, exporters

llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1")

class MyAgent(Agent, llm=llm):
    """One-line description of what this agent does."""

    async def run(self, input: str) -> str:
        """Do the task described here."""
        ...

async def main():
    enable_tracing(exporters=[exporters.jsonl(trace_dir="./traces")])
    agent = MyAgent()
    result = await agent.run("some input")
    print(result)

asyncio.run(main())
```

**When this is enough:** the task is open-ended, the output format doesn't matter much, or you're still figuring out what the agent needs to do.

**Coding rules:**
- The public method (`run`) is abstract (`...` body) — the LLM implements it.
- Keep the docstring as instructions: what to do, what the input means, what to return.
- Don't add logic outside the agent. Pass raw inputs; let the agent do the work.

---

### Step 2 — Add structured output

When you need the output to be reliably typed and parseable, declare a Pydantic model as the return type. The framework validates and retries automatically.

```python
from pydantic import BaseModel

class Result(BaseModel):
    answer: str
    confidence: float
    reasoning: str

class MyAgent(Agent, llm=llm):
    """Answers questions about a document."""

    async def run(self, question: str) -> Result:
        """Answer the question and explain your reasoning."""
        ...
```

**When to add this:** the downstream code needs to access specific fields, or free-form output is unreliable.

---

### Step 3 — Pass context via `__init__`, reference it in docstrings

When the same context is needed across multiple calls or methods, store it on `self` and reference it with `{self.attr}` in docstrings. Values are interpolated at runtime.

```python
class MyAgent(Agent, llm=llm):
    """Answers questions grounded in a specific document."""

    def __init__(self, document: str, domain: str):
        super().__init__()
        self.document = document
        self.domain = domain

    async def run(self, question: str) -> Result:
        """Answer the question using only the document below.

        Domain: {self.domain}
        Document: {self.document}
        """
        ...
```

**Pitfall — redundant parameter interpolation:**
```python
# BAD: `question` is a parameter — already visible, no need to repeat
async def run(self, question: str) -> Result:
    """Answer the question.
    Question: {question}   ← redundant
    """
    ...

# GOOD: only inject instance attributes not visible from the signature
async def run(self, question: str) -> Result:
    """Answer the question using only the document below.
    Domain: {self.domain}
    Document: {self.document}
    """
    ...
```

---

### Step 4 — Decompose into phases

When a single method is doing too much — reasoning poorly, losing track of intermediate findings, or producing inconsistent output — split it into sequential methods. Each method has a focused responsibility.

```python
class MyAgent(Agent, llm=llm):
    """Analyzes a document in two phases: extract facts, then synthesize."""

    def __init__(self, document: str):
        super().__init__()
        self.document = document

    async def extract_facts(self) -> list[str]:
        """Extract all factual claims from the document."""
        ...

    async def synthesize(self, facts: list[str]) -> Result:
        """Synthesize the facts into a final answer."""
        ...

    async def run(self, question: str) -> Result:
        facts = await self.extract_facts()
        return await self.synthesize(facts)
```

**Coding rules:**
- Split at natural phase boundaries: fact-gathering → reasoning → output generation.
- Each phase method produces a **typed output** (Pydantic model or typed list), not a free-form string.
- The public entrypoint (`run`) has a **concrete Python body** that chains the phases — it is never abstract.
- Don't split a focused, single-concern method — splitting adds latency without benefit.

---

### Step 5 — Add deterministic methods

Methods with a real Python body are deterministic — Python runs them, not the LLM. Use them for data loading, file reading, or any transformation that must happen exactly right.

```python
import json
from pathlib import Path

class MyAgent(Agent, llm=llm):
    """Analyzes data by reading files and fetching records."""

    def __init__(self, data_dir: Path):
        super().__init__()
        self.data_dir = data_dir

    def read_file(self, path: str) -> str:
        """Read the contents of a file."""
        return (self.data_dir / path).read_text()

    def list_files(self, pattern: str = "*.json") -> list[str]:
        """List files matching the given pattern."""
        return [p.name for p in self.data_dir.glob(pattern)]

    async def analyze(self, task: str, records: list[dict]) -> str:
        """Analyze the records and complete the task."""
        ...

    async def run(self, task: str) -> str:
        files   = self.list_files("*.json")
        records = [json.loads(self.read_file(f)) for f in files]
        return await self.analyze(task, records)
```

**Key rules:**
- A method with a real body (not `...`) is deterministic — Python runs it.
- A method whose body is `...` is a generation method — the LLM implements it.
- Call deterministic methods **unconditionally** from the entrypoint's Python body — don't let the LLM decide whether to call them.
- If a docstring says "call X" and the agent still skips it, the fix is moving the call into Python, not repeating the instruction.

---

### Step 6 — Let the agent call its own methods (CodeAct)

The default strategy is **CodeAct**: in a generation method, the LLM can call any method on `self`. No tool registration needed — just define methods on the class.

```python
class MyAgent(Agent, llm=llm):
    """Investigates a question by actively gathering evidence."""

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def read_file(self, path: str) -> str:
        """Read the contents of a file."""
        return Path(path).read_text()

    def search(self, query: str) -> list[str]:
        """Search the knowledge base for relevant passages."""
        return self._index.query(query)

    async def run(self) -> Result:
        """Answer the question below by calling self.read_file() and self.search()
        as needed to gather evidence.

        Question: {self.question}

        Gather only what the question requires. Cite your sources.
        """
        ...
```

**When to use this:** the agent needs to decide what to fetch or verify, not just classify pre-loaded context.

---

### Step 7 — Constrain a method to single-shot prediction

When a specific method is simple enough (one clear input, one clear output, no iteration needed), use `PredictStrategy` for a direct single LLM call — faster and cheaper than CodeAct.

```python
from nemo_oo_agents import strategy
from nemo_oo_agents.strategies import PredictStrategy

class MyAgent(Agent, llm=llm):

    async def run(self) -> Result:
        """Free-form CodeAct: gather evidence, investigate, decide."""
        ...

    @strategy(PredictStrategy())
    async def classify(self, text: str) -> Category:
        """Classify the text into one of the available categories — single-shot."""
        ...
```

**Never use `PredictStrategy`** on a method that may need to call tools or gather data.

---

### Step 8 — Delegate to a subagent

When one method is doing two conceptually distinct jobs, extract the inner job into its own `Agent` subclass.

```python
class ReviewerAgent(Agent, llm=llm):
    """Subagent: reviews a draft and returns structured feedback."""

    async def review(self, draft: str) -> Feedback:
        """Review the draft and return specific improvement suggestions with a score."""
        ...


class WriterAgent(Agent, llm=llm):
    """Writes an essay, then delegates review to a focused subagent."""

    def __init__(self):
        super().__init__()
        self.reviewer = ReviewerAgent()

    async def _write(self, topic: str, feedback: Feedback | None = None) -> str:
        """Write a short essay on the topic."""
        ...

    async def run(self, topic: str) -> str:
        draft    = await self._write(topic)
        feedback = await self.reviewer.review(draft)
        return await self._write(topic, feedback)
```

**LLM inheritance:** define the subagent without `llm=...` (`class ReviewerAgent(Agent):`) and it automatically inherits the parent's LLM at runtime. Only set an explicit `llm=` when you want a different model.

**When to add a subagent:**
- A generation method is doing two conceptually distinct jobs.
- You want separate contexts for the delegated work. E.g. the reviewer shouldn't be influenced by the writer's context.
- The sub-task is reusable across multiple parent agents.

---

## Hello World

A complete, runnable example:

```python
import asyncio
from nemo_oo_agents import Agent
from unifiedllm import get_llm_client

llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1")

class GreeterAgent(Agent, llm=llm):
    """A friendly greeting agent."""

    async def greet(self, name: str) -> str:
        """Generate a warm, personalized greeting for the given person."""
        ...

async def main():
    agent = GreeterAgent()
    result = await agent.greet("World")
    print(result)

asyncio.run(main())
```

Run with:
```bash
uv run python main.py
```

## Core Concepts

### Generation Methods

Methods with `...` as the body are generation methods. The LLM implements them using:
- The method **name** (semantic signal)
- The method **docstring** (the task description that becomes part of the prompt)
- The method **signature** (input types and return type as contract)
- The **class docstring** (system-level context)

```python
class Analyst(Agent, llm=llm):
    """You are a data analyst specializing in financial metrics."""

    async def summarize(self, data: str) -> str:
        """Summarize the key financial metrics from the data."""
        ...
```

**Prefill** -- code before the `...` is sent as a prefill so the LLM continues from where you left off:

```python
async def order(self, recipe: dict) -> OrderResult:
    """Order ingredients for the recipe."""
    stock = {name: self.check_stock(name) for name in recipe}
    ...  # LLM sees stock already computed and continues from here
```

### Class Docstrings (System Prompt)

The class docstring becomes the **opening of the system prompt** for every LLM call on that agent. Keep it concise and to the point -- it sets the role and constraints for every method in the class:

```python
# GOOD: concise, sets clear role and constraints
class SentimentAgent(Agent, llm=llm):
    """Classify comment sentiment as Positive, Neutral, or Negative."""

# GOOD: concise with key behavioral constraint
class CodeReviewer(Agent, llm=llm):
    """Review code for bugs and security issues. Be concise."""

# BAD: too verbose, wastes tokens on every call
class SentimentAgent(Agent, llm=llm):
    """You are a sentiment classification agent. Your sole job is to classify
    the sentiment of user comments into exactly one of three categories:
    Positive, Neutral, or Negative. You should analyze the text carefully
    and consider the overall tone..."""
```

Rules of thumb:
- One sentence is usually enough -- the method docstrings carry the detailed instructions
- State the role or domain, not the mechanics (the framework handles those)
- Avoid repeating constraints that are already enforced by return types (e.g., `Literal["Positive", "Negative"]` already constrains output -- don't restate it in the class docstring)

### Strategies

Strategies control HOW generation methods execute:

| Strategy | Best For | Description |
|---|---|---|
| `CodeActStrategy` (default) | Complex tasks | Agent in a REPL loop: generates and executes Python code iteratively, calling methods on `self` as tools |
| `PredictStrategy` | Classification, extraction | Fast single-shot structured output, no code execution |

```python
from nemo_oo_agents import strategy
from nemo_oo_agents.strategies import PredictStrategy, CodeActStrategy
from nemo_oo_agents.config import CodeActConfig

class MyAgent(Agent, llm=llm):

    # Default: CodeAct (iterative, can call tools)
    async def complex_task(self, request: str) -> str:
        """Handle the request step by step."""
        ...

    # PredictStrategy: fast, single-shot
    @strategy(PredictStrategy())
    async def classify(self, text: str) -> str:
        """Classify as positive, negative, or neutral."""
        ...

    # Tuned CodeAct: limit iterations
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=10)))
    async def bounded_task(self, query: str) -> str:
        """Complete the task in at most 10 iterations."""
        ...
```

### Structured Output

Use Pydantic models, dataclasses, or TypedDict as return types. The framework validates outputs and retries on errors:

```python
from pydantic import BaseModel, Field
from typing import Literal

class Analysis(BaseModel):
    sentiment: Literal["positive", "negative", "neutral"]
    confidence: float = Field(ge=0, le=1)
    topics: list[str]

class Analyzer(Agent, llm=llm):
    async def analyze(self, text: str) -> Analysis:
        """Analyze the text for sentiment and topics."""
        ...  # Returns a validated Analysis instance
```

For open-ended strings with semantic hints, use `Annotated`:
```python
from typing import Annotated

async def respond(self, question: str) -> Annotated[str, "Your answer"]:
    """Answer the question."""
    ...
```

### Methods as Tools

In NeMo OO Agents, any method on `self` is automatically a tool. Mix deterministic helpers and generation methods:

```python
class DataAgent(Agent, llm=llm):
    """Agent that processes data using tools."""

    # Deterministic helper -- available as a tool for the LLM
    def count_words(self, text: str) -> int:
        """Count the number of words in text."""
        return len(text.split())

    # External tool as class attribute
    bash = BashTool()

    # Generation method -- can call count_words() and bash()
    async def process(self, request: str) -> str:
        """Process the data request using available tools."""
        ...
```

**Built-in tools:**

```python
from nemo_oo_agents.tools.bash_tool import BashTool
from nemo_oo_agents.tools.file_tool import FileTool
from nemo_oo_agents.tools.todo_manager import TodoManager

class DevAgent(Agent, llm=llm):
    bash = BashTool()       # Run shell commands
    files = FileTool()      # Read/write files
    todos = TodoManager()   # Track multi-step work with dependencies
```

### Dynamic Prompts

Use `{self.attr}` in docstrings to inject instance state that changes per-agent-instance (configuration, loaded context, runtime settings). The framework resolves `{self.attr}` at call time so the LLM always sees the current value.

```python
class Translator(Agent, llm=llm):
    def __init__(self, language: str = "Spanish"):
        super().__init__()
        self.language = language

    async def translate(self, text: str) -> str:
        """Translate the text to {self.language}."""
        ...
```

**Do not interpolate method parameters** (`{text}`, `{question}`, ...) into the docstring -- they are already part of the function signature, which the LLM sees. Repeating them duplicates tokens and can confuse the model. Only interpolate values the LLM cannot see from the signature: `{self.attr}` for instance state, or computed expressions like `{len(items)}` that produce information the signature does not carry.

## Writing Good Agents

### Design Principles

1. **One responsibility per agent class**: Keep agents focused. Use composition for complex workflows.
2. **Docstrings are prompts**: Make them clear, specific, and actionable. The LLM follows them literally.
3. **Method names are part of the prompt**: Pick clear, verb-first names. `order_ingredients` reads much better than `process`.
4. **Type hints define contracts**: Use precise return types (Pydantic models, Literal, etc.) for reliable outputs. The framework enforces return types at runtime -- invalid outputs trigger validation retries.
5. **Methods are tools**: Split logic into small, well-named methods. Each becomes a tool the LLM can call.
6. **Test incrementally**: Build and test one method at a time before composing them.

### Common Anti-Patterns

**1. Instructions in class docstring, vague method docstrings:**
```python
# BAD: class docstring has all the logic, method docstring is useless
class ReportAgent(Agent, llm=llm):
    """You write research reports. Gather sources first. Extract key facts.
    Cross-check each claim against at least two sources. Reject unsupported
    claims. Organize by theme. Preserve exact figures..."""

    async def write(self, topic: str) -> str:
        """Write the report."""  # Too vague -- the LLM doesn't know what to do
        ...

# GOOD: class sets role, method carries specific instructions
class ReportAgent(Agent, llm=llm):
    """Produce research reports grounded in cited sources."""

    async def write(self, topic: str) -> Report:
        """Write a report on the topic.

        Gather sources, extract key claims, cross-check each claim against at
        least two sources, reject unsupported claims, and organize the final
        text by theme. Preserve exact figures from the sources verbatim."""
        ...
```

**2. Interpolating method parameters into docstrings:**
```python
# BAD: `topic` is already a parameter -- the signature is visible to the LLM,
#      repeating it in the docstring just duplicates information
async def summarize(self, topic: str) -> Summary:
    """Summarize the topic.

    Topic: {topic}
    """
    ...

# GOOD: only interpolate instance state the LLM cannot see from the signature
async def summarize(self, topic: str) -> Summary:
    """Summarize the topic using the current corpus.

    Corpus: {self.corpus_name} ({self.doc_count} documents)
    """
    ...
```

Template interpolation is for instance state (`{self.attr}`) and computed expressions
(`{len(items)}`), not for echoing parameter names that the LLM already has in the
signature.

**3. Missing imports for CodeAct-generated code:**
```python
# BAD: CodeAct generates `json.loads(response)` but json isn't imported
from nemo_oo_agents import Agent

# GOOD: import modules the LLM is likely to need in generated code
import json
from nemo_oo_agents import Agent
```

**4. No deterministic helpers -- everything in one CodeAct loop:**
```python
# BAD: the LLM must figure out everything inline
class ReportAgent(Agent, llm=llm):
    async def write(self, topic: str) -> Report:
        """Gather sources, extract facts, cross-check, and write the report."""
        ...  # CodeAct tries to do everything, often fails on edge cases

# GOOD: break into deterministic helpers the LLM can call as tools
class ReportAgent(Agent, llm=llm):
    def load_sources(self, topic: str) -> list[Source]:
        """Return matching sources from the corpus."""
        return [s for s in self._corpus if topic.lower() in s.title.lower()]

    def cross_check(self, claim: str, sources: list[Source]) -> int:
        """Return the number of sources that support the claim."""
        return sum(1 for s in sources if claim in s.text)

    async def write(self, topic: str) -> Report:
        """Write a cross-checked report on the topic."""
        ...  # CodeAct orchestrates load_sources and cross_check
```

**5. String returns instead of structured types:**
```python
# BAD: return type is str, output is unpredictable
async def classify(self, text: str) -> str:
    """Classify sentiment. Return positive, negative, or neutral."""
    ...

# GOOD: Literal or Pydantic constrains the output
async def classify(self, text: str) -> Literal["positive", "negative", "neutral"]:
    """Classify the sentiment of the text."""
    ...
```

**6. Parallel asyncio.gather on generation methods:**
```python
# BAD: agents have an internal lock -- these run sequentially, not in parallel
results = await asyncio.gather(*[
    agent.process(item) for item in items
])

# GOOD: use separate agent instances / sub-agent instances for true parallelism
agents = [ProcessorAgent() for _ in items]
results = await asyncio.gather(*[
    a.process(item) for a, item in zip(agents, items)
])
```

### Patterns

**Agent composition** -- use one agent as a field of another for multi-agent systems:
```python
class Orchestrator(Agent, llm=llm):
    """Coordinate research and writing."""

    def __init__(self):
        super().__init__()
        self.researcher = ResearchAgent()  # nested agent
        self.writer = WriterAgent()

    async def run(self, topic: str) -> str:
        """Research the topic and write a report."""
        data = await self.researcher.gather(topic)
        return await self.writer.draft(data)
```

Nested agent calls inherit the parent's LLM unless the nested agent class specifies its own.

**Orchestrator pattern** -- one method calls others within a single agent:
```python
class Pipeline(Agent, llm=llm):
    async def run(self, input: str) -> str:
        """Orchestrate the full pipeline."""
        data = await self.extract(input)
        analysis = await self.analyze(data)
        return await self.summarize(analysis)

    async def extract(self, raw: str) -> dict: ...
    async def analyze(self, data: dict) -> str: ...
    async def summarize(self, analysis: str) -> str: ...
```

**Choose the right strategy**:
- Use `PredictStrategy` for simple classification, extraction, single-shot tasks
- Use `CodeActStrategy` (default) for multi-step reasoning, tool use, complex tasks
- `max_iterations` is a safety net, not the primary tuning dial -- the more important decisions are about **task decomposition**: should the outer entrypoint be deterministic or a generation method? What does it call -- deterministic helpers or other generation methods?

**Batch work to reduce API calls**: Process lists in a single method call rather than looping.

## Debugging Agents

### Tracing (Auto-Enabled)

Tracing is **automatic**. Every `Agent.__init__()` call probes `localhost:5001`; if the development server is running, spans are sent via OTLP automatically -- no code changes needed. The tracing package ships with `nemo_oo_agents` so nothing extra to install.

**Development workflow:**
```bash
# Terminal 1: start the trace viewer
nemo start-dev   # Launches viewer at http://localhost:5001

# Terminal 2: run your agent -- traces appear automatically
uv run python main.py
```

**Explicit tracing** is needed when you want to control where spans go -- writing JSONL to disk, pointing at a custom OTLP endpoint, using Langfuse/Phoenix, or providing a custom exporter:

```python
from nemo_oo_agents.tracing import enable_tracing, exporters

# JSONL files on disk (for offline analysis or CI)
# NOTE: trace_dir is a DIRECTORY, not a file path. Files are written as
# `{trace_dir}/{session_id}.jsonl` -- one file per session.
enable_tracing(exporters=[exporters.jsonl(trace_dir="./traces")])

# Custom OTLP collector
enable_tracing(exporters=[exporters.otlp("https://collector.example.com")])

# Multiple destinations at once
enable_tracing(exporters=[
    exporters.jsonl(trace_dir="./traces"),
    exporters.otlp("https://collector.example.com"),
])
```

Traces capture every LLM call, code execution, and tool invocation with parent-child span relationships. Use `no_trace()` to skip tracing for a specific code block when you do not want it recorded.

### Common Issues

| Issue | Cause | Fix |
|---|---|---|
| `NameError` in CodeAct generated code | Module not imported at top of agent file | See **Making Libraries Available to the LLM** |
| `KeyError` on `{param}` in docstring | Curly braces in docstrings are template variables; a name with no matching `self.attr` or expression raises at render time | Escape literal braces as `{{param}}`, or only interpolate `{self.attr}` / computed expressions |
| `asyncio.gather` on generation methods runs sequentially | Each agent has an internal lock -- only one generation method runs at a time per instance | Use separate sub-agent instances for true parallelism (each instance has its own lock) |
| LLM calls its own entry-point method recursively | Entry-point generation method is visible in `<self>` and the LLM picks it as a tool | Add `@hidden` to the entry-point method so it is not rendered in the tool list |

### Inspecting the Prompt

See exactly what the LLM receives for any method -- essential for debugging:

```python
from nemo_oo_agents import print_prompt, build_prompt_data
from unifiedllm import FakeLLMClient

# Create agent with a fake LLM (no API calls)
agent = MyAgent(llm=FakeLLMClient())

# Print the full prompt for a specific method call
await print_prompt(agent.process_order, recipe={"butter": 110})

# Or get structured prompt data for programmatic inspection
data = await build_prompt_data(agent.process_order, recipe={"butter": 110})
```

This shows the system prompt (class docstring + context blocks), the `<self>` section (visible methods and fields), the `<execution_context>` (available symbols in CodeAct), and the method docstring with template variables resolved.

### Debugging a Stuck Agent

```bash
kill -USR2 <pid>    # Dumps traceback + current cell code (auto-installed at import)
```

```python
from nemo_oo_agents import enable_logging
enable_logging(level="DEBUG")    # Framework logger hierarchy at nemo_oo_agents
```

### Testing with FakeLLMClient

Test agent logic without network calls:

```python
from unifiedllm import FakeLLMClient

# Script responses the fake LLM will return in order
fake = FakeLLMClient.with_code_responses(["print('hello')", "return_result('done')"])
agent = MyAgent(llm=fake)
result = await agent.process("test")

# Inspect what the agent sent to the LLM
assert fake.call_count == 2
```

### Debugging Tips

1. **Inspect the prompt first**: Use `print_prompt` to see what the LLM actually receives -- most bugs are visible in the rendered prompt
2. **Run `nemo start-dev`** before you run your agent and traces appear automatically -- no code changes needed
3. **Check the docstring**: It IS part of the prompt the LLM gets. Unclear docstrings produce unreliable results.
4. **Check the class docstring**: It's part of the system prompt. Keep it concise -- verbose class docstrings waste tokens on every call.
5. **Reduce scope**: Test one method in isolation before composing
6. **Use `PredictStrategy`** for debugging structured output issues (removes CodeAct complexity)
7. **Check `max_iterations`**: Default CodeAct may iterate too many times on simple tasks
8. **Check imports**: CodeAct-generated code can only use modules imported at the top of your agent file. If the LLM generates `json.loads(...)`, you need `import json` in your module.
9. **Dump a stuck agent**: Send `kill -USR2 <pid>` to get the traceback and current cell code
10. **Enable framework logging**: `enable_logging(level="DEBUG")` for detailed execution traces

## Advanced Features

### Context API

Pin information into the LLM's system prompt via `self.context`:

```python
# Static block -- pinned once
agent.context["rules"] = "Always respond in bullet points."

# Dynamic block -- re-evaluated every turn
agent.context.set_dynamic("notes", "self.render_notes()")

# Remove a block
agent.context.remove("rules")
```

The LLM can also manage context blocks in CodeAct (`self.context["plan"] = "Step 1: ..."`).

**Scoped overrides** -- temporarily override context for a specific call:
```python
from nemo_oo_agents import ScopedContext

with ScopedContext({"focus": "Only answer about pricing"}):
    result = await agent.respond(question)
```

### Visibility System

Control what the LLM can see. This matters because everything visible in `<self>` becomes part of every prompt -- wasting tokens and sometimes confusing the LLM.

**Rule: only hide the entry-point method and pure-Python helpers. Never add `@hidden` to a method that the LLM should call as a tool.**

| Should be `@hidden`? | Example |
|---|---|
| Yes — entry-point generation method (prevents self-recursion) | `respond`, `run`, `execute` — the top-level method called externally |
| Yes — pure-Python helpers that are **never** called by the LLM | Internal wiring only called from other Python code, never needed as a tool |
| **No** — any method the LLM needs to call, including Python helpers | `search_web`, `write_file`, `_format_result` — if the LLM must call it, keep it visible |

**Method-level hiding** -- hide internal helpers:
```python
from nemo_oo_agents import hidden

class MyAgent(Agent, llm=llm):
    @hidden
    async def _internal_helper(self, data: str) -> str:
        """This method is hidden from the LLM but still callable."""
        return data.upper()
```

**Entry-point hiding** -- hide the main generation method from `<self>` to prevent self-recursion:
```python
class MyAgent(Agent, llm=llm):
    @hidden  # Without this, the LLM sees 'respond' in <self> and may call itself
    async def respond(self, message: str) -> str:
        """Respond to the user message."""
        ...
```

**Import-level hiding** -- prevent framework internals from polluting CodeAct's execution context:
```python
from nemo_oo_agents import hidden

# Without this, SkillManager, BaseModel, etc. appear in <execution_context>
with hidden:
    from nemo_oo_agents import SkillManager, TextSkill
    from nemo_oo_agents.agents import TokenBudgetSummarizer
```

**Field-level hiding** -- exclude fields from `<self>` rendering:
```python
from typing import Annotated
from nemo_oo_agents import hidden

class MyAgent(Agent, llm=llm):
    _cache: Annotated[dict, hidden] = {}  # Not shown to LLM
```

### Media Types

Handle multimodal content -- **only works with multimodal-capable LLMs** (e.g. Claude 4.x, GPT-4o, Gemini). Text-only models will error when passed `Image` or `Audio`.

```python
from nemo_oo_agents import Image, Audio, File

class MediaAgent(Agent, llm=llm):
    async def describe_image(self, image: Image) -> str:
        """Describe what you see in this image."""
        ...
```

### Skills System

Inject curated context (guidelines, examples, domain knowledge):

```python
from nemo_oo_agents import SkillManager, TextSkill

class MyAgent(Agent, llm=llm):
    def __init__(self):
        super().__init__()
        self.coding_skill = TextSkill(path="path/to/skill-directory")
```

### MCP Integration

Connect external tool servers via Model Context Protocol:

```python
from mcp_nemo_oo_agents import MCPManager

class MyAgent(Agent, llm=llm):
    external_tool = MCPManager.create_from_server("server-name")
```

Configure servers in `.mcp.json` at the project root.

### Event API

Query and filter agent history:

```python
from nemo_oo_agents import EventQuery

# Query past events
recent = agent.events.query(limit=20)
messages = agent.events.query(EventQuery(type="message"))

# Filter what history the LLM sees per method
@strategy(PredictStrategy(), events=EventQuery.current_call())
async def classify(self, text: str) -> str: ...
```

Built-in event query factories: `current_call()`, `by_type()`, `last_n()`.

### History Summarization

For long-running conversations, auto-compress older history:

```python
from nemo_oo_agents.agents import TokenBudgetSummarizer, MethodSummarizer
from nemo_oo_agents.config import TokenBudgetConfig

# Compress when token budget exceeded
TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(max_tokens=1000))

# Or compress after each method call completes
MethodSummarizer.install(agent)
```

### Persistence

Persist agent state across restarts with `SQLiteStorageManager`:

```python
from nemo_oo_agents.storage import SQLiteStorageManager

storage = SQLiteStorageManager("agent_state.db")
agent = MyAgent(storage=storage)  # resumes from last snapshot
```

Events, context blocks, LLM-defined methods, and user attributes are all serialized. Exclude fields with `Annotated[T, nosnapshot]`. For cross-process transfer, use `snapshot_to_json(agent)`.

### Configuration Tuning

Beyond `max_iterations`, tune the agent loop per method:

```python
from nemo_oo_agents.config import CodeActConfig, ExecutionConfig, TruncationConfig

@strategy(CodeActStrategy(config=CodeActConfig(
    max_iterations=15,
    max_retries=3,
    cell_timeout=30,           # seconds per code cell
    max_tokens=4096,           # LLM output limit
    temperature=0.0,
)))
async def precise_task(self, data: str) -> Result: ...
```

| Config | Purpose |
|---|---|
| `CodeActConfig` | `max_iterations`, `max_retries`, `cell_timeout`, `max_tokens`, `temperature`, `top_p`, `max_tool_calls` |
| `TruncationConfig` | Cap block sizes, total context tokens, stdout/stderr length, pprint depth |
| `ExecutionConfig` | `max_nesting_depth` to prevent runaway agent-in-agent chains |
| `RestrictionsConfig` | Denylist modules and per-module functions in generated code |

All config objects support `merge_with()` for layered defaults (framework → class → instance → method).

### Code Execution Sandbox

CodeAct-generated code runs in a safe sandbox:
- **AST-level safety** blocks `exec`/`eval`, raw imports, dunder escapes, and blocking calls automatically
- **REPL state persists** across cells -- variables defined in cell N are available in cell N+1
- **Pre-imported utilities**: `pprint`, `logger`, `asyncio`, and common typing constructs are available in generated code without explicit imports
- Module-level imports from your agent file are inherited by the execution context

### Self-Extending Agents

The LLM can define new helper methods at runtime to decompose complex tasks. This happens automatically in CodeAct when the LLM determines it needs a new capability.

## Traceability: Keep Logic Inside the Agent

Only code that runs inside agent methods appears in traces. Logic that runs before the agent is instantiated — in `main()` or module-level helper functions — is invisible: bugs there produce no trace evidence, and the agent cannot adapt when they fail.

**Anti-pattern — preprocessing outside the agent:**

```python
# BAD: all the interesting logic runs before the agent starts
def run(input_path: str) -> None:
    raw = load_file(input_path)        # bug here? invisible in trace
    parsed = parse_structure(raw)      # failure? agent never knows
    enriched = fetch_metadata(parsed)  # incomplete data? no evidence

    agent = MyAgent(data=enriched)     # agent just classifies pre-digested input
    result = await agent.classify()
```

**Preferred pattern — pass raw inputs, let the agent do the work:**

```python
# GOOD: raw input only; the agent fetches and reasons over everything itself
agent = MyAgent(input_path=input_path)
result = await agent.run()
```

**Rules of thumb:**
- Pass only raw inputs to the agent (file path, prompt string, record id).
- If you're writing a helper function that prepares context for the agent, ask: should this be a method on the agent instead?
- Use `PredictStrategy` only when the input is already fully formed. If the agent needs to gather or verify anything, use CodeAct.

---

## Making Libraries Available to the LLM

In CodeAct, the LLM generates Python code that executes in the agent module's namespace. A library is only accessible in that generated code if it has been imported at the **module level** of the agent file — imports inside a method body or in `main()` are not in scope.

```python
import json           # noqa: F401  ← available to LLM-generated code
import re             # noqa: F401
from pathlib import Path

from nemo_oo_agents import Agent
from unifiedllm import get_llm_client
from trace_explorer import TraceExplorer  # noqa: F401

llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1")

class MyAgent(Agent, llm=llm):
    async def run(self, task: str) -> str:
        """Complete the task.

        You can use json, re, Path, and TraceExplorer freely.
        """
        ...
```

The `# noqa: F401` comment suppresses the "imported but unused" linter warning — these imports exist solely to populate the LLM's execution namespace.

**Rule:** if the LLM needs to call `json.loads(...)`, `re.search(...)`, or any third-party library in its generated code, that library must be imported at the top of the file.

---

## Quick Reference

| Need | Solution |
| ---- | -------- |
| Reliable typed output | Pydantic return type |
| Shared context across methods | `self.attr`, `{self.attr}` in docstrings |
| Multi-step reasoning | Sequential async methods (Step 4) |
| Agent fetches/decides dynamically | CodeAct (default) — define methods on `self` (Step 6) |
| Fast single-shot classification | `@strategy(PredictStrategy())` (Step 7) |
| Delegate to a focused subagent | Second `Agent` subclass; store instance on `self`; `await` its methods (Step 8) |
| Load skills from a directory | `SkillManager.install(self, skills_dir=path)` in `__init__` |
| Run a skill script | `await self.<skill_name>.run_script("script.sh")` |
| Read a skill file | `self.<skill_name>.read_file("SKILL.md")` |
| Shell commands | `self.bash = BashTool(working_dir=path)` → `await self.bash.run("cmd")` |
| File operations | `self.files = FileTool(bash=self.bash)` → `await self.files.read(path)` |
| Agent self-planning / step tracking | `self.todos = TodoManager()` → `t = self.todos.add("step", deps=[prev.id])` → `self.todos.done(t.id)` → `self.todos.status()` |
| Tune CodeAct iterations | `@strategy(CodeActStrategy(config=CodeActConfig(max_iterations=N)))` |
| Per-method LLM override | `@strategy(..., llm=other_llm)` |
| Observability | `enable_tracing(exporters=[exporters.jsonl(trace_dir="./traces")])` before first call |
| Make library available to LLM | Import at module level with `# noqa: F401` |
| Prevent self-recursion | `@hidden` on the entry-point method |
| Parallel execution | Separate agent instances per item + `asyncio.gather` |

---

## What Else You Need

| Feature | Where to look |
|---|---|
| Full strategy reference | `references/REFERENCE.md` |
| Structured output examples | `references/REFERENCE.md` |
| Dynamic prompt patterns | `references/REFERENCE.md` |
| Context API details | `references/REFERENCE.md` |
| Event API & EventQuery | `references/REFERENCE.md` |
| Persistence & snapshots | `references/REFERENCE.md` |
| Config reference (CodeActConfig, TruncationConfig) | `references/REFERENCE.md` |
| Self-extending agents | `references/REFERENCE.md` |
| Testing with FakeLLMClient | `references/REFERENCE.md` |
| Tool design rubric (helper methods, external tools) | `references/tool-design.md` |
| Multi-phase agents (controlled outer loop, typed phases) | `references/multi-phase-agents.md` |
| Subagent composition (delegating with typed outputs) | `references/subagent-composition.md` |
| TodoManager API (agent self-planning) | `references/todo-manager.md` |
