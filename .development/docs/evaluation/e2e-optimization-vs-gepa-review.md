# E2E Optimization vs GEPA: Critical Review

**Date**: 2026-01-18
**Purpose**: Compare our e2e_optimization implementation against GEPA, identify improvements and gaps, and propose changes.

## Executive Summary

Our e2e_optimization framework is heavily inspired by GEPA but aims to evolve **entire agent code**, not just prompts. While we've made good architectural choices (Pydantic models, TraceExplorer, mechanical checks), there are several areas where GEPA does it better that we should adopt.

| Aspect | e2e_optimization | GEPA | Winner |
|--------|-----------------|------|--------|
| Abstraction layer | Tight coupling to nemo_oo_agents | Protocol-based adapter | GEPA |
| Candidate selection | `get_best_by_accuracy()` only | Pareto/CurrentBest/EpsilonGreedy | GEPA |
| Component selection | All-at-once | Round-robin or All | GEPA |
| Minibatch sampling | Random FRONTIER | Epoch-shuffled deterministic | GEPA |
| Merge strategy | Implemented but incomplete | Full merge with subsample test | GEPA |
| Trace analysis | TraceExplorer + mechanical checks | Adapter-defined | e2e_opt |
| Retry logic | Has RetryContext | None (just iterate) | e2e_opt |
| Type safety | Pydantic throughout | Protocol-based, loose | e2e_opt |
| State persistence | JSON | Pickle (cloudpickle option) | Tie |

---

## Step-by-Step Comparison

### GEPA's Core Loop

```
1. Select candidate from frontier (Pareto, CurrentBest, or EpsilonGreedy)
2. Sample minibatch (epoch-shuffled deterministic)
3. Evaluate with trace capture
4. Skip if all scores perfect (optional)
5. Select components to update (round-robin or all)
6. Build reflective dataset (adapter-defined)
7. Propose new texts via LLM
8. Create new candidate with new texts
9. Evaluate on same minibatch (no traces)
10. If improved: full eval and add to candidate pool
11. Schedule merge attempts if accepted
```

### Our Core Loop

```
1. Run full evaluation (n_runs per test)
2. Compute consistency (FRONTIER/SYSTEMATIC classification)
3. Sample FRONTIER failures
4. Run mechanical checks (deterministic)
5. Optionally run TraceAnalyzerAgent (LLM-based diagnosis)
6. Build reflection prompt with all samples
7. Propose new strategy code
8. Validate Python syntax
9. Run acceptance test on sampled failures
10. Accept or retry (with RetryContext)
11. Update Pareto frontier if accepted
```

---

## What We Do Better

### 1. TraceExplorer API ✓

```python
# Our approach - programmatic trace exploration
trace = TraceExplorer.from_file("trace.006trace.jsonl")
trace.get_overview()
trace.search_content(r"NameError")
trace.get_mechanical_findings()

for session in trace.sessions:
    for turn in session.turns:
        if isinstance(turn, ExecutionTurn) and turn.error:
            # Precise error location
```

GEPA's adapter just returns opaque `Trajectory` objects. Our TraceExplorer provides:
- Session tree navigation (nested agents)
- Content search with regex
- Built-in mechanical checks
- Structured output for LLM consumption

### 2. Mechanical Checks (Deterministic) ✓

```python
class ExecutionErrorCheck(MechanicalCheck):
    def run(self, trace: TraceExplorer) -> list[MechanicalFinding]:
        # Deterministic pattern matching, no LLM needed
```

GEPA relies entirely on LLM reflection for error analysis. Our mechanical checks:
- Fast (no API calls)
- Reproducible
- Extensible registry pattern
- Catch common issues: syntax errors, max iterations, infinite loops

### 3. RetryContext for Failed Mutations ✓

```python
class RetryContext(BaseModel):
    attempt: int
    max_attempts: int
    previous_code: str  # The rejected code
    failures: list[FailureInfo]  # What failed and why
```

GEPA just moves on if a mutation doesn't improve. We provide explicit context about what failed, enabling more targeted retries.

### 4. Pydantic Models Throughout ✓

Every step has a typed output model (Sample, ConsistencyAnalysis, AcceptanceResult). GEPA uses loose dicts for most intermediate state. Our approach:
- Better IDE support
- Validation at boundaries
- Self-documenting code
- Reliable serialization

### 5. Whole-Agent Evolution ✓

GEPA evolves individual text components (prompts). We evolve **entire Python files**, enabling:
- Algorithm changes
- New helper functions
- Structural modifications
- Tool definitions

---

## What GEPA Does Better

### 1. Protocol-Based Adapter (Critical Miss) ✗

```python
# GEPA's clean abstraction
class GEPAAdapter(Protocol):
    def evaluate(batch, candidate, capture_traces: bool) -> EvaluationBatch: ...
    def make_reflective_dataset(candidate, eval_batch, components) -> Mapping: ...
    propose_new_texts: ProposalFn | None = None
```

**Our problem**: We're tightly coupled to:
- `eval_pipeline` CLI subprocess
- `.006eval.jsonl` / `.006trace.jsonl` file formats
- nemo_oo_agents framework assumptions

**Impact**: Hard to use for non-nemo_oo_agents systems, hard to test in isolation.

**Recommendation**: Extract an `OptimizationAdapter` protocol:
```python
class OptimizationAdapter(Protocol):
    async def evaluate(tests: list[str], candidate: dict[str, str]) -> EvalResult: ...
    def make_reflective_context(eval_result, samples) -> str: ...
    def extract_new_code(llm_response: str) -> dict[str, str]: ...
```

### 2. Candidate Selection Strategies (Miss) ✗

```python
# GEPA's options
class ParetoCandidateSelector:  # Random from frontier
class CurrentBestCandidateSelector:  # Always greedy
class EpsilonGreedyCandidateSelector:  # 10% explore, 90% exploit
```

**Our problem**: We only use `get_best_by_accuracy()`:
```python
def select_parent_from_frontier(self) -> StrategyScore | None:
    parent = self._pareto_frontier.select_parent()  # Random only
```

**Impact**: Always mutating the best candidate leads to:
- Premature convergence
- Loss of diversity
- Missing strategies that excel at specific tests

**Recommendation**: Add configurable candidate selection:
```python
candidate_selector: CandidateSelector = config.get("candidate_selector", "pareto")
# Options: "pareto", "current_best", "epsilon_greedy"
```

### 3. Component/Round-Robin Selection (Miss) ✗

```python
# GEPA's approach
class RoundRobinReflectionComponentSelector:
    def __call__(...) -> list[str]:
        # Cycle through components, update one at a time
```

**Our problem**: We always evolve all target files together:
```python
target_files: list[str]  # All updated in one shot
```

**Impact**:
- High variance in mutations (changing everything at once)
- Hard to attribute improvements to specific changes
- Noisier signal for the LLM

**Recommendation**: Add component-level tracking:
```python
class ComponentSelector:
    def select_components(iteration: int, components: list[str]) -> list[str]:
        # Round-robin or priority-based
```

### 4. Epoch-Shuffled Batch Sampling (Miss) ✗

```python
# GEPA's deterministic sampling
class EpochShuffledBatchSampler:
    def next_minibatch_ids(trainset, state) -> list[DataId]:
        # Shuffles once per epoch, pads with least-frequent
        # Deterministic given seed
```

**Our problem**: Random sampling from FRONTIER:
```python
def _select_frontier_samples(..., seed):
    random.seed(seed)  # Only within one call
    random.shuffle(frontier_samples)
    return frontier_samples[:n_samples]
```

**Impact**:
- May repeatedly sample same failures
- No guarantee of coverage across all failures
- Not reproducible across iterations

**Recommendation**: Add stateful batch sampler:
```python
class MinibatchSampler:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.seen_ids: set[str] = set()

    def next_batch(self, available: list[str], size: int) -> list[str]:
        # Prioritize unseen, then least-seen
```

### 5. Minibatch Acceptance Test (Partial Miss) ⚠️

```python
# GEPA's approach
old_sum = sum(proposal.subsample_scores_before)
new_sum = sum(proposal.subsample_scores_after)
if new_sum <= old_sum:
    continue  # Reject, no full eval
else:
    # Accept: run full eval
```

**Our problem**: We run acceptance on sampled failures, but:
- We re-run full evaluation, not just the minibatch
- More expensive than necessary
- The acceptance test in `accept_or_reject()` is convoluted

**Code smell** in optimizer.py:
```python
async def accept_or_reject(self, tests: list[str] | None = None):
    # 200+ lines of complex logic
    # Subprocess calls, file parsing, etc.
```

**Recommendation**: Simplify acceptance to GEPA pattern:
1. Test proposed strategy on same samples used for reflection
2. Compare sum of scores directly
3. Only run full eval if minibatch improves

### 6. Merge Strategy (Incomplete) ⚠️

GEPA's merge:
```python
class MergeProposer:
    def propose(self, state) -> CandidateProposal | None:
        # 1. Find common ancestor
        # 2. Select best components from each parent
        # 3. Test on subsample
        # 4. Return proposal
```

**Our problem**: `try_merge()` exists but:
- Runs full subprocess evaluation (slow)
- No subsample acceptance test
- Complex shell-out logic
- Not integrated into main loop

**Recommendation**:
1. Add merge scheduling like GEPA (`last_iter_found_new_program`)
2. Use minibatch acceptance test before full eval
3. Integrate merge as proposer strategy, not separate method

---

## Bugs and Code Quality Issues

### Bug 1: Pareto Dominance Logic Error

In [state.py:121-154](util/e2e_optimization/src/e2e_optimization/state.py#L121-L154):

```python
def dominates(self, other: "StrategyScore") -> bool:
    # ...
    # Also check docstring_tokens (lower is better)
    if self.docstring_tokens > other.docstring_tokens:
        at_least_as_good = False  # BUG: This breaks dominance logic
    elif self.docstring_tokens < other.docstring_tokens:
        strictly_better = True
```

**Problem**: The dominance check mixes test scores (maximize) with docstring_tokens (minimize) incorrectly. If strategy A has better test scores but more tokens, it won't dominate B even though it should be on the frontier.

**Fix**: Treat as separate objectives, not combined in `dominates()`:
```python
def dominates(self, other: "StrategyScore") -> bool:
    """Dominates if better on ALL objectives."""
    # Test scores: maximize
    test_dominates = self._test_dominates(other)
    # Tokens: minimize (treat as negative for dominance)
    token_dominates = self.docstring_tokens <= other.docstring_tokens
    return test_dominates and token_dominates
```

### Bug 2: RNG Not Preserved Across Iterations

In [optimizer.py:960-961](util/e2e_optimization/src/e2e_optimization/optimizer.py#L960-L961):

```python
def _select_frontier_samples(..., seed):
    if seed is not None:
        random.seed(seed)  # Global state!
```

**Problem**: Using global `random.seed()` affects all random operations. If multiple optimizers run, they interfere.

**Fix**: Use local RNG instance:
```python
def _select_frontier_samples(..., seed):
    rng = random.Random(seed)
    rng.shuffle(frontier_samples)
```

### Bug 3: Missing Trace File Handling

In [optimizer.py:797-807](util/e2e_optimization/src/e2e_optimization/optimizer.py#L797-L807):

```python
for sample in samples:
    if sample.trace_file:
        trace_path = self._find_trace_file(sample.trace_file)
        if trace_path:
            try:
                findings, ann_path = run_and_write_mechanical_checks(trace_path)
            except Exception as e:
                logger.warning(...)  # Silent failure
```

**Problem**: If `_find_trace_file` returns None, we silently skip. If mechanical checks fail, we only log warning. No way to know which samples have valid analysis.

**Fix**: Track analysis status in Sample:
```python
class Sample(BaseModel):
    # ...
    analysis_status: Literal["pending", "success", "failed"] = "pending"
    analysis_error: str | None = None
```

### Bug 4: opt Property Reference Before Assignment

In [optimizer.py:333](util/e2e_optimization/src/e2e_optimization/optimizer.py#L333):

```python
async def propose_merge(...):
    reflect_model = self.opt.get("reflect_model", "claude-opus")
```

But `self.opt` is a property that reads from config:
```python
@property
def opt(self) -> dict[str, Any]:
    return self.config.get("optimization", {})
```

**Problem**: If config has no "optimization" key, returns empty dict. Then `get()` works, but it's not obvious from the code.

**Fix**: Make it explicit and provide better defaults:
```python
@property
def opt(self) -> dict[str, Any]:
    defaults = {"n_runs": 3, "n_samples": 5, "reflect_model": "gpt-4", ...}
    return {**defaults, **self.config.get("optimization", {})}
```

### Code Smell: Giant optimizer.py

The file is 30k+ tokens with:
- State management
- Evaluation orchestration
- Trace file finding
- Reflection prompt building
- LLM calls
- Acceptance testing
- Pareto operations
- Merge logic

**Recommendation**: Split into focused modules:
```
optimizer/
  __init__.py       # Public API
  orchestrator.py   # Main loop
  evaluator.py      # Eval subprocess management
  reflector.py      # Reflection prompt + LLM calls
  acceptance.py     # Acceptance testing logic
  merge.py          # Merge strategy
```

---

## Interface Design Issues

### 1. No Clear Adapter Pattern

GEPA's adapter is the key abstraction. Ours is implicit:

```python
# GEPA: explicit interface
adapter.evaluate(batch, candidate, capture_traces=True)
adapter.make_reflective_dataset(candidate, eval_batch, components)

# Ours: subprocess + file parsing
cmd = ["python", "-m", "eval_pipeline", ...]
process = await asyncio.create_subprocess_exec(*cmd, ...)
```

**Recommendation**: Define `EvalAdapter` protocol for dependency injection.

### 2. Config Schema Not Enforced

We load YAML and assume structure:
```python
config = yaml.safe_load(f)
opt = config.setdefault("optimization", {})
opt.setdefault("n_runs", 3)
```

GEPA uses dataclasses with explicit fields.

**Recommendation**: Add Pydantic config model:
```python
class OptimizationConfig(BaseModel):
    n_runs: int = 3
    n_samples: int = 5
    reflect_model: str = "gpt-4"
    candidate_selector: Literal["pareto", "current_best", "epsilon_greedy"] = "pareto"
```

### 3. Async/Sync Mixing

```python
async def run_eval(...):  # Async
    process = await asyncio.create_subprocess_exec(...)

def _compute_consistency(...):  # Sync
    # Called from async context
```

**Recommendation**: Make compute functions sync, I/O functions async. Don't mix.

---

## Proposed Changes (Priority Order)

### P0: Critical Fixes

1. **Fix Pareto dominance logic** - Currently broken for multi-objective
2. **Use local RNG** - Prevent cross-contamination
3. **Add sample analysis status tracking**

### P1: Architecture Improvements

4. **Extract EvalAdapter protocol** - Enable testing and alternative backends
5. **Add candidate selection strategies** - Pareto, CurrentBest, EpsilonGreedy
6. **Add component selection (round-robin)** - More targeted mutations
7. **Split optimizer.py** - Separate concerns into modules

### P2: Algorithm Improvements

8. **Epoch-shuffled batch sampling** - Better coverage
9. **Minibatch-only acceptance test** - Faster iteration
10. **Integrate merge into main loop** - With subsample acceptance

### P3: Quality of Life

11. **Pydantic config validation**
12. **Progress tracking/experiment logging** (like GEPA's trackers)
13. **Resume from any step** (currently partial)

---

## Implementation Status (Updated 2026-01-18)

All identified issues have been addressed:

### P0 Critical Fixes ✅
- **Fixed Pareto dominance logic** - Now properly handles multi-objective optimization
- **Local RNG** - Uses `random.Random(seed)` instances instead of `random.seed()`
- **Sample analysis status tracking** - New `AnalysisStatus` enum tracks SUCCESS/FAILED/SKIPPED

### P1 Architecture Improvements ✅
- **Replaced subprocess with Python API** - New `Agent006Evaluator` class wraps eval_pipeline
- **Candidate selection strategies** - `ParetoFrontier.select_parent()` now supports `pareto`, `current_best`, `epsilon_greedy`
- **Component selection** - New `ComponentSelector` class with `all` and `round_robin` strategies
- **Split optimizer.py** - Created new modules:
  - `evaluator.py` - `Agent006Evaluator` for Python API evaluation
  - `reflector.py` - `Reflector` class for reflection prompts and LLM calls
  - `acceptance.py` - `AcceptanceTester` with minibatch-only pattern

### P2 Algorithm Improvements ✅
- **Epoch-shuffled batch sampling** - New `EpochShuffledBatchSampler` class
- **Minibatch-only acceptance test** - New `AcceptanceTester` class
- **Merge integrated into main loop** - Uses configured `try_merge_every` setting

### New Configuration Options
```yaml
optimization:
  seed: 42
  candidate_selection: pareto  # pareto, current_best, epsilon_greedy
  component_selection: all     # all, round_robin
  epsilon: 0.1                 # For epsilon_greedy
  try_merge_every: 0           # 0 = disabled
  max_merge_invocations: 5
```

---

## Summary

Our e2e_optimization framework now has feature parity with GEPA while maintaining our unique advantages:

**Our advantages (preserved):**
- Whole-agent evolution (not just prompts)
- TraceExplorer for programmatic trace exploration
- Mechanical checks for deterministic pattern detection
- RetryContext for targeted retry feedback

**GEPA patterns adopted:**
- Candidate selection strategies (diversity vs greedy)
- Component-level evolution option
- Epoch-shuffled batch sampling for coverage
- Minibatch-only acceptance testing for speed
