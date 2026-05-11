# Skills Redesign

## Summary

Skills are the unit of capability composition for agents. This document
describes the redesigned skill lifecycle: discovery → register → activate.

## Goals

1. **Default agent is minimal** — no optional capabilities visible to the LLM.
2. **Explicit opt-in** — developer controls what's loaded and what the LLM sees.
3. **Auto-discovery** — skills from PyPI packages are found via entry points.
4. **Namespace access** — `self.skills.nemo.shell` for qualified disambiguation.

## Skill Lifecycle

```
┌───────────┐      ┌────────────┐      ┌───────────┐
│ Discovered │ ──→  │ Registered  │ ──→  │ Activated  │
└───────────┘      └────────────┘      └───────────┘
  entry points       self.<leaf>          LLM-visible
```

### 1. Discovery (automatic)

Skills are discovered from entry points:

```toml
# In a PyPI package's pyproject.toml:
[project.entry-points."nemo_oo_agents.skills"]
"nemo.shell" = "nemo_oo_agents.tools.shell_tools:ShellTools"
"nemo.repo" = "nemo_oo_agents.tools.repo_tools:RepoTools"
"nemo.todo" = "nemo_oo_agents.tools.todo:TodoManager"
"superpowers.skillwriting" = "nemo_oo_agents.tools.library_writing_lib:SkillWriting"
```

Names use `category.skill_name` notation (dot-separated).

### 2. Registration (explicit)

The agent registers skills it wants to use:

```python
self.skills = SkillRegistry(self)

# From entry points (class known, pass kwargs):
self.skills.register('nemo.shell', cwd=config.working_dir)
self.skills.register('nemo.todo')  # no args needed

# Pre-constructed instance (for complex deps):
self.skills.register('nemo.repo', RepoTools(root='.', session=self.shell._session))

# Custom skill (not in entry points):
self.skills.register('custom.deploy', DeploySkill, env='prod')
```

`register()` does:
1. Instantiate the skill (if class + kwargs)
2. `setattr(agent, leaf_name, skill)` — e.g. `self.shell`
3. `skill.attach(agent)` — lifecycle hook

### 3. Activation (LLM visibility)

```python
self.skills.activate(['nemo.*', 'superpowers.*'])
```

- Activates matching skills (makes visible via `doc(self)`)
- Auto-loads from entry points if not already registered
- Resolves dependencies transitively (with cycle detection)

## API

```python
class SkillRegistry(Skill):
    def register(name, skill_or_cls=None, /, **kwargs): ...
    def activate(patterns: list[str]): ...
    def deactivate(patterns: list[str]): ...
    def reload(name: str | None = None): ...  # hot-reload one or all

    def discovered() -> list[str]: ...
    def loaded() -> list[str]: ...
    def activated() -> list[str]: ...
```

### Namespace Access

```python
self.shell                    # shortcut (set by register)
self.skills.nemo.shell        # fully-qualified (no collisions)
```

The first segment of a dotted access is always a category, returning
a namespace proxy. No bracket access, no leaf shortcuts on `self.skills`.

## Categories

| Category | Skills |
|----------|--------|
| `nemo` | shell, repo, todo, context, events, producers, web |
| `superpowers` | skillwriting, methodwriting |

## Skill Authoring

### SkillWriting (`self.libs`)

Scaffold and manage persistent skill packages:

```python
await self.libs.create('stats', 'Statistical utilities')
await self.shell.write(f'{self.libs.path}/stats/stats.py', source)
self.skills.reload('local.stats')
await self.libs.run_tests('stats')
```

API: `create()`, `run_tests()`, `list()`, `path` property.
File I/O: use `self.shell.write()` / `self.shell.edit()`.
Hot-reload: use `self.skills.reload(name)`.

### Skill Class

```python
from nemo_oo_agents.skill import Skill

class MySkill(Skill):
    \"\"\"My custom tool.\"\"\"
    requires = ('nemo.shell',)  # dependency declarations

    def do_thing(self, arg: str) -> str:
        \"\"\"Do the thing.\"\"\"
        ...
```

### Slash Commands

Text skills with frontmatter become TUI slash commands:

```markdown
---
name: mycommand
description: Do something useful
argument-hint: <action>
---
Body text shown to the agent when /mycommand is invoked.
```

### Sharing Skills

1. **Local**: `self.libs.create()` → edit → reload
2. **Team**: shared git repo → `SkillWriting(self, path='/team/skills')`
3. **Community**: publish as PyPI package with entry points

## Generation Methods (planned — not in this MR)

Skills will be able to define `@strategy` methods that run on the parent
agent's runtime. See commit `1c0212c1` for a reference implementation.

## Visibility Mechanism

- `activate()` calls `spec(agent, attr, hidden=False)` to unhide
- `deactivate()` calls `spec(agent, attr, hidden=True)` to hide
- Activated skills appear in `doc(self)`
- The old Skills table in `execution_context` has been removed

## Agent.__nosnapshot__

The `Agent` base class has `__nosnapshot__ = True` to prevent circular
serialization when skills hold `_agent` references (via `attach()`).
