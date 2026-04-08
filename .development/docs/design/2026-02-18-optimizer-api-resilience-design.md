# E2E Optimizer API Resilience Design

**Date:** 2026-02-18
**Status:** Approved
**Author:** Claude + User

## Problem Statement

The E2E optimizer fails on transient API issues:
- **Observed:** 9 consecutive connection errors during continuation attempt
- **Impact:** 8+ hour optimization runs fail and must be restarted from scratch
- **Root cause:** No retry logic - all LLM calls fail immediately on transient errors

## Solution: Migrate to UnifiedLLM with Retry Support

**Decision:** Replace direct `litellm` calls with `UnifiedLLM`, which provides built-in retry logic.

### Why UnifiedLLM?
- ✅ Already has retry logic implemented and tested
- ✅ Used across agent006 codebase
- ✅ Handles transient errors (429, 500, timeouts, connections)
- ✅ Exponential backoff with configurable parameters
- ✅ Observable via logging and callbacks

## Architecture

### Current Implementation (reflector.py)

```python
import litellm

response = await litellm.acompletion(
    model=self.model,
    messages=[{"role": "user", "content": prompt}],
    max_tokens=self.max_tokens,
    temperature=self.temperature,
    api_key=self.api_key,
    api_base=self.endpoint,
    timeout=600,
)
```

### Proposed Implementation

```python
from unifiedllm import UnifiedLLM, RetryConfig, with_retry

class Reflector:
    def __init__(
        self,
        model: str = "gpt-4",
        temperature: float = 0.7,
        max_tokens: int = 8000,
        endpoint: str | None = None,
        api_key: str | None = None,
        retry_config: RetryConfig | None = None,
    ):
        self.client = UnifiedLLM(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_base=endpoint,
            api_key=api_key,
        )
        self.retry_config = retry_config or RetryConfig(
            max_retries=5,
            base_delay=2.0,
            max_delay=60.0,
            rate_limit_extra_retries=3,
        )

    async def reflect(self, context: ReflectionContext) -> ReflectionResult:
        prompt = self.build_prompt(context)

        try:
            response = await with_retry(
                self.client.acompletion,
                messages=[{"role": "user", "content": prompt}],
                timeout=600,
                config=self.retry_config,
            )
            content = response.choices[0].message.content
            tokens = response.usage.total_tokens if response.usage else 0
            # ... continue with code extraction
```

## Configuration

Add retry config to optimizer YAML:

```yaml
# config.yaml
llm:
  model: "nvidia_internal/aws/anthropic/bedrock-claude-sonnet-4-5-v1"
  temperature: 0.7
  max_tokens: 8000
  retry:
    max_retries: 5           # Number of retries per attempt
    base_delay: 2.0           # Initial backoff (seconds)
    max_delay: 60.0           # Cap on backoff time
    rate_limit_extra_retries: 3  # Extra retries for 429s
```

Optimizer passes config to Reflector:

```python
# optimizer.py
retry_config = RetryConfig(**config["llm"].get("retry", {}))
reflector = Reflector(
    model=config["llm"]["model"],
    temperature=config["llm"]["temperature"],
    retry_config=retry_config,
)
```

## Retry Behavior

### Transient Errors (will retry)
- `litellm.InternalServerError` (500 errors, connection errors)
- `litellm.Timeout` / `litellm.APIConnectionError`
- `litellm.RateLimitError` (429 - with longer backoff)

### Permanent Errors (fail fast)
- `litellm.AuthenticationError` (invalid API keys)
- `litellm.BadRequestError` (400 - malformed requests)
- Unknown exceptions (safer to fail than retry blindly)

### Backoff Schedule
- Attempt 1: immediate
- Attempt 2: wait 2s
- Attempt 3: wait 4s
- Attempt 4: wait 8s
- Attempt 5: wait 16s
- Attempt 6: wait 32s (capped at max_delay=60s)

**Max wait time per reflection:** ~600s per attempt × 5 retries + 62s backoff ≈ 50 minutes

## Files to Modify

1. **reflector.py**
   - Replace `litellm.acompletion()` with `UnifiedLLM` + `with_retry()`
   - Add `retry_config` parameter to `__init__`
   - Update error handling (UnifiedLLM may raise different exceptions)

2. **optimizer.py**
   - Pass `retry_config` when creating Reflector instance
   - Load retry config from YAML

3. **config.yaml** (optional)
   - Add `llm.retry` section with custom retry parameters
   - Defaults are sensible, but users can tune if needed

## Testing Plan

### 1. Unit Tests
Mock UnifiedLLM to simulate connection errors:
- Verify retries happen according to backoff schedule
- Verify transient errors retry, permanent errors don't
- Verify max_retries honored

### 2. Integration Test
Run small optimization (2 iterations, 3 test cases):
- Verify it completes without errors
- Check logs for retry messages (if any transient failures occur)

### 3. Production Test
Continue from iteration 3 → iteration 10:
- Monitor logs for retry behavior
- Verify completion without manual intervention
- Compare runtime to baseline (should be similar unless retries needed)

## Limitations

**What this solves:**
- ✅ Brief connection blips (network instability)
- ✅ Temporary server errors (500, 502, 503)
- ✅ Rate limits (429)
- ✅ Gateway timeouts (504)

**What this doesn't solve:**
- ❌ Extended API outages (hours-long downtime)
- ❌ Process crashes (OOM, infrastructure failures)
- ❌ Legitimately slow operations (if reflection truly takes 8 hours)

For those scenarios, would need:
- Granular state checkpointing (Approach 2 from brainstorm)
- Better timeout tuning or streaming keepalives
- Process monitoring and auto-restart

## Success Criteria

1. **Wrapper script scenario** (9 connection errors) → Should succeed after 2-3 retries
2. **Full optimization** (iteration 3 → 10) → Completes without manual intervention
3. **Observable** → Logs show retry attempts when transient failures occur
4. **No regression** → Pass rate and code quality unchanged

## Next Steps

1. Implement changes to reflector.py
2. Update optimizer.py to pass retry config
3. Add retry config to dabstep config.yaml
4. Test locally with small optimization
5. Run full optimization (iteration 3 → 10)
6. Monitor and document results
