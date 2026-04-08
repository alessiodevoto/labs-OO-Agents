# Configuration Unification — Migration Guide

> **Branch:** `feat/configuration-unification`
> **Date:** 2026-03-05

## TL;DR

All configuration across agent006, unifiedllm, agentdoc, and context-blocks has been unified into **frozen Pydantic models** with a consistent `merge_with()` override pattern. Flat kwargs on strategies, tools, and summarizers are replaced by a single `config=` parameter.

---

## What Changed

### Pattern: flat kwargs → `config=ConfigObject()`

Every configurable component now takes an optional `config=` argument instead of individual keyword arguments. All config classes are **frozen Pydantic BaseModels** with a `merge_with()` method for layered overrides.

---

## agent006 — Quick Migration Table

### Strategies

| Before | After |
|--------|-------|
| `CodeActStrategy(max_iterations=20, max_retries=5, code_execution_timeout=300.0)` | `CodeActStrategy(config=CodeActConfig(max_iterations=20, max_retries=5, cell_timeout=300.0))` |
| `StructuredOutputStrategy(max_retries=5)` | `StructuredOutputStrategy(config=StructuredOutputConfig(max_retries=5))` |
| `ReflexionStrategy(max_reflections=5)` | `ReflexionStrategy(config=ReflexionConfig(max_iterations=5))` |

**Renames:**
- `code_execution_timeout` → `cell_timeout`
- `max_reflections` → `max_iterations`

**Default change:** `CodeActConfig.max_iterations` default is now **50** (was 10).

### Tools

| Before | After |
|--------|-------|
| `BashTool(timeout=60, use_sandbox=True)` | `BashTool(config=BashConfig(default_timeout=60, use_sandbox=True))` |
| `WebSearchTool(default_num_results=10)` | `WebSearchTool(config=WebSearchConfig(default_num_results=10))` |

**Renames:** `timeout` → `default_timeout`
**Default change:** `BashConfig.use_sandbox` default is now **False** (was True).

### Summarizers

| Before | After |
|--------|-------|
| `TokenBudgetSummarizer.install(agent, max_tokens=80_000)` | `TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(max_tokens=80_000))` |
| `MethodSummarizer.install(agent, min_events=5)` | `MethodSummarizer.install(agent, config=MethodSummarizerConfig(min_events=5))` |

### TruncationConfig field renames

| Old field | New field |
|-----------|-----------|
| `block_limit` | `max_block_chars` |
| `context_limit` | `max_context_tokens` |
| `event_limit` | `max_event_tokens` |
| `stdout_limit` | `max_stdout_chars` |
| `stderr_limit` | `max_stderr_chars` |
| `max_length` | `max_pprint_elements` |
| `max_string` | `max_pprint_string` |
| `max_depth` | `max_pprint_depth` |

Import is now `from agent006.config import TruncationConfig` (old `runtime.truncation_config` still re-exports for compat).

### Agent class

| Before | After |
|--------|-------|
| *class-level:* `_block_formatter`, `_provider_formatter` | *class-level:* gone; use `render_config=RenderConfig(...)` at instance level |
| — | *class-level:* `execution=ExecutionConfig(max_nesting_depth=5)` |
| — | *instance-level:* `render_config=RenderConfig(block_formatter=..., provider_formatter=...)` |

```python
# Before
class MyAgent(Agent, llm=llm):
    _block_formatter = PlainBlockFormatter()

# After
from context_blocks import RenderConfig, PlainBlockFormatter

agent = MyAgent(llm, render_config=RenderConfig(block_formatter=PlainBlockFormatter()))
```

### New: ExecutionConfig

Controls framework-level limits. Set at class level:

```python
from agent006.config import ExecutionConfig

class MyAgent(Agent, llm=llm, execution=ExecutionConfig(max_nesting_depth=5)):
    ...
```

---

## Imports

All new config classes are exported from `agent006.config`:

```python
from agent006.config import (
    ExecutionConfig,
    CodeActConfig,
    StructuredOutputConfig,
    ReflexionConfig,
    TokenBudgetConfig,
    MethodSummarizerConfig,
    BashConfig,
    WebSearchConfig,
    TruncationConfig,
)
```

---

## unifiedllm

| Change | Details |
|--------|---------|
| `RetryConfig` | Moved from `unifiedllm.retry` → `unifiedllm.retry_config` (also re-exported from `unifiedllm`). Now a frozen Pydantic model. `retryable_status_codes` is `frozenset`, `retryable_exceptions` is `tuple`. |
| `HttpConfig` (new) | Controls httpx pool/timeout settings — was previously hardcoded. `from unifiedllm import HttpConfig` |

These are smaller changes; most users won't need to touch them unless customizing retry or HTTP behavior.

---

## agentdoc

| Change | Details |
|--------|---------|
| `DocConfig` | Moved from `agentdoc.config` → `agentdoc.doc_config` (re-exported from `agentdoc`). Now frozen Pydantic. `max_value_length` renamed to `max_value_chars`. `hidden_prefixes` is `tuple`, `hidden_names` is `frozenset`. |

---

## context-blocks

| Change | Details |
|--------|---------|
| `RenderConfig` (new) | Groups `block_formatter` + `provider_formatter`. `from context_blocks import RenderConfig` |
| `render_context()` | New `count_tokens` parameter required when using token-based limits (`context_limit` / `event_limit`). |

---

## Key Behavioral Changes

1. **`CodeActConfig.max_iterations`** default: 10 → **50**
2. **`BashConfig.use_sandbox`** default: True → **False**
3. **Token-aware truncation:** `render_context()` raises `ValueError` if token limits are set but no `count_tokens` callable is provided.
4. **All configs are immutable** (frozen). Use `merge_with()` or `model_copy(update={...})` to derive variants.

---

## `merge_with()` Pattern

Every config supports layered overrides:

```python
base = CodeActConfig(max_iterations=50)
override = CodeActConfig(max_iterations=10)  # only override what you set
merged = base.merge_with(override)           # → max_iterations=10, rest from base
```

`merge_with()` uses `model_fields_set` — only explicitly-set fields on the override take effect.
