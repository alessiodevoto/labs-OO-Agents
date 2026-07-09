# Issue 259 — CodeAct exec_globals ignores parent class module

## Motivation

An `Agent` subclass whose parent lives in a **different module** could not use the parent
module's module-level symbols (functions, constants, types) inside `execute_python()`. The
sharpest way to see the bug: an **un-overridden** inherited generation method behaved
differently depending on the **runtime type of the instance it ran on**.

```python
# parent_mod.py
SHARED = 7
def shared_util(x): return x * 2                 # module-level helper

class ParentAgent(Agent, llm=llm):
    async def parent_task(self) -> int:
        """... generates code that calls shared_util(SHARED) ..."""
        ...

# child_mod.py  (DIFFERENT file)
class ChildAgent(ParentAgent, llm=llm): ...      # inherits parent_task unchanged
```

```python
await ParentAgent().parent_task()   # type(self)=ParentAgent → globals from parent_mod → shared_util present ✓
await ChildAgent().parent_task()    # type(self)=ChildAgent  → globals from child_mod  → shared_util MISSING ✗ NameError
```

Same method, same code, no override — yet one instance works and the other raises `NameError`.

## Root cause

Every place that builds generated-code globals took only the **leaf** class's own module via
`inspect.getmodule(type(self))` + `filter_module_globals(module)`, never walking the MRO. So
the REPL namespace was keyed off the *runtime type's* module, not the module where the running
method is *defined*. Put parent + child in the same file and it works (one module); the
cross-module case is the natural "shared base agent" pattern.

Method dispatch itself was never broken — `await self.parent_task()` resolves fine. The gap is
strictly the **module-level names referenced inside generated code**. (Parent methods/fields
already surface via `doc(type(self))` MRO flattening, and parent method signature types already
appear under "Referenced Types" — so scope is just ancestor-module globals.)

## Affected sites (all shared the single-leaf-module pattern)

1. `runtime/actor.py:1238-1239` — REPL `exec_globals` (the primary bug).
2. `strategies/codeact.py:_extract_module_context` (≈2330) — feeds the `<execution_context>`
   "Available types/functions" display and `_build_builtins`.
3. `strategies/codeact.py:execution_context` (≈377) — categorizes defined-vs-imported using a
   single `agent_module.__name__`.
4. `strategies/generated_code.py:60-61` — `ExecutionNamespaceBuilder.build`, documented to
   "mirror the effective globals used by `ActorRuntime.execute_code()`", so it must stay in sync.

Out of scope: `tools/library_writing_lib.py:_importable_modules` (dependency-linting helper, not
exec_globals / prompt context).

## Design

Two shared helpers in `agentdoc/_visibility.py` (re-exported from `agentdoc/visibility.py`,
stubs in `_visibility.pyi`):

```python
def iter_agent_mro_modules(agent_class) -> list[types.ModuleType]:
    """Distinct user-defined modules across the MRO, base → leaf order.
    Skips framework (nooa*) and builtins modules; the leaf module is
    always included. De-duplicated, base-most position retained."""

def filter_mro_module_globals(agent_class) -> dict[str, Any]:
    """Merge filter_module_globals across iter_agent_mro_modules, base → leaf,
    so the leaf module wins on name collisions."""
```

- **Leaf-wins ordering:** iterate `reversed(agent_class.__mro__)` (base first, leaf last) and
  `result.update(filter_module_globals(module))` per module → leaf overrides on collision,
  matching Python's own name resolution.
- **Skip rule** for an ancestor module `m` (never the leaf): `m is None` or `m.__name__` is
  `builtins` / `nooa` / starts with `nooa.`.

Site changes:

1. **actor.py** & **generated_code.py** — replace the leaf-only call with
   `filter_mro_module_globals(type(agent))` (the helper handles the empty case).
2. **codeact.py `_extract_module_context`** — when an agent is provided, build globals from
   `filter_mro_module_globals(type(agent))` and compute
   `own_module_names = {m.__name__ for m in iter_agent_mro_modules(type(agent))}`; the
   defined-vs-imported check becomes `obj_module in own_module_names`. No-agent fallback keeps
   single-module behavior.
3. **codeact.py `execution_context`** — same `own_module_names` membership check, so the
   displayed "Available types/functions" reflect the merged namespace (label softened to
   "defined in agent or ancestor modules").

## Tests

`tests/test_cross_module_inheritance.py` with helper modules `tests/helpers/cross_module_parent.py`
and `cross_module_child.py` (parent + child in separate modules, both using `CodeActStrategy`):

- Parent-module function / constant / type surface in `filter_mro_module_globals(ChildAgent)`.
- A name defined in both modules resolves to the child's value (leaf wins).
- Hidden parent symbols (`@hidden`, `Annotated[..., hidden]`, `with hidden:`) stay absent.
- Framework/builtins modules excluded from `iter_agent_mro_modules`.
- `execution_context` for `ChildAgent` lists the parent type/function.
- End-to-end: a `ChildAgent` CodeAct method, driven by a scripted `execute_python` tool call
  running `return_result(shared_util(SHARED_CONSTANT))`, succeeds without `NameError`.
- Regression guard: single-module parent (same file) still works.

```sh
uv run pytest tests/test_cross_module_inheritance.py tests/test_module_imports.py \
  tests/agentdoc/test_visibility.py tests/agentdoc/test_visibility_public.py -q
uv run ruff check
```

## Acceptance criteria mapping

- Child can call parent-module functions/constants/types in `execute_python()` → sites 1, 2, 4.
- Name collisions: leaf wins → `filter_mro_module_globals` base→leaf update order.
- `<execution_context>` "Available types" reflects merged namespace → site 3.
- Regression test → `tests/test_cross_module_inheritance.py`.
