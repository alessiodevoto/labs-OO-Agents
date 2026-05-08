# Skills Redesign

## Summary

Skills are the unit of capability composition for agents. This document
describes the redesigned skill lifecycle: discovery → load → activate.

## Goals

1. **Default agent is minimal** — no optional capabilities visible to the LLM.
2. **Explicit opt-in** — developer controls what's loaded and what the LLM sees.
3. **Auto-discovery** — skills from PyPI packages, filesystem dirs, and libraries
   are found automatically; the agent filters what to use.
4. **Generation methods** — `@strategy` methods on Skills route through the
   parent agent's runtime (CodeAct or Predict), as if defined on the agent.

## Skill Lifecycle

```
┌───────────┐      ┌────────┐      ┌───────────┐
│ Discovered │ ──→  │ Loaded  │ ──→  │ Activated  │
└───────────┘      └────────┘      └───────────┘
     broad            filtered         LLM-visible
```

### 1. Discovery (automatic, broad)

Skills are discovered from three sources:

- **Entry points** — `importlib.metadata.entry_points(group='nemo_oo_agents.skills')`
- **Skills directories** — `SkillManager.discover(skills_dirs)` (text + Python skills)
- **Libraries** — `LibraryManager` scans `libs/` for packages with `pyproject.toml`

Entry-point names use `category/skill_name` notation:

```toml
[project.entry-points."nemo_oo_agents.skills"]
"stdskill/shell" = "nemo_oo_agents.tools.shell_tools:ShellTools"
"stdskill/repo" = "nemo_oo_agents.tools.repo_tools:RepoTools"
"superpowers/libwriting" = "nemo_oo_agents.tools.library_writing_lib:LibraryWriting"
```

### 2. Loading (filtered by agent)

The agent controls which discovered skills are instantiated and attached:

```python
class MyAgent(Agent, skills=['stdskill/*']):
    ...  # loads all stdskill category
```

Or explicitly in `__init__`:

```python
self.skills.load(['stdskill/shell', 'superpowers/libwriting'])
```

Special values:
- `skills='*'` — load everything discovered (TUIAgent default)
- `skills=[]` — nothing auto-loaded; fully manual

Skills with constructor args must be constructed manually:

```python
self.shell = ShellTools(cwd=config.working_dir)
self.shell.attach(self)
```

### 3. Activation (LLM visibility)

Loaded skills are hidden from the LLM by default. Activation makes them
visible in `doc(self)`:

```python
self.skills.activate(['stdskill/shell', 'stdskill/repo'])
```

Glob patterns supported:
- `stdskill/*` — activate all in category
- `*` — activate everything loaded

## API

```python
class SkillRegistry:
    """Manages skill discovery, loading, and activation."""

    def discovered(self) -> list[str]:
        """All discovered skill names (category/name)."""

    def loaded(self) -> list[str]:
        """Currently loaded (attached) skill names."""

    def activated(self) -> list[str]:
        """Currently activated (LLM-visible) skill names."""

    def load(self, patterns: list[str]) -> None:
        """Load skills matching patterns from discovered set."""

    def activate(self, patterns: list[str]) -> None:
        """Make loaded skills matching patterns visible to the LLM."""

    def deactivate(self, patterns: list[str]) -> None:
        """Hide activated skills from the LLM (still loaded)."""
```

## Categories

| Category | Skills | Description |
|----------|--------|-------------|
| `stdskill` | shell, repo, context, events, todo | Standard agent tools |
| `superpowers` | libwriting, skills, generation | Advanced capabilities |
| `tui` | brainstorm, tdd, review, ship, root-cause | TUI workflow skills |

## Generation Methods (planned — not in this MR)

Skills will be able to define `@strategy` methods that run on the parent agent's runtime:

```python
class MySkill(Skill):
    @strategy(PredictStrategy())
    async def classify(self, text: str) -> str:
        """Classify {text} as positive/negative/neutral."""
        ...
```

When `skill.attach(agent)` is called, generation methods will be bound so that
`skill.classify("hello")` routes through `agent.runtime`.

> **Reference implementation**: commit `1c0212c1213b6f6c9f50796a6e6d0e0951bf27b4` on branch
> `feat/skill-generation-methods` has a full working implementation of generation
> method binding via `skill_generation.py`. It was removed from this MR to keep
> scope focused on the registry/lifecycle infrastructure.

## Visibility Mechanism

- `is_hidden_field(agent, name)` checks activation state.
- Activated skills pass the visibility check → appear in `doc(self)`.
- `execution_context` no longer renders a Skills table (removed).
- Discovery happens via `doc(self)` and TUI `/help`.

## Slash Commands

Text skills with `user-invocable: true` (default) in their SKILL.md
frontmatter are registered as TUI slash commands. Command names are
normalized to lowercase for case-insensitive matching.

## Migration

1. Existing `spec(self, "context", hidden=False)` calls become
   `self.skills.activate(["stdskill/context"])`.
2. Agents that previously relied on the Skills table in execution_context
   should use `doc(self)` for discovery.
3. `@hidden` annotations on Skill fields are superseded by the
   activate/deactivate mechanism.
