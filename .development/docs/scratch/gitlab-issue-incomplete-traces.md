# GitLab Issue: Incomplete Trace Files Due to Test Timeout Race Condition

## Title
Incomplete trace files when LLM calls timeout - missing output attributes and response data

## Labels
- `bug`
- `tracing`
- `enhancement`
- `prompt-optimization`

## Priority
**Medium** - Impacts debugging capability but doesn't break core functionality

---

## Problem Description

Trace files are incomplete when LLM calls take longer than the test timeout. The trace shows the LLM request but **no response data**, making it impossible to debug whether the LLM actually responded or what went wrong.

### Example
- Trace: `default_qwen3-next-80b_sentiment_single_20251211_192136_021447.006trace.jsonl`
- Experiment: http://0.0.0.0:5003/#/experiment/capabilitytests_20251211_192136
- Trace viewer: http://0.0.0.0:5001/?file=default_qwen3-next-80b_sentiment_single_20251211_192136_021447.006trace.jsonl

**Observed behavior:**
- Trace has only 4 events (7KB file)
- `acompletion` span has input messages but **no output messages**
- Span status: `UNSET` (incomplete)
- Test error: `TimeoutError: Test exceeded 60s limit`

### Key Evidence

```json
// From trace file - acompletion span
{
  "name": "acompletion",
  "duration_ns": 59973570000,  // ~60 seconds
  "attributes": {
    "llm.input_messages.0.message.content": "...",  // ✅ Has input
    // ❌ No llm.output_messages.* attributes
    "openinference.span.kind": "LLM"
  },
  "status": {"status_code": "UNSET"}  // ⚠️ Not OK or ERROR
}
```

```json
// From experiment results
{
  "error": "TimeoutError: Test exceeded 60s limit",
  "trace": {
    "llm_output": null,  // ❌ No LLM response captured
    "message": "TIMEOUT after 60s (partial trace: 1 turns captured)"
  }
}
```

---

## Root Cause Analysis

### The Race Condition

1. **LLM call duration**: qwen3-next-80b takes ~60 seconds to respond
2. **Test timeout**: Default timeout is 60 seconds (`DEFAULT_TEST_TIMEOUT = 60`)
3. **Timeout fires**: `asyncio.wait_for()` raises `TimeoutError` at approximately the same time the HTTP response arrives
4. **Missing output**: The litellm instrumentation's response handler never completes, so output attributes are never set on the span

### Program Flow

```
runner.py:asyncio.wait_for(run_custom_test(...), timeout=60)
  └─> test_function()
      └─> agent.classify_single()
          └─> LLM call via litellm
              ├─> litellm instrumentation creates acompletion span
              ├─> span.set_attribute("llm.input_messages.*")  ✅
              ├─> await HTTP request (~60s)
              ├─> [TIMEOUT HAPPENS HERE - TimeoutError raised]
              └─> span.set_attribute("llm.output_messages.*")  ❌ NEVER REACHED
```

### Why Output Attributes Are Missing

OpenTelemetry's span lifecycle:
1. `span.start()` - span is recording
2. Set input attributes - ✅ completes
3. Make async LLM call - ⏳ waiting
4. **TimeoutError propagates** - exception unwinds stack
5. Span is force-closed by OTel SDK or context manager
6. Set output attributes - ❌ never reached
7. `span.end()` - span exported with partial data

---

## Impact

### Debugging Impact
- **Cannot see LLM response** (even if it arrived)
- **Cannot determine actual failure mode**:
  - Did the LLM respond but slowly?
  - Was there a network hang?
  - Did the model actually timeout?
- **Partial trace data** makes root cause analysis difficult

### User Experience Impact
- Users see "timeout" but can't tell if it's a model issue or configuration issue
- No actionable error information in traces
- Have to correlate with external logs (if they exist)

---

## Proposed Solutions

### 1. ✅ Make Test Timeout Configurable (Required)

Currently, timeout is hardcoded to 60 seconds. Different models have different latencies:
- `gpt-4o-mini`: ~2-5 seconds
- `qwen3-next-80b`: ~60 seconds
- `o1-preview`: can take 90+ seconds for complex reasoning

**Implementation:**

```yaml
# In config/sentiment.yaml
test_cases:
  sentiment_single:
    timeout: 120  # Per-test timeout override

# In config/models.yaml
models:
  qwen3-next-80b:
    model_name: "nvidia_nim/qwen/qwen3-next-80b-a3b-instruct"
    timeout: 120  # Per-model default timeout
```

```python
# In runner.py
def get_test_timeout(test_config: dict, model_config: dict) -> int:
    """Get timeout for test, checking test config, then model config, then default."""
    return (
        test_config.get("timeout")
        or model_config.get("timeout")
        or DEFAULT_TEST_TIMEOUT
    )

# Usage
test_timeout = get_test_timeout(test_config, model_config)
result = await asyncio.wait_for(
    run_custom_test(...),
    timeout=test_timeout
)
```

**CLI support:**
```bash
# Override timeout via CLI
python runner.py config/sentiment.yaml --timeout 120

# Or per-model
python runner.py config/sentiment.yaml --model-timeout qwen3-next-80b=120
```

### 2. ✅ Add Progressive Timeout Warnings

Show progress indicators at intervals to distinguish "slow" from "hung":

```python
async def run_with_timeout_warnings(coro, timeout: int):
    """Run coroutine with progress warnings."""
    warn_intervals = [timeout * 0.5, timeout * 0.75, timeout * 0.9]

    async def warn_at(delay: float, label: str):
        await asyncio.sleep(delay)
        print(f"  ⚠️  {label} (after {delay:.0f}s)")

    # Start warning tasks
    warning_tasks = [
        asyncio.create_task(warn_at(d, f"Still running ({i+1}/{len(warn_intervals)})"))
        for i, d in enumerate(warn_intervals)
    ]

    # Wait for completion or timeout
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    finally:
        # Cancel any pending warnings
        for task in warning_tasks:
            task.cancel()
```

### 3. ✅ Force Flush Spans on Timeout

Ensure spans are exported before timeout exception propagates:

```python
# In runner.py timeout handler
except TimeoutError:
    # Force immediate span export
    from openinference_instrumentation_nemo_oo_agents import get_current_exporter
    exporter = get_current_exporter()
    if exporter:
        exporter.force_flush()  # Ensure spans are written

    # Then handle timeout...
```

**Add to JSONLSpanExporter:**
```python
# In _jsonl_exporter.py
def force_flush(self, timeout_millis: int = 5000) -> bool:
    """Force flush any buffered spans.

    Returns True if flush succeeded within timeout.
    """
    # SimpleSpanProcessor exports immediately, but ensure file is synced
    if self._file:
        self._file.flush()
        os.fsync(self._file.fileno())
    return True
```

### 4. ✅ Add Span Events for Progress Tracking

Add events at key points to track where timeout occurred:

```python
# In litellm wrapper or hooks
def before_llm_call(self, span):
    span.add_event("llm.request.start", {
        "timestamp": time.time(),
        "model": model_name
    })

def after_llm_call(self, span, response):
    span.add_event("llm.response.received", {
        "timestamp": time.time(),
        "content_length": len(response.choices[0].message.content) if response.choices else 0
    })
```

### 5. ✅ Add Timeout Context to Spans

When timeout occurs, annotate open spans with context:

```python
# In runner.py timeout handler
except TimeoutError:
    # Annotate current span with timeout info
    from opentelemetry import trace
    span = trace.get_current_span()
    if span and span.is_recording():
        span.add_event("test.timeout", {
            "timeout_seconds": test_timeout,
            "elapsed_seconds": time.time() - start_time,
            "test_id": test_id,
            "model": model_id
        })
        span.set_status(Status(StatusCode.ERROR, "Test timeout"))
```

### 6. Consider: Use Streaming for Slow Models

For models with >30s typical latency, use streaming to:
- Detect partial progress
- Get faster feedback
- Identify actual hangs vs. slow responses

```python
# In unifiedllm.py
async def acompletion_with_timeout_detection(self, timeout: int, **kwargs):
    """Use streaming to detect progress and avoid false timeouts."""
    if timeout > 30:
        # Use streaming for long-running requests
        kwargs["stream"] = True
        chunks = []
        last_chunk_time = time.time()

        async for chunk in await litellm.acompletion(**kwargs):
            chunks.append(chunk)
            last_chunk_time = time.time()

            # Check for stall (no chunks for 30s)
            if time.time() - last_chunk_time > 30:
                raise TimeoutError("No response chunks for 30s - possible hang")

        return self._combine_chunks(chunks)
    else:
        # Use regular mode for fast models
        return await litellm.acompletion(**kwargs)
```

---

## Acceptance Criteria

### Must Have (P0)
- [ ] Test timeout is configurable per-test in YAML config
- [ ] Test timeout is configurable per-model in models.yaml
- [ ] Test timeout is configurable via CLI (`--timeout` flag)
- [ ] Timeout resolution order: CLI > test config > model config > default
- [ ] Default timeout remains 60s for backward compatibility
- [ ] Documentation updated with timeout configuration examples

### Should Have (P1)
- [ ] Progressive timeout warnings at 50%, 75%, 90% of timeout
- [ ] Spans are force-flushed on timeout (spans written to disk)
- [ ] Timeout events added to spans with context (elapsed time, model, test ID)
- [ ] Integration test for timeout handling with slow mock LLM

### Nice to Have (P2)
- [ ] Span events for "request.start" and "response.received"
- [ ] Streaming mode for models with timeout > 30s
- [ ] Timeout statistics in experiment summary (avg time, timeout rate)

---

## Technical Notes

### Files to Modify

1. **`util/prompt-optimization/runner.py`**
   - Add `get_test_timeout()` function
   - Add CLI `--timeout` and `--model-timeout` args
   - Add timeout warning wrapper
   - Add span flush on timeout
   - Add timeout event to spans

2. **`util/prompt-optimization/config/*.yaml`**
   - Add `timeout` field to test cases
   - Document in schema/examples

3. **`util/config/models.yaml`**
   - Add `timeout` field to model definitions
   - Set appropriate defaults (qwen3-next-80b: 120, o1: 180)

4. **`packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_jsonl_exporter.py`**
   - Add `force_flush()` method

5. **`packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_hooks_impl.py`**
   - Add span events for LLM request/response milestones

### Testing Strategy

```python
# Test case for timeout configuration
async def test_timeout_configuration():
    """Test that timeout can be configured at multiple levels."""
    # Test 1: CLI override
    args = ["--timeout", "120"]
    timeout = get_test_timeout_from_args(args)
    assert timeout == 120

    # Test 2: Test-level config
    test_config = {"timeout": 90}
    timeout = get_test_timeout(test_config, {})
    assert timeout == 90

    # Test 3: Model-level config
    model_config = {"timeout": 120}
    timeout = get_test_timeout({}, model_config)
    assert timeout == 120

    # Test 4: Resolution order (CLI > test > model > default)
    timeout = get_test_timeout(
        test_config={"timeout": 90},
        model_config={"timeout": 120},
        cli_timeout=150
    )
    assert timeout == 150

async def test_timeout_span_annotation():
    """Test that timeout adds event to span."""
    with tracer.start_as_current_span("test") as span:
        try:
            await asyncio.wait_for(slow_task(), timeout=1)
        except TimeoutError:
            # Should add timeout event to span
            pass

    # Verify span has timeout event
    events = get_span_events(span)
    assert any(e["name"] == "test.timeout" for e in events)
```

---

## Related Issues

- #XXX - Add span events for better observability (if exists)
- #XXX - Improve error reporting in test runner (if exists)

---

## Additional Context

### Reference Documentation
- Analysis document: `docs/incomplete-trace-analysis.md`
- OpenTelemetry span lifecycle: https://opentelemetry.io/docs/concepts/signals/traces/
- OpenInference conventions: https://github.com/Arize-ai/openinference

### Example Incomplete Trace
- File: `util/prompt-optimization/traces/default_qwen3-next-80b_sentiment_single_20251211_192136_021447.006trace.jsonl`
- Size: 7KB (only 4 events)
- Missing: LLM output messages, completion status

### Slow Models (Need Higher Timeouts)
- `qwen3-next-80b`: 60-90s typical
- `o1-preview`: 90-180s with reasoning
- `o1-mini`: 30-60s
- `deepseek-r1`: 45-90s with reasoning

---

## Implementation Plan

### Phase 1: Configuration (1-2 days)
1. Add timeout fields to YAML configs
2. Add CLI arguments
3. Implement timeout resolution logic
4. Add tests for configuration
5. Update documentation

### Phase 2: Observability (1 day)
1. Add progressive warnings
2. Add span events
3. Add timeout annotations
4. Add force flush on timeout

### Phase 3: Testing & Validation (1 day)
1. Integration tests with slow mock LLM
2. Test timeout at various levels (CLI, test, model)
3. Verify spans are complete up to timeout
4. Verify timeout warnings appear

### Phase 4: Documentation (0.5 days)
1. Update README with timeout configuration
2. Add troubleshooting guide
3. Add examples for slow models

**Total Estimate: 3-4 days**

---

## Questions for Discussion

1. Should we have a maximum timeout cap? (e.g., 300s = 5min)
2. Should streaming be opt-in or automatic for timeout > 30s?
3. Should we retry timeouts automatically (with exponential backoff)?
4. Should timeout thresholds be model-specific in code vs. config?
