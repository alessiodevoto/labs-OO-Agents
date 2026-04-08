# Issue: Block merge() vs shadow semantics - clarify design intent

**Created:** 2026-02-03
**Status:** Partially resolved
**Related:** `feat/context-set-text-parameter` branch

## Resolution (2026-02-03)

- Renamed `merge()` → `apply_overrides()` for clarity
- Changed implementation from field-by-field mutation to full block replacement via `model_copy()`
- This automatically handles `value` field and any future fields
- Renamed `text=` → `value=` to support any Python value type (rendered via pprint)

Remaining question: should `scoped()` semantics be investigated further?

## Summary

The `BlockManager` has two ways to override blocks, with different semantics:

1. **`merge()`** - permanent mutation
2. **`scoped()` / `with`** - temporary shadow with restore

The design intent and naming may be confusing. This issue is to investigate and clarify.

## Current Behavior

### merge() - Permanent Mutation

```python
# In BlockManager.merge(), lines 257-262:
existing.expr = block_or_none.expr
existing.update = block_or_none.update
# ... mutates the existing Block object directly
```

Used at agent init time to apply decorator/constructor overrides:

```python
# agent.py lines 366-375
self.blocks.merge("context", decorator_blocks, allow_protected_override=True)
self.blocks.merge("context", self._instance_blocks, allow_protected_override=True)
```

### scoped() - Temporary Shadow

```python
# In ScopedContext.__enter__(), line 77:
self.saved_blocks[section][key] = existing.model_copy(deep=True)  # Save original

# In ScopedContext.__exit__(), lines 151-155:
existing.expr = saved_block.expr  # Restore original
```

Used at runtime for temporary overrides:

```python
with manager.scoped(context={"debug": "self.state"}):
    # debug block exists here
# debug block removed/restored
```

## Questions to Investigate

1. **Is the naming clear?**
   - `merge()` sounds like it could be non-destructive
   - Should it be called `apply()` or `mutate()` instead?

2. **Is permanent mutation the right design for init-time?**
   - Pro: Simple, no layering complexity
   - Con: Can't easily "undo" or introspect what came from where

3. **Should there be a unified model?**
   - Could use a stack-based approach where all overrides are layers
   - Resolution happens at render time by walking the stack
   - More complex but more flexible

4. **What happens if runtime code calls merge() instead of scoped()?**
   - Currently nothing prevents this
   - Would permanently mutate blocks, breaking expectations

## Potential Improvements

### Option A: Rename for clarity
- `merge()` → `apply_overrides()` or `mutate()`
- Keep current behavior, just clearer naming

### Option B: Make merge() also shadow
- `merge()` pushes a layer onto a stack
- `unmerge()` or context manager pops the layer
- Resolution walks the stack at render time

### Option C: Restrict merge() to init-time only
- Add a flag/check that merge() can only be called during `__init__`
- Runtime code must use `scoped()` or `set()`

## Next Steps

1. ~~Fix missing text field~~ - Done (now uses full block replacement)
2. ~~Rename merge() for clarity~~ - Done (now `apply_overrides()`)
3. Consider if `scoped()` semantics need further investigation
