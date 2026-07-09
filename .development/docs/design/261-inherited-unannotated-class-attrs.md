# Issue 261 — `doc(type(self))`: inherited un-annotated class attributes dropped

## Problem

`extract_type_info` / `doc()` drops **un-annotated** class-level attributes that are
inherited from a parent class. Annotated inherited fields (`x: T = ...`) and inherited
methods work fine; only bare class attributes (`shell = ShellTools()`, `todos = TodoManager()`)
declared on a base agent are missing from the child's `<self>` API listing.

### Root cause

`src/nooa/agentdoc/_structured.py`, `_extract_plain_class_fields`, **step 2**
(lines ~1013–1045) scans only the leaf class's own `__dict__`:

```python
for name, value in obj.__dict__.items():
    ...
```

Inherited un-annotated attributes live in ancestor `__dict__`s, so they are never seen.
- Step 1 (annotated fields) already walks the full MRO → annotated fields survive.
- `_extract_methods` walks the full MRO → methods survive.
- Step 2 does not → bug.

Reproduction confirmed: `class Parent: annotated_attr: Tool = Tool(); bare_attr = Tool()`,
`class Child(Parent): ...` → `extract_type_info(Child).fields == ['annotated_attr']`
(missing `bare_attr`).

## Fix

Rewrite step 2 to walk the full MRO instead of only `obj.__dict__`:

1. Collect candidate attribute names from every class in `obj.__mro__` (excluding `object`),
   ordered **base → derived** (mirroring step 1's annotated-field ordering, so parent
   attrs render before child attrs). Use the same sort key as step 1:
   `key=lambda c: (len(c.__mro__), mro_index[c])`.
2. For each candidate name, resolve the **effective** value with
   `inspect.getattr_static(obj, name, REQUIRED)` — this follows normal MRO resolution so the
   **leaf class wins** on collisions, and avoids triggering descriptors. Names that only
   exist in `obj.__mro__` class dicts resolve within the class MRO (before the metaclass),
   so metaclass attributes are not pulled in.
3. Apply the existing filters unchanged: skip names already in `seen_names` (annotated fields
   from step 1), private/dunder (`name.startswith("_")`), callables (non-`type`),
   classmethod/staticmethod/property/cached_property, and other descriptors.
4. Build the `FieldInfo` exactly as today (`_ClassRef` for class attrs → `type[Name]`,
   `_InstanceRef` for instances → `Name()`).

Hidden filtering happens centrally in `extract_type_info` via `is_hidden_field(obj, f.name)`
(applied after `_extract_fields`). `is_hidden_field` already walks the MRO for **annotation-based**
hiding (`Annotated[T, hidden]`), so inherited annotated-hidden fields are suppressed today.

### Required companion fix: imperative `spec()` hidden must walk the MRO

The only way to hide an **un-annotated** class attribute is the imperative
`spec(MyClass, "attr", hidden=True)` (an `Annotated[T, hidden]` annotation would make the attr
*annotated*, handled by step 1). But `is_hidden_field`'s imperative-override check uses
`get_field_metadata(cls, name)`, which reads only `vars(cls)` (the leaf's own
`_agentdoc_fields_docs`) — it does **not** walk the MRO. So `spec(Parent, "secret", hidden=True)`
is ignored on `Child`, and the inherited bare attr would leak (verified).

Fix `is_hidden_field` (`src/nooa/agentdoc/_visibility.py`): replace the single
`get_field_metadata(cls, name)` imperative check with an MRO walk (leaf → base), returning the
first class that declares a `hidden` bool. Leaf wins, matching the rest of the resolution order.
`get_field_metadata` keeps its own-dict-only semantics (other callers rely on it); only
`is_hidden_field` becomes MRO-aware. This also fixes the latent case of inherited *annotated*
fields hidden via class-level `spec()`.

### Why `getattr_static` over iterating each class `__dict__` and overwriting

`getattr_static` gives exact Python attribute-resolution semantics for the leaf class in a
single lookup, so "leaf wins" is correct even when a leaf overrides a parent attr with a
value of a different kind (e.g. parent `foo = Inst()`, child `foo = staticmethod(...)`):
the child's value is what's evaluated against the filters, matching what the LLM/runtime sees.

### Metaclass-attribute safety

`inspect.getattr_static(cls, name)` *does* fall through to the metaclass MRO if a name is not
found in `cls.__mro__`. Leakage is prevented because candidate **names** are collected only from
the `__dict__`s of classes in `obj.__mro__` (metaclass attrs are not in those), so every queried
name resolves within the class MRO before the metaclass is consulted. A regression test asserts a
metaclass-only attribute does not appear in child doc.

## Scope / non-goals

- No change to annotated-field handling (step 1), `__init__` fields (step 3), or properties (step 4).
- No change to method extraction.
- Generic fix in agentdoc — not Agent-specific. Framework attrs on the `Agent` base are all
  either private (`_enable_tracing`, `_abc_impl`, …) or dunder (`__nosnapshot__`) or
  annotated-and-hidden, so walking the full MRO does not leak framework internals.

## Tests

Add to `tests/agentdoc/test_inherited_fields.py` (new `TestInheritedUnannotatedAttrs` class):

1. **Inherited bare attr present**: `Parent` with `annotated_attr: Tool = Tool()` and
   `bare_attr = Tool()`; `Child(Parent)` → `extract_type_info(Child)` field names contain
   both; rendered as `Tool()` instance marker.
2. **Deep inheritance**: grandparent bare attr shows on grandchild.
3. **Leaf override wins**: parent `tool = ToolA()`, child `tool = ToolB()` → child's
   `ToolB()` marker, single entry.
4. **Imperative-spec suppression (parent-declared)**: parent bare attr hidden via
   `spec(Parent, "secret_tool", hidden=True)` stays out of `doc(Child)` — regression guard for
   the `is_hidden_field` MRO fix. Also: leaf `spec(Child, ...)` still wins.
5. **Ordering**: inherited bare attr renders before child's own bare attr.
6. **Metaclass non-leakage**: an attribute defined only on a custom metaclass does not appear in
   child doc.
7. **Agent-level regression** (the real-world case): base `Agent` subclass with an un-annotated
   tool attr (`shell = ShellTools()` style) + subclass → attr present in child `doc()`, and no
   framework internals (`_enable_tracing`, `runtime`, …) leaked.

Run: `uv run pytest tests/agentdoc/ -q` and `uv run ruff check src/nooa/agentdoc/_structured.py src/nooa/agentdoc/_visibility.py`.

## Acceptance criteria (from issue)

- [x] `doc(Child)` lists inherited un-annotated class attributes with `ClassName()` markers.
- [x] Leaf-class overrides win on name collision.
- [x] `@hidden` / Skip-marked attributes still suppressed.
- [x] Regression test added.
