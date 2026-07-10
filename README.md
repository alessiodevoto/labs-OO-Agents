# NeMo OO Agents

> **Renaming notice:** NeMo OO Agents is being renamed to **NeMo Object-Oriented Agents** (`nooa`). The new name will take effect in an upcoming release.

**What if your Python methods could think?** With NeMo OO Agents, they can. Write AI agents using familiar Python OOP—no new paradigms to learn. Define method signatures with type hints and docstrings, and your methods automatically become intelligent agents that can reason, execute code, and even create new methods to decompose complex tasks.

## Why NeMo OO Agents?

- **Familiar Python OOP**: Classes for scope, methods for capabilities, inheritance for composition
- **Zero Boilerplate**: Define `async def analyze_data(self, text: str) -> str: ...` and it just works
- **Type-Safe**: All returns are validated, including full Pydantic integration for structured outputs
- **Pluggable Strategies**: Optimize per-method with `@strategy` decorator - fast PredictStrategy or iterative CodeAct
- **Self-Extending**: Your methods can think about complex problems and create new methods dynamically to decompose tasks
- **Async Native**: First-class async/await support throughout
- **Observable Context**: Context blocks show transparently where information comes from—the LLM can even manage its own context
- **Rich Introspection**: `doc()` gives LLMs a rich interface to explore objects, enabling progressive disclosure
- **Software 1.0 / Software 3.0 (SW1/SW3) Symmetry**: Seamlessly interleave deterministic Python and LLM reasoning
- **Context-Efficient**: Automatic handling of large parameters with smart truncation and `pprint()`

## Installation

### Just want the OO CLI/TUI?

```bash
curl -LsSf https://gitlab-master.nvidia.com/interactive-agents/nooa/-/raw/main/scripts/install.sh | sh
exec $SHELL          # pick up the ~/.local/bin PATH change
nooa tui          # launch the interactive agent REPL
```

The script installs `uv`, a managed Python, and the three lockstep packages (CLI + core + the `nemo-oo-agents-nvidia` aliases), then prompts for your `NVIDIA_INTERNAL_API_KEY` and saves it to `~/.config/nooa/secrets.yaml`. Upgrade later with `uv tool upgrade nemo-labs-oo-agents-cli`.

### Use as a library

Add to a Python project (typical workflow when *building* agents, not just running the TUI):

```bash
# Create a new project (if needed)
uv init my-agent-project
cd my-agent-project

# Core framework + NVIDIA-gateway model aliases (claude-haiku, nemotron3-nano-30b,
# gpt-5.2, …). External users who don't want the NVIDIA aliases drop the second `uv add`.
uv add "nemo-oo-agents @ git+https://gitlab-master.nvidia.com/interactive-agents/nooa.git@main"
uv add "nemo-oo-agents-nvidia @ git+https://gitlab-master.nvidia.com/interactive-agents/nooa.git@main#subdirectory=packages/nemo-oo-agents-nvidia"

# Optional: CLI + TUI (`nooa` command, web terminal, agent REPL)
uv add "nemo-labs-oo-agents-cli @ git+https://gitlab-master.nvidia.com/interactive-agents/nooa.git@main#subdirectory=packages/nemo-labs-oo-agents-cli"

# Optional: benchmark harness (SWE-bench, Terminal Bench, LoCoMo, Tau Bench, DABStep)
uv add "nemo-oo-agents-benchmarks @ git+https://gitlab-master.nvidia.com/interactive-agents/nooa.git@main#subdirectory=packages/nemo-oo-agents-benchmarks"
```

NeMo OO Agents ships as four lockstep packages from this repo:

- **`nemo-oo-agents`** — the core framework. Includes the agent runtime, context blocks, and the unified LLM client. Optional extras: `[tracing]` (OpenTelemetry exporters), `[viewer]` (FastAPI trace viewer), `[mcp]`, `[nemo-flow]`.
- **`nemo-labs-oo-agents-cli`** — the `nooa` command and agent TUI. Optional `[datascience]` extra pre-loads numpy/pandas/plotly/scipy/sklearn into the LLM REPL execution namespace; `[web]` adds the `nooa term` web frontend.
- **`nemo-oo-agents-benchmarks`** — eval harness (SWE-bench, Terminal Bench, LoCoMo, Tau Bench, DABStep) and the `nemo-harbor` runner.
- **`nemo-oo-agents-nvidia`** — opt-in NVIDIA-gateway model aliases. Registers via the `nooa.bundled_configs` entry-point group; install to add the aliases, omit for an OSS-only registry.

### API Keys

Keys are read from `~/.config/nooa/secrets.yaml` unless already exported in your shell (the export wins). The installer writes this file; you can also edit it by hand:

```yaml
# ~/.config/nooa/secrets.yaml   (chmod 600; gitignore the project-local one)
env:
  NVIDIA_INTERNAL_API_KEY: your-api-key-here
  # ANTHROPIC_API_KEY: sk-ant-...
```

For **library use** (not the CLI), drop a `.env` in your project directory instead — it's loaded by scripts and the viewer:

```bash
echo 'NVIDIA_INTERNAL_API_KEY=your-api-key-here' > .env
```

Run `nooa config show` to see which `secrets.yaml` / `settings.yaml` / `llm_config.yaml` layers are loading (secret values are redacted — only key names are shown).

- **NVIDIA Inference HUB**: All the quickstart examples and bundled aliases route through inference.nvidia.com. Get your key at [inference.nvidia.com](https://inference.nvidia.com) → `NVIDIA_INTERNAL_API_KEY`.

### Development Setup (Advanced Use)

<details>
<summary><strong>Click here to see instructions for the development setup</strong></summary>

If you want to contribute to NeMo OO Agents or change code of the library, use the following steps.

```bash
git clone ssh://git@gitlab-master.nvidia.com:12051/interactive-agents/nooa.git
cd nooa/
./setup.sh  # Sets up venv, installs dependencies, copies .env template
# Then create .env with your API key (see API Keys above)
```

</details>



## Quick Start

NeMo OO Agents's key strength is that you can start with zero boilerplate and progressively add structure only when you need it.

> **Note**: The examples below use `from nooa.util.quickstart import *` which provides common imports (`Agent`, `llm`, `BaseModel`, `strategy`, `autorun`, etc.) for brevity. Each example is copy-paste runnable.

### Step 1: Your First Generation Method

Methods with `...` bodies are called **generation methods** - they're implemented by agentic strategy using LLMs at runtime. The method signature defines the contract (inputs/outputs), and the **docstring provides instructions** to guide the LLM:

```python
from nooa.util.quickstart import *
from nooa.unifiedllm import get_llm_client

# Pick whichever you have credentials for. The NVIDIA aliases below require
# `nemo-oo-agents-nvidia` (installed by default per the Installation section)
# and NVIDIA_INTERNAL_API_KEY.
llm = get_llm_client("claude-haiku")     # → NVIDIA-gateway Claude Haiku
# llm = get_llm_client("gpt-4o-mini")    # → OpenAI direct; needs OPENAI_API_KEY


class FeedbackAgent(Agent, llm=llm):
    """You are an agent specializing in analyzing customer feedback."""

    async def analyze_feedback(self, text: str) -> str:
        """Analyze customer feedback for sentiment and key topics in one sentence."""
        ...  # Generation method - LLM implements based on docstring


@autorun
async def main():
    agent = FeedbackAgent()
    result = await agent.analyze_feedback("Great product, but shipping was slow")
    print(result)
```
`get_llm_client()` is a thin wrapper on top of [litellm](https://docs.litellm.ai/), so any litellm-supported model name works directly:

```python
llm = get_llm_client("gpt-4o-mini")                # OpenAI (needs OPENAI_API_KEY)
llm = get_llm_client("claude-sonnet-4-5-20250514") # Anthropic (needs ANTHROPIC_API_KEY)
llm = get_llm_client("gemini/gemini-2.5-flash")    # Google (needs GEMINI_API_KEY)
```

If you installed `nemo-oo-agents-nvidia` (the Installation section above includes it), an extra set of NVIDIA-gateway aliases (claude-*, nemotron-*, qwen-*, gemini-*, gpt-*, llama-*) is registered automatically via the `nooa.bundled_configs` entry-point group:

```python
llm = get_llm_client("claude-haiku")          # NVIDIA-gateway Claude Haiku
llm = get_llm_client("nemotron3-nano-30b")    # NVIDIA Nemotron Nano
```

Set `NVIDIA_INTERNAL_API_KEY` (or `NVIDIA_API_KEY` for the public NIM endpoint) and they Just Work. External users who don't install `nemo-oo-agents-nvidia` see an OSS-only registry. To customize, run `nooa config eject` (writes to `~/.config/nooa/llm_config.yaml`), drop an `llm_config.yaml` in your project's `.nooa/` dir, or point `NEMO_OO_LLM_CONFIG` at one or more YAML files. Run `nooa config show` to inspect which files are loading.

See [`src/nooa/unifiedllm/registry.py`](src/nooa/unifiedllm/registry.py) for the YAML schema, or `CompletionClient()` directly for full control.

> **Key insight**: In NeMo OO Agents, your method name, parameters, and docstring ARE the prompt. Try renaming `analyze_feedback` to `analyze_feedback_briefly` or `give_detailed_feedback_analysis`—the output changes accordingly, without modifying any other code. This is the fundamental paradigm shift: code structure drives LLM behavior.

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/01_first_generation_method.py
```
</details>

### Step 2: Use Structured Output to Enforce Method Contracts with Auto-Retry

Use any Pydantic model as a return type. NeMo OO Agents automatically validates outputs and retries on errors. The LLM receives validation messages and corrects its response, ensuring you always get type-safe, valid data. This makes integrating LLM outputs with deterministic code robust and reliable:

```python
from typing import Literal

from nooa.util.quickstart import *


class FeedbackAnalysis(BaseModel):
    sentiment: Literal["positive", "negative", "neutral", "mixed"]
    topics: list[str]
    urgency: Literal["low", "medium", "high"]
    summary: str
    confidence: float = Field(ge=0, le=1)  # Pydantic constraints enforced!


class FeedbackAgent(Agent, llm=llm):
    """Agent for analyzing customer feedback with structured output."""

    async def analyze_feedback(self, text: str) -> FeedbackAnalysis:
        """Analyze customer feedback comprehensively."""
        ...


@autorun
async def main():
    agent = FeedbackAgent()
    result = await agent.analyze_feedback("Broken feature, needs immediate fix!")
    print(result)  # Guaranteed valid FeedbackAnalysis instance
```

Any Pydantic features work: `Field` constraints, validators, nested models, optional fields, and more. In addition, NeMo OO Agents also supports `dataclasses`, `TypedDict`s, and validates basic types like `str`, `int`, `bool`, plus container types like `dict` and `list`.

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/02_structured_outputs.py
```
</details>

### Step 3: Your Methods Are Your Tools (SW1/SW3 Interleaving)

In NeMo OO Agents, you don't need a separate "tool" abstraction—**your regular Python methods ARE the tools**. The LLM can call any method on `self`, enabling seamless interleaving of deterministic code (SW1) and LLM reasoning (SW3). No decorators, no registration, no schema definitions:

```python
from typing import TypedDict

from nooa.util.quickstart import *


class Result(TypedDict):
    can_fulfill: bool
    total_cost: float
    unavailable_items: list[str]


class InventoryAgent(Agent, llm=llm):
    """You are an agent that checks inventory using deterministic helper methods."""

    def __init__(self):
        super().__init__()
        self.inventory = {
            "apple": {"stock": 50, "price": 0.75},
            "banana": {"stock": 30, "price": 0.50},
            "orange": {"stock": 0, "price": 0.80},  # Out of stock
        }

    # SW1: Deterministic Python - automatically available as "tools" for the LLM
    def get_stock(self, item: str) -> int:
        """Get current stock for an item."""
        return self.inventory.get(item, {}).get("stock", 0)

    def get_price(self, item: str) -> float:
        """Get price for an item."""
        return self.inventory.get(item, {}).get("price", 0.0)

    # SW3: Generation method - LLM implements this, calling SW1 methods as needed
    async def can_fulfill_order(self, items: list[str], budget: float) -> Result:
        """Check if order can be fulfilled within budget."""
        ...


@autorun
async def main():
    agent = InventoryAgent()
    result = await agent.can_fulfill_order(["apple", "banana", "orange"], budget=5.0)
    print(f"Can fulfill: {result['can_fulfill']}")
    print(f"Total cost: {result['total_cost']}")
    print(f"Unavailable items: {result['unavailable_items']}")
```

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/03_codeact_tools.py
```
</details>

### Step 4: Choose How Your Methods Think

Use `@strategy` to control reasoning style (agentic strategy) per-method. You can also add external tools (APIs, databases, MCP servers) as class attributes—they become callable just like your methods:

```python
from typing import Annotated

from nooa.config import CodeActConfig
from nooa.tools import ShellTools
from nooa.util.quickstart import *


class AnalysisAgent(Agent, llm=llm):
    """Agent demonstrating different strategy options."""

    shell = ShellTools()  # External tool - LLM can call this too

    @strategy(PredictStrategy())
    async def classify_sentiment(self, text: str) -> str:
        """Classify as positive, negative, or neutral."""
        ...

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=10)))
    async def perform_task(
        self,
        request: str,
    ) -> Annotated[str, "Your answer"]:
        """Perform the task requested by the user and provide a friendly response."""
        ...


@autorun
async def main():
    agent = AnalysisAgent()

    # PredictStrategy (fast, single-shot)
    sentiment = await agent.classify_sentiment("I love this product! Best purchase ever!")
    print("Sentence: I love this product! Best purchase ever!")
    print(f"Sentiment: {sentiment}\n")

    # CodeActStrategy (can execute Python code, call methods, iterate)
    requests = [
        "What is the current working directory?",
        "List the files in the current directory.",
    ]
    for request in requests:
        response = await agent.perform_task(request=request)
        print(f"Request: {request}")
        print(f"Response: {response}\n")
```


NeMo OO Agents supports two strategies (with more planned in the future). Advanced users can implement their own strategies.

| Strategy | Best For | Description |
|----------|----------|-------------|
| `CodeActStrategy` | Complex tasks (default) | Executes Python code iteratively, can call methods on `self` |
| `PredictStrategy` | Classification, extraction | Fast single-shot structured output, no code execution |

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/04_strategies.py
```
</details>

### Step 5: Explore with Progressive Disclosure

LLMs can use `doc()` to explore unknown objects. This is powerful when working with factories or APIs that return `Any`:

```python
from typing import Any

from nooa.util.quickstart import *

_WAREHOUSE = {
    "ART-001": Artwork("Starry Night Print", "Van Gogh Studio", appraised_value=15000.0),
    "STK-001": StockHolding("NVDA", shares=100, price_per_share=875.50),
    "JWL-001": Jewelry("Diamond Ring", carats=2.5, rate_per_carat=8000.0),
    "COL-001": Collectible("Vintage Baseball Card", base_value=5000.0, condition="excellent"),
    "ART-002": Artwork("Modern Abstract", "Local Artist", appraised_value=2500.0),
    "STK-002": StockHolding("AAPL", shares=50, price_per_share=185.25),
}


def get_item(item_id: str) -> Any:
    """Retrieve an item from the warehouse by ID."""
    return _WAREHOUSE.get(item_id)


class WarehouseAppraiser(Agent, llm=llm):
    """Agent that appraises items without knowing their types ahead of time."""

    get_item = staticmethod(get_item)

    async def appraise_item(self, item_id: str) -> float:
        """Get the monetary value of an item."""
        ...


@autorun
async def main():
    appraiser = WarehouseAppraiser()
    test_items = ["ART-001", "STK-001", "JWL-001", "COL-001"]

    for item_id in test_items:
        value = await appraiser.appraise_item(item_id)
        item = get_item(item_id)
        item_type = type(item).__name__
        print(f"  {item_id} ({item_type}): ${value:,.2f}")
```

The LLM must use `doc(item)` to discover how to extract value from each type:
- `Artwork`: `item.get_appraisal()["value"]`
- `StockHolding`: `item.get_total_value()`
- `Jewelry`: `item.compute_value()`
- `Collectible`: `item.estimate_value()`

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/05_progressive_disclosure.py
```
</details>

### Step 6: Tracing

Tracing is automatic. Start the development server and all agent method calls are traced — orchestrators, LLM methods, and private helpers — with parent-child relationships preserved:

```bash
nooa start-dev   # start trace viewer on http://localhost:5001
```

```python
from nooa import hidden
from nooa.util.quickstart import *


class MathAgent(Agent, llm=llm):
    """Agent that performs calculations with full tracing."""

    async def run(self, expression: str) -> str:
        """Orchestrator: evaluate the expression, then explain it."""
        value = await self.calculate(expression)
        formatted = await self._format(value)
        explanation = await self.explain(expression, formatted)
        return explanation

    async def calculate(self, expression: str) -> float:
        """Evaluate the mathematical expression and return the numeric result."""
        ...

    async def explain(self, expression: str, result: str) -> str:
        """Explain in one sentence why {expression} equals {result}."""
        ...

    @hidden
    async def _format(self, value: float) -> str:
        """Private helper — formats the result for display."""
        return f"{value:g}"


@autorun
async def main():
    agent = MathAgent()
    result = await agent.run("(10 + 5) * 2")
    print(f"Result: {result}")
```

Traces capture every LLM call, code execution, and tool invocation, with spans nested by call hierarchy. If the viewer is not running, tracing is silently disabled. Set `OTLP_ENDPOINT` to send traces to a viewer on a different host or port. See the [viewer README](packages/nemo-oo-agents-viewer/README.md) for more on the viewer API, trace import/export, and the trace format convention.

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/06_tracing.py
```
</details>

### Step 7: Dynamic Prompts with Templating

Use `{self.attribute}` in docstrings to inject specific runtime values in your prompts:

```python
from nooa.util.quickstart import *


class TranslatorAgent(Agent, llm=llm):
    """Agent that translates text with configurable behavior."""

    def __init__(self, target_language: str = "Spanish", **kwargs):
        super().__init__(**kwargs)
        self.target_language = target_language
        self.translation_count = 0

    async def translate(self, text: str) -> str:
        """Translate the text to {self.target_language}.

        Keep the translation natural and idiomatic.
        """
        ...

    async def translate_formal(self, text: str) -> str:
        """Translate the text to {self.target_language} using formal register.

        Use polite/formal forms (e.g., 'usted' in Spanish, 'Sie' in German).
        """
        ...


@autorun
async def main():
    # The {self.target_language} in docstrings is expanded at runtime
    spanish = TranslatorAgent(target_language="Spanish")
    german = TranslatorAgent(target_language="German")

    text = "Hello, how are you today?"

    print(f"Original: {text}\n")

    result_es = await spanish.translate(text)
    print(f"Spanish: {result_es}")

    result_de = await german.translate(text)
    print(f"German: {result_de}")

    result_formal = await spanish.translate_formal(text)
    print(f"Spanish (formal): {result_formal}")
```


Template variables work with any Python expression: `{self.attr}`, `{len(items)}`, `{param.upper()}`.

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/07_dynamic_prompts.py
```
</details>

### Step 8: Context Blocks

Context blocks let you pin information directly into the LLM's system prompt—so it's always visible without re-passing it in every method call. Static blocks hold a fixed value; dynamic blocks re-evaluate an expression each turn so the LLM always sees live state:

```python
from nooa.util.quickstart import *
from nooa.agentdoc import spec


class NoteTakingAgent(Agent, llm=llm):
    """Agent that stores notes and answers questions about them."""

    def __init__(self):
        super().__init__()
        self._notes: list[str] = []
        spec(self, "context", hidden=False)  # Expose context management to LLM

    def add_note(self, text: str) -> None:
        """Add a note to the collection."""
        self._notes.append(text)

    def render_notes(self) -> str:
        """Render all stored notes as a formatted list."""
        return "\n".join(f"- {n}" for n in self._notes) or "No notes yet."

    async def record(self, note: str) -> str:
        """Store this note using add_note and confirm what was saved."""
        ...

    async def answer(self, question: str) -> str:
        """Answer the question using the notes visible in your context."""
        ...


@autorun
async def main():
    agent = NoteTakingAgent()

    # Dynamic block: expression re-evaluated every turn — LLM always sees latest notes
    agent.context.set_dynamic("notes", "self.render_notes()")

    notes = [
        "Deploy uses blue-green strategy with 5-minute health checks.",
        "Database migrations run before traffic shifts.",
        "Rollback is automatic if error rate exceeds 1% for 2 minutes.",
    ]
    for note in notes:
        await agent.record(note)

    # LLM sees all three notes in its context — no need to pass them explicitly
    answer = await agent.answer("What triggers an automatic rollback?")
    print(f"Answer: {answer}")

    # Static block: pin a value once — useful for specs, plans, decisions
    agent.context["policy"] = "Always prefer rollback over forward-fix during incidents."
    answer2 = await agent.answer("Should we try to fix forward or roll back?")
    print(f"Answer: {answer2}")
```

Both block types appear in the LLM's system prompt as labelled sections. The LLM can also manage blocks itself in CodeAct—adding, updating, or removing them as its understanding evolves.

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/08_context_blocks.py
```
</details>

### Step 9: Automatic History Summarization

Each exchange in a multi-turn conversation adds events to the agent's history. Left unchecked, this eventually fills the model's context window. `TokenBudgetSummarizer` compresses older turns automatically when a budget threshold is crossed—keeping history bounded so the conversation can run indefinitely.

```python
from nooa.agents import TokenBudgetSummarizer
from nooa.config import TokenBudgetConfig
from nooa.util.quickstart import *
from nooa.agentdoc import spec


class InterviewAgent(Agent, llm=llm):
    """A technical interviewer conducting a multi-turn conversation."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        spec(self, "events", hidden=False)  # Expose event querying

    async def ask(self, candidate_answer: str) -> str:
        """Continue the technical interview based on the candidate's latest answer.

        Ask a relevant follow-up or move to a new topic as appropriate.
        Keep track of what has already been covered.
        """
        ...

    async def evaluate(self) -> str:
        """Based on the full interview so far, provide a brief candidate evaluation."""
        ...


ANSWERS = [
    "I have 3 years of Python experience, mostly building REST APIs.",
    "I use FastAPI for most projects — I like its async support and automatic OpenAPI docs.",
    "For databases, I usually go with PostgreSQL and SQLAlchemy as the ORM.",
    "I handle errors with try/except blocks and return proper HTTP status codes.",
    "For testing I write unit tests with pytest and mock out external dependencies.",
    "I've used Redis for caching and Celery for background task queues.",
    "I deploy on AWS — mostly ECS for long-running services and Lambda for event-driven jobs.",
]


@autorun
async def main():
    agent = InterviewAgent()
    TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(max_tokens=1000))

    for i, answer in enumerate(ANSWERS, 1):
        await agent.ask(answer)
        n = len(agent.events.query())
        print(f"  {i:<6}  {n:>14}")

    evaluation = await agent.evaluate()
    print(f"\nEvaluation: {evaluation}")
```

For agents that process discrete batches rather than open-ended conversations, `MethodSummarizer` compresses each completed method call's history instead:

```python
from nooa.agents import MethodSummarizer

MethodSummarizer.install(agent)  # Compress after each method call completes
```

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/09_summarization.py
```
</details>

## More Features

The features below follow the same Agent pattern — class attributes, method signatures, docstrings — so there's nothing new to learn.

### Skills

Skills inject curated context (guidelines, examples, domain knowledge) directly into your agent. Skills are included in the core package — no extra install needed.

Attach a skill as an instance attribute—every instance automatically gets that context:

```python
from pathlib import Path

from nooa import TextSkill
from nooa.util.quickstart import *

ASSETS = Path("path/to/skills")  # directory containing skill folders


class FrontendAgent(Agent, llm=llm):
    """Agent with a single file-based skill."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frontend_design = TextSkill(path=ASSETS / "frontend-design")

    async def respond(self, prompt: str) -> str:
        """Respond to a user message."""
        ...


@autorun
async def main():
    agent = FrontendAgent()
    result = await agent.respond("What are the best practices for responsive layout?")
    print(result)
```

You can also discover and activate all skills in a directory with `SkillRegistry` (`from nemo_oo_agents.skill_registry import SkillRegistry`): construct `self.skills = SkillRegistry(self)` in `__init__`, then `self.skills.discover_skills_dirs([skills_dir])` and `self.skills.load(["cmd.*"])` / `self.skills.activate(["cmd.*"])`. Skills are instance attributes — every instance of the agent automatically has the skill context available.

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/10_skills.py
```
</details>

### MCP Tools

MCP (Model Context Protocol) tools let your agent call external services through a standard interface. The TUI (`nemo-labs-oo-agents-cli`) ships MCP support out of the box — it's a hard dependency, so `self.mcp` and the `/mcp` command are always available. In the **core** `nemo-oo-agents` library MCP stays an optional extra (for glibc<2.28 hosts): install with `uv sync --extra mcp` (or `uv add 'nemo-oo-agents[mcp]'`).

For the TUI, declare MCP servers in the same project config file used for
models, skills, and other TUI settings: `.nooa/config.toml`. Use
environment variables for secrets; string values are expanded when the server is
connected.

```toml
[tui]
mcp_auto_connect = ["maas-confluence-stg"]

[tui.mcp_servers.maas-confluence-stg]
url = "https://maas.stg.astra.nvidia.com/maas/confluence/mcp"
transport = "streamable-http"

[tui.mcp_servers.maas-confluence-stg.headers]
Authorization = "Bearer ${MAAS_API_KEY}"
```

At startup the TUI attaches auto-connected servers to the agent using a valid
Python attribute name, so `maas-confluence-stg` becomes
`self.maas_confluence_stg`. You can also connect manually with
`/mcp connect maas-confluence-stg` and inspect configured servers with
`/mcp list`.

A VS Code / Claude-style `.mcp.json` file is still supported via `mcp_file` or
`--mcp-file`; inline `mcp_servers` in `config.toml` override file entries with
the same name.

Inside the TUI, MCP servers are managed through `self.mcp`, an `MCPRegistry`
skill that mirrors `self.skills`:

- `self.mcp.discovered()` — configured server names (from `config.toml` /
  `.mcp.json`).
- `await self.mcp.connect(["name"])` — open a server (and activate it),
  attaching it as `self.<name>`.
- `self.mcp.activate([...])` / `self.mcp.deactivate([...])` — control whether a
  connected server's tools are listed for the agent; a deactivated server keeps
  its authenticated client alive.
- An `<mcp>` context block lists every server each turn under **Active**,
  **Connected but inactive**, or **Configured**.

The same actions are available as a slash command — `/mcp list`,
`/mcp connect <server>`, `/mcp disconnect <server>` — whose output is shown to
you without spending an agent turn.

Outside the TUI (library use), there's no `self.mcp` — call the stateless
`MCPManager` factory directly. Reference the server by name in your agent:

```python
from nooa.mcp import MCPManager

from nooa.util.quickstart import *


class ConfluenceAgent(Agent, llm=llm):
    """Agent with MCP tool access."""

    confluence_tool = MCPManager.create_from_server("maas-confluence-stg")

    async def respond(self, prompt: str) -> str:
        """Respond to a user message using the Confluence MCP tool."""
        ...


@autorun
async def main():
    agent = ConfluenceAgent()
    result = await agent.respond("What are the best practices for claude code?")
    print(result)
```

Alternatively, pass the connection details inline to skip the `.mcp.json` file:

```python
confluence_tool = MCPManager.create_from_server(
    "maas-confluence-stg",
    url="https://maas.stg.astra.nvidia.com/maas/confluence/mcp",
    transport="streamable-http",
    headers={},
)
```

The MCP tool appears alongside your regular methods — the LLM calls it the same way it calls any other `self` method.

<details>
<summary><strong>Show cloned repo command</strong></summary>

```bash
uv run python examples/quickstart/11_mcp.py
```
</details>

### Sandbox

Run agents in isolated, ephemeral compute environments. Install the optional `sandbox` extra first:

```bash
uv add git+https://gitlab-master.nvidia.com/interactive-agents/nooa.git[sandbox] --branch main
```

This installs `openshell` and the `nooa sandbox` CLI wrapper. All sandbox infrastructure is handled automatically — credentials, policy, dependencies — so you can focus on your agent code.

To set up openshell:

```bash
openshell gateway start
```

Refer to the [openshell documentation](https://github.com/NVIDIA/OpenShell) for more information.

**Usage:**

```bash
nooa sandbox -- python agent.py    # run a script
nooa sandbox -- nooa tui       # launch the TUI
nooa sandbox -- bash               # open a shell
```

Requires a `pyproject.toml` in the current directory. Before running your command, the sandbox always:

1. Uploads the current directory
2. Installs dependencies via `uv sync` (picks up the nooa version pinned in your project)

**Uploads:**

Control what gets uploaded with `--upload` (repeatable). Append `:ro` for read-only or `:rw` for read-write (default):

```bash
# upload specific paths instead of the full cwd
nooa sandbox --upload src --upload data -- python agent.py

# make a directory read-only inside the sandbox
nooa sandbox --upload src:ro --upload data -- python agent.py
```

`pyproject.toml` is always included so `uv sync` can run.

**Credentials:**

Inject extra environment variables with `--env` (repeatable). A short-lived credential provider is created automatically and cleaned up after the sandbox exits:

```bash
nooa sandbox --env HF_TOKEN=abc123 -- python agent.py
nooa sandbox --env HF_TOKEN=abc123 --env WANDB_KEY=xyz -- python agent.py
```

**Network:**

Allow additional outbound domains with `--allow-domain` (repeatable):

```bash
nooa sandbox --allow-domain api.myservice.com -- python agent.py
```

For advanced workflows (port forwarding, long-running tasks, connecting to existing sandboxes), use `openshell` directly.

## Advanced Features

Beyond the quickstart, NeMo OO Agents offers these advanced capabilities:

### Self-Extending Agents

Within CodeAct, the LLM can define new helper methods dynamically:

```python
class DataAgent(Agent, llm=llm):
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=20)))
    async def process_dataset(self, data: list[dict]) -> dict:
        """Process dataset. Create helper methods as needed."""
        ...

# LLM can define new methods at runtime:
#     @strategy(PredictStrategy())
#     async def extract_features(self, item: dict) -> dict:
#         """Extract features from a single item."""
#         ...
#     features = [await self.extract_features(item) for item in data]
```

### LLM Cascading Resolution

Configure LLMs at any granularity—from class-wide defaults to per-method overrides—enabling cost optimization (cheap models for simple tasks, powerful models for complex ones), A/B testing, and gradual model rollouts, all without changing your agent's interfaces or business logic:

```python
class MyAgent(Agent, llm=default_llm):             # 1. Class-level default
    @strategy(CodeActStrategy(), llm=special_llm)  # 2. Method-level override
    async def complex_task(self) -> str:
        sub = MySubAgent()                         # 3. Subagent without llm= inherits the
        ...                                        #    calling parent's LLM (any explicit
                                                   #    llm= on the subagent overrides it)

agent = MyAgent(llm=different_llm)                 # 4. Instance-level override
```

Child agents inherit their parent's LLM by default; give a subagent its own `llm=` (class or instance level) to override. Inheritance is resolved at construction from the calling agent, so construct no-`llm` subagents inside a parent agent method — not in `__init__` or at module level.

### Event-Driven History

Subscribe to agent events or query past events:

```python
agent.event_manager.on("message", lambda e: print(f"Message: {e.content}"))
recent = agent.events.query()
```

---

*Start with a simple agent and progressively add complexity only when you need it.*
