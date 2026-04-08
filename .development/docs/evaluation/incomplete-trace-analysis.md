# Incomplete Trace Analysis

## Problem

Trace file appears incomplete: `default_qwen3-next-80b_sentiment_single_20251211_192136_021447.006trace.jsonl`

- Trace has 4 events (only 7KB)
- LLM call (`acompletion` span) has input but **no output**
- Test timed out after 60 seconds
- Experiment run: `capabilitytests_20251211_192136`

## Root Cause Analysis

### What Happened

1. **LLM Call Duration**: The `acompletion` span shows the LLM call took ~60 seconds
   - Start: `1765477296049846000` ns
   - End: `1765477356023416000` ns
   - Duration: `59973570000` ns (~60 seconds)

2. **Test Timeout**: The test has a 60-second timeout (`DEFAULT_TEST_TIMEOUT = 60` in [runner.py:397](../util/prompt-optimization/runner.py#L397))

3. **Race Condition**: The asyncio timeout fired at approximately the same time the LLM response arrived:
   - `asyncio.wait_for(run_custom_test(...), timeout=60)` at [runner.py:1173](../util/prompt-optimization/runner.py#L1173)
   - The TimeoutError was raised before the LLM response could be processed

4. **Missing Output**: The `acompletion` span has:
   - ✅ Input messages (`llm.input_messages.*`)
   - ❌ No output messages (`llm.output_messages.*`)
   - Status: `UNSET` (not `OK` or `ERROR`)

### Why the Output is Missing

The OpenTelemetry span lifecycle works as follows:

```
span.start()
  → set input attributes
  → make LLM call
  → [TIMEOUT HAPPENS HERE - asyncio.TimeoutError raised]
  → set output attributes (NEVER REACHED)
  → span.end()
```

When the asyncio timeout fires:
1. The `TimeoutError` exception propagates up
2. The test handler catches it at [runner.py:1189](../util/prompt-optimization/runner.py#L1189)
3. The span may be force-closed by the OTel SDK without output attributes
4. The litellm instrumentation's response handler never runs

### Program Flow

```
runner.py:main()
  └─> run_suite()
      └─> run_custom_test() wrapped in asyncio.wait_for(timeout=60)
          └─> test_function (e.g., test_sentiment_single)
              └─> agent.classify_single()
                  └─> @plan method execution
                      ├─> before_generation hook (creates generation span)
                      ├─> LLM call via litellm
                      │   └─> litellm instrumentation creates acompletion span
                      │       ├─> records input (✅)
                      │       ├─> waits for response (~60s)
                      │       └─> [TIMEOUT] records output (❌ never reached)
                      └─> after_generation hook
```

## Why This Matters

The incomplete trace makes debugging difficult because:
- Can't see the LLM response (even partial)
- Can't determine if the LLM actually returned something
- Can't analyze whether the timeout was due to:
  - Slow LLM response
  - Network issues
  - Actual hang/deadlock

## How to Debug

### 1. Check if the LLM Response Actually Arrived

The span shows an `end_time`, which suggests the HTTP request completed. But did the response contain data?

**Action**: Add logging in the LLM client to capture response status before span completion:

```python
# In unifiedllm.py or litellm wrapper
response = await litellm.acompletion(...)
logger.info(f"LLM response received: {response.choices[0].message.content[:100] if response.choices else 'NO CHOICES'}")
return response
```

### 2. Check Span Export Timing

OpenTelemetry uses `SimpleSpanProcessor` which exports spans synchronously on `span.end()`. If the timeout happens during span processing, the output might not be set yet.

**Action**: Check when spans are actually written to the JSONL file:

```python
# In _jsonl_exporter.py
def export(self, spans):
    logger.info(f"Exporting {len(spans)} spans at {time.time()}")
    # existing export logic
```

### 3. Increase Timeout or Add Progressive Timeout

**Short-term fix**: Increase test timeout to 120 seconds for slow models:

```yaml
# In config/sentiment.yaml
test_cases:
  sentiment_single:
    timeout: 120  # Increase from default 60s
```

**Better fix**: Add progressive timeout warnings:

```python
# In runner.py
async def run_with_timeout_warnings(coro, timeout):
    """Run coroutine with progress warnings at 30s, 45s, 55s."""
    warnings = [30, 45, 55]

    async def warn_at(delay):
        await asyncio.sleep(delay)
        print(f"  ⚠️  Still running after {delay}s...")

    tasks = [coro]
    for delay in warnings:
        tasks.append(warn_at(delay))

    done, pending = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)

    if not done or coro not in done:
        raise TimeoutError()

    return await coro
```

### 4. Capture Partial LLM Response on Timeout

The litellm library may have partial response data even when a timeout occurs. We should try to capture it:

```python
# In runner.py timeout handler
except TimeoutError:
    # Try to extract any partial LLM response from the exporter's buffer
    if "agent" in test_state:
        agent = test_state["agent"]
        # Check if there's a pending litellm response in flight
        # (This would require hooking into litellm's internal state)
```

### 5. Add Span Finalization Hooks

Add a hook to ensure spans are finalized even on timeout:

```python
# In openinference_instrumentation_agent006/_hooks_impl.py
def ensure_span_finalized(self, span, timeout_occurred=False):
    """Ensure span has minimal data even on timeout."""
    if timeout_occurred and span.is_recording():
        span.add_event("timeout_occurred")
        span.set_attribute("timeout", True)
        # Try to get partial response if available
```

## How to Get Better Info in Traces

### 1. Add Intermediate Events

Add span events at key points to track progress:

```python
# In LLM client wrapper
span = trace.get_current_span()
span.add_event("llm_request_sent")
response = await litellm.acompletion(...)
span.add_event("llm_response_received")
span.set_attribute("llm.response_size", len(response.choices[0].message.content))
```

### 2. Use Streaming for Long-Running Calls

For models that take >30s, use streaming to get partial results:

```python
# In unifiedllm.py
async def acompletion_with_streaming(self, **kwargs):
    """Use streaming for long-running requests to detect hangs."""
    kwargs["stream"] = True
    chunks = []

    async for chunk in await litellm.acompletion(**kwargs):
        chunks.append(chunk)
        # Record progress in span
        span = trace.get_current_span()
        span.add_event("chunk_received", {"chunk_size": len(str(chunk))})

    return self._combine_chunks(chunks)
```

### 3. Add Timeout Context to Spans

When a timeout occurs, add context to all open spans:

```python
# In runner.py timeout handler
except TimeoutError:
    # Mark all open spans with timeout context
    from opentelemetry import trace
    span = trace.get_current_span()
    if span.is_recording():
        span.add_event("test_timeout", {
            "timeout_seconds": test_timeout,
            "timestamp": time.time()
        })
```

### 4. Export Spans Immediately on Timeout

Ensure spans are flushed before the process exits:

```python
# In runner.py timeout handler
except TimeoutError:
    # Force span export before handling timeout
    from openinference_instrumentation_agent006 import get_current_exporter
    exporter = get_current_exporter()
    if exporter:
        exporter.force_flush()  # Add this method to JSONLSpanExporter
```

### 5. Add Test-Level Telemetry

Add a wrapper span around the entire test to capture test-level metadata:

```python
# In runner.py before test execution
with tracer.start_as_current_span("test_execution") as test_span:
    test_span.set_attribute("test_id", test_id)
    test_span.set_attribute("model", model_id)
    test_span.set_attribute("timeout", test_timeout)

    try:
        result = await asyncio.wait_for(run_custom_test(...), timeout=test_timeout)
        test_span.set_attribute("test_passed", result["passed"])
    except TimeoutError:
        test_span.set_attribute("test_timeout", True)
        test_span.add_event("timeout_details", {
            "elapsed": time.time() - start_time,
            "open_spans": len(open_spans),
        })
```

## Recommendations

### Immediate Actions

1. **Increase timeout** for qwen3-next-80b to 120s (model is consistently slow)
2. **Add timeout warnings** at 30s, 45s, 55s to track progress
3. **Log LLM response arrival** before span attributes are set

### Short-term Improvements

1. **Add span events** for request sent / response received
2. **Force flush spans** on timeout before exception propagates
3. **Capture partial response** from litellm internal state if available

### Long-term Architecture Changes

1. **Use streaming** for models with >30s typical latency
2. **Implement progressive timeout** (warn at intervals, fail at max)
3. **Add test-level span** to wrap entire test execution
4. **Buffer span attributes** and flush atomically to prevent incomplete exports

## Related Files

- [runner.py](../util/prompt-optimization/runner.py) - Test runner with timeout logic
- [otel_hooks.py](../util/prompt-optimization/otel_hooks.py) - Tracing instrumentation
- [_hooks_impl.py](../packages/openinference-instrumentation-agent006/src/openinference_instrumentation_agent006/_hooks_impl.py) - Hook implementation
- [_jsonl_exporter.py](../packages/openinference-instrumentation-agent006/src/openinference_instrumentation_agent006/_jsonl_exporter.py) - Span exporter
