# TodoManager

Built-in tool for agent self-planning. State persists across turns (snapshot-backed), so the agent can plan upfront, execute step-by-step, and check its own progress.

## When to use it

- Multi-step workflows where the agent must **not skip steps** (e.g., gather → analyze → verify → report)
- Long-running tasks where the agent's context may be summarized and it needs to remember what's pending
- Sub-question tracking in research/debugging tasks ("what did I already look into?")

## When NOT to use it

- Single-turn classification or extraction -- overhead not worth it
- Short tasks where the LLM can keep the whole plan in the current turn's context

## Attaching TodoManager

```python
from nemo_oo_agents.tools.todo import TodoManager

class MyAgent(Agent, llm=llm):
    def __init__(self):
        super().__init__()
        self.todo = TodoManager()

    async def run(self, task: str) -> str:
        """Plan the work with self.todo, then execute step by step."""
        ...
```

## Core workflow

The LLM typically plans upfront, then works through the list:

```python
# Plan (in generated CodeAct code)
t1 = self.todo.add("Explore repo structure")
t2 = self.todo.add("Reproduce failing test", deps=[t1.id])
t3 = self.todo.add("Implement fix", deps=[t2.id])
t4 = self.todo.add("Verify tests pass", deps=[t3.id])

# Execute
# ... do exploration work ...
self.todo.done(t1.id)
print(self.todo.status())    # shows remaining todos
```

## Tracking sub-questions and reasoning

Attach metadata and comments to todos to preserve context across turns:

```python
q = self.todo.add("Which sources contradict the main claim?")
self.todo.set_var(q.id, "candidate", "source_3.pdf, page 12")
self.todo.comment(q.id, "source_3 uses different dataset -- not a true contradiction")
self.todo.done(q.id)
```

`comments` is a journal -- append-only -- while `set_var` stores structured metadata.

## API reference

| Method | Returns | Description |
|---|---|---|
| `add(title, deps=[], notes="", **vars)` | `Todo` | Create a new todo. `deps` lists blocking todo IDs. `vars` are stored metadata. |
| `done(id)` | `Todo \| None` | Mark done. Returns `None` if id not found. |
| `reopen(id)` | `Todo \| None` | Mark pending again. |
| `update(id, **kwargs)` | `Todo \| None` | Update `title`, `status`, `notes`. |
| `set_var(id, key, value)` | `Todo \| None` | Store metadata on the todo. |
| `get_var(id, key)` | `Any` | Read metadata. |
| `comment(id, body)` | `TodoComment \| None` | Append a journal entry. |
| `comments(id)` | `list[TodoComment]` | Read all journal entries. |
| `list_todos(status=None)` | `list[Todo]` | List all or filter by status. |
| `status()` | `str` | Human-readable summary of progress. |

All read methods (`list_todos`, `status`, `get_var`, `comments`) are idempotent. All mutating methods return the affected object or `None` -- never raise on missing id.

## Snapshot integration

TodoManager state is serialized by `SQLiteStorageManager` automatically. After a restart:

```python
from nemo_oo_agents.storage import SQLiteStorageManager

storage = SQLiteStorageManager("agent_state.db")
agent = MyAgent(storage=storage)  # todos restored from last snapshot
```

The agent resumes with its full todo list, including completed/pending status and comments.

## Pattern: mandatory steps

Combine with the multi-phase pattern to enforce step sequencing while giving the LLM flexibility within each step:

```python
async def run(self, task: str) -> Result:
    """Work through the required phases in order."""
    # Plan (deterministic, enforced)
    t1 = self.todo.add("Gather data")
    t2 = self.todo.add("Analyze", deps=[t1.id])
    t3 = self.todo.add("Verify", deps=[t2.id])

    # Execute -- each phase is a generation method that updates todos as it goes
    data = await self.gather()
    self.todo.done(t1.id)
    analysis = await self.analyze(data)
    self.todo.done(t2.id)
    result = await self.verify(analysis)
    self.todo.done(t3.id)
    return result
```

## Pitfalls

- **Over-planning.** Creating 20 todos for a 3-step task -- the LLM spends time managing the list instead of doing work. Start with 3-5 todos; the LLM can add more as needed.
- **Using todos as comments.** If it's just a note, call `self.todo.comment(...)`. Don't create a `"Note: XYZ"` todo that never gets marked done.
- **Ignoring `deps`.** Without dependencies, the LLM may tackle todos in the wrong order. Add `deps` for any step that truly requires another to finish first.
