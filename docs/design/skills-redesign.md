# Skills Redesign — Design Document

## Goal

Skills are decoupled PyPI-installable packages **on top of** (not in) the framework, with dependency resolution, activate/deactivate, and progressive disclosure.

## Problems Today

- Skills are coupled with `execution_context` + special handling in `doc(self)` / `doc(AgentClass)`
- The coupling makes it not extensible — can't have flat skills on one agent and searchable skills on another
- No way to signal dependencies between skills
- `SkillManager` + `LibraryManager` are two separate systems that do similar things
- No activate/deactivate lifecycle

## Design Decisions

### Packaging & Discovery

1. **Skills = real PyPI packages** (`uv install nemo-skill-foo`)
2. **Discovery via entry points** (`importlib.metadata`, group `"nemo_oo_agents.skills"`)
3. **Two-phase install**: `uv add` installs the package; agent config controls which are active (default = all installed)
4. **One package can register multiple skills** — e.g. `nemo-oo-agents` ships shell, repo, todo, brainstorm, tdd as built-in skills via multiple entry points
5. **Agent-authored libraries** (`self.libs`) produce package skills with the same shape

### Loading Paths

Two only:

| Path | Shape | Use when |
|------|-------|----------|
| **Built-in skill** | Part of `nemo-oo-agents` (entry points) | Core runtime capabilities: events, context, skills, jobs |
| **Package skill** | `pyproject.toml` + entry points | Code, dependencies, multiple commands, programmatic handlers |
| **Directory text skill** | `SKILL.md` with frontmatter | Prompt-only, no code, no pip deps, implicitly depends on shell |

No middle ground (no loose `.py` files in a directory). If you need code, make a package.

### Skill Base Class Contract

```python
class Skill:
    """One-liner for the skill table (first line of docstring).
    
    Full documentation rendered by doc(self.<skill>).
    Method signatures, usage examples, behavioral notes.
    """
    id: str
    requires: list[str] = []   # other skill IDs needed for activation
    
    # path property is on TextSkill only, not the base class
    
    def attach(self, agent) -> None:
        """Called when activated. Receives agent reference."""
        ...
    
    def detach(self) -> None:
        """Called on deactivate. Cleanup."""
        ...
    
    def get_context_blocks(self) -> dict[str, str | DynamicContext | None]:
        """Context blocks this skill contributes when active.
        
        Called once on activate. DynamicContext values are
        re-evaluated every turn. Static strings set once.
        """
        return {}
```

### Documentation Convention

- **No separate `description` field** — follows oo-agents docstring convention
- First line of class docstring = one-liner for the skill table
- Rest of docstring = full docs for `doc(self.<skill>)`
- Public methods are introspected automatically by `doc()`

### Slash Commands

Slash commands are **text macros**. `@slash_command` decorates async methods that **return the prompt string**:

```python
class GitLabSkill(Skill):
    """GitLab integration — CI, MRs, issues."""
    requires = ["shell"]
    
    @slash_command("gl-ci", argument_hint="<pipeline-id>")
    async def monitor_ci(self, args: str) -> str:
        """Monitor a CI pipeline."""
        return f"Monitor CI pipeline {args}. Call spawn(...)."
    
    @slash_command("gl-issues")
    async def list_issues(self, args: str) -> str:
        """List open issues for a project."""
        issues = await self.fetch_open_issues(args)
        formatted = "\n".join(f"- #{i.id}: {i.title}" for i in issues)
        return f"Open issues for {args}:\n{formatted}\nTriage these."
```

**Flow**: `/gl-ci 12345` → framework calls `await skill.monitor_ci("12345")` → pastes returned string as user message → LLM responds.

- Method can do programmatic work (API calls, file reads) before returning
- Method docstring = description for help/tab-completion
- Framework auto-activates the skill when the command fires

### Activation Model

- **Both LLM and user can activate** skills
- `user_only=True` on `@slash_command` gates destructive operations (user must explicitly invoke)
- Activation resolves dependencies transitively (activating `tdd` auto-activates `shell` + `todo`)
- Deactivation blocked if other active skills depend on this one

### Progressive Disclosure

Three levels:

1. **Table row** (always visible) — one-liner per available skill, enough to know it exists
2. **Context block** (optional, on activate) — persistent short content via `get_context_blocks()`
3. **`doc(self.<skill>)`** (on demand) — full method signatures and docs

The skill table is just a context block rendered by `SkillsManager`. Swappable — different implementations can do static table, dynamic promotion, semantic search, etc.

### Composition

- Skills can have **sub-skills as members** (Skill instances as attributes)
- Sub-skills are an internal composition detail — the framework only sees top-level skills
- No framework-level sub-skill activation; parent manages its own progressive disclosure

### Context Blocks

Skills use `get_context_blocks() → dict[str, str | DynamicContext | None]`:
- Same return type as strategy's `get_block_overrides()`
- Called once on activate (registers block definitions)
- `DynamicContext(expr)` values re-evaluated every turn by existing runtime machinery
- Static `str` values set once and stay constant
- Skill can also use `self.context[key]` directly for mid-session dynamic changes

### Text Skills Always Require Shell

All text skills implicitly require `"shell"` — a text skill without shell access is useless (the LLM can't execute anything). The loader auto-injects `requires = ["shell"]` for directory text skills.

### Two Dependency Graphs

| | `pyproject.toml` dependencies | `Skill.requires` |
|---|---|---|
| **What** | Python packages | Other skill IDs |
| **Resolved by** | `uv`/`pip` at install time | SkillsManager at activation time |
| **Example** | `dependencies = ["python-gitlab>=4.0"]` | `requires = ["shell", "events"]` |
| **Failure** | `pip install` fails | `self.skills.activate()` fails |

They're orthogonal. A skill's `pyproject.toml` can reference other PyPI packages that contain skills — `uv` installs them, and their entry points become available to the SkillsManager. `Skill.requires` controls activation ordering within a running agent.

```toml
# pyproject.toml for nemo-skill-gitlab
[project]
dependencies = ["python-gitlab>=4.0", "nemo-skill-shell>=0.1"]
#                ↑ Python package dep    ↑ another skill package (brings entry points)
```

### Directory Text Skills

SKILL.md frontmatter declares metadata:

```yaml
---
name: brainstorm
description: Elicit requirements before design
requires:
  - shell
  - todo
---
## Brainstorm Workflow

1. Create an umbrella todo...
2. Ask questions...
```

The loader generates an equivalent Skill instance with a `@slash_command` method that returns the body with `$ARGUMENTS` substituted. Limited to text-only; upgrade to package skill for anything programmatic.

### SkillsManager

`SkillsManager` is itself a Skill that manages other skills:

```python
class MyAgent(Agent, llm=llm):
    skills = SkillsManager(
        active=["shell", "todo", "repo"],     # start active
        include=["shell", "todo", "repo", "tdd", "brainstorm"],  # available
        dirs=["./skills", "~/.nemo/skills"],  # directory text skills
    )
```

**API:**

```python
class SkillsManager(Skill):
    def list(self) -> list[SkillInfo]: ...
    def active(self) -> list[str]: ...
    def activate(self, skill_id: str) -> None: ...
    def deactivate(self, skill_id: str) -> None: ...
    def reload(self, skill_id: str) -> None: ...
```

- Reload is explicit only (`self.skills.reload(id)`)
- `self.libs` triggers reload automatically after writes
- Developer configures `include`/`exclude`/`active` to control what's visible

### Runtime Surface

- Same skill works in TUI or headless — the runtime decides how to surface commands
- `path` property is on text skills only (they need it so prompts can reference `{self.<skill>.path}/scripts/...`). Package skills don't expose `path` — their code handles its own assets internally.

### Migration

No backward compatibility needed. Aggressive plan:
1. Build new `Skill` base class + `@slash_command` + `get_context_blocks()`
2. Build `SkillsManager`
3. Port built-in skills (`ShellTools` → `ShellSkill`, etc.)
4. Port text skills (add `requires` to frontmatter)
5. Wire `SkillsManager` into agent init
6. Fix `doc(self)` to read from `SkillsManager`
7. Delete old `skill_manager.py`, `library_manager.py`

---

## Open Discussion: Generation Methods on Skills

### The Question

Can a Skill provide **generation methods** — methods decorated with `@strategy(...)` that call the LLM?

**Decision: Yes.** Skills can give agents new generation methods. When a skill is activated, any `@strategy`-decorated methods become callable as `self.<skill>.<method>(...)`.

### The Unsolved Problem: Free vs Agent

Generation methods have two orthogonal axes:
- **Strategy** (how): `PredictStrategy`, `CodeActStrategy`, etc.
- **Context** (what it sees): Free (no event history) vs Agent (sees conversation history)

We haven't decided how a skill author **marks up** which one a method is. Options discussed:

1. **Separate decorators**: `@generation` (free) vs `@agent_generation`
2. **Parameter on `@strategy`**: `@strategy(PredictStrategy(), context="agent")`  
3. **Default = free, opt-in agent**: `@strategy(PredictStrategy(), agent=True)`
4. **Convention**: methods on Skills are always free; methods on Agents see history

### Motivating Example: A Fully-Loaded Todo Skill

Could the Todo skill include all of:
- The todo database (data structure + methods)
- The Doer sub-agent factory (`make_doer()`)
- The `do_it()` agent generation method for self-orchestration

```python
class TodoSkill(Skill):
    """In-memory todo manager with dependency and variable support."""
    requires = ["shell"]
    
    # --- Data / tool methods (plain Python) ---
    def add(self, title: str, deps=None) -> Todo: ...
    def done(self, id: str) -> None: ...
    def status(self) -> str: ...
    
    # --- Sub-agent factory ---
    def make_doer(self) -> DoerAgent: ...
    
    # --- Agent generation method: orchestrates work on a todo ---
    @strategy(CodeActStrategy())  # free? agent? how to mark?
    async def do_it(self, todo: Todo) -> str:
        """Execute this todo item using self.shell, self.repo, etc."""
        ...
    
    # --- Context blocks ---
    def get_context_blocks(self):
        return {"todo_status": DynamicContext("self.todo.status()")}
```

Installing this one skill gives an agent: data management, orchestration, sub-agents, context blocks. All in one package.

### Even More Subversive: Could `handle()` Be a Skill?

Today, `handle()` is the agent's main loop — the `@strategy(CodeActStrategy())` method that processes each turn. What if the main orchestration loop were itself provided by a skill?

```python
class OrchestratorSkill(Skill):
    """Main conversation loop with async job management."""
    requires = ["shell", "todo", "skills"]
    
    @strategy(CodeActStrategy())
    async def handle(self, notification: dict) -> RespondResult:
        """Handle a single turn of the conversation..."""
        ...
```

This would mean:
- The "how to respond to a user message" logic is swappable
- Different agents could install different orchestrator skills (a coding agent vs a research agent vs a chat agent)
- The agent class becomes almost empty — just a configuration of which skills to activate
- Async job management (`queue_manager`, `spawn`, etc.) could be a skill too

**The agent reduces to**: an LLM + a set of skills + a strategy for the main loop.

This is maximally "on top not in" — even the core behavior is a skill. Whether we want to go this far is a design taste question. The framework would need to support one "primary" skill whose generation method IS the agent's main loop.

### Questions to Resolve

1. How to mark free vs agent generation methods (syntax/decorator)
2. Should `do_it()` / `make_doer()` be on the Todo skill or separate?
3. How far down the "everything is a skill" rabbit hole do we go? Is `handle()` a skill? Are queues/producers a skill?
4. If Skills can have generation methods, do they need access to the LLM client? How is it provided? (Via `attach(agent)` → `self._agent.llm`?)


## Addendum: Built-in Skills & The "OO Stdlib"

### Everything is a Skill

Capabilities previously hidden in the runtime (events, context, queues) are promoted to full skills. They show up in the skill table, are doc()-able, and can be activated/deactivated like any other skill.

The built-in "stdlib" skills:

| Skill ID | Provides | Description |
|----------|----------|-------------|
| `events` | `self.events` | Query and manage agent event history |
| `context` | `self.context` | Manage context blocks in the system prompt |
| `skills` | `self.skills` | Discover, activate, deactivate skills (the SkillsManager) |
| `jobs` | `self.jobs` | Background job spawning and monitoring |

These are registered by the runtime automatically. A minimal agent might only activate `events` + `context`. A full TUI agent activates everything.

### Requiring a Capability = Activating its LLM Surface

When a skill declares `requires = ["events"]`, this means:
- The events skill must be active (LLM can see and use `self.events`)
- Its methods appear in `doc(self.events)`
- Its context block (if any) is injected

This is the same as requiring any other skill — there's no special "virtual capability" concept. Built-in skills are just skills that happen to be provided by the runtime rather than an installed package.

### Grouping: Jobs + Handle

`jobs` (background job management) groups naturally with the conversation loop:
- `self.jobs.spawn(producer, channel)` — start a background job
- `self.jobs.status()` — check running jobs
- The dispatcher uses jobs/queues to deliver notifications to `handle()`

Whether `handle()` itself lives on the agent class or becomes part of a skill is TBD — for now it stays on the agent, but jobs is its own activatable skill.

### Motivating Example: Todo Skill (Revised)

```python
class TodoSkill(Skill):
    """In-memory todo manager with dependency and variable support."""
    id = "todo"
    requires = ["shell", "events", "context"]
    
    # --- Data management ---
    def add(self, title: str, deps=None) -> Todo: ...
    def done(self, id: str) -> None: ...
    def status(self) -> str: ...
    def get_var(self, id: str, key: str) -> Any: ...
    def set_var(self, id: str, key: str, value: Any) -> None: ...
    
    # --- Execution machinery ---
    def make_doer(self) -> DoerAgent:
        """Build a fresh DoerAgent for isolated execution of a todo item."""
        ...
    
    @strategy(CodeActStrategy())  # generation method (free vs agent TBD)
    async def do_it(self, todo: Todo) -> str:
        """Execute a single todo item using available tools."""
        ...
    
    # --- Context ---
    def get_context_blocks(self):
        return {"todo_status": DynamicContext("self.todo.status()")}
```

Installing `todo` gives: database, planner, sub-agent factory, self-orchestration, context blocks. One skill, complete functionality.
