# E2E Optimization Reboot Proposal

## Problem Statement

1. **Flow is unclear** - Hard to understand what's actually being optimized, what artifacts are produced, and how they flow between stages
2. **Viewer is fragile** - Keeps breaking, suggesting over-engineering for unclear requirements
3. **Validation stuck** - Step 7 "Validate on sentiment_single toy problem" hasn't progressed

## Simplified Approach (Agreed)

1. **SentimentStrategy** - Created a dedicated subclass that only contains the templates to optimize
2. **Use prompt-optimization viewer** - Reuse existing viewer for evaluation runs
3. **Strategy files** - Write evolved strategies to `.py` files, inspect in text editor

### Files Created

- `util/e2e_optimization/src/e2e_optimization/examples/sentiment/strategy.py` - SentimentStrategy class
- Updated `agent.py` to use SentimentStrategy instead of base PurePythonStrategy

---

## Part 0: Making Eval Results Compatible with Prompt-Optimization Viewer

### The Gap

The prompt-optimization viewer expects `ExperimentResult` format with:
- `metadata.timestamp`, `metadata.models`, etc.
- `results[].single.results[]` with `TestResult` objects
- Each `TestResult` needs: `input`, `expected`, `output`, `eval`, `trace`

The e2e evaluation runner saves:
```json
{
  "task_id": "sentiment_000",
  "iterations": [{
    "success": false,
    "score": 0.0,
    "error_message": "...",
    "trace_path": "..."    // ← Has LLM data but missing expected/actual
  }]
}
```

**Missing from saved JSON:**
- `expected` - What the correct answer should be
- `output` (actual) - What the agent produced
- `trace` - LLM input/output for display

**Where this data exists:**
- `expected` → Available in `Task.expected_output` (not saved)
- `output` (actual) → Available in `EvalResult.metadata["actual"]` (not saved!)
- `trace` → Available in trace JSONL files (need to parse)

### Plan: Extend Runner to Save More Data

**Option A: Modify `evaluation/runner.py::_save_task_result()`** ✓ Chosen

Add missing fields:
```python
data = {
    ...
    "expected": result.task.expected_output,  # ADD THIS
    "iterations": [
        {
            ...
            "output": r.metadata.get("actual"),  # ADD THIS
            "metadata": r.metadata,               # ADD THIS
        }
    ],
}
```

This makes the saved JSON complete. Then `viewer_export.py` can convert to viewer format.

**Option B: Parse Traces at Export Time**

Keep runner unchanged, parse trace files in `viewer_export.py` to extract expected/actual.

Downside: Duplicates logic, trace parsing is fragile.

### Implementation (Completed)

Instead of modifying the runner, we integrated directly into the optimizer:

1. **Created `lib/experiment_format.py`** - Functions to create/update ExperimentResult format
2. **Modified `agents/optimizer.py`** to:
   - Create ExperimentResult at start with `status: "running"`
   - After each candidate evaluation, convert results and append as new variant
   - Save to `util/prompt-optimization/results/e2e_optimization_{benchmark}_{timestamp}.json`
   - Mark `status: "completed"` at end

**Live Updates**: The viewer shows results as they come in because:
- File is saved after each candidate evaluation
- `status: "running"` tells viewer it's in progress
- Each candidate becomes a variant (e.g., `gen0_baseline`, `gen1_variant_a`)

**Files Created/Modified**:
- `util/e2e_optimization/src/e2e_optimization/lib/experiment_format.py` (new)
- `util/e2e_optimization/src/e2e_optimization/agents/optimizer.py` (modified)

## Part 1: The Optimization Flow (What Actually Happens)

### Stage-by-Stage Breakdown

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          OPTIMIZATION LOOP                              │
└─────────────────────────────────────────────────────────────────────────┘

INPUTS:
  - strategy_source: PurePythonStrategy code (src/agent006/strategies/pure_python.py)
  - agent_source: SentimentAgent code (examples/sentiment/agent.py)
  - benchmark: 20 sentiment classification tasks (examples/sentiment/data.jsonl)
  - framework_doc: Agent006 reference (framework_doc.md)

┌──────────────────────────────────────────────────────────────────────────┐
│ GENERATION 0 (Baseline)                                                  │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [1. EVALUATE]                                                           │
│       │                                                                  │
│       ├── Input: population = [baseline strategy]                        │
│       │                                                                  │
│       ├── What runs:                                                     │
│       │     For each task in data.jsonl (20 tasks):                      │
│       │       - Create SentimentAgent with strategy                      │
│       │       - Call agent.classify_single(text)                         │
│       │       - LLM generates Python code that returns sentiment         │
│       │       - Execute code, compare to expected                        │
│       │       - If wrong, iterate up to 3 times (self-improvement)       │
│       │                                                                  │
│       ├── Artifacts produced:                                            │
│       │     - traces/optimization/trace_YYYYMMDD_HHMMSS.jsonl            │
│       │     - results/optimization/gen_0_baseline.json                   │
│       │                                                                  │
│       └── Metrics: accuracy=0.8, token_cost, latency                     │
│                                                                          │
│  [2. ANALYZE TRACES]                                                     │
│       │                                                                  │
│       ├── Input: trace files from evaluation                             │
│       │                                                                  │
│       ├── What runs:                                                     │
│       │     - ExtendedTraceAnalyzer.extract_trace_analysis()             │
│       │     - ExtendedTraceAnalyzer.find_error_recovery_patterns()       │
│       │                                                                  │
│       └── Output: list of failures, recovery patterns                    │
│                                                                          │
│  [3. REFLECT] (Claude Sonnet 4.5)                                        │
│       │                                                                  │
│       ├── Input: failures, strategy code, agent code, framework doc      │
│       │                                                                  │
│       ├── What runs:                                                     │
│       │     - ReflectorAgent.diagnose_failures()                         │
│       │     - Returns: Diagnosis with error_categories, specific_issues, │
│       │                suggested_fixes, priority_templates               │
│       │                                                                  │
│       └── Output: Diagnosis dataclass                                    │
│                                                                          │
│  [4. EVOLVE] (Claude Sonnet 4.5)                                         │
│       │                                                                  │
│       ├── Input: best strategy code, diagnosis, num_variants=5           │
│       │                                                                  │
│       ├── What runs:                                                     │
│       │     - EvolverAgent.generate_mutations()                          │
│       │     - Produces N new PurePythonStrategy subclasses               │
│       │                                                                  │
│       └── Output: list[CodeVariant] - modified strategy code             │
│                                                                          │
│  [5. SELECT]                                                             │
│       │                                                                  │
│       ├── Input: evaluated candidates + new mutations                    │
│       │                                                                  │
│       ├── What runs:                                                     │
│       │     - pareto_select() on objectives: accuracy, token_cost, latency│
│       │                                                                  │
│       └── Output: population for next generation                         │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

REPEAT for max_generations (default 5)

FINAL OUTPUT:
  - results/optimization/optimization_result_YYYYMMDD_HHMMSS.json
  - results/optimization/optimization_result_YYYYMMDD_HHMMSS.py (best strategy code)
  - results/optimization/optimization_result_latest.json (symlink)
```

### What Gets Optimized

The **prompt templates** in `PurePythonStrategy`:

| Template | What It Does | Priority |
|----------|--------------|----------|
| `strategy_instructions` | Main system instructions for the LLM | High |
| `initial_task_template` | How the task is presented to the LLM | Medium |
| `error_*` templates | How errors are reported back to LLM for retry | Medium |

The evolver generates **new subclasses** that override these templates with different prompts.

### Current Status (from results)

```
Generation 0 (baseline): accuracy = 80%
- 20 tasks evaluated
- No mutations generated yet
- Loop stopped after gen 0
```

**Question**: Why did it stop? Did reflection/evolution fail, or was it early stopping?

---

## Part 2: Viewer Requirements (What Do We Need to See?)

### At Each Stage, What Do We Want to Look At?

| Stage | What to View | Format |
|-------|--------------|--------|
| **EVALUATE** | Progress: N/20 tasks done, pass/fail per task | Live progress bar, task list |
| **EVALUATE** | Per-task details: input text, expected, actual, trace | Click-to-expand |
| **ANALYZE** | Failures grouped by error type | Table with counts |
| **REFLECT** | Diagnosis output: issues, fixes, patterns | Formatted text |
| **EVOLVE** | Generated mutations: name, description, target issues | Table |
| **SELECT** | Pareto frontier visualization | Simple chart |
| **OVERALL** | Generation-over-generation accuracy trend | Line chart |

### Proposed Simple Viewer

**Option A: No Custom Viewer**

Just use the CLI + JSON output:
- `e2e evaluate --example sentiment` → prints results to terminal
- `e2e reflect --traces ...` → prints diagnosis
- JSON files can be inspected with `jq` or viewed in VSCode

**Option B: Minimal Static HTML Viewer**

Single HTML file that:
1. Reads `optimization_result_latest.json` on load
2. Shows generation history as a table
3. Shows best accuracy trend as a chart (Chart.js)
4. No backend needed - just open the HTML file

**Option C: Minimal Live Viewer (Current, Simplified)**

FastAPI backend with:
- `GET /api/status` - Returns current optimization state
- `GET /api/generations` - Returns list of generations
- `GET /api/generation/{n}/tasks` - Returns task results for generation N

Frontend: Single page with auto-refresh every 5s.

### Recommendation

**Start with Option A** (no viewer) to validate the loop works:
1. Run `e2e optimize --example sentiment --generations 3`
2. Inspect output files manually
3. Confirm loop completes with mutations

Then build **Option B** (static HTML) if needed for presentation.

---

## Part 3: Immediate Next Steps

### Step 1: Validate the Loop Works

Run optimization and observe what happens:

```bash
cd util/e2e_optimization
source ../../.venv/bin/activate
python -m e2e_optimization optimize --example sentiment --generations 2 --verbose
```

**Watch for**:
- Does generation 0 complete with baseline accuracy?
- Does reflection produce a diagnosis?
- Does evolver produce mutations?
- Does generation 1 run with mutations?

### Step 2: Fix Any Blockers

Based on step 1 output, fix whatever is broken. Common issues:
- LLM client errors (API keys, model names)
- Strategy generation failures (invalid code produced)
- Trace parsing errors

### Step 3: Delete the Viewer (Temporarily)

Remove viewer code to reduce cognitive load:
```bash
rm -rf src/e2e_optimization/viewer/
```

Re-add it later once the core loop works.

---

## Questions for Discussion

1. **Do we need a viewer at all right now?** The loop should work without one.

2. **Should we simplify the objectives?** Currently tracking 3 (accuracy, token_cost, latency). Maybe just accuracy for now?

3. **Is the reflection → evolution pipeline working?** The result shows 0 tokens, suggesting LLM calls may not be happening.

4. **Should we add more logging?** The verbose flag exists but may not log enough detail.
