# Design: Configurable Import Restrictions (v2)

**Date:** 2025-07-24
**Branch:** `feat/configurable-import-restrictions`

## Problem

Currently, the code validator forbids all imports except modules already present in
`exec_globals`. This is overly restrictive — agents can't `import json`, `import csv`,
etc. even though these are perfectly safe.

## Goal

Flip the default to allow most imports. Provide a deny list for dangerous modules.
Developers can customize the deny list per-process.

## 3-Tier Module Restriction Model

```
Tier 1: BLOCKED (hard block)
  - Stripped from exec_globals namespace
  - Blocked at AST import validation
  - Dangerous for event-loop: subprocess, socket, etc.
  - Configured via: blocked_modules + blocked_calls

Tier 2: RESTRICTED (soft block)
  - Blocked at AST import validation only
  - Modules that shouldn't be casually imported but aren't event-loop hazards
  - Configured via: restricted_imports
  - DEFAULT: small set (os, shutil, pathlib, sys, ctypes, importlib)

Tier 3: ALLOWED (everything else)
  - Any installed module can be imported freely
  - json, csv, re, collections, itertools, math, etc.
```

## API Changes

### restrictions.py

```python
# Small default deny list — modules with side effects or security implications
DEFAULT_RESTRICTED_IMPORTS: frozenset[str] = frozenset({
    "os",
    "shutil",
    "pathlib",
    "sys",
    "ctypes",
    "importlib",  # bypass vector for all other restrictions
})

DEFAULT_BLOCKED_CALLS: dict[str, frozenset[str]] = {
    # ... existing entries ...
    "importlib": frozenset({"import_module"}),  # NEW: prevent dynamic bypass
}

class RestrictionsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocked_modules: frozenset[str] = DEFAULT_BLOCKED_MODULES
    blocked_calls: dict[str, frozenset[str]] = DEFAULT_BLOCKED_CALLS
    restricted_imports: frozenset[str] = DEFAULT_RESTRICTED_IMPORTS
```

Usage:
- Default: `RestrictionsConfig()` — blocks event-loop hazards + small deny list
- Open: `RestrictionsConfig(restricted_imports=frozenset())` — allow all imports
- Strict: `RestrictionsConfig(restricted_imports=RESTRICTED_MODULES)` — old behavior

### code_validator.py — ValidationContext

```python
@dataclass
class ValidationContext:
    # ... existing fields ...
    restricted_imports: frozenset[str] = field(default_factory=frozenset)
```

### code_validator.py — SecurityValidator._is_module_available()

```python
def _is_module_available(self, module_name: str) -> bool:
    # Check restricted_imports deny list
    restricted = self.context.restricted_imports
    if restricted:
        if match_blocked_module(module_name, restricted) is not None:
            return False
    # Allow everything not restricted
    return True
```

The old whitelist logic (checking importable_modules) is removed entirely.

### code_validator.py — Error message

```python
def _make_import_error(self, node, module_name) -> ValidationIssue:
    msg = (
        f"import of '{module_name}' is restricted. "
        f"This module is in the restricted_imports deny list. "
        f"Also forbidden: eval(), exec(), compile(), __import__(), "
        f"input(), globals(), locals(), breakpoint()"
    )
    ...
```

### actor.py — execute_code()

```python
# In ValidationContext construction:
context = ValidationContext(
    code=code,
    agent_class=type(self.agent),
    available_names=set(exec_globals.keys()),
    importable_modules=importable_modules,  # kept for compat
    restricted_imports=effective_restrictions.restricted_imports,
    forbidden_self_calls=forbidden_self_calls,
    execution_count=execution_count,
    agent=self.agent,
    exec_globals=exec_globals,
    return_type=return_type,
)
```

### library_writing_lib.py — _lint_source()

```python
# Pass restricted_imports through to the ValidationContext
context = ValidationContext(
    code=source,
    agent_class=type(self._agent),
    available_names=set(),
    importable_modules=importable,
    restricted_imports=self._get_restricted_imports(),
)
```

Where `_get_restricted_imports()` pulls from the agent's runtime restrictions config,
or falls back to `DEFAULT_RESTRICTED_IMPORTS`.

## Files to Change

1. `src/nemo_oo_agents/runtime/restrictions.py` — Add DEFAULT_RESTRICTED_IMPORTS,
   add restricted_imports field, add importlib to blocked_calls
2. `src/nemo_oo_agents/runtime/code_validator.py` — Add restricted_imports to
   ValidationContext, rewrite _is_module_available(), update error message
3. `src/nemo_oo_agents/runtime/actor.py` — Pass restricted_imports to ValidationContext
4. `src/nemo_oo_agents/tools/library_writing_lib.py` — Pass restricted_imports
5. `tests/runtime/test_restrictions.py` — Test RestrictionsConfig with restricted_imports
6. `tests/runtime/test_code_validator.py` — Test SecurityValidator deny-list behavior

## Backwards Compatibility

This is a behavior change: previously only exec_globals modules were importable.
Now most modules are importable by default. This is intentional — the old behavior
was too restrictive. The small DEFAULT_RESTRICTED_IMPORTS list keeps dangerous modules
blocked while allowing common stdlib imports.
