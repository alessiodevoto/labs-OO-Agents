# Configuration Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify 100+ config parameters across 4 packages into consistent frozen Pydantic models with `merge_with()`, rename fields to follow `max_*_chars / max_*_tokens / *_timeout` conventions, and fix the BLOCKING token-counting issue in the renderer.

**Architecture:** Each config class is a frozen Pydantic `BaseModel` with a `merge_with(other)` method that uses `other.model_fields_set` to determine which fields were explicitly set. Config classes live in `*_config.py` files; strategies/tools/summarizers accept a `config=` constructor argument; no backwards-compatibility shims.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, source `.venv/bin/activate` before all commands.

**Design reference:** `docs/plans/2026-02-23-configuration-unification-design.md`

**Test runner:** All `pytest` commands run from repo root (`/Volumes/dev/dev/nemo_oo_agents`) with venv activated.

---

## Phase 1: Package-level configs (unifiedllm, agentdoc, context-blocks)

These four tasks are independent of each other and of nemo_oo_agents — do them in any order.

---

### Task 1: RetryConfig — migrate dataclass → Pydantic

**Files:**
- Create: `packages/unifiedllm/src/unifiedllm/retry_config.py`
- Modify: `packages/unifiedllm/src/unifiedllm/retry.py`
- Modify: `packages/unifiedllm/src/unifiedllm/__init__.py`
- Create: `packages/unifiedllm/tests/test_retry_config.py`

**Step 1: Write the failing tests**

```python
# packages/unifiedllm/tests/test_retry_config.py
import asyncio
import pytest
from pydantic import BaseModel, ValidationError
from unifiedllm.retry_config import RetryConfig


def test_retry_config_is_pydantic_model():
    assert issubclass(RetryConfig, BaseModel)


def test_retry_config_defaults():
    c = RetryConfig()
    assert c.max_retries == 3
    assert c.base_delay == 1.0
    assert c.max_delay == 60.0
    assert c.exponential_base == 2.0
    assert c.jitter_factor == 0.3
    assert c.rate_limit_extra_retries == 3
    assert c.rate_limit_base_delay == 3.0
    assert c.rate_limit_backoff_base == 3.0
    assert c.retryable_status_codes == frozenset({429, 500, 502, 503, 504})
    assert asyncio.TimeoutError in c.retryable_exceptions
    assert c.retry_on_empty_content is False
    assert c.on_retry is None


def test_retry_config_frozen():
    c = RetryConfig()
    with pytest.raises(ValidationError):
        c.max_retries = 5


def test_retryable_status_codes_is_frozenset():
    c = RetryConfig()
    assert isinstance(c.retryable_status_codes, frozenset)


def test_retryable_exceptions_is_typed_tuple():
    c = RetryConfig()
    assert isinstance(c.retryable_exceptions, tuple)
    for exc_type in c.retryable_exceptions:
        assert isinstance(exc_type, type)
        assert issubclass(exc_type, BaseException)


def test_merge_with_overrides_only_explicit_fields():
    base = RetryConfig()
    override = RetryConfig(max_retries=5)
    merged = base.merge_with(override)
    assert merged.max_retries == 5
    assert merged.base_delay == 1.0  # not overridden


def test_merge_with_rejects_round_tripped_config():
    base = RetryConfig()
    round_tripped = RetryConfig.model_validate(RetryConfig().model_dump())
    with pytest.raises(AssertionError, match="merge_with"):
        base.merge_with(round_tripped)


def test_rate_limit_backoff_base_is_new_field():
    # This field did not exist in the old dataclass
    c = RetryConfig(rate_limit_backoff_base=5.0)
    assert c.rate_limit_backoff_base == 5.0
```

**Step 2: Run tests — verify they fail**

```bash
source .venv/bin/activate
pytest packages/unifiedllm/tests/test_retry_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'unifiedllm.retry_config'`

**Step 3: Create `retry_config.py`**

```python
# packages/unifiedllm/src/unifiedllm/retry_config.py
import asyncio
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict


class RetryConfig(BaseModel):
    """Retry behaviour for LLM API calls."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.3
    rate_limit_extra_retries: int = 3
    rate_limit_base_delay: float = 3.0
    rate_limit_backoff_base: float = 3.0  # was hardcoded as 3.0 in _calculate_delay()
    retryable_status_codes: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    retryable_exceptions: tuple[type[Exception], ...] = (
        asyncio.TimeoutError,
        TimeoutError,
        ConnectionError,
    )
    retry_on_empty_content: bool = False
    on_retry: Callable[[int, Exception, float], None] | None = None

    def merge_with(self, other: "RetryConfig") -> "RetryConfig":
        assert other.model_fields_set, (
            "merge_with() received a config with no model_fields_set. "
            "Was it constructed from model_dump() or model_validate()? "
            "Config objects must be freshly constructed: RetryConfig(field=value)."
        )
        return self.model_copy(
            update={k: getattr(other, k) for k in other.model_fields_set}
        )
```

**Step 4: Update `retry.py` — replace dataclass import and use `rate_limit_backoff_base`**

In `packages/unifiedllm/src/unifiedllm/retry.py`:
- Remove the `@dataclass class RetryConfig` definition (lines 34–60)
- Add at top: `from unifiedllm.retry_config import RetryConfig`
- Find the hardcoded `3.0 ** attempt` in `_calculate_delay()` and replace with `config.rate_limit_backoff_base ** attempt`

The `_calculate_delay` change will look like:
```python
# Before:
delay = self.config.rate_limit_base_delay * (3.0 ** attempt)
# After:
delay = self.config.rate_limit_base_delay * (self.config.rate_limit_backoff_base ** attempt)
```

**Step 5: Update `__init__.py` — export from retry_config**

In `packages/unifiedllm/src/unifiedllm/__init__.py`, change:
```python
# Before:
from unifiedllm.retry import EmptyContentError, RetryConfig, RetryingWrapper, sync_retry, with_retry
# After:
from unifiedllm.retry import EmptyContentError, RetryingWrapper, sync_retry, with_retry
from unifiedllm.retry_config import RetryConfig
```

**Step 6: Run tests — verify they pass**

```bash
pytest packages/unifiedllm/tests/test_retry_config.py packages/unifiedllm/tests/test_retry.py -v
```
Expected: All PASS

**Step 7: Commit**

```bash
git add packages/unifiedllm/src/unifiedllm/retry_config.py \
        packages/unifiedllm/src/unifiedllm/retry.py \
        packages/unifiedllm/src/unifiedllm/__init__.py \
        packages/unifiedllm/tests/test_retry_config.py
git commit -m "$(cat <<'EOF'
feat(unifiedllm): migrate RetryConfig from dataclass to frozen Pydantic model

- Extract to retry_config.py with merge_with() using model_fields_set
- retryable_status_codes: set -> frozenset (immutable)
- retryable_exceptions: tuple -> tuple[type[Exception], ...] (typed)
- Surface rate_limit_backoff_base (was hardcoded 3.0 in _calculate_delay)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: HttpConfig — surface httpx hardcoded values

**Files:**
- Create: `packages/unifiedllm/src/unifiedllm/http_config.py`
- Modify: `packages/unifiedllm/src/unifiedllm/unifiedllm.py`
- Modify: `packages/unifiedllm/src/unifiedllm/__init__.py`
- Create: `packages/unifiedllm/tests/test_http_config.py`

**Step 1: Write the failing tests**

```python
# packages/unifiedllm/tests/test_http_config.py
import pytest
from pydantic import BaseModel, ValidationError
from unifiedllm.http_config import HttpConfig


def test_http_config_is_pydantic_model():
    assert issubclass(HttpConfig, BaseModel)


def test_http_config_defaults():
    c = HttpConfig()
    assert c.max_connections == 100
    assert c.max_keepalive_connections == 0
    assert c.keepalive_expiry == 0.0
    assert c.connect_timeout == 10.0
    assert c.read_timeout == 60.0
    assert c.write_timeout == 10.0
    assert c.pool_timeout == 10.0


def test_http_config_frozen():
    c = HttpConfig()
    with pytest.raises(ValidationError):
        c.connect_timeout = 5.0


def test_merge_with_overrides_only_explicit_fields():
    base = HttpConfig()
    override = HttpConfig(read_timeout=30.0)
    merged = base.merge_with(override)
    assert merged.read_timeout == 30.0
    assert merged.connect_timeout == 10.0  # not overridden


def test_completion_client_accepts_http_config():
    from unifiedllm import CompletionClient, HttpConfig
    # Should not raise — just verifies the constructor signature
    client = CompletionClient("gpt-4o-mini", http_config=HttpConfig(connect_timeout=5.0))
    assert client._http_config.connect_timeout == 5.0
```

**Step 2: Run tests — verify they fail**

```bash
pytest packages/unifiedllm/tests/test_http_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'unifiedllm.http_config'`

**Step 3: Create `http_config.py`**

```python
# packages/unifiedllm/src/unifiedllm/http_config.py
from pydantic import BaseModel, ConfigDict


class HttpConfig(BaseModel):
    """HTTP connection pool and timeout settings for the global httpx patch.

    The values here are applied to ALL httpx.AsyncClient instances in the
    process (via a module-level monkey-patch). Whichever CompletionClient
    is created first sets these values for the process lifetime.
    """

    model_config = ConfigDict(frozen=True)

    max_connections: int = 100
    max_keepalive_connections: int = 0   # 0 = disabled, prevents CLOSE_WAIT
    keepalive_expiry: float = 0.0
    connect_timeout: float = 10.0
    read_timeout: float = 60.0           # catches CLOSE_WAIT hangs
    write_timeout: float = 10.0
    pool_timeout: float = 10.0

    def merge_with(self, other: "HttpConfig") -> "HttpConfig":
        assert other.model_fields_set, (
            "merge_with() requires a freshly-constructed config."
        )
        return self.model_copy(
            update={k: getattr(other, k) for k in other.model_fields_set}
        )
```

**Step 4: Update `unifiedllm.py` — wire HttpConfig into CompletionClient and patch**

In `packages/unifiedllm/src/unifiedllm/unifiedllm.py`:

a) Add import at top:
```python
from unifiedllm.http_config import HttpConfig
```

b) Add module-level variable for active config (above `_apply_httpx_no_pool_patch`):
```python
_active_http_config: "HttpConfig | None" = None
```

c) Replace the hardcoded values in `_no_pool_async_init` inside `_apply_httpx_no_pool_patch()` with reads from `_active_http_config`:
```python
def _no_pool_async_init(self, *args, **kwargs):
    from unifiedllm.http_config import HttpConfig as _HC
    cfg = _active_http_config or _HC()
    if "limits" not in kwargs:
        kwargs["limits"] = httpx.Limits(
            max_connections=cfg.max_connections,
            max_keepalive_connections=cfg.max_keepalive_connections,
            keepalive_expiry=cfg.keepalive_expiry,
        )
    elif isinstance(kwargs["limits"], httpx.Limits):
        kwargs["limits"] = httpx.Limits(
            max_connections=kwargs["limits"].max_connections or cfg.max_connections,
            max_keepalive_connections=0,
            keepalive_expiry=0.0,
        )
    if "timeout" not in kwargs:
        kwargs["timeout"] = httpx.Timeout(
            connect=cfg.connect_timeout,
            read=cfg.read_timeout,
            write=cfg.write_timeout,
            pool=cfg.pool_timeout,
        )
    elif isinstance(kwargs["timeout"], (int, float)):
        kwargs["timeout"] = httpx.Timeout(
            connect=cfg.connect_timeout,
            read=cfg.read_timeout,
            write=cfg.write_timeout,
            pool=cfg.pool_timeout,
        )
    return _original_async_init(self, *args, **kwargs)
```

d) Add a function to set the active config (called by CompletionClient):
```python
def _set_http_config(config: "HttpConfig") -> None:
    global _active_http_config
    _active_http_config = config
    _apply_httpx_no_pool_patch()
```

e) Update `CompletionClient.__init__` to accept and store `http_config`:
```python
def __init__(
    self,
    model: str,
    retry_config: RetryConfig | None = None,
    http_config: HttpConfig | None = None,   # NEW
    cache_control_injection_points: list[dict[str, Any]] | None = None,
    **config,
):
    super().__init__(model, **config)
    self.retry_config = retry_config
    self._http_config = http_config or HttpConfig()
    _set_http_config(self._http_config)
    if cache_control_injection_points is not None:
        self.cache_control_injection_points = cache_control_injection_points
    else:
        self.cache_control_injection_points = DEFAULT_CACHE_CONTROL_INJECTION_POINTS
```

**Step 5: Export from `__init__.py`**

Add to `packages/unifiedllm/src/unifiedllm/__init__.py`:
```python
from unifiedllm.http_config import HttpConfig
```
And add `"HttpConfig"` to `__all__`.

**Step 6: Run tests**

```bash
pytest packages/unifiedllm/tests/test_http_config.py packages/unifiedllm/tests/ -v
```
Expected: All PASS

**Step 7: Commit**

```bash
git add packages/unifiedllm/src/unifiedllm/http_config.py \
        packages/unifiedllm/src/unifiedllm/unifiedllm.py \
        packages/unifiedllm/src/unifiedllm/__init__.py \
        packages/unifiedllm/tests/test_http_config.py
git commit -m "$(cat <<'EOF'
feat(unifiedllm): add HttpConfig and wire into global httpx monkey-patch

- New HttpConfig frozen Pydantic model surfaces all hardcoded httpx values
- CompletionClient accepts http_config= parameter
- Patch reads from _active_http_config instead of hardcoded literals
- Known limitation: first CompletionClient created sets process-wide values

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: DocConfig — migrate dataclass → Pydantic

**Files:**
- Create: `packages/agentdoc/src/agentdoc/doc_config.py`
- Modify: `packages/agentdoc/src/agentdoc/config.py` (replace dataclass, keep `DEFAULT_CONFIG`)
- Modify: `packages/agentdoc/src/agentdoc/__init__.py`
- Create: `packages/agentdoc/tests/test_doc_config.py`

**Step 1: Write the failing tests**

```python
# packages/agentdoc/tests/test_doc_config.py
import pytest
from pydantic import BaseModel, ValidationError
from agentdoc.doc_config import DocConfig


def test_doc_config_is_pydantic_model():
    assert issubclass(DocConfig, BaseModel)


def test_doc_config_defaults():
    c = DocConfig()
    assert c.max_value_length == 50
    assert c.max_list_items == 10
    assert c.max_dict_items == 10
    assert c.hidden_prefixes == ("_",)
    assert c.hidden_names == frozenset()
    assert c.include_types is True
    assert c.include_defaults is True
    assert c.include_docstrings is True
    assert c.include_hints is True


def test_doc_config_frozen():
    c = DocConfig()
    with pytest.raises(ValidationError):
        c.max_value_length = 100


def test_hidden_prefixes_is_immutable_tuple():
    c = DocConfig()
    assert isinstance(c.hidden_prefixes, tuple)
    with pytest.raises(AttributeError):
        c.hidden_prefixes.append("test")  # tuples have no append


def test_hidden_names_is_frozenset():
    c = DocConfig()
    assert isinstance(c.hidden_names, frozenset)


def test_should_hide_by_prefix():
    c = DocConfig()
    assert c.should_hide("_private") is True
    assert c.should_hide("public") is False


def test_should_hide_by_name():
    c = DocConfig(hidden_names=frozenset({"secret"}))
    assert c.should_hide("secret") is True
    assert c.should_hide("public") is False


def test_merge_with_overrides_only_explicit_fields():
    base = DocConfig()
    override = DocConfig(max_value_length=100)
    merged = base.merge_with(override)
    assert merged.max_value_length == 100
    assert merged.max_list_items == 10  # not overridden
```

**Step 2: Run tests — verify they fail**

```bash
pytest packages/agentdoc/tests/test_doc_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'agentdoc.doc_config'`

**Step 3: Create `doc_config.py`**

```python
# packages/agentdoc/src/agentdoc/doc_config.py
from pydantic import BaseModel, ConfigDict


class DocConfig(BaseModel):
    """Configuration for agentdoc documentation generation."""

    model_config = ConfigDict(frozen=True)

    max_value_length: int = 50
    max_list_items: int = 10
    max_dict_items: int = 10
    hidden_prefixes: tuple[str, ...] = ("_",)
    hidden_names: frozenset[str] = frozenset()
    include_types: bool = True
    include_defaults: bool = True
    include_docstrings: bool = True
    include_hints: bool = True

    def should_hide(self, name: str) -> bool:
        if name in self.hidden_names:
            return True
        return any(name.startswith(prefix) for prefix in self.hidden_prefixes)

    def merge_with(self, other: "DocConfig") -> "DocConfig":
        assert other.model_fields_set, (
            "merge_with() requires a freshly-constructed config."
        )
        return self.model_copy(
            update={k: getattr(other, k) for k in other.model_fields_set}
        )
```

**Step 4: Update `config.py` — replace dataclass with import + keep DEFAULT_CONFIG**

Replace the entire content of `packages/agentdoc/src/agentdoc/config.py` with:
```python
# config.py — re-exports DocConfig for backwards compatibility within this package
from agentdoc.doc_config import DocConfig

DEFAULT_CONFIG = DocConfig()

__all__ = ["DocConfig", "DEFAULT_CONFIG"]
```

**Step 5: Export from `__init__.py`**

Ensure `agentdoc/__init__.py` exports `DocConfig` (add if missing):
```python
from agentdoc.doc_config import DocConfig
```

**Step 6: Run tests**

```bash
pytest packages/agentdoc/tests/test_doc_config.py packages/agentdoc/tests/test_core.py -v
```
Expected: All PASS (core.py tests should still pass as `config.py` still exports `DocConfig` and `DEFAULT_CONFIG`)

**Step 7: Commit**

```bash
git add packages/agentdoc/src/agentdoc/doc_config.py \
        packages/agentdoc/src/agentdoc/config.py \
        packages/agentdoc/src/agentdoc/__init__.py \
        packages/agentdoc/tests/test_doc_config.py
git commit -m "$(cat <<'EOF'
feat(agentdoc): migrate DocConfig from dataclass to frozen Pydantic model

- hidden_prefixes: list[str] -> tuple[str, ...] (immutable)
- hidden_names: set[str] -> frozenset[str] (immutable)
- Add merge_with() using model_fields_set
- config.py now re-exports from doc_config.py

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: RenderConfig — new config for formatter selection

**Files:**
- Create: `packages/context-blocks/src/context_blocks/render_config.py`
- Modify: `packages/context-blocks/src/context_blocks/__init__.py`
- Create: `packages/context-blocks/tests/test_render_config.py`

**Step 1: Write the failing tests**

```python
# packages/context-blocks/tests/test_render_config.py
import pytest
from pydantic import BaseModel, ValidationError
from context_blocks.render_config import RenderConfig
from context_blocks.formatter import (
    XMLBlockFormatter,
    MarkdownBlockFormatter,
    OpenAIProviderFormatter,
)


def test_render_config_is_pydantic_model():
    assert issubclass(RenderConfig, BaseModel)


def test_render_config_defaults():
    c = RenderConfig()
    assert isinstance(c.block_formatter, XMLBlockFormatter)
    assert isinstance(c.provider_formatter, OpenAIProviderFormatter)


def test_render_config_frozen():
    c = RenderConfig()
    with pytest.raises(ValidationError):
        c.block_formatter = MarkdownBlockFormatter()


def test_render_config_custom_formatters():
    c = RenderConfig(block_formatter=MarkdownBlockFormatter())
    assert isinstance(c.block_formatter, MarkdownBlockFormatter)
    assert isinstance(c.provider_formatter, OpenAIProviderFormatter)


def test_merge_with_overrides_only_explicit_fields():
    base = RenderConfig()
    override = RenderConfig(block_formatter=MarkdownBlockFormatter())
    merged = base.merge_with(override)
    assert isinstance(merged.block_formatter, MarkdownBlockFormatter)
    assert isinstance(merged.provider_formatter, OpenAIProviderFormatter)  # not overridden


def test_merge_with_rejects_round_tripped_config():
    base = RenderConfig()
    # Constructing with no explicit fields = empty model_fields_set
    empty = RenderConfig.__new__(RenderConfig)
    # Simulate a round-trip where model_fields_set is empty by using model_validate
    round_tripped = RenderConfig.model_validate({"block_formatter": XMLBlockFormatter(), "provider_formatter": OpenAIProviderFormatter()})
    with pytest.raises(AssertionError, match="merge_with"):
        base.merge_with(round_tripped)
```

**Step 2: Run tests — verify they fail**

```bash
pytest packages/context-blocks/tests/test_render_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'context_blocks.render_config'`

**Step 3: Create `render_config.py`**

```python
# packages/context-blocks/src/context_blocks/render_config.py
from pydantic import BaseModel, ConfigDict, Field

from context_blocks.formatter import (
    BlockFormatter,
    OpenAIProviderFormatter,
    ProviderFormatter,
    XMLBlockFormatter,
)


class RenderConfig(BaseModel):
    """Controls how context blocks are formatted and how messages are assembled.

    block_formatter: How system prompt blocks are serialized (XML or Markdown).
    provider_formatter: How the message list is assembled for the LLM provider.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    block_formatter: BlockFormatter = Field(default_factory=XMLBlockFormatter)
    provider_formatter: ProviderFormatter = Field(default_factory=OpenAIProviderFormatter)

    def merge_with(self, other: "RenderConfig") -> "RenderConfig":
        assert other.model_fields_set, (
            "merge_with() requires a freshly-constructed config."
        )
        return self.model_copy(
            update={k: getattr(other, k) for k in other.model_fields_set}
        )
```

**Step 4: Export from `__init__.py`**

Add to `packages/context-blocks/src/context_blocks/__init__.py`:
```python
from context_blocks.render_config import RenderConfig
```
Add `"RenderConfig"` to `__all__` if present.

**Step 5: Run tests**

```bash
pytest packages/context-blocks/tests/test_render_config.py -v
```
Expected: All PASS

**Step 6: Commit**

```bash
git add packages/context-blocks/src/context_blocks/render_config.py \
        packages/context-blocks/src/context_blocks/__init__.py \
        packages/context-blocks/tests/test_render_config.py
git commit -m "$(cat <<'EOF'
feat(context-blocks): add RenderConfig for formatter selection

- Frozen Pydantic model with block_formatter and provider_formatter fields
- Replaces class-attribute pattern on Agent (wired in Phase 3)
- merge_with() uses model_fields_set

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2: Create nemo_oo_agents/config/ and add config models

---

### Task 5: Create config/ module + ExecutionConfig

**Files:**
- Create: `src/nemo_oo_agents/config/__init__.py`
- Create: `src/nemo_oo_agents/config/execution_config.py`
- Create: `tests/config/__init__.py`
- Create: `tests/config/test_execution_config.py`

**Step 1: Write the failing tests**

```python
# tests/config/test_execution_config.py
import pytest
from pydantic import BaseModel, ValidationError
from nemo_oo_agents.config.execution_config import ExecutionConfig


def test_execution_config_is_pydantic_model():
    assert issubclass(ExecutionConfig, BaseModel)


def test_execution_config_defaults():
    c = ExecutionConfig()
    assert c.max_nesting_depth == 10


def test_execution_config_frozen():
    c = ExecutionConfig()
    with pytest.raises(ValidationError):
        c.max_nesting_depth = 5


def test_merge_with_overrides_only_explicit_fields():
    base = ExecutionConfig()
    override = ExecutionConfig(max_nesting_depth=5)
    merged = base.merge_with(override)
    assert merged.max_nesting_depth == 5
```

**Step 2: Run tests — verify they fail**

```bash
pytest tests/config/test_execution_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'nemo_oo_agents.config'`

**Step 3: Create the config module**

```bash
mkdir -p src/nemo_oo_agents/config tests/config
touch src/nemo_oo_agents/config/__init__.py tests/config/__init__.py
```

Create `src/nemo_oo_agents/config/execution_config.py`:
```python
# src/nemo_oo_agents/config/execution_config.py
from pydantic import BaseModel, ConfigDict


class ExecutionConfig(BaseModel):
    """Framework-level execution guards. Set at Agent class level:

        class MyAgent(Agent, execution=ExecutionConfig(max_nesting_depth=5)):
            ...
    """

    model_config = ConfigDict(frozen=True)

    max_nesting_depth: int = 10  # max agent-in-agent recursion depth

    def merge_with(self, other: "ExecutionConfig") -> "ExecutionConfig":
        assert other.model_fields_set, (
            "merge_with() requires a freshly-constructed config."
        )
        return self.model_copy(
            update={k: getattr(other, k) for k in other.model_fields_set}
        )
```

Update `src/nemo_oo_agents/config/__init__.py`:
```python
from nemo_oo_agents.config.execution_config import ExecutionConfig

__all__ = ["ExecutionConfig"]
```

**Step 4: Run tests**

```bash
pytest tests/config/test_execution_config.py -v
```
Expected: All PASS

**Step 5: Commit**

```bash
git add src/nemo_oo_agents/config/ tests/config/
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): create config/ module with ExecutionConfig

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: TruncationConfig — rename fields, move, switch merge strategy

**Context:** TruncationConfig already exists at `src/nemo_oo_agents/runtime/truncation_config.py` as a frozen Pydantic model using a private `_explicitly_set` field for merge tracking. We rename 8 fields, move to `config/`, and switch to `model_fields_set`.

**Field rename map:**
| Old | New |
|-----|-----|
| `block_limit` | `max_block_chars` |
| `context_limit` | `max_context_tokens` |
| `event_limit` | `max_event_tokens` |
| `stdout_limit` | `max_stdout_chars` |
| `stderr_limit` | `max_stderr_chars` |
| `max_length` | `max_pprint_elements` |
| `max_string` | `max_pprint_string` |
| `max_depth` | `max_pprint_depth` |

**Files:**
- Create: `src/nemo_oo_agents/config/truncation_config.py`
- Delete: `src/nemo_oo_agents/runtime/truncation_config.py` (replaced, no re-export)
- Modify: `tests/runtime/test_truncation_config.py` (update field names)
- Modify: `tests/runtime/test_truncation_config_integration.py` (update field names)
- Modify: All files that `import TruncationConfig from nemo_oo_agents.runtime.truncation_config`

**Step 1: Find all import sites**

```bash
grep -r "truncation_config\|TruncationConfig" src/ tests/ --include="*.py" -l
```
Note every file listed — each will need its import updated.

**Step 2: Update test file to use new field names (they will fail)**

In `tests/runtime/test_truncation_config.py`, replace all old field names with new ones. Example:
```python
# Before:
config = TruncationConfig(block_limit=5000)
assert config.block_limit == 5000

# After:
config = TruncationConfig(max_block_chars=5000)
assert config.max_block_chars == 5000
```
Apply the same rename map to all field references in both test files.

**Step 3: Run tests — verify they fail**

```bash
pytest tests/runtime/test_truncation_config.py -v
```
Expected: FAIL — old field names no longer accepted (or `ModuleNotFoundError` from new import path)

**Step 4: Create `src/nemo_oo_agents/config/truncation_config.py`**

```python
# src/nemo_oo_agents/config/truncation_config.py
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated


class TruncationConfig(BaseModel):
    """Controls output size at render time.

    max_block_chars: Per-block character clamp. Applied to context blocks and events.
    max_context_tokens: Total token budget for system/context blocks (None = no limit).
                        Requires count_tokens to be passed to render().
    max_event_tokens: Total token budget for event/message blocks (None = no limit).
                      Requires count_tokens to be passed to render().
    max_stdout_chars: Max chars from execute_python() stdout per cell.
    max_stderr_chars: Max chars from execute_python() stderr per cell.
    max_pprint_elements: Max container elements in pprint output.
    max_pprint_string: Max string chars in pprint output.
    max_pprint_depth: Max nesting depth in pprint output.
    """

    model_config = ConfigDict(frozen=True)

    max_block_chars: Annotated[int, Field(description="Max chars per block")] = 20_000
    max_context_tokens: Annotated[
        int | None, Field(description="Total context token budget")
    ] = None
    max_event_tokens: Annotated[
        int | None, Field(description="Total event token budget")
    ] = None
    max_stdout_chars: Annotated[int, Field(description="Max stdout chars")] = 50_000
    max_stderr_chars: Annotated[int, Field(description="Max stderr chars")] = 20_000
    max_pprint_elements: Annotated[
        int | None, Field(description="Max container elements in pprint")
    ] = 50
    max_pprint_string: Annotated[
        int | None, Field(description="Max string chars in pprint")
    ] = 500
    max_pprint_depth: Annotated[
        int | None, Field(description="Max nesting depth in pprint")
    ] = 4

    def merge_with(self, other: "TruncationConfig") -> "TruncationConfig":
        assert other.model_fields_set, (
            "merge_with() received a config with no model_fields_set. "
            "Was it constructed from model_dump() or model_validate()? "
            "Config objects must be freshly constructed: TruncationConfig(field=value)."
        )
        return self.model_copy(
            update={k: getattr(other, k) for k in other.model_fields_set}
        )
```

**Step 5: Update all import sites**

For each file found in Step 1, change:
```python
# Before:
from nemo_oo_agents.runtime.truncation_config import TruncationConfig
# After:
from nemo_oo_agents.config.truncation_config import TruncationConfig
```

Also update `src/nemo_oo_agents/config/__init__.py`:
```python
from nemo_oo_agents.config.execution_config import ExecutionConfig
from nemo_oo_agents.config.truncation_config import TruncationConfig

__all__ = ["ExecutionConfig", "TruncationConfig"]
```

**Step 6: Delete old file**

```bash
git rm src/nemo_oo_agents/runtime/truncation_config.py
```

**Step 7: Run tests**

```bash
pytest tests/runtime/test_truncation_config.py tests/runtime/test_truncation_config_integration.py -v
```
Expected: All PASS

**Step 8: Commit**

```bash
git add src/nemo_oo_agents/config/truncation_config.py \
        src/nemo_oo_agents/config/__init__.py \
        tests/runtime/test_truncation_config.py \
        tests/runtime/test_truncation_config_integration.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): rename TruncationConfig fields and move to config/

- block_limit -> max_block_chars, context_limit -> max_context_tokens, etc.
- Moved from runtime/ to config/
- Replaced _explicitly_set with model_fields_set approach in merge_with()

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Strategy configs

**Files:**
- Create: `src/nemo_oo_agents/config/strategy_config.py`
- Create: `tests/config/test_strategy_configs.py`

**Step 1: Write the failing tests**

```python
# tests/config/test_strategy_configs.py
import pytest
from pydantic import BaseModel, ValidationError
from nemo_oo_agents.config.strategy_config import (
    CodeActConfig,
    ReflexionConfig,
    StructuredOutputConfig,
)


class TestCodeActConfig:
    def test_is_pydantic_model(self):
        assert issubclass(CodeActConfig, BaseModel)

    def test_defaults(self):
        c = CodeActConfig()
        assert c.max_iterations == 10
        assert c.max_retries == 3
        assert c.cell_timeout == 600.0
        assert c.max_tokens is None
        assert c.temperature is None
        assert c.top_p is None
        assert c.max_tool_calls is None

    def test_frozen(self):
        c = CodeActConfig()
        with pytest.raises(ValidationError):
            c.max_iterations = 5

    def test_merge_with(self):
        base = CodeActConfig()
        override = CodeActConfig(max_iterations=5, temperature=0.7)
        merged = base.merge_with(override)
        assert merged.max_iterations == 5
        assert merged.temperature == 0.7
        assert merged.max_retries == 3  # not overridden


class TestStructuredOutputConfig:
    def test_defaults(self):
        c = StructuredOutputConfig()
        assert c.max_retries == 10
        assert c.max_tokens is None
        assert c.temperature is None
        assert c.top_p is None
        assert c.max_error_chars == 1000

    def test_merge_with(self):
        base = StructuredOutputConfig()
        override = StructuredOutputConfig(max_retries=5)
        merged = base.merge_with(override)
        assert merged.max_retries == 5
        assert merged.max_error_chars == 1000


class TestReflexionConfig:
    def test_defaults(self):
        c = ReflexionConfig()
        assert c.max_iterations == 3
        assert c.max_tokens is None
        assert c.temperature is None
        assert c.top_p is None

    def test_merge_with(self):
        base = ReflexionConfig()
        override = ReflexionConfig(max_iterations=5)
        merged = base.merge_with(override)
        assert merged.max_iterations == 5
```

**Step 2: Run — verify fail**

```bash
pytest tests/config/test_strategy_configs.py -v
```

**Step 3: Create `strategy_config.py`**

```python
# src/nemo_oo_agents/config/strategy_config.py
from pydantic import BaseModel, ConfigDict


def _merge_with(self, other):
    assert other.model_fields_set, (
        "merge_with() requires a freshly-constructed config."
    )
    return self.model_copy(
        update={k: getattr(other, k) for k in other.model_fields_set}
    )


class CodeActConfig(BaseModel):
    """Config for CodeActStrategy."""

    model_config = ConfigDict(frozen=True)

    max_iterations: int = 10
    max_retries: int = 3
    cell_timeout: float = 600.0       # renamed from code_execution_timeout
    max_tokens: int | None = None     # NEW: per-strategy output token cap
    temperature: float | None = None  # NEW: override model default
    top_p: float | None = None        # NEW: override model default
    max_tool_calls: int | None = None # NEW: max tool calls per turn

    def merge_with(self, other: "CodeActConfig") -> "CodeActConfig":
        return _merge_with(self, other)


class StructuredOutputConfig(BaseModel):
    """Config for StructuredOutputStrategy."""

    model_config = ConfigDict(frozen=True)

    max_retries: int = 10
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_error_chars: int = 1000       # was hardcoded 1000/2000

    def merge_with(self, other: "StructuredOutputConfig") -> "StructuredOutputConfig":
        return _merge_with(self, other)


class ReflexionConfig(BaseModel):
    """Config for ReflexionStrategy."""

    model_config = ConfigDict(frozen=True)

    max_iterations: int = 3           # renamed from max_reflections
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None

    def merge_with(self, other: "ReflexionConfig") -> "ReflexionConfig":
        return _merge_with(self, other)
```

**Step 4: Export from config `__init__.py`**

Add to `src/nemo_oo_agents/config/__init__.py`:
```python
from nemo_oo_agents.config.strategy_config import CodeActConfig, ReflexionConfig, StructuredOutputConfig
```

**Step 5: Run tests**

```bash
pytest tests/config/test_strategy_configs.py -v
```
Expected: All PASS

**Step 6: Commit**

```bash
git add src/nemo_oo_agents/config/strategy_config.py \
        src/nemo_oo_agents/config/__init__.py \
        tests/config/test_strategy_configs.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): add CodeActConfig, StructuredOutputConfig, ReflexionConfig

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Summarizer configs

**Files:**
- Create: `src/nemo_oo_agents/config/summarizer_config.py`
- Create: `tests/config/test_summarizer_configs.py`

**Step 1: Write the failing tests**

```python
# tests/config/test_summarizer_configs.py
import pytest
from pydantic import BaseModel, ValidationError
from nemo_oo_agents.config.summarizer_config import MethodSummarizerConfig, TokenBudgetConfig


class TestTokenBudgetConfig:
    def test_defaults(self):
        c = TokenBudgetConfig()
        assert c.max_tokens == 100_000
        assert c.preserve_recent == 10
        assert c.target_chars == 1000

    def test_frozen(self):
        c = TokenBudgetConfig()
        with pytest.raises(ValidationError):
            c.max_tokens = 50_000

    def test_merge_with(self):
        base = TokenBudgetConfig()
        override = TokenBudgetConfig(max_tokens=80_000)
        merged = base.merge_with(override)
        assert merged.max_tokens == 80_000
        assert merged.preserve_recent == 10


class TestMethodSummarizerConfig:
    def test_defaults(self):
        c = MethodSummarizerConfig()
        assert c.min_events == 3
        assert c.exclude_root is True
        assert c.target_chars == 1000

    def test_frozen(self):
        c = MethodSummarizerConfig()
        with pytest.raises(ValidationError):
            c.min_events = 5

    def test_merge_with(self):
        base = MethodSummarizerConfig()
        override = MethodSummarizerConfig(min_events=5)
        merged = base.merge_with(override)
        assert merged.min_events == 5
        assert merged.exclude_root is True
```

**Step 2: Run — verify fail**

```bash
pytest tests/config/test_summarizer_configs.py -v
```

**Step 3: Create `summarizer_config.py`**

```python
# src/nemo_oo_agents/config/summarizer_config.py
from pydantic import BaseModel, ConfigDict


class TokenBudgetConfig(BaseModel):
    """Config for TokenBudgetSummarizer.
    Set via: TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(...))
    """
    model_config = ConfigDict(frozen=True)

    max_tokens: int = 100_000
    preserve_recent: int = 10
    target_chars: int = 1000

    def merge_with(self, other: "TokenBudgetConfig") -> "TokenBudgetConfig":
        assert other.model_fields_set, "merge_with() requires a freshly-constructed config."
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})


class MethodSummarizerConfig(BaseModel):
    """Config for MethodSummarizer.
    Set via: MethodSummarizer.install(agent, config=MethodSummarizerConfig(...))
    """
    model_config = ConfigDict(frozen=True)

    min_events: int = 3
    exclude_root: bool = True
    target_chars: int = 1000

    def merge_with(self, other: "MethodSummarizerConfig") -> "MethodSummarizerConfig":
        assert other.model_fields_set, "merge_with() requires a freshly-constructed config."
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})
```

**Step 4: Export + run + commit** (same pattern as Task 7)

```bash
git add src/nemo_oo_agents/config/summarizer_config.py \
        tests/config/test_summarizer_configs.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): add TokenBudgetConfig and MethodSummarizerConfig

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Tool configs

**Files:**
- Create: `src/nemo_oo_agents/config/tool_configs.py`
- Create: `tests/config/test_tool_configs.py`

**Step 1: Write the failing tests**

```python
# tests/config/test_tool_configs.py
from pathlib import Path
import pytest
from pydantic import BaseModel, ValidationError
from nemo_oo_agents.config.tool_configs import BashConfig, WebSearchConfig


class TestBashConfig:
    def test_defaults(self):
        c = BashConfig()
        assert c.default_timeout == 30.0
        assert c.use_sandbox is False
        assert c.srt_settings is None
        assert c.srt_executable is None

    def test_frozen(self):
        c = BashConfig()
        with pytest.raises(ValidationError):
            c.default_timeout = 60.0

    def test_merge_with(self):
        base = BashConfig()
        override = BashConfig(default_timeout=60.0)
        merged = base.merge_with(override)
        assert merged.default_timeout == 60.0
        assert merged.use_sandbox is False


class TestWebSearchConfig:
    def test_defaults(self):
        c = WebSearchConfig()
        assert c.default_num_results == 5
        assert c.request_timeout == 10.0

    def test_frozen(self):
        c = WebSearchConfig()
        with pytest.raises(ValidationError):
            c.request_timeout = 30.0

    def test_merge_with(self):
        base = WebSearchConfig()
        override = WebSearchConfig(request_timeout=30.0)
        merged = base.merge_with(override)
        assert merged.request_timeout == 30.0
        assert merged.default_num_results == 5
```

**Step 2: Create `tool_configs.py`**

```python
# src/nemo_oo_agents/config/tool_configs.py
from pathlib import Path
from pydantic import BaseModel, ConfigDict


class BashConfig(BaseModel):
    """Config for BashTool. Set via: BashTool(config=BashConfig(...))"""

    model_config = ConfigDict(frozen=True)

    default_timeout: float = 30.0           # renamed from timeout
    use_sandbox: bool = False
    srt_settings: str | Path | None = None
    srt_executable: str | None = None

    def merge_with(self, other: "BashConfig") -> "BashConfig":
        assert other.model_fields_set, "merge_with() requires a freshly-constructed config."
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})


class WebSearchConfig(BaseModel):
    """Config for WebSearchTool. Set via: WebSearchTool(config=WebSearchConfig(...))"""

    model_config = ConfigDict(frozen=True)

    default_num_results: int = 5
    request_timeout: float = 10.0           # was hardcoded in _search_duckduckgo()

    def merge_with(self, other: "WebSearchConfig") -> "WebSearchConfig":
        assert other.model_fields_set, "merge_with() requires a freshly-constructed config."
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})
```

**Step 3: Run tests + export + commit**

```bash
pytest tests/config/test_tool_configs.py -v
# Expected: All PASS

git add src/nemo_oo_agents/config/tool_configs.py tests/config/test_tool_configs.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): add BashConfig and WebSearchConfig

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3: Wire configs into consumers

---

### Task 10: Fix BLOCKING issue — renderer token counting

This must be done before wiring RenderConfig into Agent (the render path must be correct first).

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/renderer.py`
- Modify: `packages/context-blocks/tests/test_renderer.py`

**Step 1: Write the failing tests**

Add to `packages/context-blocks/tests/test_renderer.py`:
```python
def test_render_raises_if_token_limit_set_without_counter():
    from context_blocks.renderer import render_context
    from nemo_oo_agents.config.truncation_config import TruncationConfig
    config = TruncationConfig(max_context_tokens=10_000)
    with pytest.raises(ValueError, match="max_context_tokens requires a token counter"):
        render_context(..., truncation=config, count_tokens=None)


def test_render_uses_count_tokens_when_provided():
    from context_blocks.renderer import render_context
    from nemo_oo_agents.config.truncation_config import TruncationConfig
    call_count = []
    def counter(s: str) -> int:
        call_count.append(1)
        return len(s) // 4

    config = TruncationConfig(max_context_tokens=10_000)
    render_context(..., truncation=config, count_tokens=counter)
    assert len(call_count) > 0


def test_render_accepts_none_counter_when_no_token_limits():
    from context_blocks.renderer import render_context
    from nemo_oo_agents.config.truncation_config import TruncationConfig
    config = TruncationConfig()  # no max_context_tokens or max_event_tokens
    # Should not raise
    render_context(..., truncation=config, count_tokens=None)
```

**Step 2: Update `renderer.py`**

Find the `render_context()` function signature and add `count_tokens` parameter:
```python
def render_context(
    ...,
    truncation: TruncationConfig | None = None,
    count_tokens: Callable[[str], int] | None = None,  # NEW
) -> ...:
```

At the start of `render_context()`, add validation:
```python
if truncation is not None and count_tokens is None:
    if truncation.max_context_tokens is not None or truncation.max_event_tokens is not None:
        raise ValueError(
            "max_context_tokens / max_event_tokens require a token counter. "
            "Pass count_tokens=llm.count_tokens to render_context()."
        )
```

Then replace `len(b.content)` with `count_tokens(b.content)` in the context/event budget checks:
```python
# Before:
if total + len(b.content) > truncation.max_context_tokens:
# After:
if total + count_tokens(b.content) > truncation.max_context_tokens:
```

Also update the callers of `render_context()` in the runtime/actor to pass `count_tokens=self._llm.count_tokens` (or similar — search for `render_context(` in `src/nemo_oo_agents/`).

**Step 3: Run tests**

```bash
pytest packages/context-blocks/tests/test_renderer.py -v
```
Expected: All PASS

**Step 4: Commit**

```bash
git add packages/context-blocks/src/context_blocks/renderer.py \
        packages/context-blocks/tests/test_renderer.py
git commit -m "$(cat <<'EOF'
fix(context-blocks): enforce token counting in renderer — no len() fallback

If max_context_tokens or max_event_tokens is set, count_tokens= is required.
Raises ValueError with clear message if omitted. Fixes the BLOCKING issue.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Update CodeActStrategy to use CodeActConfig

**Files:**
- Modify: `src/nemo_oo_agents/strategies/codeact.py`
- Modify: `tests/strategies/test_codeact_strategy.py`

**Step 1: Write failing tests** (add to existing test file)

```python
from nemo_oo_agents.config.strategy_config import CodeActConfig

def test_codeact_accepts_config_object():
    strategy = CodeActStrategy(config=CodeActConfig(max_iterations=5))
    assert strategy.config.max_iterations == 5

def test_codeact_default_config():
    strategy = CodeActStrategy()
    assert strategy.config.max_iterations == 10
    assert strategy.config.cell_timeout == 600.0

def test_codeact_rejects_old_flat_kwargs():
    with pytest.raises(TypeError):
        CodeActStrategy(max_iterations=5)  # old API — must fail

def test_codeact_sampling_kwargs_exclude_none():
    """Verify None sampling params are not passed to llm.acall."""
    strategy = CodeActStrategy()
    sampling = strategy._build_sampling_kwargs()
    assert "max_tokens" not in sampling
    assert "temperature" not in sampling

def test_codeact_sampling_kwargs_include_set_values():
    strategy = CodeActStrategy(config=CodeActConfig(temperature=0.7, max_tokens=1000))
    sampling = strategy._build_sampling_kwargs()
    assert sampling["temperature"] == 0.7
    assert sampling["max_tokens"] == 1000
    assert "top_p" not in sampling  # None, excluded
```

**Step 2: Update `codeact.py`**

Change `__init__`:
```python
def __init__(
    self,
    config: CodeActConfig | None = None,
    *,
    error_formatter: "IPythonErrorFormatter | None" = None,
):
    from nemo_oo_agents.config.strategy_config import CodeActConfig as _CC
    self.config = config or _CC()
    self.error_formatter = error_formatter
```

Remove the old flat attribute assignments (`self.max_iterations = ...`, etc.).

Replace all `self.max_iterations`, `self.max_retries`, `self.code_execution_timeout` references with `self.config.max_iterations`, `self.config.max_retries`, `self.config.cell_timeout`.

Add `_build_sampling_kwargs()` helper:
```python
def _build_sampling_kwargs(self) -> dict:
    return {
        k: v for k, v in {
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
        }.items() if v is not None
    }
```

In the main loop where `llm.acall()` is called, add `**self._build_sampling_kwargs()` to the call.

If `max_tool_calls` is set, add a counter in the tool call loop:
```python
tool_call_count = 0
# ... in tool call loop:
if self.config.max_tool_calls is not None:
    tool_call_count += 1
    if tool_call_count >= self.config.max_tool_calls:
        break
```

**Step 3: Run tests**

```bash
pytest tests/strategies/test_codeact_strategy.py -v
```
Expected: All PASS (including existing tests, which need their CodeActStrategy() calls updated to match new API)

**Step 4: Commit**

```bash
git add src/nemo_oo_agents/strategies/codeact.py \
        tests/strategies/test_codeact_strategy.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): CodeActStrategy accepts CodeActConfig — remove flat kwargs

- config= parameter replaces individual max_iterations, max_retries etc.
- cell_timeout replaces code_execution_timeout
- Sampling params (max_tokens, temperature, top_p) passed to llm.acall()
- max_tool_calls enforced in tool call loop

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Update StructuredOutputStrategy to use StructuredOutputConfig

**Files:**
- Modify: `src/nemo_oo_agents/strategies/structured_output.py`
- Modify: tests as needed

**Step 1: Write failing test**

```python
from nemo_oo_agents.config.strategy_config import StructuredOutputConfig

def test_structured_output_accepts_config():
    from nemo_oo_agents.strategies.structured_output import StructuredOutputStrategy
    s = StructuredOutputStrategy(config=StructuredOutputConfig(max_retries=5))
    assert s.config.max_retries == 5

def test_structured_output_rejects_flat_kwargs():
    from nemo_oo_agents.strategies.structured_output import StructuredOutputStrategy
    with pytest.raises(TypeError):
        StructuredOutputStrategy(max_retries=5)
```

**Step 2: Update `structured_output.py`**

```python
def __init__(self, config: StructuredOutputConfig | None = None):
    from nemo_oo_agents.config.strategy_config import StructuredOutputConfig as _SC
    self.config = config or _SC()
```

Replace all `self.max_retries` → `self.config.max_retries`.
Replace hardcoded `1000` / `2000` truncation values → `self.config.max_error_chars`.
Add `_build_sampling_kwargs()` and use in `llm.acall()`.

**Step 3: Run tests + commit**

```bash
pytest tests/strategies/ -k "structured" -v
git add src/nemo_oo_agents/strategies/structured_output.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): StructuredOutputStrategy accepts StructuredOutputConfig

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Update ReflexionStrategy to use ReflexionConfig

**Files:**
- Modify: `src/nemo_oo_agents/strategies/reflexion.py`
- Modify: `tests/strategies/test_reflexion_strategy.py`

**Step 1: Write failing test**

```python
def test_reflexion_accepts_config():
    s = ReflexionStrategy(config=ReflexionConfig(max_iterations=5))
    assert s.config.max_iterations == 5

def test_reflexion_rejects_old_max_reflections_kwarg():
    with pytest.raises(TypeError):
        ReflexionStrategy(max_reflections=5)  # old name, must fail
```

**Step 2: Update `reflexion.py`**

```python
def __init__(
    self,
    base: GenerationStrategy | None = None,
    config: ReflexionConfig | None = None,
):
    from nemo_oo_agents.strategies.pure_python import PurePythonStrategy
    from nemo_oo_agents.config.strategy_config import ReflexionConfig as _RC
    self.base = base if base is not None else PurePythonStrategy()
    self.config = config or _RC()
```

Replace `self.max_reflections` → `self.config.max_iterations` throughout.
Add `_build_sampling_kwargs()` and use in `llm.acall()`.

**Step 3: Run tests + commit**

```bash
pytest tests/strategies/test_reflexion_strategy.py -v
git add src/nemo_oo_agents/strategies/reflexion.py \
        tests/strategies/test_reflexion_strategy.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): ReflexionStrategy accepts ReflexionConfig

- max_reflections -> max_iterations
- config= replaces flat kwargs

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Update TokenBudgetSummarizer and MethodSummarizer

**Files:**
- Modify: `src/nemo_oo_agents/agents/summarization.py`
- Modify: `tests/agents/test_summarization_agents.py`

**Context:** Both summarizers currently use `Annotated` class attributes and accept `**kwargs` in `install()`. We replace the class attrs with a `config=` parameter on `install()` and move the values into the config objects. The `target_chars` attr on `SummarizationAgent` base class should also be removed — it moves into each config.

**Step 1: Write failing tests**

```python
from nemo_oo_agents.config.summarizer_config import TokenBudgetConfig, MethodSummarizerConfig

def test_token_budget_install_with_config(mock_agent):
    s = TokenBudgetSummarizer.install(mock_agent, config=TokenBudgetConfig(max_tokens=80_000))
    assert s.config.max_tokens == 80_000

def test_token_budget_install_rejects_flat_kwargs(mock_agent):
    with pytest.raises(TypeError):
        TokenBudgetSummarizer.install(mock_agent, max_tokens=80_000)

def test_method_summarizer_install_with_config(mock_agent):
    s = MethodSummarizer.install(mock_agent, config=MethodSummarizerConfig(min_events=5))
    assert s.config.min_events == 5
```

**Step 2: Update `summarization.py`**

Replace `TokenBudgetSummarizer` class:
- Remove `Annotated` class attrs `max_tokens`, `preserve_recent`
- Remove `target_chars` from `SummarizationAgent` base
- Add `config: TokenBudgetConfig` instance attribute (set in `install()`)
- Update `install()` to accept `config: TokenBudgetConfig | None = None` (remove `**kwargs`)
- Update `_should_summarize()` and `_compute_range()` to use `self.config.*`

Apply same pattern to `MethodSummarizer`.

The base class `SummarizationAgent.summarize()` method signature `summarize(self, history_markdown, target_chars)` needs `target_chars` to come from the subclass config:
```python
# In _run_summarization:
summary = await self.summarize(history_markdown, self.config.target_chars)
```

**Step 3: Run tests + commit**

```bash
pytest tests/agents/test_summarization_agents.py -v
git add src/nemo_oo_agents/agents/summarization.py \
        tests/agents/test_summarization_agents.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): summarizers accept config= instead of Annotated class attrs

- TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(...))
- MethodSummarizer.install(agent, config=MethodSummarizerConfig(...))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Update BashTool to use BashConfig

**Files:**
- Modify: `src/nemo_oo_agents/tools/bash_tool.py`
- Modify: `tests/tools/test_bash.py`

**Step 1: Write failing test**

```python
from nemo_oo_agents.config.tool_configs import BashConfig

def test_bash_tool_accepts_config():
    tool = BashTool(config=BashConfig(default_timeout=60.0))
    assert tool.config.default_timeout == 60.0

def test_bash_tool_rejects_flat_timeout_kwarg():
    with pytest.raises(TypeError):
        BashTool(timeout=60.0)
```

**Step 2: Update `bash_tool.py`**

```python
def __init__(
    self,
    working_dir: str | Path = ".",      # kept: per-instance, not a shared config
    config: BashConfig | None = None,
) -> None:
    from nemo_oo_agents.config.tool_configs import BashConfig as _BC
    self.working_dir = Path(working_dir).resolve()
    self.config = config or _BC()
```

Replace `self.timeout` → `self.config.default_timeout`, `self.use_sandbox` → `self.config.use_sandbox`, etc.

**Step 3: Run tests + commit**

```bash
pytest tests/tools/test_bash.py -v
git add src/nemo_oo_agents/tools/bash_tool.py tests/tools/test_bash.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): BashTool accepts BashConfig — rename timeout -> default_timeout

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Update WebSearchTool to use WebSearchConfig

**Files:**
- Modify: `src/nemo_oo_agents/tools/web_search_tool.py`

**Step 1: Write failing test**

```python
from nemo_oo_agents.config.tool_configs import WebSearchConfig

def test_web_search_tool_accepts_config():
    tool = WebSearchTool(config=WebSearchConfig(request_timeout=30.0))
    assert tool.config.request_timeout == 30.0

def test_web_search_tool_uses_config_timeout():
    """Hardcoded timeout=10 should be replaced by config value."""
    tool = WebSearchTool(config=WebSearchConfig(request_timeout=5))
    # The tool uses self.config.request_timeout internally
    assert tool.config.request_timeout == 5
```

**Step 2: Update `web_search_tool.py`**

```python
def __init__(self, config: WebSearchConfig | None = None):
    from nemo_oo_agents.config.tool_configs import WebSearchConfig as _WC
    self.config = config or _WC()
```

Replace `self.default_num_results` → `self.config.default_num_results`.
Replace hardcoded `timeout=10` at lines 74 and 129 with `timeout=self.config.request_timeout`.

**Step 3: Run tests + commit**

```bash
pytest tests/ -k "web_search" -v
git add src/nemo_oo_agents/tools/web_search_tool.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): WebSearchTool accepts WebSearchConfig — surface request_timeout

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Wire RenderConfig into Agent

**Context:** `src/nemo_oo_agents/agent.py` currently has `_block_formatter` and `_provider_formatter` as class attributes. We add `render_config: RenderConfig | None = None` to `Agent.__init__`, store it as an instance attribute, and update the runtime to read formatters from it.

**Files:**
- Modify: `src/nemo_oo_agents/agent.py`
- Modify: wherever the runtime reads `_block_formatter` / `_provider_formatter`

**Step 1: Find all usages of `_block_formatter` and `_provider_formatter`**

```bash
grep -rn "_block_formatter\|_provider_formatter" src/ tests/ --include="*.py"
```

**Step 2: Write failing test**

```python
from context_blocks.render_config import RenderConfig
from context_blocks.formatter import MarkdownBlockFormatter

def test_agent_accepts_render_config():
    agent = MyTestAgent(llm=fake_llm, render_config=RenderConfig(block_formatter=MarkdownBlockFormatter()))
    assert isinstance(agent.render_config.block_formatter, MarkdownBlockFormatter)

def test_agent_default_render_config():
    agent = MyTestAgent(llm=fake_llm)
    from context_blocks.formatter import XMLBlockFormatter
    assert isinstance(agent.render_config.block_formatter, XMLBlockFormatter)
```

**Step 3: Update `agent.py`**

In `Agent.__init__`, add:
```python
from context_blocks.render_config import RenderConfig as _RC
self.render_config = render_config or _RC()
```

Remove the class attributes:
```python
# Delete these lines:
_block_formatter: BlockFormatter = XMLBlockFormatter()
_provider_formatter: ProviderFormatter = OpenAIProviderFormatter()
```

Update `_FRAMEWORK_ATTRS` to include `render_config` instead.

Wherever the runtime previously accessed `self._block_formatter` and `self._provider_formatter`, update to `self.render_config.block_formatter` and `self.render_config.provider_formatter`.

**Step 4: Run tests + commit**

```bash
pytest tests/ -x -q
git add src/nemo_oo_agents/agent.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): Agent accepts render_config= — replace class attr formatters

- RenderConfig replaces _block_formatter and _provider_formatter class attrs
- Agent(render_config=RenderConfig(block_formatter=MarkdownBlockFormatter()))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Wire ExecutionConfig into Agent metaclass

**Context:** ExecutionConfig is set at class definition time: `class MyAgent(Agent, execution=ExecutionConfig(...))`. This requires AgentMeta to accept the `execution` keyword in `__init_subclass__`.

**Files:**
- Modify: `src/nemo_oo_agents/agent.py` (or wherever `AgentMeta` is defined)

**Step 1: Find AgentMeta**

```bash
grep -n "AgentMeta\|__init_subclass__" src/nemo_oo_agents/agent.py | head -20
```

**Step 2: Write failing test**

```python
from nemo_oo_agents.config.execution_config import ExecutionConfig

def test_agent_class_level_execution_config():
    class MyAgent(Agent, execution=ExecutionConfig(max_nesting_depth=5)):
        pass
    assert MyAgent._execution_config.max_nesting_depth == 5

def test_agent_default_execution_config():
    class MyAgent(Agent):
        pass
    assert MyAgent._execution_config.max_nesting_depth == 10
```

**Step 3: Update `AgentMeta.__init_subclass__`**

In the metaclass or `Agent.__init_subclass__`:
```python
def __init_subclass__(cls, execution: ExecutionConfig | None = None, **kwargs):
    super().__init_subclass__(**kwargs)
    from nemo_oo_agents.config.execution_config import ExecutionConfig as _EC
    cls._execution_config = execution or _EC()
```

In the actor/runtime, replace the existing nesting depth check (if any) with:
```python
if depth > self._execution_config.max_nesting_depth:
    raise RecursionError(
        f"Agent nesting depth {depth} exceeds max_nesting_depth="
        f"{self._execution_config.max_nesting_depth}. "
        "Set a higher limit with: class MyAgent(Agent, execution=ExecutionConfig(max_nesting_depth=N))"
    )
```

**Step 4: Run tests + commit**

```bash
pytest tests/ -x -q
git add src/nemo_oo_agents/agent.py
git commit -m "$(cat <<'EOF'
feat(nemo_oo_agents): ExecutionConfig wired into Agent via __init_subclass__

- class MyAgent(Agent, execution=ExecutionConfig(max_nesting_depth=5))
- Raises RecursionError with clear message when depth exceeded

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4: Final verification

---

### Task 19: Run full test suite and fix any failures

**Step 1: Run everything**

```bash
source .venv/bin/activate
pytest tests/ packages/unifiedllm/tests/ packages/context-blocks/tests/ packages/agentdoc/tests/ -v --tb=short 2>&1 | tee /tmp/test_results.txt
```

**Step 2: Triage failures**

Common failure patterns to expect:
- Any test that constructs a strategy/tool with old flat kwargs → update to `config=` form
- Any test that reads old field names (`block_limit`, `code_execution_timeout`, etc.) → update to new names
- Any test that imports from `nemo_oo_agents.runtime.truncation_config` → update to `nemo_oo_agents.config.truncation_config`

Fix each failure, run the specific test file after each fix, and commit fixes in logical groups.

**Step 3: Final commit (if needed)**

```bash
git add -p  # stage specific fixes
git commit -m "$(cat <<'EOF'
fix: update remaining test sites to new config API after unification

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Quick Reference: File Changes Summary

| Package | File | Action |
|---------|------|--------|
| unifiedllm | `retry_config.py` | **Create** — Pydantic RetryConfig |
| unifiedllm | `http_config.py` | **Create** — Pydantic HttpConfig |
| unifiedllm | `retry.py` | **Modify** — remove dataclass, import from retry_config, use rate_limit_backoff_base |
| unifiedllm | `unifiedllm.py` | **Modify** — CompletionClient accepts http_config, patch reads from config |
| agentdoc | `doc_config.py` | **Create** — Pydantic DocConfig |
| agentdoc | `config.py` | **Replace** — re-export only |
| context-blocks | `render_config.py` | **Create** — Pydantic RenderConfig |
| context-blocks | `renderer.py` | **Modify** — add count_tokens param, raise if token limit set without counter |
| nemo_oo_agents | `config/` | **Create** — new directory |
| nemo_oo_agents | `config/execution_config.py` | **Create** |
| nemo_oo_agents | `config/truncation_config.py` | **Create** — renamed fields, model_fields_set |
| nemo_oo_agents | `config/strategy_config.py` | **Create** — CodeActConfig, StructuredOutputConfig, ReflexionConfig |
| nemo_oo_agents | `config/summarizer_config.py` | **Create** — TokenBudgetConfig, MethodSummarizerConfig |
| nemo_oo_agents | `config/tool_configs.py` | **Create** — BashConfig, WebSearchConfig |
| nemo_oo_agents | `runtime/truncation_config.py` | **Delete** |
| nemo_oo_agents | `strategies/codeact.py` | **Modify** — config= API, sampling kwargs |
| nemo_oo_agents | `strategies/structured_output.py` | **Modify** — config= API |
| nemo_oo_agents | `strategies/reflexion.py` | **Modify** — config= API, max_reflections→max_iterations |
| nemo_oo_agents | `agents/summarization.py` | **Modify** — config= API, remove Annotated attrs |
| nemo_oo_agents | `tools/bash_tool.py` | **Modify** — config= API, timeout→default_timeout |
| nemo_oo_agents | `tools/web_search_tool.py` | **Modify** — config= API, surface request_timeout |
| nemo_oo_agents | `agent.py` | **Modify** — render_config= API, ExecutionConfig class kwarg |
