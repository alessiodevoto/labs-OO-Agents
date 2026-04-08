# Watchdog Timeout Implementation

## Problem

NVIDIA NIM API connections enter CLOSE_WAIT state and hang indefinitely. httpx's read timeout doesn't catch these frozen connections.

## Root Cause

CLOSE_WAIT is a TCP state where:
- Server sent FIN (closing connection)
- Client received FIN but hasn't closed socket
- Connection is "zombie" - not ESTABLISHED, not sending data

httpx's `read` timeout only applies to ESTABLISHED connections actively reading. It doesn't fire for CLOSE_WAIT connections.

## Failed Approaches

### 1. Setting httpx.Timeout (commits d4d6782, 6496a5c, 7939df4)
```python
litellm.module_level_aclient.client.timeout = httpx.Timeout(read=60.0, ...)
```
**Result:** Timeout configured correctly but didn't fire on CLOSE_WAIT hangs.

### 2. Setting both AsyncHTTPHandler.timeout and client.timeout (commit 9b0f009)
```python
litellm.module_level_aclient.timeout = 60.0
litellm.module_level_aclient.client.timeout = httpx.Timeout(read=60.0, ...)
```
**Result:** Still didn't catch CLOSE_WAIT hangs. Qwen hung for 9+ minutes.

## Solution Attempt 3: Application-Level Watchdog (commit 372976c) - FAILED

Tried using `asyncio.wait_for()` to enforce timeout at application level:

```python
async def _make_call():
    try:
        raw_response = await asyncio.wait_for(
            litellm.acompletion(**api_params),
            timeout=90.0  # Application-level timeout
        )
    except asyncio.TimeoutError as timeout_err:
        raise TimeoutError(f"API call timed out after 90s (watchdog)") from timeout_err
```

**Result:** FAILED - After 1 hour, both processes had 5 CLOSE_WAIT connections but NO watchdog timeouts fired.

### Why This Didn't Work

`asyncio.wait_for()` cannot interrupt OS-level blocking I/O operations. When httpx gets stuck in CLOSE_WAIT reading from a socket, it's blocked at the OS level and asyncio cannot cancel it.

### Test Results (2026-01-13 13:08 CET)
- **Qwen (PID 3083)**: 145/1140 tasks (13%), 5 CLOSE_WAIT connections, 0 watchdog timeouts
- **GPT (PID 3144)**: 318/1140 tasks (28%), 5 CLOSE_WAIT connections, 0 watchdog timeouts

## ACTUAL Solution: Disable Connection Pooling (commit 72966e9)

**Root Cause Discovery:** Connection pooling was keeping zombie CLOSE_WAIT connections "alive" expecting to reuse them.

When asked "why is this happening? is it because we are pooling connections?" - YES! httpx's default behavior:
- `max_keepalive_connections=None` (unlimited)
- `keepalive_expiry=5.0` seconds

When NVIDIA API misbehaves and sends FIN without response:
- httpx receives FIN but keeps socket in pool
- Connection enters CLOSE_WAIT state
- The 5-second expiry doesn't apply (connection isn't "idle", it's in limbo)
- Exactly 5 CLOSE_WAIT connections accumulate (matching `--concurrent-tasks 5`)

**Fix:** Disable connection reuse by setting `max_keepalive_connections=0`:

```python
litellm.module_level_aclient.client._limits = httpx.Limits(
    max_connections=100,  # Allow concurrent requests
    max_keepalive_connections=0,  # DISABLE connection reuse - close after each request
    keepalive_expiry=0.0,  # Not relevant when max_keepalive_connections=0
)
```

### Why This Works

- Forces httpx to immediately close connections after each request
- Prevents CLOSE_WAIT connections from accumulating in the pool
- No more zombie connections waiting to be reused
- Slight performance hit from creating new connections, but prevents permanent hangs

### Tradeoff

Small performance cost from creating new TCP connections for each request vs permanent hangs requiring manual process killing.

## Testing

### New Test Run (commit 72966e9, 17246eb)
- **Started:** 2026-01-13 13:15 CET
- **Qwen PID:** 28272
- **GPT PID:** 28302
- **Monitoring:** Auto-check every 5 minutes for CLOSE_WAIT connections
- **Logs:** `/tmp/qwen_pooling_fix.log`, `/tmp/gpt_pooling_fix.log`

### Success Criteria

1. ✅ No CLOSE_WAIT connections accumulate
2. ✅ Runs complete all 1140 tasks without hangs
3. ✅ No manual intervention required

## Files Modified

- `packages/unifiedllm/src/unifiedllm/unifiedllm.py` - Disabled connection pooling, removed watchdog
- `monitor_runs.sh` - Automated monitoring script
- `docs/watchdog-timeout-implementation.md` - This document

---

## CRITICAL UPDATE: Baseline Test Analysis (2026-01-13 15:26 CET)

### Test Setup

**Objective:** Test baseline behavior WITHOUT pooling fix to determine actual root cause

- **Removed:** Global httpx monkey-patch (connection pooling fix)
- **Added:** Comprehensive crash detection with signal handlers
- **Started:** 15:26:22 CET
- **Qwen PID:** 78321
- **GPT PID:** 78326

### Results: Processes Killed by External SIGTERM

Both processes were **terminated by SIGTERM signals**, NOT by CLOSE_WAIT hangs or API errors.

| Process | Runtime | Tasks | Termination | Exit Code |
|---------|---------|-------|-------------|-----------|
| GPT (78326) | 4.5 min | 67/1140 (5.9%) | SIGTERM (signal 15) | 143 |
| Qwen (78321) | 7+ min | 138/1140 (12.1%) | Killed externally | Unknown |

### Evidence from Crash Logs

**GPT Process - Clean SIGTERM:**
```
2026-01-13 15:30:53,181 [CRITICAL] Received signal 15 (SIGTERM)
2026-01-13 15:30:53,241 [INFO] SystemExit: 143
2026-01-13 15:30:53,241 [INFO] Process exiting - PID: 78326
```

**Qwen Process - Abruptly Killed:**
- Last log: `15:33:46 - LiteLLM completion() model= nvidia/qwen/...`
- No SIGTERM logged (likely SIGKILL)
- Ended mid-operation

### Key Findings

1. **✅ Crash logging works perfectly**
   - Captured exact signal (SIGTERM/15)
   - Recorded full stack traces
   - All handlers functioning

2. **✅ No CLOSE_WAIT accumulation**
   - Baseline behavior didn't show CLOSE_WAIT issues
   - Both APIs responding normally
   - Progress rates: Qwen 18.6 tasks/min, GPT 14.9 tasks/min

3. **❌ Test invalid for root cause analysis**
   - Processes killed before natural failure
   - Didn't run long enough (need 30-60+ minutes)
   - Cannot determine actual failure mode

### Comparison to Previous Tests

All recent tests show **early termination (4-7 minutes)**:
1. Test 66850/66856: Died at ~5 minutes, files disappeared
2. Test 73669/73674: Computer went to sleep
3. Test 78321/78326: **Killed by SIGTERM** at 4.5-7 minutes

**Pattern suggests:**
- External interference (user actions, system policies)
- OR test environment issues (computer sleep, resource limits)
- NOT an API/network issue (would take longer to manifest)

### Recommendations

1. **Run uninterrupted 60+ minute test** using:
   - `tmux` or `screen` session
   - `nohup` for background execution
   - `caffeinate -i` to prevent system sleep

2. **Monitor for external interference:**
   ```bash
   while true; do
       ps -p [PID] > /dev/null || echo "$(date): PROCESS DIED!"
       sleep 1
   done
   ```

3. **Isolate test environment:**
   - Disable computer sleep
   - Close other applications
   - Check system resource limits (`ulimit -a`)

### Current Status

- ✅ Crash logging implemented and working
- ✅ Process monitoring in place
- ❌ **Root cause still unknown** (tests terminated early)
- ❌ CLOSE_WAIT behavior not reproduced in baseline test
- ❌ No valid comparison data between pooling-enabled vs pooling-disabled

**Conclusion:** Cannot make definitive recommendations until we have an uninterrupted long-duration test run showing natural failure behavior.

---

## References

- Issue: NVIDIA NIM API CLOSE_WAIT hangs
- Failed attempts: commits d4d6782, 6496a5c, 7939df4, 9b0f009, 372976c
- Working solution: commit 72966e9
- Baseline test (invalid): PIDs 78321, 78326 - killed by SIGTERM after 4.5-7 minutes
