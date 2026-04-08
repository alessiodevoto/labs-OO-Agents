# Suppress TemplateStrategy Generation Spans

## Problem

TemplateStrategy methods (e.g. `_build_task_message`) emit `span.generation` spans with `generation.strategy: "TEMPLATE"` and `openinference.span.kind: "LLM"`. These spans are noise:
- TemplateStrategy does no LLM call — it only renders string templates (~83μs)
- The rendered prompt already appears in the parent strategy's span
- Labeling template rendering as "LLM" span kind is misleading

## Approach

Add a `traceable` property to `GenerationStrategy` (default `True`), override to `False` in `TemplateStrategy`. Guard the `before_generation`/`after_generation` hook calls in `actor.py` so they're skipped when `strategy.traceable is False`.

## Changes

### 1. `src/nemo_oo_agents/strategies/base.py` — Add `traceable` property to `GenerationStrategy`

```python
@property
def traceable(self) -> bool:
    """Whether generation hooks should fire for this strategy.

    Default is True. Override to False for strategies that don't call the LLM
    (e.g. TemplateStrategy) to suppress noisy trace spans.
    """
    return True
```

### 2. `src/nemo_oo_agents/strategies/template.py` — Override `traceable` to `False`

```python
@property
def traceable(self) -> bool:
    """No tracing — template rendering is not an LLM call."""
    return False
```

### 3. `src/nemo_oo_agents/runtime/actor.py` — Guard hook calls in both `execute_nested` (~line 1146) and `_execute_task` (~line 1766)

Both sites have the same pattern. Use a `should_trace` boolean to guard both before and after hooks:

```python
should_trace = strategy.traceable

hook_context = None
if should_trace:
    hook_context = call_before_hook("before_generation", ...)
```

And in the `finally` block:

```python
if should_trace:
    call_after_hook("after_generation", hook_context, ...)
```

**Why `should_trace` boolean, not `hook_context is not None`:** `call_before_hook` can legitimately return `None` when no hooks are installed or when a hook raises an exception. Using `hook_context is not None` would conflate "intentionally skipped" with "hook failed/absent". The boolean is unambiguous.

**generation_id push/pop:** Intentionally left unchanged. The generation_id stack management is cheap and harmless — it's needed for proper parent-child ID correlation if any nested strategies within the template expansion are themselves traceable.

### 4. Tests

- Add `test_traceable_default_true` in `tests/strategies/test_generation_strategy.py`
- Add `test_traceable_false` in `tests/strategies/test_template_strategy.py`
- Add test with mock hooks installed: verify `before_generation`/`after_generation` are NOT called when a `traceable=False` strategy executes

## Files touched

1. `src/nemo_oo_agents/strategies/base.py` — add `traceable` property
2. `src/nemo_oo_agents/strategies/template.py` — override `traceable`
3. `src/nemo_oo_agents/runtime/actor.py` — guard hook calls (2 sites)
4. `tests/strategies/test_template_strategy.py` — test `traceable` is `False`
5. `tests/strategies/test_generation_strategy.py` — test `traceable` default is `True`
