# Make `mcp` an optional dependency

Issue: https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/189

## Problem

`mcp>=1.0.0` is a hard dependency in `pyproject.toml`. Its dep chain
(`mcp → pydantic ≥2.7 → pydantic-core ≥2.18`) only ships
`manylinux_2_28` wheels for `pydantic-core`, which SIGSEGV on Ubuntu 16.04
(glibc 2.23). Removing `mcp` from the core deps lets pip resolve `pydantic`
on the 2.5.x line where older-glibc wheels exist.

## Why this is safe (verified against current tree)

- `import nemo_oo_agents` does NOT import `nemo_oo_agents.mcp` at top level
  (verified: `src/nemo_oo_agents/__init__.py` contains no `mcp` import).
- All MCP-using sites already guard the import:
  - `packages/nemo-oo-agents-cli/.../tui/commands.py:550-554` —
    `try: from nemo_oo_agents.mcp import MCPManager / except ImportError`
    with message `"MCP not enabled. Run `uv sync --extra mcp` and restart."`
    (already references the not-yet-existing extra).
  - `examples/quickstart/11_mcp.py:19-22` — `try/except ImportError` raising
    a clear "run `uv sync --extra mcp`" message.
  - `examples/assets/wiki_mcp_server.py` — imports `mcp`, but this is a
    standalone example server, not a package import path.
  - `src/nemo_oo_agents/strategies/codeact.py:2164` — string comment only,
    no runtime import.
- CI runs `uv sync --all-extras --no-extra sandbox` (4 occurrences in
  `.gitlab-ci.yml`); `--all-extras` picks up new optional groups
  automatically, so the existing `tests/test_mcp/` suite keeps running
  in CI without further changes.

## Plan

### 1. `pyproject.toml`

- Remove `"mcp>=1.0.0"` from `[project].dependencies` (line 26).
- Add to `[project.optional-dependencies]`:
  ```toml
  mcp = ["mcp>=1.0.0"]
  ```
- Replace **only line 76** — the stale comment
  `"# mcp is now nemo_oo_agents.mcp (deps in main dependencies)"` —
  with a note pointing at the new extra. Do NOT touch lines 77–86, which
  document the CLI install path and nemo-flow workaround.

No other edits to `pyproject.toml` are required. CI invocations stay
unchanged because `--all-extras` already includes new groups.

### 1b. `uv.lock`

Run `uv lock` after editing `pyproject.toml` and commit the result.
The resolution will shift (the core deps may settle on an older pydantic
line now that `mcp` no longer forces ≥2.7) so the lockfile must reflect
that — otherwise `uv sync` in CI will rewrite it on every run.

### 2. Gate MCP tests on the dep being installed

Add at the top of both test modules (after the file docstring, before
existing imports):

```python
import pytest
pytest.importorskip("mcp")
```

Files:
- `tests/test_mcp/test_client.py`
- `tests/test_mcp/test_tool.py`

`pytest.importorskip` raises `pytest.skip` at collection time when the
import fails, so the suite is skipped (not errored) on installs without
the extra. `tests/test_mcp/__init__.py` does not need the skip — it has
no test-bearing code and no `mcp` import.

### 3. README.md install note (lines ~678–699)

The existing MCP section says *"MCP support is included in the core package
— no extra install needed."* That becomes false. Replace that sentence with
an install instruction pointing at the `mcp` extra
(`uv add 'nemo-oo-agents[mcp]'` or `uv sync --extra mcp`).

While editing this paragraph, also correct the adjacent stale import on
line 699 (`from mcp_nemo_oo_agents import MCPManager` →
`from nemo_oo_agents.mcp import MCPManager`). It is broken today and
sits in the section we're rewriting; fixing it is a one-line addition,
not a refactor.

### 4. No source-code changes

`src/nemo_oo_agents/mcp/{client,oauth,tool}.py` already import `mcp`
unconditionally; that is correct — the submodule is only loaded on
explicit import (`from nemo_oo_agents.mcp import …`), and the two
external call sites already wrap that in `try/except ImportError`.

## Files to touch

| File | Change |
|---|---|
| `pyproject.toml` | move `mcp` from `dependencies` → `optional-dependencies.mcp`; refresh comment on line 76 |
| `uv.lock` | regenerate with `uv lock` |
| `README.md` | replace "no extra install needed" sentence with `uv sync --extra mcp` note; fix adjacent stale import |
| `tests/test_mcp/test_client.py` | add `pytest.importorskip("mcp")` |
| `tests/test_mcp/test_tool.py` | add `pytest.importorskip("mcp")` |

## Verification

1. `uv lock` produces an updated lockfile; commit it.
2. `uv sync` (no extras) succeeds; `uv run python -c "import nemo_oo_agents"` works.
3. `uv sync --extra mcp` installs `mcp`; `uv run python -c "from nemo_oo_agents.mcp import MCPManager"` works.
4. With `mcp` not installed (e.g. via a fresh venv without the extra),
   `pytest tests/test_mcp/` reports skipped, not failed.
5. With `mcp` installed, `pytest tests/test_mcp/` still runs the full suite.
6. `uv run ruff check` and `uv run ruff format --check` pass.

## Non-goals / out of scope

- No changes to MCP runtime behaviour.
- No changes to CI commands (`--all-extras` already picks up the new group).
- No deprecation of the `mcp` submodule itself — only its install path.

## Risk / rollback

Rollback is a single revert of `pyproject.toml` + the two test files.
The change is purely declarative (dependency metadata) and does not
modify any importable runtime code path.
