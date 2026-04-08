# Optimizer API Resilience Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make e2e_optimization robust to transient API failures by migrating from direct litellm calls to UnifiedLLM with built-in retry logic.

**Architecture:** Replace `litellm.acompletion()` in `reflector.py` with `UnifiedLLM` client wrapped by `with_retry()`. Add configurable `RetryConfig` passed from optimizer config.

**Tech Stack:** UnifiedLLM, litellm (underlying), RetryConfig with exponential backoff

**Design Doc:** `docs/plans/2026-02-18-optimizer-api-resilience-design.md`

---

## Pre-Implementation

### Task 0: Verify UnifiedLLM is available

**Step 1: Check unifiedllm is in dependencies**

Run: `cd /localhome/local-rcabral/nemo_oo_agents/util/e2e_optimization && grep -r "unifiedllm" pyproject.toml`

Expected: Should find unifiedllm in dependencies or workspace references

**Step 2: If not found, add dependency**

If missing, edit `util/e2e_optimization/pyproject.toml`:
```toml
[project]
dependencies = [
    # ... existing deps
    "unifiedllm",
]

[tool.uv.sources]
unifiedllm = { workspace = true }
```

**Step 3: Sync dependencies**

Run: `cd /localhome/local-rcabral/nemo_oo_agents/util/e2e_optimization && uv sync`

Expected: Dependencies installed successfully

---

## Task 1: Add RetryConfig Support to Reflector

**Files:**
- Modify: `util/e2e_optimization/src/e2e_optimization/reflector.py`

**Step 1: Add imports at top of reflector.py**

After line 17 (after existing imports), add:

```python
from unifiedllm import RetryConfig, UnifiedLLM, with_retry
```

**Step 2: Update Reflector.__init__ signature**

Find the `__init__` method (~line 56) and update:

```python
def __init__(
    self,
    model: str = "gpt-4",
    temperature: float = 0.7,
    max_tokens: int = 8000,
    endpoint: str | None = None,
    api_key: str | None = None,
    retry_config: RetryConfig | None = None,
):
    """Initialize reflector.

    Args:
        model: LLM model name
        temperature: Sampling temperature
        max_tokens: Maximum tokens in response
        endpoint: Optional API endpoint override
        api_key: Optional API key override
        retry_config: Optional retry configuration for LLM calls
    """
    self.model = model
    self.temperature = temperature
    self.max_tokens = max_tokens
    self.endpoint = endpoint
    self.api_key = api_key
    self.retry_config = retry_config or RetryConfig(
        max_retries=5,
        base_delay=2.0,
        max_delay=60.0,
        rate_limit_extra_retries=3,
    )
```

**Step 3: Verify syntax**

Run: `python -m py_compile util/e2e_optimization/src/e2e_optimization/reflector.py`

Expected: No output (successful compilation)

**Step 4: Commit**

```bash
git add util/e2e_optimization/src/e2e_optimization/reflector.py
git commit -m "feat(reflector): add RetryConfig parameter to Reflector

Add retry_config parameter to Reflector.__init__ with sensible defaults.
Import UnifiedLLM and retry utilities.

Part of optimizer API resilience implementation.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Migrate reflect() to use UnifiedLLM

**Files:**
- Modify: `util/e2e_optimization/src/e2e_optimization/reflector.py`

**Step 1: Create UnifiedLLM client in __init__**

After the retry_config assignment in `__init__`, add:

```python
    # Create UnifiedLLM client for making LLM calls with retry
    self.client = UnifiedLLM(
        model=self.model,
        temperature=self.temperature,
        max_tokens=self.max_tokens,
        api_base=self.endpoint,
        api_key=self.api_key,
    )
```

**Step 2: Replace litellm.acompletion in reflect() method**

Find the `async def reflect()` method (~line 198). Replace the try/except block starting at line 207:

**OLD CODE (lines 207-234):**
```python
    import litellm

    prompt = self.build_prompt(context)
    logger.info(f"Reflection prompt: {len(prompt)} chars")

    try:
        response = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            api_key=self.api_key,
            api_base=self.endpoint,
            timeout=600,
        )
        content = response.choices[0].message.content
        tokens = getattr(response, "usage", None)
        tokens_used = tokens.total_tokens if tokens else 0

    except Exception as e:
        logger.error(f"Reflection LLM call failed: {e}")
        return ReflectionResult(
            success=False,
            new_code={},
            change_summary="",
            raw_response="",
            error=str(e),
        )
```

**NEW CODE:**
```python
    prompt = self.build_prompt(context)
    logger.info(f"Reflection prompt: {len(prompt)} chars")

    try:
        # Use with_retry wrapper for automatic retry on transient failures
        response = await with_retry(
            self.client.acompletion,
            messages=[{"role": "user", "content": prompt}],
            timeout=600,
            config=self.retry_config,
        )
        content = response.choices[0].message.content
        tokens = response.usage.total_tokens if response.usage else 0
        tokens_used = tokens

    except Exception as e:
        logger.error(f"Reflection LLM call failed after retries: {e}")
        return ReflectionResult(
            success=False,
            new_code={},
            change_summary="",
            raw_response="",
            error=str(e),
        )
```

**Step 3: Verify syntax**

Run: `python -m py_compile util/e2e_optimization/src/e2e_optimization/reflector.py`

Expected: No output (successful compilation)

**Step 4: Commit**

```bash
git add util/e2e_optimization/src/e2e_optimization/reflector.py
git commit -m "feat(reflector): migrate to UnifiedLLM with retry support

Replace direct litellm.acompletion() calls with UnifiedLLM client
wrapped by with_retry() for automatic retry on transient failures.

- Create UnifiedLLM client in __init__
- Use with_retry() in reflect() method
- Retries handle connection errors, 500s, timeouts, rate limits

Part of optimizer API resilience implementation.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Update Optimizer to pass retry_config

**Files:**
- Modify: `util/e2e_optimization/src/e2e_optimization/optimizer.py`

**Step 1: Find where Reflector is instantiated**

Search for where Reflector is created. It's likely in the `__init__` or setup method of the Optimizer class.

Run: `grep -n "Reflector(" util/e2e_optimization/src/e2e_optimization/optimizer.py`

Expected: Shows line number(s) where Reflector is instantiated

**Step 2: Import RetryConfig at top of optimizer.py**

Add to imports section:

```python
from .reflector import Reflector, ReflectionContext, ReflectionResult
from unifiedllm import RetryConfig
```

**Step 3: Load retry config from self.opt dict**

Where Reflector is instantiated (likely in reflect() method around line 1440), update to:

```python
# Load retry config from optimizer config
retry_config = None
if "llm" in self.opt and "retry" in self.opt["llm"]:
    retry_config = RetryConfig(**self.opt["llm"]["retry"])

reflector = Reflector(
    model=model,
    temperature=temperature,
    max_tokens=max_tokens,
    endpoint=endpoint,
    api_key=api_key,
    retry_config=retry_config,  # Add this line
)
```

**Step 4: Verify syntax**

Run: `python -m py_compile util/e2e_optimization/src/e2e_optimization/optimizer.py`

Expected: No output (successful compilation)

**Step 5: Commit**

```bash
git add util/e2e_optimization/src/e2e_optimization/optimizer.py
git commit -m "feat(optimizer): pass retry config to Reflector

Load retry configuration from optimizer config and pass to Reflector.
Allows users to customize retry behavior via config.yaml.

Part of optimizer API resilience implementation.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Add retry config to DABStep config

**Files:**
- Modify: `util/e2e_optimization/src/e2e_optimization/examples/dabstep/config.yaml`

**Step 1: Add retry section to llm config**

Edit the config file and add retry section under llm:

```yaml
llm:
  model: "nvidia_internal/aws/anthropic/bedrock-claude-sonnet-4-5-v1"
  temperature: 0.7
  max_tokens: 8000
  endpoint: null
  api_key: null
  retry:
    max_retries: 5
    base_delay: 2.0
    max_delay: 60.0
    rate_limit_extra_retries: 3
```

**Step 2: Verify YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('util/e2e_optimization/src/e2e_optimization/examples/dabstep/config.yaml'))"`

Expected: No output (valid YAML)

**Step 3: Commit**

```bash
git add util/e2e_optimization/src/e2e_optimization/examples/dabstep/config.yaml
git commit -m "config(dabstep): add LLM retry configuration

Add retry config with sensible defaults:
- 5 max retries with exponential backoff (2s base, 60s cap)
- 3 extra retries for rate limit errors

Part of optimizer API resilience implementation.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Test with small optimization (verification)

**Files:**
- None (testing only)

**Step 1: Run a minimal test optimization**

Run a 1-iteration optimization with 3 test cases to verify it works:

```bash
cd /localhome/local-rcabral/nemo_oo_agents/util/e2e_optimization
source ../../.venv/bin/activate
python -m e2e_optimization.examples.dabstep.run_optimization \
  --max-iterations 1 \
  --n-samples 3 \
  --n-runs 1
```

Expected: Should complete without errors. Check logs for retry messages (if any transient failures occur).

**Step 2: Verify logs show retry capability**

Search logs for retry-related messages:

```bash
# Look for retry initialization or usage
grep -i "retry" /path/to/latest/optimization/log.txt
```

Expected: Should see retry config being used (or no retries if no failures)

**Step 3: Document test results**

Create a quick note about the test:

```bash
echo "Test run on $(date): 1-iteration optimization completed successfully with retry support" >> docs/plans/2026-02-18-optimizer-api-resilience-test-log.md
```

---

## Task 6: Continue from iteration 3 (production test)

**Files:**
- None (running optimization)

**Step 1: Resume optimization from iteration 3**

```bash
cd /localhome/local-rcabral/nemo_oo_agents/util/e2e_optimization
source ../../.venv/bin/activate

# Resume from last completed iteration
python -m e2e_optimization resume \
  src/e2e_optimization/examples/dabstep/results/dabstep/dabstep_20260217_133715/iteration_003
```

**Step 2: If resume doesn't work, use run_loop**

If the resume command doesn't continue to new iterations (it only completes interrupted ones):

```bash
# Start fresh iteration 4 using the existing results directory
python -m e2e_optimization.examples.dabstep.run_optimization \
  --max-iterations 10 \
  --resume-from src/e2e_optimization/examples/dabstep/results/dabstep/dabstep_20260217_133715
```

(Note: Check if run_optimization has a --resume-from flag. If not, may need to implement continuation logic.)

**Step 3: Monitor for retries**

While running, monitor logs in another terminal:

```bash
tail -f /path/to/latest/run/logs | grep -i "retry\|connection\|timeout"
```

Expected: Should see retry messages if transient failures occur, and they should recover automatically.

**Step 4: Wait for completion**

Let the optimization run to completion (iteration 10). This may take several hours.

Expected: Should complete without manual intervention. Retries should handle transient connection errors.

---

## Success Criteria

1. ✅ **Code changes compile** - No syntax errors in modified files
2. ✅ **Small test completes** - 1-iteration optimization runs successfully
3. ✅ **Full optimization completes** - Iteration 3 → 10 without manual intervention
4. ✅ **Retries are observable** - Logs show retry attempts when transient failures occur
5. ✅ **No regression** - Pass rate and code quality comparable to previous runs

## Rollback Plan

If issues arise:

```bash
# Revert all changes
git revert dace1a6..HEAD

# Or revert specific commits
git log --oneline  # Find commit hashes
git revert <commit-hash>
```

## Future Enhancements

If retries alone don't solve all issues:

1. **Granular checkpointing** - Save state after each phase (eval, analysis, reflection)
2. **Progress heartbeats** - Log keepalives during long operations
3. **Timeout tuning** - Adjust reflection timeout based on prompt size
4. **Streaming improvements** - Better streaming handling to avoid gateway timeouts

## Notes

- UnifiedLLM already has comprehensive retry logic tested in `packages/unifiedllm/tests/test_retry.py`
- Default retry config (5 retries, 2s base, 60s cap) should handle most transient issues
- Max wait per reflection call: ~50 minutes (600s × 5 retries + backoff)
- Rate limits get extra retries (3 more) since they're predictable and recoverable
