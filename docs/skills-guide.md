# Skills Guide

Skills are the primary way to extend an agent's capabilities. A skill is any object whose docstring serves as a usage guide for the agent — the framework surfaces it in the agent's execution context automatically.

---

## When to use skills

Use a skill when you want to give the agent access to a capability that:

- Has a reusable API the agent should call (a library, a service, a set of helpers)
- Needs a written guide to explain when and how to use it
- Should be opt-in — not every agent needs it

Don't turn every method into a skill. Regular agent methods (with type annotations and docstrings) are already visible via `doc(self)`. Skills are for *composable, external, or optional* capabilities.

---

## Forms a skill can take

### 1. Python subclass — custom capability with a written guide

```python
from agent006 import Skill

class GitWorkflow(Skill):
    """Git workflow helpers for branching, committing, and reviewing.

    Use this skill when the agent needs to interact with a git repository.

    ## Branch
        self.git.create_branch("feat/my-feature")

    ## Commit
        self.git.commit("fix: correct off-by-one error")

    ## PR
        self.git.open_pr(title="...", body="...")
    """

    def create_branch(self, name: str) -> None: ...
    def commit(self, message: str) -> None: ...
    def open_pr(self, title: str, body: str) -> None: ...
```

Assign it to the agent:

```python
class MyAgent(Agent, llm=llm):
    def __init__(self):
        self.git = GitWorkflow()
```

### 2. Wrap a third-party object — delegate to an existing library

When you want to register a library for discovery, use `Skill(obj)`. It exposes
the library's docstring and attributes via `dir()`. The LLM accesses the library
through the skill attribute (e.g. `self.pd.read_csv()`):

```python
import pandas as pd

class MyAgent(Agent, llm=llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pd = Skill(pd)  # LLM discovers pd via doc(self.pd); uses it as self.pd.<method>
```

For a custom docstring with usage examples, subclass instead:

```python
import pandas as pd

class PandasSkill(Skill):
    """Pandas — DataFrame manipulation and analysis.

    Use this skill to load, filter, transform, and export tabular data.

    ## Load
        df = pd.read_csv("data.csv")

    ## Filter
        filtered = df[df["score"] > 0.5]

    ## Export
        df.to_json("out.json", orient="records")
    """

class MyAgent(Agent, llm=llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pd = PandasSkill(pd)
```

### 3. Textual skill — a SKILL.md file on disk

Textual skills live in directories containing a `SKILL.md` file. Load a single skill with `TextSkill`, or use `SkillManager` to bulk-load a directory:

```python
from pathlib import Path
from agent006 import TextSkill, SkillManager

class MyAgent(Agent, llm=llm):
    def __init__(self):
        self.git = TextSkill(path=Path("skills/git-workflow"))

        # Or load an entire directory at once:
        SkillManager.install(self, skills_dir=Path("skills/"))
        # Each subdirectory with SKILL.md becomes self.<dir_name>
        # e.g. skills/git-workflow/SKILL.md → self.git_workflow
```

`TextSkill` also exposes `run_script()` and `read_file()` for running scripts bundled alongside the SKILL.md.

A `SKILL.md` file has a frontmatter header and a body that becomes the agent's usage guide:

```markdown
---
name: git-workflow
description: Git workflow helpers for branching and committing.
---

## Usage

Use `self.git_workflow` to interact with the repository.

### Create a branch
    self.git_workflow.create_branch("feat/my-feature")
```

---

## How skills surface in context

The agent's execution context automatically includes a `## Skills` table listing all `Skill` instances on the agent:

```
## Skills

BEFORE starting any task, check if any of these skills applies.
You MUST call `doc(self.<skill>)` before using it — do not assume you know the API.

| Skill          | Description                                      |
|----------------|--------------------------------------------------|
| `self.git`     | Git workflow helpers for branching and committing. |
| `self.pd`      | Pandas — DataFrame manipulation and analysis.    |
```

The agent sees only the one-liner. To get the full API, it calls:

```python
doc(self.git)       # prints the full usage guide
doc(self.pd)        # same for pandas
```

To pin a skill's full docs to the execution context for repeated use:

```python
self.context["git_guide"] = doc(self.git)
```

---

## Framework skills — ContextApi and EventsApi

Every agent has two built-in managers, always present and always hidden from the LLM:

| Attribute           | Type             | Purpose |
|---------------------|------------------|---------|
| `context_manager`  | `ContextManager` | Dict-like store for context blocks |
| `event_manager`    | `EventManager`   | Event bus — records all LLM interactions |

`ContextApi` and `EventsApi` are **always present** on every Agent as `self.context` and `self.events`, but **hidden from the LLM by default**. Subclasses opt in by calling `spec(self, "context", hidden=False)` (and/or `spec(self, "events", hidden=False)`) in their `__init__` to expose them via `doc(self)`.

### ContextApi

The agent can use `self.context` to pin information across turns:

```python
self.context["plan"] = doc(self.git)      # pin skill docs
self.context["state"] = "step 2 of 4"     # track progress
del self.context["plan"]                  # remove when done
```

### EventsApi

The agent can query past events by type, tag, or text:

```python
self.events.query(type="tool_call", limit=1)  # most recent tool call
self.events.query(type="error")               # all errors
self.events.query(query="timeout")            # events containing "timeout"
```

---

## Visibility — hide and unhide skills

### Hiding a user skill from the LLM

Annotate with `Annotated[T, hidden]` to prevent a skill from appearing in `doc(self)` and the `## Skills` table:

```python
from typing import Annotated
from agent006 import Skill, hidden

class MyAgent(Agent, llm=llm):
    internal_tool: Annotated[MySkill, hidden]  # hidden from LLM
```

### Summary

| Attribute        | `doc(self)`  | `doc(Agent)`  | Notes                                  |
|------------------|--------------|---------------|----------------------------------------|
| `context`        | hidden       | hidden        | Always present; opt-in with `spec(self, "context", hidden=False)` |
| `events`         | hidden       | hidden        | Always present; opt-in with `spec(self, "events", hidden=False)`  |
| User skills      | **visible**  | visible       | Hide with `Annotated[MySkill, hidden]` |

---

## Docstring conventions

The docstring is the skill's prompt to the agent. Follow this structure:

```markdown
First line — one-liner shown in the ## Skills table.

Second paragraph — when to use this skill (1–3 sentences).

## Section header — group related operations
    example_call()     → what it returns
    another_call(x)    → what it returns

## Gotchas
- Edge cases the agent should know about.
```

Rules:
- First line is the one-liner — keep it under 80 characters
- Write for the agent, not the developer
- Show examples with `→` annotations
- Document gotchas and failure modes explicitly
