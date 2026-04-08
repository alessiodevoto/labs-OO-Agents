# Capability Optimization: Lessons Learned

Observations and insights from running ~45 prompt optimization experiments (Jan 16-19, 2026).

## Key Finding: Systematic Overfitting

**The reflection model consistently overfits to test suite naming conventions.**

Instead of discovering generalizable prompt improvements, the reflector produces explicit task-type mappings like:

```
- For sentiment_*/json_qa_*/simple classification: call return_result(...) directly
- For calculate_*/router_*/order_*/context_*/needle_* tasks: use execute_python
```

This is **memorizing the test suite structure**, not learning transferable patterns.

### Why This Happens

1. **Failure traces expose test names** - The reflector sees `sentiment_single_001`, `router_validate_002`, etc.
2. **Pattern matching is easier than abstraction** - It's simpler to say "sentiment_* → tool A" than to articulate *why* certain tasks don't need code execution
3. **No held-out validation** - We accept improvements on a mini-batch from the same distribution, so overfitting is rewarded

---

## Observations

### What Worked (Partially)

1. **Mini-batch acceptance testing** - Prevented broken strategies from being accepted
2. **Trace-based failure analysis** - Identified genuine failure patterns
3. **Structured reflection format** - Produced actionable, readable outputs

### What Didn't Work

1. **No generalization pressure** - Nothing stops the reflector from using test names
2. **Same-distribution validation** - Mini-batch is a subset of training, not a held-out test
3. **Token count as complexity proxy** - Longer prompts aren't necessarily worse; the metric didn't help

---

## Potential Improvements

### 1. Blind the Reflector to Test Names

**Problem**: Reflector sees `sentiment_single_001`, `fast_food_order_005`, etc.

**Options**:
- [ ] Anonymize test names in failure traces (e.g., `test_A_001`)
- [ ] Remove test_type from consistency analysis
- [ ] Use only the method signature and docstring, not suite metadata

### 2. Add Held-Out Validation

**Problem**: We validate on samples from the same test suite.

**Options**:
- [ ] Split tests into train/validation sets
- [ ] Create synthetic validation tests with different naming
- [ ] Use a different model set for validation

### 3. Penalize Test-Name References

**Problem**: Proposed strategies literally contain `sentiment_*`, `router_*`, etc.

**Options**:
- [ ] Reject strategies that reference test suite names
- [ ] Add scorer that detects hardcoded task-type patterns
- [ ] Require strategies to work without knowing test names

### 4. Improve Reflection Prompting

**Problem**: Current reflection prompt doesn't discourage overfitting.

**Options**:
- [ ] Add explicit instruction: "Don't reference test names or categories"
- [ ] Ask for *principles* not *rules for specific tests*
- [ ] Require proposed changes to be testable on unseen task types

### 5. Rethink the Objective

**Problem**: We optimize for pass rate on a fixed test suite.

**Options**:
- [ ] Optimize for *consistency* across models, not just pass rate
- [ ] Optimize for *minimal prompt length* that achieves baseline
- [ ] Optimize for *robustness to prompt perturbations*

---

## Questions to Explore

1. **Would a human-written prompt do better?** The reflection consistently produces similar patterns - maybe a thoughtful human prompt is better than N iterations of automated optimization.

2. **Is the test suite too narrow?** 23 test types with predictable naming makes overfitting easy. More diverse, unpredictably named tests would stress-test generalization.

3. **Are we optimizing the wrong surface?** The CapabilityStrategy inherits from CodeActStrategy. Maybe the base strategy prompts need work, not the subclass overrides.

4. **Is mode_selection even the right metric?** Many failures are "correct answer, wrong tool". Does it matter if the agent uses execute_python for sentiment if it gets the right answer?

---

## Concrete Next Steps

**Short-term:**
- [ ] Analyze: What's the baseline pass rate with no strategy modifications?
- [ ] Analyze: Which test types have the highest failure rates?
- [ ] Decide: Should we continue automated optimization or switch to manual prompt engineering?

**Medium-term:**
- [ ] Implement test name anonymization in reflection prompts
- [ ] Create a held-out test set for validation
- [ ] Try manual prompt improvements based on failure patterns (without test names)

**Long-term:**
- [ ] Consider whether this optimization framework is the right approach
- [ ] Evaluate if simpler prompts + better base strategy would outperform

---

## Infrastructure Bug Fix: Memory Leak (Jan 19, 2026)

### Problem
Experiments consistently died at iteration 4 due to OOM. Memory grew ~150MB per iteration and was never released.

### Root Cause
`enable_tracing()` in `openinference_instrumentation_nemo_oo_agents` was called once per iteration. Each call:
1. Created a new `JSONLSpanExporter`
2. Added a new `SimpleSpanProcessor` to the global `TracerProvider`
3. Stored the new exporter in a `ContextVar` (overwriting the old reference)

**Result:** Span processors accumulated. By iteration 4:
- 4 processors all processing every span
- Each processor held an exporter with file handles to all sample trace files
- Old exporters' file handles were never closed (only current exporter was tracked)

### Fix
Made `enable_tracing()` idempotent:
1. Added `_global_exporter` module-level singleton
2. Early return if exporter already exists
3. Both global and ContextVar track the same exporter

**File:** `packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/__init__.py`

### Test
```python
exporter1 = enable_tracing(trace_dir="/tmp/dir1")
exporter2 = enable_tracing(trace_dir="/tmp/dir2")  # Different dir!
assert exporter1 is exporter2  # Same exporter returned
```

---

## Notes for Discussion

*Space for collaborative notes and decisions:*

-
-
-
