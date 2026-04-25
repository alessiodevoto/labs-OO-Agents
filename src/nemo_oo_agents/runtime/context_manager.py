# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dict-like context manager for agent context blocks.

Provides a simple dict-like API for LLM-generated code to manage
what information appears in the system prompt.

Usage:
    self.context["notes"] = "Here are my notes..."              # static
    self.context.set_dynamic("status", "self.format_status()")  # dynamic
    value = self.context["notes"]                                # read
    del self.context["notes"]                                    # remove
    "notes" in self.context                                      # check

Cache lifecycle for DynamicContext blocks:
    set_dynamic("key", "expr")  → stores DynamicContext in _blocks, invalidates cache
    _prepare_context() runs     → evaluates expr, calls _update_resolved({"key": value})
    self.context["key"]         → returns cached value from _dynamic_cache
"""

from collections.abc import ItemsView, Iterator, KeysView
from typing import Any

from context_blocks import DynamicContext
from context_blocks.exceptions import DynamicNotResolvedError, ProtectedBlockError


class ContextManager:
    """Dict-like API for managing context blocks.

    Stores context blocks as key -> value mappings. Values are either static
    values (any type) or DynamicContext markers (for expressions re-evaluated each turn).

    Single source of truth:
    - Static blocks: value lives in _blocks only. __getitem__ reads from _blocks.
    - DynamicContext blocks: DynamicContext marker in _blocks, resolved value in _dynamic_cache.
      Cache is populated by _update_resolved() after each _prepare_context() run,
      and invalidated on set_dynamic() or __setitem__().

    Framework blocks (system_prompt, self, etc.) are managed separately
    by _prepare_context() and cannot be set here.
    """

    def __init__(self, protected_keys: set[str] | None = None):
        self._blocks: dict[str, Any | DynamicContext] = {}
        self._protected_keys: set[str] = protected_keys or set()
        self._dynamic_cache: dict[str, Any] = {}
        self._immutable: dict[str, bool] = {}

    def __setitem__(self, key: str, value: Any) -> None:
        """Set a static context block.

        Args:
            key: Block key (unique identifier).
            value: Any value. Stored as-is; pprint formatting happens at
                   render time in _prepare_context().
                   For dynamic blocks, use set_dynamic() instead.

        Raises:
            ProtectedBlockError: If key is protected.
            TypeError: If value is a DynamicContext (use set_dynamic instead).
        """
        self.set(key, value)

    def set(self, key: str, value: Any, *, immutable: bool = False) -> None:
        """Set a static context block, with optional immutability declaration.

        Args:
            key: Block key (unique identifier).
            value: Any value. Stored as-is; pprint formatting happens at render time.
            immutable: Declare that this block's content will not change between
                turns. Renderers use this to place it in a cacheable prefix.

        Raises:
            ProtectedBlockError: If key is protected.
            TypeError: If value is a DynamicContext (use set_dynamic instead).
        """
        if key in self._protected_keys:
            raise ProtectedBlockError(key, "modify")

        if isinstance(value, DynamicContext):
            raise TypeError(
                f"Use self.context.set_dynamic({key!r}, {value.expr!r}) "
                f"instead of self.context[{key!r}] = DynamicContext(...)"
            )

        self._blocks[key] = value
        self._immutable[key] = immutable
        self._invalidate(key)

    def set_dynamic(self, key: str, expr: str, *, immutable: bool = False) -> None:
        """Set a dynamic context block that re-evaluates each turn.

        Args:
            key: Block key (unique identifier).
            expr: Python expression to evaluate each turn.
            immutable: Declare that the expression's output is stable across
                turns. Renderers use this to place it in a cacheable prefix.

        Raises:
            ProtectedBlockError: If key is protected.
        """
        if key in self._protected_keys:
            raise ProtectedBlockError(key, "modify")

        self._blocks[key] = DynamicContext(expr, immutable=immutable)
        self._immutable[key] = immutable
        self._invalidate(key)

    def is_immutable(self, key: str) -> bool:
        """Return True if the block was set with immutable=True."""
        return self._immutable.get(key, False)

    def __getitem__(self, key: str) -> Any:
        """Get the value of a context block.

        Static blocks: Returns the original value directly from _blocks.
        DynamicContext blocks: Returns the last resolved value from _dynamic_cache.

        Raises:
            KeyError: If key not found.
            DynamicNotResolvedError: If accessing a DynamicContext block before the
                first LLM turn (expression hasn't been evaluated yet).
        """
        if key not in self._blocks:
            raise KeyError(key)

        value = self._blocks[key]

        # Static block: return directly (single source of truth)
        if not isinstance(value, DynamicContext):
            return value

        # DynamicContext block: return from cache, or raise if not yet resolved
        if key not in self._dynamic_cache:
            raise DynamicNotResolvedError(key, value.expr)
        return self._dynamic_cache[key]

    def __delitem__(self, key: str) -> None:
        """Remove a context block.

        Raises:
            KeyError: If key not found.
            ProtectedBlockError: If key is protected.
        """
        if key not in self._blocks:
            raise KeyError(key)
        if key in self._protected_keys:
            raise ProtectedBlockError(key, "remove")
        del self._blocks[key]
        self._immutable.pop(key, None)
        self._invalidate(key)

    def __contains__(self, key: object) -> bool:
        """Check if a context block exists."""
        return key in self._blocks

    def __len__(self) -> int:
        return len(self._blocks)

    def __iter__(self) -> Iterator[str]:
        return iter(self._blocks)

    def keys(self) -> KeysView[str]:
        """Return block keys."""
        return self._blocks.keys()

    def _raw_items(self) -> ItemsView[str, Any]:
        """Return raw key-value pairs including DynamicContext markers.

        Internal method for context_builder — not part of the LLM-facing API.
        Use keys() + __getitem__ for resolved access.
        """
        return self._blocks.items()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a block value, returning default if not found.

        Like dict.get() — returns default instead of raising KeyError.
        """
        try:
            return self[key]
        except KeyError:
            return default

    def pop(self, key: str, *args: Any) -> Any:
        """Remove and return a block value.

        Like dict.pop() — returns default if provided, raises KeyError otherwise.
        """
        if key not in self._blocks:
            if args:
                return args[0]
            raise KeyError(key)
        if key in self._protected_keys:
            raise ProtectedBlockError(key, "remove")

        # Get value before removal
        raw = self._blocks[key]
        if isinstance(raw, DynamicContext):
            value = self._dynamic_cache.get(key, raw)
        else:
            value = raw

        del self._blocks[key]
        self._immutable.pop(key, None)
        self._invalidate(key)
        return value

    def _invalidate(self, key: str) -> None:
        """Invalidate cached resolved value for a dynamic block.

        Called on set, set_dynamic, delete, and pop to ensure
        stale cache entries are cleared.
        """
        self._dynamic_cache.pop(key, None)

    def _update_resolved(self, resolved: dict[str, Any]) -> None:
        """Cache resolved DynamicContext block values.

        Called by _prepare_context() after evaluating all DynamicContext expressions.
        Only DynamicContext block values should be cached — static blocks are read
        directly from _blocks.
        """
        self._dynamic_cache.update(resolved)
