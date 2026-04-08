# Prompt-Optimization Library Migration

**Date**: 2025-12-05
**Context**: Updated prompt-optimization library to work with unified runtime refactor

## Overview

The prompt-optimization library has been updated to be compatible with the Phase 2 strategy middleware refactor documented in [docs/methodic006/overview.md](../methodic006/overview.md).

## Key Changes

### 1. Strategy API Migration

**Old API (string-based)**:
```python
from nemo_oo_agents import EPHEMERAL, PERSISTENT, PURE_PYTHON, STRUCTURED_OUTPUT, Agent, agent, plan

@plan(generation_strategy=PURE_PYTHON, method_lifetime=EPHEMERAL)
async def my_method(self, x: int):
    ...
```

**New API (instance-based)**:
```python
from nemo_oo_agents import Agent, agent, plan
from nemo_oo_agents.strategies import PurePythonStrategy, StructuredOutputStrategy

@plan(strategy=PurePythonStrategy())
async def my_method(self, x: int):
    ...
```

### 2. Event System Migration

**Old API**:
```python
agent.events  # Direct access to events list
```

**New API**:
```python
agent.history_manager.events  # Access via HistoryManager
```

The HistoryManager provides:
- `.events` - All recorded events (property, returns copy)
- `.recent(limit)` - Most recent N events
- `.for_call(call_id)` - Events from specific call
- `.for_call_tree(call_id)` - Events from call and nested calls
- `.on(event_type, handler)` - Subscribe to events

### 3. LLM Client Parameter

**Old**: `llm_client` parameter
**New**: `llm` parameter

```python
# Old
agent = MyAgent(llm_client=client)

# New
agent = MyAgent(llm=client)
```

## Files Modified

### Test Agents

1. **[test_agents/playground_pure_python.py](../../util/prompt-optimization/test_agents/playground_pure_python.py)**
   - Updated imports to use `PurePythonStrategy`
   - Changed all `@plan` decorators to use `strategy=PurePythonStrategy()`
   - Removed references to `PURE_PYTHON`, `EPHEMERAL`, `PERSISTENT` constants

2. **[test_agents/playground_structured_output.py](../../util/prompt-optimization/test_agents/playground_structured_output.py)**
   - Updated imports to use `StructuredOutputStrategy`
   - Changed all `@plan` decorators to use `strategy=StructuredOutputStrategy()`
   - Removed references to `STRUCTURED_OUTPUT`, `EPHEMERAL` constants

3. **[test_agents/capability_tests.py](../../util/prompt-optimization/test_agents/capability_tests.py)**
   - Already using correct API ✅

4. **[test_agents/sentiment.py](../../util/prompt-optimization/test_agents/sentiment.py)**
   - Already using correct API ✅

5. **[test_agents/playground_agents.py](../../util/prompt-optimization/test_agents/playground_agents.py)**
   - Already using correct API ✅

### Runner

**[runner.py](../../util/prompt-optimization/runner.py)**
- Updated all `agent.events` references to `agent.history_manager.events`
- Changes in functions:
  - `run_data_driven_test()`: Line 317, 324, 334
  - `run_batch_test()`: Line 376, 383, 392
  - `run_custom_test()`: Line 447, 454
  - Timeout handling: Lines 564, 565, 572

### Test Functions

All test functions in [test_functions/](../../util/prompt-optimization/test_functions/) directory:
- No changes needed - already using `llm` parameter correctly ✅

### Evaluators

All evaluators in [evaluators/](../../util/prompt-optimization/evaluators/) directory:
- No changes needed - they receive events as parameters ✅

### Config Files

YAML config files in [config/](../../util/prompt-optimization/config/) directory:
- No changes needed - they reference agent class paths, not strategy constants ✅
- Strategy labels like "DIRECT", "ITERATIVE" in YAML are just metadata for reporting

## Testing

All updated code has been validated:

```bash
cd util/prompt-optimization
source ../../.venv/bin/activate

# Test imports
python -c "from test_agents.playground_pure_python import SimpleAgent_PurePython; print('✓ Imports successful')"

# Test agent instantiation
python -c "from test_agents.capability_tests import SentimentAgent; from unifiedllm import CompletionClient; agent = SentimentAgent(llm=CompletionClient(model='fake')); print('✓ Agent creation successful')"

# Test runner functions
python -c "from runner import import_class; agent_class = import_class('test_agents.capability_tests.SentimentAgent'); print(f'✓ Dynamic import successful: {agent_class.__name__}')"
```

## Architecture Changes

The refactor introduces:

1. **Instance-based strategies**: Each strategy is a class instance with its own configuration
   - `PurePythonStrategy(max_iterations=10)` instead of `"PURE_PYTHON"`
   - Allows per-method strategy configuration

2. **Event-based history**: Unified event pipeline via `HistoryManager`
   - All events flow through `add()` method
   - Supports subscriptions via `on()` method
   - Query methods for filtering: `for_call()`, `recent()`, `since()`

3. **RuntimeServices protocol**: Strategies receive a runtime interface
   - `runtime.generate()` - LLM generation
   - `runtime.execute_code()` - Code execution
   - `runtime.execute_nested()` - Nested strategy execution
   - `runtime.history` - Reference to HistoryManager

## Running Tests

To run prompt optimization experiments:

```bash
cd util/prompt-optimization
source ../../.venv/bin/activate

# Run capability tests
python runner.py config/capability_tests.yaml --models <model-id>

# Run playground tests
python runner.py config/playground.yaml --test simple

# Run sentiment tests
python runner.py config/sentiment.yaml --models <model-id> --test single
```

See [HOW_TO_RUN_TESTS.md](../../util/prompt-optimization/HOW_TO_RUN_TESTS.md) for more details.

## Related Documentation

- [Unified Runtime Simplification Plan](../methodic006/overview.md) - Main refactor overview
- [Phase 2: Strategy Middleware](../methodic006/phase-2-strategy-middleware.md) - Strategy design details
- [Prompt Optimization README](../../util/prompt-optimization/README.md) - Library usage guide
