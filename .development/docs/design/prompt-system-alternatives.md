# Alternative Prompt System Designs

Exploring more elegant approaches than "dataclass with 15 fields".

---

## Option A: Dataclass per Strategy (Current Plan)

```python
@dataclass
class PurePythonPrompts:
    task: str = DEFAULT_TASK
    error_empty: str = "..."
    error_syntax: str = "..."
    error_method: str = "..."
    feedback_not_done: str = "..."
    # ... 6+ fields
```

**Pros:**
- Type-safe, IDE autocomplete
- Easy to understand which prompts exist
- Each field is independently swappable

**Cons:**
- Verbose - many fields per strategy
- Hard to see the "shape" of the prompt system
- Duplicates structure across strategies (PythonTask copies PurePython)

---

## Option B: Single PromptSet with Categories

```python
@dataclass
class PromptSet:
    """All prompts organized by category."""

    task: str  # Main instructions
    errors: dict[str, str]  # "empty" -> "...", "syntax" -> "..."
    feedback: dict[str, str]  # "not_done" -> "...", "with_output" -> "..."


# Usage
prompts = PromptSet(
    task=PURE_PYTHON_TASK,
    errors={
        "empty": "Empty response...",
        "syntax": "Syntax error...",
        "return_outside": "Return outside function...",
    },
    feedback={
        "not_done": "Define {method} to complete.",
        "with_output": "Output:\n{stdout}\n\n{not_done}",
    },
)

# In strategy
self.prompts.errors["syntax"].format(method=name)
```

**Pros:**
- Fewer top-level fields
- Categories make organization clear
- Same structure works for all strategies

**Cons:**
- Loses type safety on dict keys (typo = KeyError at runtime)
- IDE can't autocomplete error names

---

## Option C: Protocol + Composable Defaults

```python
class PromptProvider(Protocol):
    """Interface for providing prompts."""

    def get_task_prompt(self) -> str: ...
    def format_error(self, error_type: str, **kwargs) -> str: ...
    def format_feedback(self, feedback_type: str, **kwargs) -> str: ...


class DefaultPurePythonPrompts:
    """Default implementation - can subclass to override specific parts."""

    TASK = """## PURE_PYTHON Mode..."""

    ERRORS = {
        "empty": "Empty response...",
        "syntax": "Syntax error...",
    }

    def get_task_prompt(self) -> str:
        return self.TASK

    def format_error(self, error_type: str, **kwargs) -> str:
        template = self.ERRORS.get(error_type, "Unknown error")
        return template.format(**kwargs)


# Custom prompts via subclass
class MinimalPrompts(DefaultPurePythonPrompts):
    TASK = """Output Python code. Define the method."""  # Shorter!
```

**Pros:**
- Override only what you need
- Class hierarchy shows relationships
- Methods can have logic (not just templates)

**Cons:**
- More complex than dataclass
- Inheritance can get confusing with multiple levels

---

## Option D: YAML/External Files

```yaml
# prompts/pure_python/v1.yaml
task: |
  ## PURE_PYTHON Mode

  **Output Format**: Your entire response must be valid Python code.
  ...

errors:
  empty: "Empty response. Output Python code directly."
  syntax: "Syntax error - ensure you output a complete method definition."
  return_outside: |
    This error means you output a return statement without defining the function.
    You MUST define the complete method.

feedback:
  not_done: "Define `{method}` to complete the task."
```

```python
class PromptSet:
    @classmethod
    def load(cls, path: str) -> "PromptSet":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)

# Usage
prompts = PromptSet.load("prompts/pure_python/v1.yaml")
```

**Pros:**
- Prompts are data, not code - easy to version/compare
- Non-engineers can edit prompts
- Natural fit for A/B testing variants
- Git diff shows prompt changes clearly

**Cons:**
- Runtime errors if YAML is malformed
- No IDE support for template variables
- Extra files to manage
- **YAML escaping is painful** for prompts:
  - `:` looks like a key (`**Format**: text` breaks)
  - `{method}` braces can confuse parsers
  - `#` starts comments
  - Must use `|` for multiline, adds visual noise

---

## Option E: Jinja2 Templates

```python
from jinja2 import Environment, PackageLoader

env = Environment(loader=PackageLoader("nemo_oo_agents", "prompts"))

class PurePythonPrompts:
    def __init__(self, version: str = "v1"):
        self.env = env
        self.version = version

    def get_task(self) -> str:
        return self.env.get_template(f"pure_python/{self.version}/task.jinja2").render()

    def format_error(self, error_type: str, **ctx) -> str:
        return self.env.get_template(f"pure_python/{self.version}/errors/{error_type}.jinja2").render(**ctx)
```

```jinja2
{# prompts/pure_python/v1/errors/syntax.jinja2 #}
Syntax error at line {{ line_number }}.

{% if suggestion %}
Suggestion: {{ suggestion }}
{% endif %}

Define the full method as shown in 'Method Information' section.
```

**Pros:**
- Full templating power (conditionals, loops, includes)
- Reuse common snippets across strategies
- Industry standard for templates

**Cons:**
- Heavy dependency for simple string formatting
- Overkill for most prompts
- Learning curve for Jinja2 syntax

---

## Option F: Builder Pattern

```python
class PromptBuilder:
    """Fluent API for constructing prompts."""

    def __init__(self):
        self._parts = []

    def section(self, title: str, content: str) -> "PromptBuilder":
        self._parts.append(f"## {title}\n\n{content}")
        return self

    def rule(self, text: str) -> "PromptBuilder":
        self._parts.append(f"- {text}")
        return self

    def example(self, title: str, code: str) -> "PromptBuilder":
        self._parts.append(f"**{title}:**\n```python\n{code}\n```")
        return self

    def build(self) -> str:
        return "\n\n".join(self._parts)


# Usage
task_prompt = (
    PromptBuilder()
    .section("Output Format", "Your entire response must be valid Python code.")
    .section("Interaction Pattern", "This is a code execution loop...")
    .rule("No import statements")
    .rule("Define the complete method")
    .example("Correct", "async def add(self, a, b):\n    return a + b")
    .build()
)
```

**Pros:**
- Programmatic construction
- Reusable building blocks
- Self-documenting structure

**Cons:**
- Harder to see the final prompt at a glance
- More code than just a string
- Doesn't help with error/feedback prompts

---

## Option G: Enum + Registry

```python
class PromptType(Enum):
    TASK = "task"
    ERROR_EMPTY = "error.empty"
    ERROR_SYNTAX = "error.syntax"
    ERROR_RETURN = "error.return_outside"
    FEEDBACK_NOT_DONE = "feedback.not_done"


class PromptRegistry:
    """Central registry for all prompts."""

    _prompts: dict[str, dict[PromptType, str]] = {}

    @classmethod
    def register(cls, strategy: str, prompt_type: PromptType, template: str):
        cls._prompts.setdefault(strategy, {})[prompt_type] = template

    @classmethod
    def get(cls, strategy: str, prompt_type: PromptType, **kwargs) -> str:
        template = cls._prompts[strategy][prompt_type]
        return template.format(**kwargs)


# Registration (at module load)
PromptRegistry.register("pure_python", PromptType.TASK, PURE_PYTHON_TASK)
PromptRegistry.register("pure_python", PromptType.ERROR_EMPTY, "Empty response...")

# Usage
msg = PromptRegistry.get("pure_python", PromptType.ERROR_SYNTAX, method="foo")
```

**Pros:**
- Enum gives type safety for prompt types
- Central registry - easy to list all prompts
- Strategies don't need to hold prompt state

**Cons:**
- Global mutable state (registry)
- Registration order matters
- More indirection

---

## Option H: Plain Python Modules (No YAML)

```python
# prompts/pure_python_v1.py
"""Pure Python prompts - version 1 (original)."""

TASK = """## PURE_PYTHON Mode (Code Execution Loop)

**Output Format**: Your entire response must be valid Python code. No markdown, no fences.
...
"""

ERRORS = {
    "empty": "Empty response. Output Python code directly. Define `{method}` to complete.",
    "syntax": "Syntax error - ensure you output a complete method definition.",
    "return_outside": """This error means you output a return statement without defining the function.
You MUST define the complete method:
```python
async def {method}(...):
    return ...
```""",
}

FEEDBACK = {
    "not_done": "Define `{method}` to complete the task.",
}
```

```python
# prompts/pure_python_v2_minimal.py
"""Pure Python prompts - version 2 (minimal)."""

TASK = """Output valid Python code. Define the target method to complete."""

ERRORS = {
    "empty": "Empty response. Define `{method}`.",
    "syntax": "Syntax error. Define complete method.",
}

FEEDBACK = {
    "not_done": "Define `{method}`.",
}
```

```python
# Usage
from prompts import pure_python_v1 as prompts
# or
from prompts import pure_python_v2_minimal as prompts

class PurePythonStrategy:
    def __init__(self, prompts_module=None):
        self.prompts = prompts_module or pure_python_v1
```

**Pros:**
- No escaping issues - it's just Python strings
- IDE support (syntax highlighting, autocomplete)
- Type checking works
- Easy imports, no file I/O
- Git diff still shows changes clearly
- Can use f-strings, triple quotes naturally

**Cons:**
- "Prompts as code" (but is that really bad?)
- Need Python knowledge to edit (but you do anyway)

---

## Option I: Intent + Format Separation (POML/DSPy-inspired)

The key insight from POML and DSPy: **separate what you want from how you say it**.

- **POML**: Semantic tags (`<task>`, `<example>`) + CSS-like styling for format
- **DSPy**: Signatures define intent, compiler generates optimal prompt

### Approach: Intent Dataclass + Format Renderer

```python
# prompts/intents.py
from dataclasses import dataclass, field

@dataclass
class PurePythonIntent:
    """WHAT the model needs to understand (not HOW to say it)."""

    # Core behavior
    output_format: str = "valid Python code, no markdown fences"
    interaction_model: str = "code execution loop with feedback"
    completion_signal: str = "define target method with full signature"

    # Constraints
    forbidden: list[str] = field(default_factory=lambda: [
        "import statements",
        "partial implementations",
        "raw return statements outside function",
    ])

    # Available tools
    builtins: dict[str, str] = field(default_factory=lambda: {
        "reasoning": "chain-of-thought (not shown to user)",
        "message": "send message to user",
        "print": "debug output (returned to you)",
        "self": "the agent instance",
    })

    # Error guidance (intent, not exact wording)
    error_guidance: dict[str, str] = field(default_factory=lambda: {
        "empty_response": "remind to output code",
        "syntax_error": "remind to define complete method",
        "return_outside_fn": "explain they need function wrapper",
    })


@dataclass
class StructuredOutputIntent:
    """Intent for structured output strategy."""

    output_format: str = "JSON matching return type schema"
    completion_signal: str = "valid Pydantic model"

    error_guidance: dict[str, str] = field(default_factory=lambda: {
        "validation_error": "show field errors, remind of schema",
    })
```

```python
# prompts/formatters.py
from abc import ABC, abstractmethod

class PromptFormatter(ABC):
    """Renders intent into actual prompt text."""

    @abstractmethod
    def render_task(self, intent: PurePythonIntent) -> str: ...

    @abstractmethod
    def render_error(self, intent: PurePythonIntent, error_type: str, **ctx) -> str: ...


class VerboseFormatter(PromptFormatter):
    """Detailed prompts with examples - good for weaker models."""

    def render_task(self, intent: PurePythonIntent) -> str:
        sections = []

        sections.append(f"## Output Format\n\n{intent.output_format}")

        sections.append("## Interaction Pattern\n\n" + intent.interaction_model)

        if intent.forbidden:
            forbidden_list = "\n".join(f"- {f}" for f in intent.forbidden)
            sections.append(f"## Forbidden\n\n{forbidden_list}")

        if intent.builtins:
            builtin_list = "\n".join(f"- `{k}` - {v}" for k, v in intent.builtins.items())
            sections.append(f"## Available Builtins\n\n{builtin_list}")

        # Add example for verbose mode
        sections.append(self._render_example())

        return "\n\n".join(sections)

    def render_error(self, intent: PurePythonIntent, error_type: str, **ctx) -> str:
        guidance = intent.error_guidance.get(error_type, "fix and retry")
        # Verbose: expand guidance into full explanation
        return self._expand_error_guidance(guidance, error_type, **ctx)


class MinimalFormatter(PromptFormatter):
    """Terse prompts - good for strong models, saves tokens."""

    def render_task(self, intent: PurePythonIntent) -> str:
        # One-liner version
        forbidden = ", ".join(intent.forbidden[:2])  # Just top 2
        return f"{intent.output_format}. No {forbidden}. {intent.completion_signal}."

    def render_error(self, intent: PurePythonIntent, error_type: str, **ctx) -> str:
        # Minimal error message
        return f"Error: {error_type}. {intent.error_guidance.get(error_type, 'Fix it.')}".format(**ctx)


class XMLFormatter(PromptFormatter):
    """POML-style semantic tags - some models respond well to XML structure."""

    def render_task(self, intent: PurePythonIntent) -> str:
        return f"""<output_format>{intent.output_format}</output_format>

<interaction>{intent.interaction_model}</interaction>

<forbidden>
{chr(10).join(f'  <rule>{f}</rule>' for f in intent.forbidden)}
</forbidden>

<builtins>
{chr(10).join(f'  <fn name="{k}">{v}</fn>' for k, v in intent.builtins.items())}
</builtins>

<completion>{intent.completion_signal}</completion>"""
```

```python
# Usage in strategy
class PurePythonStrategy:
    def __init__(
        self,
        intent: PurePythonIntent | None = None,
        formatter: PromptFormatter | None = None,
    ):
        self.intent = intent or PurePythonIntent()
        self.formatter = formatter or VerboseFormatter()

    @property
    def strategy_prompt(self) -> str:
        return self.formatter.render_task(self.intent)

    def _emit_error(self, error_type: str, **ctx) -> str:
        return self.formatter.render_error(self.intent, error_type, **ctx)
```

### Benefits

1. **Intent is stable**: `PurePythonIntent` rarely changes - it's the "spec"
2. **Format is tunable**: Swap formatters without changing intent
3. **A/B test formats**: Same intent, different renderers
4. **Model-specific optimization**: Strong models get `MinimalFormatter`, weak models get `VerboseFormatter`
5. **Semantic structure**: Intent dataclass is machine-readable

### For Optimization

```python
# Optimize format, not intent
for formatter in [VerboseFormatter(), MinimalFormatter(), XMLFormatter()]:
    strategy = PurePythonStrategy(formatter=formatter)
    results = run_capability_tests(strategy)
    print(f"{formatter.__class__.__name__}: {results.pass_rate}")
```

### Tradeoffs

**Pros:**
- Clean separation of concerns
- Intent is self-documenting (dataclass fields)
- Can optimize format independently
- Different formatters for different models

**Cons:**
- Two abstractions instead of one
- Formatter logic can get complex
- Intent changes require updating all formatters
- More indirection than plain strings

---

## Recommendation

### Option J: Unified Config Dataclass (IMPLEMENTED)

All configuration in a single dataclass. PythonTaskStrategy is now just `task_message_mode=True`.

```python
# pure_python.py - all config in one dataclass

from dataclasses import dataclass

@dataclass
class PurePythonConfig:
    """Configuration for PurePythonStrategy. Override any field to customize."""

    # Execution limits
    max_iterations: int = 10
    max_retries: int = 3

    # Mode: False = prompt in system, True = prompt in task message
    task_message_mode: bool = False

    # Task templates (for task_message_mode)
    initial_task: str = "{instructions}\n\n{agent_doc}\n\n{task}\n\n{method_info}\n\n{current_call}"
    condensed_task: str = "{task}\n\n{method_info}\n\n{current_call}"

    # Prompts
    instructions: str = """## PURE_PYTHON Mode (Code Execution Loop)
...(existing prompt)...
"""
    error_empty: str = "Empty response. Output Python code directly. Define `{method}` to complete."
    error_syntax: str = "**Syntax error - ensure you output a complete method definition.**..."
    error_return_outside: str = "**This error means you output a return statement...**"
    error_method_raised: str = "Method `{method}` raised error:\n```\n{error}\n```\nFix and redefine `{method}`."
    feedback_not_done: str = "Define `{method}` to complete the task."


class PurePythonStrategy(GenerationStrategy):
    def __init__(self, config: PurePythonConfig | None = None):
        self.config = config or PurePythonConfig()

    @property
    def strategy_prompt(self) -> str:
        if self.config.task_message_mode:
            return ""
        return self.config.instructions
```

**To customize:**
```python
# Override specific fields
minimal = PurePythonConfig(
    instructions="Output Python code. Define the target method.",
    error_empty="Empty. Define `{method}`.",
)
strategy = PurePythonStrategy(config=minimal)

# Task message mode (replaces PythonTaskStrategy)
strategy = PurePythonStrategy(config=PurePythonConfig(task_message_mode=True))
```

**Why this wins:**
- Zero new files
- All config in one place (execution limits + prompts + mode)
- Consolidated PythonTaskStrategy into `task_message_mode=True`
- IDE autocomplete for all config fields
- Easy to override: just pass a different dataclass instance

---

## Comparison Matrix

| Approach | Type Safety | Versioning | Simplicity | No Escaping | New Files |
|----------|-------------|------------|------------|-------------|-----------|
| A. Dataclass per strategy | High | Low | Medium | Yes | Yes |
| B. Single PromptSet | Medium | Low | High | Yes | Yes |
| C. Protocol + inheritance | High | Low | Low | Yes | Yes |
| D. YAML files | Low | High | Medium | No | Yes |
| E. Jinja2 templates | Low | High | Low | No | Yes |
| F. Builder pattern | Medium | Low | Low | Yes | Maybe |
| G. Enum + Registry | High | Low | Medium | Yes | Yes |
| H. Python modules | Medium | High | High | Yes | Yes |
| I. Intent + Formatter | High | High | Medium | Yes | Yes |
| **J. Inline Dataclass** | **High** | **Medium** | **High** | **Yes** | **No** |

**Winner: Option J** - Dataclass in same file as strategy. Simple, no new files, easy to customize.
