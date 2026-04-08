# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unified module restrictions for agent code execution.

Single source of truth for blocked modules, blocked calls, and restricted modules.
Consumed by CodeActConfig (defaults), exec_globals stripping, and BlockingCallValidator.
"""

from __future__ import annotations

import types
from typing import Any

from pydantic import BaseModel, ConfigDict

# Fully blocked — stripped from exec_globals, block the event loop.
# These modules have no legitimate async use in CodeAct.
DEFAULT_BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "subprocess",
        "socket",
        "http.client",
        "urllib.request",
        "ftplib",
        "smtplib",
        "imaplib",
        "poplib",
        "telnetlib",
        "xmlrpc.client",
        "select",
        "signal",
    }
)

# Specific calls blocked on otherwise-allowed modules.
# Keys are module names, values are frozensets of blocked function/method names.
# Dotted names (e.g. "Thread.join") match class-qualified instance method calls.
# Plain names (e.g. "sleep") match module-level function calls.
DEFAULT_BLOCKED_CALLS: dict[str, frozenset[str]] = {
    "time": frozenset({"sleep"}),
    "os": frozenset({"system", "popen", "wait", "waitpid", "waitid"}),
    "threading": frozenset({"Thread.join", "Lock.acquire", "Event.wait", "Condition.wait"}),
    "multiprocessing": frozenset({"Process.join", "Queue.get", "Queue.put"}),
    "asyncio": frozenset({"run", "run_coroutine_threadsafe", "run_until_complete", "run_forever"}),
}


class RestrictionsConfig(BaseModel):
    """Reusable config for code execution restrictions.

    Keeps defaults co-located with the constants they reference.
    Embed in strategy configs (e.g. CodeActConfig) for composition.
    """

    model_config = ConfigDict(frozen=True)

    blocked_modules: frozenset[str] = DEFAULT_BLOCKED_MODULES
    blocked_calls: dict[str, frozenset[str]] = DEFAULT_BLOCKED_CALLS


def match_blocked_module(
    module_name: str,
    lookup: frozenset[str] | dict[str, frozenset[str]],
) -> str | None:
    """Match a module name against a lookup, checking parent modules too.

    Matches exact names and child-of-parent (e.g. "asyncio.runners" matches
    "asyncio" in the lookup). Does NOT match parents of entries — if
    "http.client" is in the lookup, "http" alone does not match.

    Returns the matched key or None.
    """
    if module_name in lookup:
        return module_name
    parts = module_name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:i])
        if parent in lookup:
            return parent
    return None


def is_from_blocked_module(obj: Any, blocked_modules: frozenset[str]) -> bool:
    """Check if an object originates from a blocked module.

    For module objects, checks obj.__name__. For other objects (functions,
    classes), checks obj.__module__. This is safe with the curated
    DEFAULT_BLOCKED_MODULES list, but could over-strip if a broadly-used
    module like "io" were added, since builtins like open() have
    __module__="io".
    """
    if isinstance(obj, types.ModuleType):
        module_name = obj.__name__
    else:
        module_name = getattr(obj, "__module__", None)
    if not module_name:
        return False
    return match_blocked_module(module_name, blocked_modules) is not None


# Restricted — require explicit declaration (allow_imports or normal import).
# Superset of DEFAULT_BLOCKED_MODULES.
RESTRICTED_MODULES: frozenset[str] = frozenset(
    {
        # Blocked modules (all restricted too)
        *DEFAULT_BLOCKED_MODULES,
        # External resource access
        "os",
        "shutil",
        "pathlib",
        "tempfile",
        "glob",
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "http",
        # Database
        "sqlite3",
        "psycopg2",
        "pymongo",
        # LLM SDKs
        "openai",
        "anthropic",
        "litellm",
        # System
        "sys",
        "ctypes",
        "multiprocessing",
        "threading",
    }
)
