# `visible_to_agent` — Agent Namespace Visibility Control

**Date:** 2026-03-09
**Branch:** pfurgale/blocking-call-prevention-design
**Depends on:** Blocking call prevention (same branch)

## Problem

Today, `exec_globals` is built from the **entire agent module `__dict__`** — every top-level import the developer writes is visible to LLM-generated code, unless it happens to be in `blocked_modules`. There is no allowlist. This means:

- Imports intended only for developer helpers leak into the agent's namespace.
- The agent sees a cluttered namespace with names it shouldn't use.
- There's no way to express intent: "this import is for the agent" vs "this import is for me."

Clean context is healthy context. The agent should only see what the developer explicitly makes visible.

## Design

### The Context Manager

`nemo_oo_agents.visible_to_agent` is a module-level context manager that declares which names (imports, constants, helpers) are visible to LLM-generated code.

```python
import nemo_oo_agents
from nemo_oo_agents import Agent

import subprocess  # developer-only — never in exec_globals

with nemo_oo_agents.visible_to_agent:
    import json
    import pandas as pd
    from pathlib import Path
    THRESHOLD = 0.5

class MyAgent(Agent, llm=llm):
    async def analyze(self, data: str) -> str:
        """Analyze {data}. json, pd, Path, THRESHOLD are available."""
        ...
```

### Behavior Rules

1. **Strict by default.** If no `visible_to_agent` block exists on the agent's module, nothing from the module dict enters exec_globals. Only hardcoded builtins are available: `self`, `asyncio`, `typing`, `doc`, `methods`, `variables`, `help`, `pprint`, strategy decorators.

2. **Only names defined inside the block become visible.** Tracked via module dict diff between `__enter__` and `__exit__`.

3. **Multiple blocks are additive.** A second `with visible_to_agent:` adds to the visible set, does not replace it.

4. **`blocked_modules` conflict = startup error.** If a name inside `visible_to_agent` resolves to a module in `blocked_modules`, raise immediately:
   ```
   nemo_oo_agents.ConfigurationError: 'subprocess' is in blocked_modules but was
   declared in visible_to_agent. Import it outside the block (developer-only)
   or remove it from blocked_modules via CodeActConfig.
   ```

5. **`RESTRICTED_MODULES` = warning.** If a restricted module is made visible, log a warning:
   ```
   WARNING: 'os' is a restricted module and has been made visible to agent
   code via visible_to_agent. Specific calls (system, popen, wait, waitpid,
   waitid) are still blocked by BlockingCallValidator.
   ```

6. **Still subject to `blocked_modules` stripping.** The stripping layer in `execute_code` runs after exec_globals construction as a safety net. If a blocked module somehow enters the visible set, stripping catches it.

### Mechanism

```python
# nemo_oo_agents/visibility.py

class _VisibleToAgent:
    """Context manager that tracks names defined inside its block.

    Uses module dict diff: snapshot keys on enter, diff on exit.
    Records visible names on the module as `_nemo_oo_agents_visible_names`.
    """

    def __enter__(self):
        frame = sys._getframe(1)
        self._module = sys.modules[frame.f_globals["__name__"]]
        self._snapshot = set(self._module.__dict__.keys())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        new_names = set(self._module.__dict__.keys()) - self._snapshot
        existing = getattr(self._module, "_nemo_oo_agents_visible_names", set())
        self._module._nemo_oo_agents_visible_names = existing | new_names

        # Validate new names against blocked_modules and restricted_modules
        self._validate_new_names(new_names)
        return False

    def _validate_new_names(self, names):
        from nemo_oo_agents.runtime.restrictions import (
            DEFAULT_BLOCKED_MODULES,
            RESTRICTED_MODULES,
            is_from_blocked_module,
        )

        for name in names:
            obj = self._module.__dict__.get(name)
            if obj is None:
                continue

            # Blocked module conflict → error
            if is_from_blocked_module(obj, DEFAULT_BLOCKED_MODULES):
                module_name = _get_module_name(obj)
                raise ConfigurationError(
                    f"'{name}' (from {module_name}) is in blocked_modules but was "
                    f"declared in visible_to_agent. Import it outside the block "
                    f"(developer-only) or remove it from blocked_modules via "
                    f"CodeActConfig."
                )

            # Restricted module → warning
            module_name = _get_module_name(obj)
            if module_name and match_blocked_module(module_name, RESTRICTED_MODULES):
                import warnings
                warnings.warn(
                    f"'{name}' (from {module_name}) is a restricted module and has "
                    f"been made visible to agent code via visible_to_agent.",
                    stacklevel=3,
                )


# Singleton — same object used across all `with` blocks
visible_to_agent = _VisibleToAgent()
```

Exposed via `nemo_oo_agents/__init__.py`:
```python
from nemo_oo_agents.visibility import visible_to_agent
```

### exec_globals Construction Change (actor.py)

```python
agent_module = inspect.getmodule(type(self.agent))
visible_names = getattr(agent_module, "_nemo_oo_agents_visible_names", None)

if visible_names is not None:
    # Allowlist mode: only include visible names from module dict
    exec_globals = {
        name: agent_module.__dict__[name]
        for name in visible_names
        if name in agent_module.__dict__
    }
else:
    # No visible_to_agent block: empty module dict (strict default)
    exec_globals = {}

# Add hardcoded builtins (self, asyncio, doc, strategies, etc.)
exec_globals.update({
    "self": self.agent,
    "asyncio": asyncio,
    "typing": _typing,
    "doc": doc,
    "methods": methods,
    "variables": variables,
    "help": doc,
    "pprint": pprint,
    "strategy": strategy,
    # ... strategy classes ...
})

# Add strategy builtins (reasoning, message, method args)
if builtins:
    exec_globals.update(builtins)

# Strip blocked modules (safety net)
exec_globals = _strip_blocked_modules(exec_globals, effective_blocked)
```

### What Changes for Existing Agents

This is a **breaking change by design**. Existing agents without `visible_to_agent` blocks will have empty module-level exec_globals (builtins only). To migrate:

1. Add `with nemo_oo_agents.visible_to_agent:` around the imports the agent needs.
2. Move developer-only imports outside the block.

### Relationship to Blocking Call Prevention

| Layer | Purpose | Mechanism |
|---|---|---|
| `visible_to_agent` | Allowlist: what CAN enter exec_globals | Module dict diff |
| `blocked_modules` stripping | Denylist: what MUST NOT be in exec_globals | `is_from_blocked_module()` |
| `BlockingCallValidator` | AST check: specific calls on allowed modules | Runtime name resolution |

All three layers compose. `visible_to_agent` is the first gate (allowlist), stripping is the safety net (denylist), and the validator catches fine-grained call patterns on modules that passed both gates.
