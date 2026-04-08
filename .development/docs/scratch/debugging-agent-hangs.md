# Debugging Agent Hangs

## Quick Reference

When an agent hangs, send SIGUSR2 to get a full stack trace + all executed code:

```bash
kill -USR2 <pid>
```

Output is written to:
- stderr (terminal)
- `~/.cache/agent006/debug_dump_<pid>.txt`

## Setup

**Automatic** - installed when you `import agent006`. No setup needed.

## What You Get

1. **LLM call status** - Shows if stuck waiting for an LLM API response
2. **Full Python traceback** with source lines (including generated Cell code)
3. **Pending LLM calls** with model, duration, and endpoint
4. **All Cell code** executed in the session (from linecache)
5. **Timestamp** and dump file location

## Finding the PID

```bash
# Find agent processes
ps aux | grep python | grep agent

# Or if running in a known terminal, use $! for last background process
```

## Example Output

```
============================================================
DEBUG DUMP at 2026-01-16T14:30:00.123456
Signal: 17 (SIGUSR2)
Dump file: /Users/you/.cache/agent006/debug_dump_12345.txt
============================================================

⚠️  LIKELY STUCK IN LLM CALL
----------------------------------------
Detected in stack:
  • LiteLLM at .../litellm/main.py:1234 in acompletion()
  • HTTP client (httpx) at .../httpx/_client.py:567 in send()
Registered pending calls: 1

CURRENT TRACEBACK:
----------------------------------------
  File "src/agent006/runtime/actor.py", line 642
    result_value = await asyncio.wait_for(coro, timeout=timeout)
  ...
  File ".../litellm/main.py", line 1234, in acompletion
    response = await client.post(...)
...

============================================================
PENDING LLM CALLS
============================================================

--- llm_1 ---
  Model: gpt-4
  Waiting: 45.3s
  Started: 2026-01-16T14:29:15.123456
  Endpoint: https://api.openai.com/v1

============================================================
REGISTERED CELL CODE (from linecache)
============================================================

--- Cell exec_abc123[1] (8 lines) ---
   1: async def __repl_wrapper__():
   2:     import requests
   3:     # ... code that's hanging
```

## Automatic Watchdog (eval-pipeline)

Both `eval-pipeline` and `run_ablation.py` support `--hang-timeout` to automatically
send SIGUSR2 if no progress is made:

```bash
# Send debug dump if no sample completes for 5 minutes
python -m eval_pipeline --config config.yaml --hang-timeout 300

# Same for ablation runs
python run_ablation.py --benchmark bfcl --hang-timeout 300
```
