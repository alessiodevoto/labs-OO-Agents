# LLM Error Handling Investigation

## Executive Summary

**Finding**: The pure python strategy implementation does **NOT** explicitly handle LLM API errors or timeouts. The error handling relies on implicit propagation through the call stack and litellm's built-in retry mechanisms.

## Call Flow Analysis

### 1. Pure Python Strategy → Runtime Generate

In `src/nemo_oo_agents/strategies/pure_python.py` (line 201):
```python
response, event_id = await runtime.generate(tools=[])
```

**No error handling**: The strategy directly awaits `runtime.generate()` with no try/except block.

### 2. Runtime Generate → LLM Client

In `src/nemo_oo_agents/runtime/actor.py` (lines 113-173):
```python
async def generate(
    self,
    *,
    tools: list[dict] | None = None,
    output_model: type | None = None,
    **kwargs: Any,
) -> tuple[Any, str]:
    # ... build messages ...

    # Use acall for unified LLM interface
    response = await llm_client.acall(
        messages,
        tools=tools or [],
        output_model=output_model,
        **kwargs,
    )

    # Create and record AssistantEvent
    event = AssistantEvent(content=content)
    event_id = self.history.add(event)

    return response, event_id
```

**No error handling**: The runtime directly calls `llm_client.acall()` with no try/except.

### 3. UnifiedLLM Client → LiteLLM

In `packages/unifiedllm/src/unifiedllm/unifiedllm.py` (lines 520-600):
```python
async def acall(
    self,
    messages: list[dict[str, Any]],
    tools: list[Tool] | None = None,
    output_model: type[BaseModel] | None = None,
    **kwargs,
) -> LLMResponse:
    api_params = {
        "model": self.model,
        "messages": messages,
        **self.config,
        **kwargs,
    }

    # Direct call to litellm - NO ERROR HANDLING
    raw_response = await litellm.acompletion(**api_params)

    # Process response...
    return LLMResponse(...)
```

**No error handling**: The client directly calls `litellm.acompletion()` with no try/except.

## What Error Handling Exists?

### 1. UnifiedLLM Has Retry Methods (But They're Not Used)

The `UnifiedLLM` base class provides `acall_llm_with_retry()` (lines 305-353) which handles:
- `ValidationError` (Pydantic validation failures)
- `json.JSONDecodeError` (JSON parsing failures)
- `ValueError` (general value errors)

**But this is NOT used** by the strategies. The strategies call `llm_client.acall()` directly, not `acall_llm_with_retry()`.

### 2. LiteLLM Built-in Retries

LiteLLM has built-in retry logic for:
- Rate limit errors (429)
- Server errors (500, 502, 503)
- Connection timeouts
- API timeouts

**Configuration**: LiteLLM retry behavior can be configured via:
```python
litellm.set_verbose = True  # Enable logging
litellm.num_retries = 3     # Default retry count
litellm.request_timeout = 600  # Timeout in seconds
```

However, **we don't explicitly configure this** in nemo_oo_agents. LiteLLM uses its default configuration.

### 3. Strategy-Level Error Loop Handles Execution Errors Only

In `pure_python.py` (lines 146-179):
```python
while not session.is_exhausted():
    code = await self._generate_code(runtime, session)  # ← LLM call here

    if not code:
        session.record_error()
        await self._send_empty_response_error(runtime, call.method_name)
        continue

    result = await self._execute_code(runtime, code, builtins, session)

    if result.error:  # ← Only handles execution errors
        session.record_error()
        await self._send_execution_error(runtime, result.error)
        continue
```

This loop handles:
- ✅ Empty LLM responses
- ✅ Code execution errors (syntax, runtime exceptions)
- ❌ LLM API errors (not caught)
- ❌ LLM timeouts (not caught)

## What Happens When LLM API Fails?

### Scenario 1: Rate Limit (429)
1. LiteLLM catches it
2. LiteLLM retries automatically (up to `num_retries` times)
3. If exhausted, raises `openai.RateLimitError` or similar
4. Exception propagates up through `runtime.generate()` → strategy → agent
5. **Agent call fails with unhandled exception**

### Scenario 2: Network Timeout
1. LiteLLM's underlying HTTP client times out
2. Raises `httpx.TimeoutException` or similar
3. Exception propagates up
4. **Agent call fails with unhandled exception**

### Scenario 3: API Server Error (500, 502, 503)
1. LiteLLM catches it
2. LiteLLM retries automatically
3. If exhausted, raises exception
4. **Agent call fails with unhandled exception**

### Scenario 4: Invalid API Key
1. LiteLLM raises `AuthenticationError`
2. No retries (not retryable)
3. **Agent call fails immediately with unhandled exception**

## Comparison with Other Components

### Evaluation Framework Has Better Error Handling

In `evaluation/llm/batching_client.py` (lines 233-280):
```python
try:
    response = await with_retry(
        self.client.acall,
        messages,
        tools=tools,
        output_model=output_model,
        config=retry_config,  # ← Explicit retry config
        **opts,
    )
except Exception as e:
    self.metrics.total_errors += 1
    logger.error(
        f"LLM request failed after retries: {type(e).__name__}: {str(e)[:200]}"
    )
    raise
```

Features:
- Explicit retry configuration (max_retries, delays, exponential backoff)
- Rate limit detection and extra retries
- Metrics tracking (retries, errors, queue times)
- Proper error logging

**This is NOT used in the main agent runtime.**

## Recommendations

### 1. Add Explicit Error Handling in Runtime.generate()

```python
async def generate(
    self,
    *,
    tools: list[dict] | None = None,
    output_model: type | None = None,
    max_retries: int = 3,
    **kwargs: Any,
) -> tuple[Any, str]:
    """Build messages from context + history, call LLM with retry logic."""

    last_error = None
    for attempt in range(max_retries):
        try:
            # Build messages
            messages = await self._build_messages(...)

            # Get LLM client
            llm_client = _current_llm_var.get()
            if llm_client is None:
                raise RuntimeError("No LLM client in context")

            # Call LLM
            response = await llm_client.acall(
                messages,
                tools=tools or [],
                output_model=output_model,
                **kwargs,
            )

            # Success - record and return
            content = response.content or ""
            if hasattr(content, "model_dump_json"):
                content = content.model_dump_json()
            event = AssistantEvent(content=content)
            event_id = self.history.add(event)

            return response, event_id

        except Exception as e:
            last_error = e

            # Check if retryable
            if _is_retryable_llm_error(e):
                logger.warning(
                    f"LLM call failed (attempt {attempt + 1}/{max_retries}): "
                    f"{type(e).__name__}: {str(e)[:100]}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue

            # Not retryable or max retries reached
            raise

    # Should not reach here
    raise last_error or RuntimeError("LLM call failed after retries")


def _is_retryable_llm_error(error: Exception) -> bool:
    """Check if an LLM error should be retried."""
    error_type = type(error).__name__
    error_msg = str(error).lower()

    # Retryable: rate limits, timeouts, server errors
    retryable_types = [
        "RateLimitError",
        "TimeoutError",
        "Timeout",
        "APITimeoutError",
        "ServiceUnavailableError",
        "InternalServerError",
        "APIConnectionError",
    ]

    if any(t in error_type for t in retryable_types):
        return True

    # Check error message for common patterns
    if any(msg in error_msg for msg in ["rate limit", "timeout", "503", "502", "500"]):
        return True

    return False
```

### 2. Configure LiteLLM Defaults

Add configuration in agent initialization or global setup:

```python
import litellm

# Configure retry behavior
litellm.num_retries = 3
litellm.request_timeout = 120  # 2 minutes
litellm.drop_params = True  # Drop unsupported params instead of erroring

# Enable detailed logging (optional, for debugging)
# litellm.set_verbose = True
```

### 3. Add Error Events to History

When an LLM error occurs and is retried, add an event to history:

```python
from nemo_oo_agents.events import ErrorEvent

runtime.history.add(
    ErrorEvent(
        content=f"LLM API error (attempt {attempt}/{max_retries}): {type(e).__name__}"
    )
)
```

This helps with:
- Debugging (visible in traces)
- Transparency (user knows why delay occurred)
- Observability (can track API reliability)

### 4. Strategy-Level Timeout

Add a timeout parameter to strategies:

```python
class PurePythonStrategy(CompositeStrategy):
    def __init__(
        self,
        *,
        max_iterations: int = 10,
        max_retries: int = 3,
        llm_timeout: float = 120.0,  # ← NEW
    ):
        self.max_iterations = max_iterations
        self.max_retries = max_retries
        self.llm_timeout = llm_timeout

    async def _generate_code(self, runtime: RuntimeServices, session: GenerationSession) -> str:
        try:
            # Pass timeout to generate()
            response, event_id = await asyncio.wait_for(
                runtime.generate(tools=[], timeout=self.llm_timeout),
                timeout=self.llm_timeout + 5.0,  # Buffer for overhead
            )
            # ... rest of code ...
        except asyncio.TimeoutError:
            session.record_error()
            await self._send_timeout_error(runtime, session)
            return ""
```

### 5. Use Evaluation's Retry Logic

Consider extracting `evaluation/llm/retry.py` into a shared module and using it in the main runtime:

```python
from nemo_oo_agents.llm.retry import with_retry, RetryConfig

response = await with_retry(
    llm_client.acall,
    messages,
    tools=tools,
    config=RetryConfig(
        max_retries=3,
        base_delay=1.0,
        max_delay=60.0,
        rate_limit_extra_retries=5,
    ),
    **kwargs,
)
```

## Security Considerations

### Current Risk: API Key Exposure

If an authentication error occurs, the exception might contain the API key or endpoint details. Ensure error messages are sanitized:

```python
except Exception as e:
    # Sanitize error message
    error_msg = str(e)
    error_msg = re.sub(r'(api[_-]?key["\s]*[:=]["\s]*)[^\s"]+', r'\1***REDACTED***', error_msg)
    logger.error(f"LLM error: {error_msg}")
    raise
```

## Testing Recommendations

Add tests for error scenarios:

```python
# tests/runtime/test_llm_error_handling.py

@pytest.mark.asyncio
async def test_llm_timeout_retry():
    """Test that LLM timeouts trigger retries."""
    mock_llm = Mock()
    mock_llm.acall = AsyncMock(side_effect=[
        TimeoutError("Request timed out"),
        TimeoutError("Request timed out"),
        LLMResponse(content="result", tool_calls=[], finish_reason="stop"),
    ])

    agent = TestAgent(llm=mock_llm)
    result = await agent.process()

    # Should succeed after 2 retries
    assert result is not None
    assert mock_llm.acall.call_count == 3


@pytest.mark.asyncio
async def test_llm_rate_limit_exponential_backoff():
    """Test exponential backoff on rate limits."""
    mock_llm = Mock()
    mock_llm.acall = AsyncMock(side_effect=[
        RateLimitError("Rate limit exceeded"),
        RateLimitError("Rate limit exceeded"),
        LLMResponse(content="result", tool_calls=[], finish_reason="stop"),
    ])

    start = time.time()
    agent = TestAgent(llm=mock_llm)
    await agent.process()
    duration = time.time() - start

    # Should have delays: 1s, 2s = 3s minimum
    assert duration >= 3.0
    assert mock_llm.acall.call_count == 3


@pytest.mark.asyncio
async def test_llm_non_retryable_error():
    """Test that authentication errors fail immediately."""
    mock_llm = Mock()
    mock_llm.acall = AsyncMock(side_effect=AuthenticationError("Invalid API key"))

    agent = TestAgent(llm=mock_llm)

    with pytest.raises(AuthenticationError):
        await agent.process()

    # Should NOT retry
    assert mock_llm.acall.call_count == 1
```

## Conclusion

**Current State**:
- ❌ No explicit LLM error handling in pure python strategy
- ❌ No explicit error handling in runtime.generate()
- ❌ No explicit error handling in unifiedllm (except unused retry methods)
- ✅ LiteLLM provides implicit retry for some errors (with defaults)
- ✅ Evaluation framework has proper retry logic (but not used in main runtime)

**Impact**:
- **Medium**: LiteLLM's defaults provide some protection
- **Risk**: Unhandled timeouts, rate limits, or server errors will crash agent execution
- **No visibility**: Errors are not logged or tracked in agent history

**Recommendation**: Implement explicit error handling at the runtime.generate() level with:
- Retry logic for transient errors
- Exponential backoff
- Error events in history
- Proper logging
- Timeout configuration
