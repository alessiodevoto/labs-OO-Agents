# BlockMetadata Usage Analysis

**Date**: 2026-01-25
**Purpose**: Determine if BlockMetadata is used meaningfully before deciding to simplify/remove.

## Summary

**Verdict**: Keep for now. BlockMetadata is used for:
1. Internal scoped context tracking (`{"scoped": True}`)
2. User-defined custom metadata (pass-through, not read by framework)

The class is simple (one field: `custom: dict`) and doesn't add significant complexity. However, it's not providing much value either.

## Detailed Analysis

### Definition

```python
# models.py
class BlockMetadata(BaseModel):
    custom: dict[str, Any] = Field(default_factory=dict)
```

### Usage Locations

#### 1. ScopedContext (Internal Use)

`scoped.py` sets `{"scoped": True}` to mark blocks as scoped:

```python
metadata = BlockMetadata(custom={"scoped": True})
```

This marker is used to track which blocks were added by the scoped context so they can be removed on exit. However, the actual scoped tracking uses `added_keys` and `saved_blocks` dicts, not the metadata marker.

**Verdict**: The `scoped` marker is set but never read. Dead code.

#### 2. BlockManager.set() (User Interface)

```python
def set(self, ..., metadata: BlockMetadata | None = None) -> Block:
```

Users can pass metadata when creating blocks. This flows through to the Block model.

#### 3. Tests

Tests verify that:
- Metadata can be set and retrieved
- Custom values are preserved
- Scoped flag is set (but never that it's read)

### What Reads BlockMetadata?

**Nothing in production code reads `BlockMetadata.custom`.**

The metadata is:
- Serialized by `_build_block_metadata()` in renderer (but only `expr`, `update`, `timestamp` - NOT `custom`)
- Passed through during block operations
- Never used for any decision-making

### Event Metadata (Different)

Note: There's also `event.metadata` on EventBase, which IS used:

```python
# formatter.py:368
tag = event.metadata.get("tag")
if tag:
    content = f"[{tag}] {content}"
```

This is different from BlockMetadata and is actually functional.

## Options

### Option A: Keep As-Is
- Low cost, allows future extensibility
- Users can attach arbitrary data to blocks

### Option B: Simplify to Optional Dict
- Change `Block.metadata: BlockMetadata` to `Block.metadata: dict[str, Any] = Field(default_factory=dict)`
- Remove BlockMetadata class
- Less indirection, same functionality

### Option C: Remove Entirely
- Remove metadata from Block
- Breaking change if anyone uses it
- Clean up tests that exercise it

## Recommendation

**Option B** (simplify to dict) makes sense as a future cleanup, but it's low priority. The current implementation is harmless.

For now: **No action needed**. The metadata system is simple and might be useful for future features (like debug info, source tracking, etc.).

## Related

- `event.metadata` is actually used (for tag prefixing)
- The `scoped: True` marker in BlockMetadata.custom appears to be dead code
