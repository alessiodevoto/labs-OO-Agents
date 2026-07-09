# Issue 252 — ResponsesClient does not retry transient 502 Bad Gateway errors

## Problem

`ResponsesClient.call()` / `acall()` call `litellm.responses()` / `litellm.aresponses()`
directly, bypassing the unified retry wrapper that `CompletionClient` applies via
`sync_retry` / `with_retry`. Transient `/v1/responses` failures (e.g. `502 Bad Gateway`
from `inference-api.nvidia.com`) therefore terminate agent runs even though
`RetryConfig.retryable_status_codes` includes `{429, 500, 502, 503, 504}`.

Two distinct gaps:

1. **No retry wrapping** — `ResponsesClient` has no `__init__`, so it never accepts or
   stores a `retry_config`. `CompletionClient.__init__` takes `retry_config`,
   `http_config`, and `cache_control_injection_points`; `ResponsesClient` inherits only
   `UnifiedLLM.__init__(model, **config)`. Passing `retry_config=` to a `ResponsesClient`
   today silently lands in `self.config` and gets forwarded to `litellm.responses()` as a
   bogus API param.
2. **Brittle error classification** — `_is_retryable_error()` only matches `f"status {code}"`
   substrings. LiteLLM's `BadGatewayError` stringifies as `litellm.BadGatewayError: 502 Bad
   Gateway` (no `status 502`), so even a wrapped Responses call might not recognise it.
   However, LiteLLM exception instances DO carry a `.status_code` attribute (verified:
   `BadGatewayError.status_code == 502`), which is the robust signal to key off.

## Scope

`src/nooa/unifiedllm/unifiedllm.py` (ResponsesClient) and
`src/nooa/unifiedllm/retry.py` (`_is_retryable_error`). No registry changes:
`get_llm_client(**overrides)` already forwards `retry_config` into the constructor `params`,
so once `ResponsesClient.__init__` accepts it, the registry path works for both client types.

## Changes

### 1. `ResponsesClient.__init__` — mirror `CompletionClient`

Add an `__init__` accepting `model`, `retry_config: RetryConfig | None = None`,
`http_config: HttpConfig | None = None`, `cache_control_injection_points`, and `**config`.
Mirror `CompletionClient.__init__` exactly:

- `super().__init__(model, **config)`
- `self.retry_config = retry_config`
- `self._http_config = http_config or HttpConfig()` + `_set_http_config(self._http_config)`
- default `cache_control_injection_points` to `DEFAULT_CACHE_CONTROL_INJECTION_POINTS` when None.

This also fixes the latent bug where `retry_config` leaked into `self.config` →
`litellm.responses()`.

### 2. Wrap the litellm call in `call()` / `acall()`

In `ResponsesClient.call()`:

```python
def _make_call():
    return cast("litellm.ResponsesAPIResponse", litellm.responses(**api_params))

with _track_llm_call(model=self.model, endpoint=self.config.get("api_base")):
    raw_response = (
        sync_retry(_make_call, config=self.retry_config)
        if self.retry_config
        else _make_call()
    )
```

In `ResponsesClient.acall()`, the async analogue with `litellm.aresponses` and
`with_retry`. This mirrors `CompletionClient` (including the `_track_llm_call` tracking
context, for debugging parity). Everything downstream of `raw_response` is unchanged.

### 3. Broaden `_is_retryable_error()` in `retry.py`

Add, before the existing string checks, a robust attribute-based check:

```python
status_code = getattr(error, "status_code", None)
if isinstance(status_code, int):
    if status_code == 429:
        return True, True
    if status_code in config.retryable_status_codes:
        return True, False
```

Keep the existing `"status 429"` / `"rate limit"` and `f"status {code}"` string checks as
fallbacks. Additionally, map each retryable status code to provider/LiteLLM phrase
fallbacks for cases where only a stringified error is available (e.g. raw gateway HTML):

```python
_RETRYABLE_STATUS_PHRASES = {
    500: ("internal server error", "internalservererror"),
    502: ("bad gateway", "badgatewayerror"),
    503: ("service unavailable", "serviceunavailableerror"),
    504: ("gateway timeout", "gateway time-out"),
}
```

In the per-code loop, also test `_RETRYABLE_STATUS_PHRASES.get(code, ())` substrings. Only
phrases for codes actually in `config.retryable_status_codes` are consulted, so disabling a
code in config still disables its phrase. `error_str` is already lowercased, so class-name
fallbacks like `badgatewayerror` match `str(litellm.BadGatewayError(...))`.

## Edge cases

- `retry_config=None` (default): behaviour unchanged — single direct call, no wrapping.
- 429 via `.status_code` must return `is_rate_limit=True` so rate-limit extra-retries/backoff apply.
- Non-retryable statuses (e.g. 400/401) must NOT be retried — `.status_code` check only
  fires for codes in `retryable_status_codes`; phrase map contains no 4xx-client phrases.
- `_track_llm_call` added to ResponsesClient for parity; it is a context manager that only
  records debug state, no behavioural change.

## Tests

New unit test file `tests/unifiedllm/test_responses_client_retry.py` (mock-based, not
integration — `litellm.responses`/`aresponses` patched):

1. `call()` retries on a transient `litellm.BadGatewayError` then succeeds; assert call
   count == 2 and result returned. Uses `RetryConfig(max_retries=2, base_delay=0.01)`.
2. `acall()` same with `litellm.aresponses` (AsyncMock).
3. `call()` with no `retry_config` does NOT retry — raises on first `BadGatewayError`,
   call count == 1.
4. Retries exhausted raises the underlying error; call count == max_retries + 1.
5. Non-retryable error (e.g. 400 / `BadRequestError`) is not retried (count == 1).
6. `retry_config=` passed to `ResponsesClient(...)` is stored on `self.retry_config` and
   does NOT leak into `self.config` (regression for the latent forwarding bug).

Extend `tests/unifiedllm/test_retry.py::TestIsRetryableError`:

7. `litellm.BadGatewayError` instance (has `.status_code == 502`) → retryable, not rate limit.
8. Bare string `"litellm.BadGatewayError: 502 Bad Gateway"` (no `status 502`) → retryable
   via phrase fallback.
9. `"503 Service Unavailable"` / `"504 Gateway Timeout"` strings → retryable.
10. A 502-style error remains NON-retryable when `retryable_status_codes` excludes 502.

## Verification

`uv run pytest tests/unifiedllm/test_retry.py tests/unifiedllm/test_responses_client_retry.py
tests/unifiedllm/test_empty_content_retry.py -q` and `uv run ruff check` on touched files.
