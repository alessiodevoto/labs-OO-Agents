# Evaluation Framework Fixes Needed

## Issue #1: Environment tools not registered with agent's tool registry

**Status:** ✅ FIXED

**Problem:** Environment tools were injected via `setattr()` but not registered with the agent's `ToolRegistry`, so the LLM never saw them as available tools.

**Root Cause:** In `run_ablation.py` line 560-562, environment tools were set as attributes but baseline_react agent uses a `ToolRegistry` to track available tools for the LLM.

**Fix Applied:** In `_run_with_environment()`, after injecting tools via `setattr()`, also register them with the agent's tool registry:

```python
# Inject environment tools into agent (multi-step envs may provide tools)
env_tools = env.get_tools()
for tool_name, tool_instance in env_tools.items():
    setattr(agent, tool_name, tool_instance)

    # Also register with agent's tool registry if it has one
    if hasattr(agent, "registry"):
        prefix = tool_name + "_"
        agent.registry.register_from_class(tool_instance, prefix=prefix)
```

**File:** `experiments/evaluation-ablations/run_ablation.py` lines 559-567

**Result:** Now the agent sees ALL tools (generic FileTools, SandboxedCommandLine, WebSearchTool + environment-specific tools like intercode_execute) and can choose the appropriate one.

---

## Issue #2: Missing `ensure_docker_available()` method

**Problem:** InterCodeEnvironment.reset() calls `self.ensure_docker_available()` (line 116) but method doesn't exist.

**File:** `evaluation/environments/intercode.py`

**Fix - Add method:**
```python
def ensure_docker_available(self) -> None:
    """Check that Docker is running and accessible."""
    try:
        import docker
        client = docker.from_env()
        client.ping()
    except ImportError as e:
        raise RuntimeError(
            "Docker SDK is required for InterCode. Install with: pip install docker"
        ) from e
    except Exception as e:
        raise RuntimeError(
            f"Docker is not available: {e}\n"
            "Make sure Docker is installed and running."
        ) from e
```

---

## Issue #3: Missing conversation_history tracking

**Status:** ✅ FIXED

**Problem:** `run_ablation.py` line 581 checks `if hasattr(env, "conversation_history")` but InterCodeEnvironment and SWEBenchEnvironment didn't track it.

**Files Fixed:**
- `evaluation/environments/intercode.py`
- `evaluation/environments/swebench.py`

**Solution Applied:** Added `_conversation_history` tracking to both environments following the pattern already used in TauBenchEnvironment:

1. Initialize `_conversation_history: list[dict[str, Any]] = []` in `__init__`
2. Reset to `[]` in `reset()`
3. Append step data in `step()` with keys: `role`, `action`, `observation`, `reward`, `done`, `step`
4. Clear in `close()`
5. Expose as property: `@property def conversation_history(self) -> list[dict[str, Any]]`

---

## Issue #4: InterCode adapter trajectory format verification

**Status:** ✅ FIXED

**Problem:** Adapter expected trajectory as dict with "steps" key, but `_run_with_environment` was returning list directly.

**Root Cause:**
- `run_ablation.py` returned `{"trajectory": trajectory, ...}` where `trajectory` was a list
- `InterCodeAdapter._extract_trajectory()` checks `isinstance(trajectory_data, dict)` and expects `data.get("steps", [])`

**Fix Applied:** Wrap trajectory list in dict with "steps" key in `_run_with_environment()`:

```python
# Multi-step: return structured output with trajectory
return {
    "trajectory": {
        "steps": trajectory,  # trajectory is list from env.conversation_history
        "total_steps": len(trajectory),
    },
    "output": output,
    "total_steps": len(trajectory),
    "success": True,
}
```

**Files:**
- `experiments/evaluation-ablations/run_ablation.py:595-598` (success case)
- `experiments/evaluation-ablations/run_ablation.py:612-615` (error case)

---

## Issue #5: Better agent compatibility detection

**Status:** ✅ FIXED

**Problem:** Line 565 in `run_ablation.py` checks `hasattr(agent, "run_in_environment")` but silently falls back if missing.

**Fix Applied:** Added warning log when multi-step environment is used with agent that doesn't support it:

```python
# Run the agent
if not is_single_step and not hasattr(agent, "run_in_environment"):
    # Multi-step environment but agent doesn't support it - warn!
    import logging
    logging.warning(
        f"Agent {type(agent).__name__} doesn't support multi-step environments. "
        f"Using standard run() - agent may not interact properly with environment."
    )
```

**File:** `experiments/evaluation-ablations/run_ablation.py:565-571`

---

## Priority Order:

1. **Issue #2 (Missing method)** - Will crash immediately
2. **Issue #3 (Missing history)** - Needed for trajectory output
3. **Issue #4 (Trajectory format)** - Needed for evaluation to work
4. **Issue #1 (Hardcoded tools)** - InterCode will still use SandboxedCommandLine instead of environment tool
5. **Issue #5 (Better logging)** - Nice to have

## Testing Plan After Fixes:

1. Run `intercode_sql` with Docker available → should use InterCodeEnvironment
2. Run `intercode_sql` without Docker → should fallback gracefully with warning
3. Verify no more `sqlite3` command calls in InterCode tests
4. Check that trajectory is properly passed to adapter for evaluation
