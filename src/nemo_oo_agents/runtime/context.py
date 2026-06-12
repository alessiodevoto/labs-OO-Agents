# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ContextApi — LLM-facing Skill wrapper for agent context blocks.

ContextApi is always present on every Agent as self.context, but hidden from the LLM
by default. Subclasses opt in by calling spec(self, "context", hidden=False) in __init__.

ContextApi wraps agent.context_manager (ContextManager) — the state always lives there.
"""

from collections.abc import Iterator, Set
from typing import TYPE_CHECKING, Any

from nemo_oo_agents.runtime.context_manager import ContextManager
from nemo_oo_agents.skill import Skill

_MISSING = object()

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
        """Set a dynamic context block (placed in volatile suffix).

        Equivalent to ``self.context.set_dynamic(key, value)``.
        """
        self._context[key] = value

    def set_dynamic(self, key: str, expr: str | None = None, *, value: Any = _MISSING) -> None:
        """Set a dynamic context block (placed in the volatile suffix).

        Accepts either an expression (positional, re-evaluated each turn)
        or a plain value (keyword-only ``value=``).

        Args:
            key: Block key.
            expr: Python expression to evaluate each turn.
            value: Plain value to store in the dynamic partition (keyword-only).
        """
        if value is not _MISSING:
            self._context.set_dynamic(key, value=value)
        else:
            self._context.set_dynamic(key, expr)

    def set_static(self, key: str, value: Any = _MISSING, *, expr: str | None = None) -> None:
        """Set a static context block (placed in the cacheable prefix).

        The static/dynamic *partition* and the value *kind* are independent.
        Pass ``value`` for a plain block that doesn't change between turns. Pass
        ``expr`` to get a block re-evaluated every turn that *still* lives in the
        cacheable prefix — the shape framework blocks like ``<self>`` use. (The
        plain ``self.context[key] = ...`` and ``set_dynamic`` paths always land
        in the volatile suffix, so ``expr=`` here is the only way to combine
        re-evaluation with the static section.)

        Args:
            key: Block key.
            value: Plain value to store in the static partition.
            expr: Python expression to evaluate each turn (keyword-only,
                mutually exclusive with ``value``).
        """
        if expr is not None and value is not _MISSING:
            raise TypeError("Cannot specify both value and expr=")
        if expr is not None:
            self._context.set_static(key, expr=expr)
        elif value is not _MISSING:
            self._context.set_static(key, value)
        else:
            raise TypeError("set_static() requires either value or expr=")

    def __getitem__(self, key: str) -> Any:
        # Protected keys are hidden from iteration (__contains__/keys()/len)
        # but readable by name — the LLM already sees their rendered content
        # in the prompt; mutations are guarded by ProtectedBlockError.
        return self._context[key]

    def __delitem__(self, key: str) -> None:
        del self._context[key]

    def __contains__(self, key: object) -> bool:
        return key in self._context and key not in self._context.protected_keys

    def __len__(self) -> int:
        return sum(1 for k in self._context if k not in self._context.protected_keys)

    def __iter__(self) -> Iterator[str]:
        return (k for k in self._context if k not in self._context.protected_keys)

    def keys(self) -> Set[str]:
        """Return user (non-protected) block keys."""
        return self._context.keys() - self._context.protected_keys

    def get(self, key: str, default: Any = None) -> Any:
        """Get a block value, returning default if not found."""
        return self._context.get(key, default)

    def pop(self, key: str, *args: Any) -> Any:
        """Remove and return a block value."""
        return self._context.pop(key, *args)

    def __repr__(self) -> str:
        return f"ContextApi({len(self)} blocks)"
