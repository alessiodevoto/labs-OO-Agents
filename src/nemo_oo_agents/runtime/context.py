# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ContextApi — LLM-facing Skill wrapper for agent context blocks.

ContextApi is always present on every Agent as self.context, but hidden from the LLM
by default. Subclasses opt in by calling spec(self, "context", hidden=False) in __init__.

ContextApi wraps agent.context_manager (ContextManager) — the state always lives there.
"""

from typing import TYPE_CHECKING, Any

from nemo_oo_agents.runtime.context_manager import ContextManager
from nemo_oo_agents.skill import Skill

if TYPE_CHECKING:
    from nemo_oo_agents.agent import Agent

__all__ = ["ContextApi", "ContextManager"]


class ContextApi(Skill):
    """Dict-like API for managing what appears in your system prompt.

    Use self.context to add, update, or remove named blocks in your prompt.
    Static blocks are set once; dynamic blocks are re-evaluated each LLM turn.
    Framework blocks (system_prompt, self) are protected and cannot be changed.

    Static blocks (set once):
        self.context["notes"] = "Remember: user prefers JSON output"
        self.context["status"] = f"Processed {n} items"

    Dynamic blocks (re-evaluated each turn):
        self.context.set_dynamic("status", "f'Items: {len(self.results)}'")

    Read / check / remove:
        value = self.context["notes"]          # raises KeyError if missing
        value = self.context.get("notes")      # returns None if missing
        "notes" in self.context                # True/False
        del self.context["notes"]              # remove
        self.context.pop("notes", None)        # remove safely

    Examples:
        # Persist notes across turns
        self.context["plan"] = "Step 1: collect, Step 2: summarise"

        # Dynamic status updated every turn
        self.context.set_dynamic("progress", "f'{self.done}/{self.total} done'")

        # Clean up when done
        del self.context["plan"]

    Load this library:
        doc(self.context)
    """

    def __init__(self, agent: "Agent"):
        self._context: ContextManager = agent.context_manager

    def __setitem__(self, key: str, value: Any) -> None:
        self._context[key] = value

    def set_dynamic(self, key: str, expr: str) -> None:
        """Set a dynamic context block that re-evaluates each turn."""
        self._context.set_dynamic(key, expr)

    def __getitem__(self, key: str) -> Any:
        return self._context[key]

    def __delitem__(self, key: str) -> None:
        del self._context[key]

    def __contains__(self, key: object) -> bool:
        return key in self._context

    def __len__(self) -> int:
        return len(self._context)

    def __iter__(self):
        return iter(self._context)

    def keys(self):
        """Return block keys."""
        return self._context.keys()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a block value, returning default if not found."""
        return self._context.get(key, default)

    def pop(self, key: str, *args: Any) -> Any:
        """Remove and return a block value."""
        return self._context.pop(key, *args)

    def __repr__(self) -> str:
        return f"ContextApi({len(self._context)} blocks)"
