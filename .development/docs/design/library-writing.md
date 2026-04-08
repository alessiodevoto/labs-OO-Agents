# Library Writing

## Goals

1. The agent can write, edit, test, and reuse persistent Python libraries across sessions.
2. The capability is a **Skill** — discoverable via `doc(self)`, operable on the same object (`self.libs`).
3. `write_file()` and `edit_file()` auto-load the library after a clean lint pass — no separate load call needed. The module is registered in `sys.modules` and injected as a bare name into exec_globals.
4. Libraries are standard Python packages with no forced file layout — any structure that makes the package importable is accepted.
5. A `LibrarySkill` is attached to the agent as a documentation handle; the callable surface is the module itself.

## Core idea

A library is a Python package on disk. `LibraryWriting` is the agent's tool for creating and editing it. `LibraryManager` loads it at startup and reloads it after edits. `LibrarySkill` is what the agent sees in `doc(self)` — a description of what the library does.

The agent writes code; the framework handles the import machinery.

---

## Three classes, three roles:

| Class | Role |
|---|---|
| `LibraryWriting` | Create, edit, test, and discover libraries |
| `LibraryManager` | Scan the libs directory on startup; hot-reload after edits |
| `LibrarySkill` | Documentation handle — the loaded library as a Skill |

---

## Agent setup

```python
class DataAgent(Agent, llm=llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.libs = LibraryWriting(self)
        # All previously created libraries are auto-loaded and available
```

`LibraryWriting(self)` installs a `LibraryManager` internally — no separate setup needed.

---

## `LibraryWriting` — the agent-facing API

### Lifecycle

```python
# 1. Scaffold the package
await self.libs.create("stats", "Statistical utilities for numerical data.")

# 2. Write code in any file name you like
await self.libs.write_file("stats", "stats.py", source)
# → lints, writes, hot-reloads; stats is now available in exec_globals

# 3. Use directly (no import needed)
result = stats.percentile(my_data, 95)

# 4. Edit
await self.libs.edit_file("stats", "stats.py", old_block, new_block)

# 5. Test
await self.libs.write_file("stats", "tests/test_stats.py", test_source)
await self.libs.run_tests("stats")
```

### Discovery

```python
await self.libs.list()           # sorted library names
await self.libs.repo_tree()      # directory tree of the libs root
await self.libs.grep(pattern)    # search across all library files
await self.libs.view_file("stats", "stats.py")  # read a file
```

### `create(lib_name, description)`

Writes two files and nothing else:

- `pyproject.toml` — name, version (`0.1.0`), description, empty `dependencies = []`
- `__init__.py` — `"""<description>"""\n`

The package is importable immediately (empty module). Code comes from `write_file()`.

### `write_file(lib_name, path, content)`

Behaviour depends on `path`:

| Path | Behaviour |
|---|---|
| `*.py` (not `__init__.py`) | Lint → write if no hard errors → hot-reload if clean. Includes test files (e.g. `tests/test_foo.py`). |
| `__init__.py` | Write directly (star re-exports are allowed) |
| `pyproject.toml` | Write → validate declared dependencies |
| anything else (e.g. `.md`, `.json`) | Write, no linting |

Returns a `LintReport` string for `.py` and `pyproject.toml`; a plain confirmation otherwise.

### `edit_file(lib_name, path, search_block, replace_block)`

Same post-write behaviour as `write_file()` — lints and reloads `.py` files, validates deps for `pyproject.toml`.

---

## `LintReport`

```python
@dataclass
class LintReport:
    written: bool = False
    loaded:  bool = False
    errors:  list[str]  # hard errors — file not written
    warnings: list[str] # soft issues — written, not loaded
```

`str(report)` returns one of:
- `OK — written and loaded`
- `WARNING — written but not loaded` (+ warning lines)
- `ERROR — file not written` (+ error lines)

### Lint rules

**`.py` files** — `SecurityValidator` only (no REPL/CodeAct policies):

| Code | Meaning | Severity |
|---|---|---|
| E001 | Forbidden builtin (`exec`, `eval`, …) | Hard error — not written |
| E002 | Import not in agent's allowed modules | Warning — written, not loaded |
| E003 | Star import (except `__init__.py`) | Hard error — not written |

**`pyproject.toml`** — each entry in `dependencies` is checked against the agent's importable modules. An undeclared or unavailable dep → E002 warning.

---

## `LibraryManager` — loading and hot-reload

`LibraryWriting` creates a `LibraryManager` internally, but it can also be used standalone — for example, to load libraries onto an agent that doesn't use `LibraryWriting`:

```python
mgr = LibraryManager.install(agent)
# agent.stats, agent.utils, ... are now set

mgr.reload()                        # reload every installed library
LibraryManager.discover(path)       # list library names without loading
```

On startup it scans `~/.nemo_oo_agents/<AgentClass>/libs/` (or an explicit `libs_dir`), finds every subdirectory with a `pyproject.toml`, imports the package, and sets it on the agent as `agent.<lib_name>`.

Hot-reload is triggered automatically after a clean `write_file()` or `edit_file()`. To reload all libraries manually (e.g. after an out-of-band edit):

```python
self.libs._libmgr.reload()
```

---

## `LibrarySkill` — the documentation handle

Each loaded library is attached to the agent as a `LibrarySkill`. It is documentation-only — the callable module lives in `sys.modules[lib_name]` and in exec_globals as a bare name.

```python
doc(self.stats)             # shows the __init__.py docstring + __dir__
stats.percentile(data, 95)  # call via the module, not via self.stats
```

`LibrarySkill.__init__` clears the package and all its submodules from `sys.modules` before re-importing — this ensures edits to any file in the package are picked up, not just `__init__.py`.

---

## Package layout

Libraries are standard Python packages. Any layout that makes the package importable is accepted:

```
~/.nemo_oo_agents/DataAgent/libs/
└── stats/
    ├── pyproject.toml   ← required — marks the directory as a library
    ├── __init__.py      ← required — module docstring = library description
    └── stats.py         ← agent-chosen name; sub-packages are fine too
```

`pyproject.toml` is the sentinel used by both `LibraryManager._scan()` and `LibraryWriting.list()`.

### No install step

Libraries are not installed into the project `.venv`. The libs root is added to `sys.path` at `LibraryWriting.__init__` time — `import stats` resolves directly. `pyproject.toml` is metadata only.

---

## What the LLM sees

```
## Skills
| Skill | Description                                              |
|-------|----------------------------------------------------------|
| libs  | Write persistent Python libraries that survive across sessions. |
| stats | Statistical utilities for numerical data.                |
```

```python
doc(self.stats)
# Statistical utilities for numerical data.
#
# - percentile(values, p) → p-th percentile
# - mean(values) → arithmetic mean
```

---

## Files

| File | Contents |
|---|---|
| `src/nemo_oo_agents/library_skill.py` | `LibrarySkill` |
| `src/nemo_oo_agents/library_manager.py` | `LibraryManager` |
| `src/nemo_oo_agents/tools/library_writing_lib.py` | `LintReport`, `LibraryWriting` |
| `src/nemo_oo_agents/tools/__init__.py` | Exports `LibraryWriting` |
| `src/nemo_oo_agents_cli/tui/agent.py` | `self.libs = LibraryWriting(self)` in `__init__` |
| `tests/tools/test_library_writing_lib.py` | Tests for all three classes |
