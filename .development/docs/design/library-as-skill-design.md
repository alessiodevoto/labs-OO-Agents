# Library-as-Skill Design

**Date:** 2026-03-10
**Branch:** feat/library-as-skill

---

## Overview

Skills are composable capabilities that agents opt into. An agent sees a skill as a single attribute — a brief one-liner in `doc(self)` — and can call `doc(self.<skill>)` to read the full usage guide.

There are three ways to create a skill. All three produce objects that are `isinstance` of `agent006.Skill`, so the runtime discovers and renders them uniformly.

---

## Usage

### 1. Python subclass — custom capabilities

Subclass `Skill` from `agent006`. Write the docstring for the agent, not for developers.

```python
from agent006 import Skill

class MethodWriting(Skill):
    """Define persistent helper methods on the agent.

    Add this skill when the agent needs reusable helpers across cells.

    ## Plain helper functions
        def celsius_to_fahrenheit(c):
            return c * 9/5 + 32
        result = celsius_to_fahrenheit(100)
        return_result(result)

    ## LLM sub-methods (use @strategy)
        @strategy(StructuredOutputStrategy())
        async def classify(self, text: str) -> str:
            \"\"\"Classify as positive, negative, or neutral.\"\"\"
            ...
    """

class MyAgent(Agent, llm=llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.method_writing = MethodWriting()
```

### 2. Wrap a third-party library

Wrap any Python object to surface it as a skill. The wrapped object's `__doc__` becomes the skill docstring.

```python
import pandas as pd
from agent006 import Skill

class MyAgent(Agent, llm=llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pd = Skill(pd)                # uses pandas' own __doc__
```

Or write a custom docstring:

```python
class PandasLib(Skill):
    """Pandas library for data manipulation and analysis.

    ## Examples
    self.pd.read_csv("data.csv")     → DataFrame
    self.pd.merge(df1, df2, on="id") → joined DataFrame
    """
    def __getattr__(self, name):
        return getattr(pd, name)
```

### 3. Text skills from SKILL.md files

`TextSkill` and `SkillManager` are part of `agent006` and support loading skills from directories.

```python
from agent006 import TextSkill, SkillManager
```

**Load a single skill by path:**

```python
skill = TextSkill(path=Path("skills/git-workflow"))
# skill.id            → "git-workflow"
# skill.description   → "Git workflow helpers"
# type(skill).__doc__ → formatted content for the agent (description + body)
```

**Bulk-load a directory with `SkillManager`** — scans subdirectories containing `SKILL.md` and attaches them as agent attributes:

```python
class MyAgent(Agent, llm=llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Scans skills/ and sets self.git_workflow, self.frontend_design, etc.
        self._skills = SkillManager.install(self, skills_dir=Path("skills/"))
```

`SkillManager.install` calls `setattr(agent, name, TextSkill(path=...))` for each discovered skill directory. The `install` classmethod name makes the mutation explicit — it installs skills onto the agent.

After init, `agent.git_workflow`, `agent.frontend_design`, etc. are available as skills (hyphens become underscores).

**SKILL.md format:**

```
skills/
  git-workflow/
    SKILL.md        ← must contain this file
```

```markdown
---
name: git-workflow
description: Best practices for Git operations
---

# Git Workflow Guide

When working with Git:
1. Always create feature branches
2. Write clear commit messages
3. Test before pushing
```

---

## How skills surface in context

At runtime, `doc(self)` scans for `Skill` attributes (via `dir()`, including class-level attrs) and renders a summary table followed by usage instructions:

```
## Skills

| Skill          | Description                                       |
|----------------|---------------------------------------------------|
| method_writing | Define persistent helper methods on the agent.   |
| pd             | Pandas library for data manipulation and analysis.|
| git_workflow   | Best practices for Git operations                 |

**Usage:**
- Inspect in REPL: `print(doc(self.<skill>))`
- Pin to context: `self.context["<skill>"] = doc(self.<skill>)`   ← only shown when context is visible
- Unpin: `del self.context["<skill>"]`                            ← only shown when context is visible
```

The pin/unpin instructions are shown only when `context` is visible on the agent. By default, `context` does not exist on the agent — it is opt-in. Create it in `__init__` to expose it to the LLM:

```python
class MyAgent(Agent, llm=llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.context = ContextApi(self)  # opt in
```

### Progressive disclosure for complex libraries

`Skill.__dir__` forwards to the wrapped object, so `dir(self.pd)` returns the full pandas namespace. The LLM can drill in progressively:

```python
doc(self.pd)                # module overview — one-liner
doc(self.pd.DataFrame)      # full class API — 133+ methods with signatures
dir(self.pd)                # list all attributes on the wrapped object
```

`doc(self.pd.DataFrame, concise=True)` renders every method with its signature and a one-liner docstring — the right level of detail for the LLM to use the API without hallucinating.

---

### Where does `Skill` live inside `agent006`?

`Skill` is defined in `agent006/skill.py` and exported from `agent006.__init__` — the same level as `Agent`, `hidden`, and `visible`. This placement reflects its role as a first-class public API type rather than an internal detail.

The reasoning: `ContextApi` and `EventsApi` are runtime objects that inherit from `Skill`. This means `Skill` must be defined *below* the runtime layer so runtime modules can import it without circularity. Placing it in `agent006/skill.py` (no dependencies on the rest of the framework) satisfies this: `runtime/context.py` and `runtime/events.py` import `from agent006.skill import Skill` cleanly.

### Generation Methods on Skills

Skills do not currently support generation methods (methods with `...` as the body). Skills are context and capability holders — the agent methods that *use* them are where generation happens. If a skill needs LLM-powered sub-tasks, define those as generation methods on the agent itself. Generation methods on skills will be supported in a future iteration.
