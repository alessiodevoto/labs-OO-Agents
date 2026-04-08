# E2E Optimization Loop - Current Status and Next Steps

**Date:** 2025-12-09
**Status:** Infrastructure complete, debugging import/packaging issues

## What's Working

### 1. Code Quality Evaluation ✅
- **File:** `util/e2e_optimization/examples/sentiment/adapter.py`
- **Feature:** Evaluates BOTH correctness AND code quality
- Heuristic-based code quality checker detects:
  - Keyword matching patterns (`if "word" in text`)
  - Long OR chains (>5 conditions)
  - Explicit keyword lists
  - Unnecessary loops
  - Overly complex conditionals (>3 if statements)
- Only passes tasks that are correct AND use acceptable code

### 2. Strategy Prompt Override ✅
- **File:** `util/e2e_optimization/lib/strategy_gen.py`
- **Feature:** Dynamic strategy subclass generation
- Fixed: Removed @plan decorator from generated methods (they inherit from base class)
- Enables evolving PurePythonStrategy prompts while keeping agent method docstrings constant

### 3. Baseline Handling ✅
- **File:** `util/e2e_optimization/agents/optimizer.py` (lines 209-210)
- **Feature:** Special case for baseline to use PurePythonStrategy directly
- Avoids @plan decorator conflicts from trying to generate baseline from source

### 4. Agent Factory Signature ✅
- **File:** `util/e2e_optimization/agents/optimizer.py` (line 235)
- **Feature:** Agent factory accepts `llm_client` parameter from runner
- Properly forwards LLM client to SentimentAgent

## Current Issue 🔧

### Import Path Error
**Error:** `No module named 'example_llm'`
**Location:** Agent factory import statement
**Fix Applied:** Changed import to `from e2e_optimization.examples.sentiment.agent import SentimentAgent`
**Problem:** Editable install using hybrid mode - some files copied instead of linked, cache not clearing properly

**Workaround Options:**
1. Copy fixed file directly to `.venv/lib/python3.12/site-packages/e2e_optimization/agents/optimizer.py`
2. Run `pip install -e util/e2e_optimization --force-reinstall --no-deps` after every change
3. Switch to regular (non-editable) install for testing

## Requested Features: Incremental Output 🎯

### 1. Timestamps ✅ (Already Implemented)
- Logs already include timestamps: `18:27:01 - module - LEVEL - message`
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
- Date format: `%H:%M:%S`

### 2. Progress Bar on Command Line 📊 (TO DO)
**Where to add:**
- `evaluation/runner.py` - Task iteration loops (lines 240-300)
- `util/e2e_optimization/agents/optimizer.py` - Generation loops (lines 196-413)

**Implementation approach:**
```python
from tqdm import tqdm

# In task loop
for task in tqdm(tasks, desc="Evaluating tasks", unit="task"):
    # ... existing task execution code

# In generation loop
for generation in tqdm(range(max_generations), desc="Optimization", unit="gen"):
    # ... existing generation code
```

**Benefits:**
- Real-time progress visibility
- Estimated time remaining
- Tasks/second throughput

### 3. Incremental Results in Viewer 📈 (TO DO)
**Where to add:**
- `util/e2e_optimization/agents/optimizer.py` - After each candidate evaluation
- Write incremental JSON to results directory as evaluation progresses

**Implementation approach:**
```python
# After each candidate evaluation (line ~292)
incremental_result = {
    "generation": generation,
    "candidate": candidate.name,
    "timestamp": datetime.now().isoformat(),
    "objectives": candidate.objectives,
    "evaluated_tasks": len(report.task_results),
    "status": "in_progress"
}

incremental_path = Path(config.results_dir) / f"gen_{generation}_{candidate.name}.json"
with open(incremental_path, "w") as f:
    json.dump(incremental_result, f, indent=2)
```

**Viewer integration:**
- Update e2e viewer backend to watch for `gen_*.json` files
- Poll results directory or use file watcher
- Update frontend to display live results as they arrive
- Show progress per generation and per candidate

**UI Features:**
- Live updating table of candidates
- Real-time accuracy graphs
- Generation timeline
- Currently evaluating indicator

## Test Plan 🧪

### Quick Test (1 generation, small dataset)
```bash
cd /Volumes/dev/dev/agent006
source .venv/bin/activate

# Copy latest fixes to installed package
cp util/e2e_optimization/agents/optimizer.py \
   .venv/lib/python3.12/site-packages/e2e_optimization/agents/optimizer.py
rm -rf .venv/lib/python3.12/site-packages/e2e_optimization/agents/__pycache__

# Run with 1 generation to test
python -m e2e_optimization.cli optimize \
  --example sentiment \
  --generations 1 \
  --population 1 \
  --trace-dir traces/test \
  --results-dir results/test
```

### Full Test (3 generations)
```bash
python -m e2e_optimization.cli optimize \
  --example sentiment \
  --generations 3 \
  --population 3 \
  --trace-dir traces/optimization \
  --results-dir results/optimization
```

## File Changes Summary

### Modified Files
1. `util/e2e_optimization/examples/sentiment/adapter.py`
   - Added `_extract_generated_code()` method
   - Added `_judge_code_quality()` method
   - Updated `evaluate()` to check both correctness and code quality

2. `util/e2e_optimization/lib/strategy_gen.py`
   - Fixed `generate_strategy_from_prompts()` to remove @plan decorator from generated methods

3. `util/e2e_optimization/agents/optimizer.py`
   - Added special case for baseline (lines 209-210)
   - Fixed agent factory signature to accept `llm_client` (line 235)
   - Fixed import path to `e2e_optimization.examples.sentiment.agent` (line 236)

### Files to Modify (for incremental output)
1. `evaluation/runner.py` - Add tqdm progress bars
2. `util/e2e_optimization/agents/optimizer.py` - Write incremental results
3. `util/e2e_optimization/viewer/backend/main.py` - Watch for incremental results
4. `util/e2e_optimization/viewer/frontend/js/main.js` - Poll and display live results

## Next Actions

### Option A: Fix Import Issue First
1. Debug why editable install isn't working
2. Get optimizer running end-to-end
3. Then add incremental output features

### Option B: Implement Incremental Output First
1. Add progress bars to working code paths
2. Implement incremental result writing
3. Update viewer to show live results
4. Fix import issue in parallel

**Recommendation:** Option B - implement incremental output on the working code, as it provides value immediately and helps with debugging the import issues.

## Dependencies

```bash
# Add to requirements (if not present)
pip install tqdm  # For progress bars
```

## References

- Baseline evaluation results: `results/evaluation/sentiment_report.json`
- Code quality checks: 0/20 pass (all use keyword matching)
- Target: Evolve PurePythonStrategy prompts to achieve >50% pass rate with proper reasoning
