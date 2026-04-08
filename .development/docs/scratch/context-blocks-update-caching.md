# GitLab Issue: Implement Block.update Caching

**Status**: Draft (GitLab MCP needs authentication - create manually when ready)

## Title

feat(context-blocks): Implement Block.update expression caching

## Description

The `Block` model has an `update` field that is documented as controlling cache invalidation, but this functionality is not currently implemented. The expression is included in metadata output but never evaluated to control actual caching behavior.

### Current State

```python
class Block(BaseModel):
    update: str = "True"  # Expression controlling re-evaluation (default: "True")
```

- The `update` field exists and defaults to `"True"`
- It's serialized into block metadata in the rendered output
- **No code evaluates this expression to control caching**

### Proposed Behavior

1. `BlockRenderer` maintains a cache of evaluated block values
2. Before evaluating `block.expr`, check if `eval(block.update)` returns `False`
3. If `update` evaluates to `False` AND a cached value exists, use the cached value
4. If `update` evaluates to `True` (or no cache exists), evaluate `block.expr` and cache the result

### Example Use Cases

```python
# Always re-evaluate (current default behavior)
Block(key="persona", expr="self.persona", update="True")

# Never re-evaluate after first render (static content)
Block(key="tools_doc", expr="self.doc()", update="False")

# Re-evaluate when state changes
Block(key="status", expr="self.format_status()", update="self.status_changed")

# Re-evaluate based on time
Block(key="metrics", expr="self.get_metrics()", update="time.time() - last_update > 60")
```

### Implementation Notes

1. Cache should be per-`BlockRenderer` instance or passed in
2. Need to handle async eval correctly
3. Consider cache invalidation API: `renderer.invalidate_cache(key=...)`
4. May want to store cache key with timestamp for debugging

### Acceptance Criteria

- [ ] `update="False"` prevents re-evaluation after first render
- [ ] `update="True"` always re-evaluates (backward compatible)
- [ ] Custom expressions work (e.g., `update="self.needs_refresh"`)
- [ ] Cache can be explicitly invalidated
- [ ] Async eval is supported
- [ ] Tests cover all cache scenarios

## Labels

- `enhancement`
- `context-blocks`
- `caching`

## Priority

Medium - functionality is documented but not implemented; not blocking current work.
