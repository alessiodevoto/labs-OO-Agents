# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""In-memory todo manager for agent task tracking.

Agents use this to plan and track progress through multi-step tasks.
The TodoManager is exposed to the agent's CodeAct REPL via self.todo,
and its API is published to LLM context via doc(self.todo).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from nemo_oo_agents import Skill


class Todo(BaseModel):
    """A single todo item."""

    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex[:8])
    title: str = ""
    status: str = "open"  # open | done | blocked
    deps: list[str] = Field(default_factory=list)
    vars: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    notes: str = ""

    def is_blocked(self, all_todos: dict[str, "Todo"]) -> bool:
        """Return True if any dependency is still open."""
        for dep_id in self.deps:
            dep = all_todos.get(dep_id)
            if dep and dep.status != "done":
                return True
        return False


class TodoManager(Skill):
    """In-memory todo manager with dependency and variable support.

    Use this to plan your work before diving in, and to track progress
    through each step. Update status as you complete each item.

    Example workflow:
        t1 = self.todo.add("Explore repo structure")
        t2 = self.todo.add("Reproduce the failing test", deps=[t1.id])
        t3 = self.todo.add("Implement fix", deps=[t2.id])
        t4 = self.todo.add("Verify tests pass", deps=[t3.id])

        self.todo.done(t1.id)
        print(self.todo.status())
    """

    def __init__(self, state: dict | None = None) -> None:
        self._todos: dict[str, Todo] = {}
        self._order: list[str] = []  # insertion order
        if state:
            self.from_dict(state)

    # ── SERIALIZATION ─────────────────────────────

    def to_dict(self) -> dict:
        """Serialize todo state to a JSON-safe dict."""
        return {
            "todos": [
                t.model_dump() for t in (self._todos[i] for i in self._order if i in self._todos)
            ],
        }

    def from_dict(self, data: dict) -> None:
        """Restore todo state from a dict (e.g. from a snapshot)."""
        self._todos.clear()
        self._order.clear()
        for raw in data.get("todos", []):
            t = Todo.model_validate(raw)
            self._todos[t.id] = t
            self._order.append(t.id)

    # ── CRUD ──────────────────────────────────────

    def add(self, title: str, deps: list[str] | None = None, notes: str = "", **vars: Any) -> Todo:
        """Add a new todo item. Returns the created Todo."""
        t = Todo(title=title, deps=list(deps or []), notes=notes, vars=dict(vars))
        self._todos[t.id] = t
        self._order.append(t.id)
        return t

    def get(self, todo_id: str) -> Todo | None:
        """Get a todo by id."""
        return self._todos.get(todo_id)

    def done(self, todo_id: str) -> Todo | None:
        """Mark a todo as done. Returns the updated Todo or None if not found."""
        t = self._todos.get(todo_id)
        if t:
            t.status = "done"
        return t

    def reopen(self, todo_id: str) -> Todo | None:
        """Re-open a done or blocked todo. Returns the updated Todo or None."""
        t = self._todos.get(todo_id)
        if t:
            t.status = "open"
        return t

    def remove(self, todo_id: str) -> bool:
        """Remove a todo. Returns True if it existed."""
        if todo_id in self._todos:
            del self._todos[todo_id]
            self._order = [i for i in self._order if i != todo_id]
            return True
        return False

    def update(self, todo_id: str, **kwargs: Any) -> Todo | None:
        """Update todo fields (title, status, notes). Returns the updated Todo or None."""
        t = self._todos.get(todo_id)
        if t is None:
            return None
        allowed = {"title", "status", "notes"}
        for k, v in kwargs.items():
            if k in allowed:
                setattr(t, k, v)
        return t

    # ── DEPENDENCIES ──────────────────────────────

    def add_dep(self, todo_id: str, dep_id: str) -> Todo | None:
        """Add a dependency to a todo. Returns the updated Todo or None."""
        t = self._todos.get(todo_id)
        if t and dep_id not in t.deps:
            t.deps.append(dep_id)
        return t

    def remove_dep(self, todo_id: str, dep_id: str) -> Todo | None:
        """Remove a dependency from a todo. Returns the updated Todo or None."""
        t = self._todos.get(todo_id)
        if t and dep_id in t.deps:
            t.deps.remove(dep_id)
        return t

    # ── VARIABLES ─────────────────────────────────

    def set_var(self, todo_id: str, key: str, value: Any) -> Todo | None:
        """Set a variable on a todo (arbitrary metadata). Returns the updated Todo or None."""
        t = self._todos.get(todo_id)
        if t:
            t.vars[key] = value
        return t

    def del_var(self, todo_id: str, key: str) -> Todo | None:
        """Delete a variable from a todo. Returns the updated Todo or None."""
        t = self._todos.get(todo_id)
        if t:
            t.vars.pop(key, None)
        return t

    def get_var(self, todo_id: str, key: str) -> Any | None:
        """Get a variable from a todo. Returns the value or None."""
        t = self._todos.get(todo_id)
        return t.vars.get(key) if t else None

    # ── QUERIES ───────────────────────────────────

    def list_todos(self, status: str | None = None) -> list[Todo]:
        """List todos, optionally filtered by status ('open', 'done', 'blocked')."""
        todos = [self._todos[i] for i in self._order if i in self._todos]
        # Auto-compute blocked status based on deps
        for t in todos:
            if t.status == "open" and t.is_blocked(self._todos):
                t.status = "blocked"
        if status is not None:
            todos = [t for t in todos if t.status == status]
        return todos

    # ── STATUS ────────────────────────────────────

    def status(self) -> str:
        """Return a formatted summary of all todos — call this to see current progress."""
        todos = [self._todos[i] for i in self._order if i in self._todos]
        if not todos:
            return "(no todos)"

        icons = {"open": "○", "done": "✓", "blocked": "●"}
        lines = []
        for t in todos:
            effective_status = t.status
            if t.status == "open" and t.is_blocked(self._todos):
                effective_status = "blocked"
            icon = icons.get(effective_status, "?")
            dep_str = f" [needs: {', '.join(t.deps)}]" if t.deps else ""
            var_str = f" {t.vars}" if t.vars else ""
            notes_str = f"\n    note: {t.notes}" if t.notes else ""
            lines.append(f"  {icon} [{t.id}] {t.title}{dep_str}{var_str}{notes_str}")

        done = sum(1 for t in todos if t.status == "done")
        total = len(todos)
        lines.insert(0, f"Todos ({done}/{total} done):")
        return "\n".join(lines)
