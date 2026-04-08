# Capability Test RCA Summary: history-phases-1-3-restack Branch

**Date**: 2026-01-27
**Branch**: `history-phases-1-3-restack`
**Overall Impact**: +0.2% (66.0% → 66.2%, +5 net passing tests)

---

## Executive Summary

The branch introduces a **history context management system** that changes how execution results and task prompts are presented to the LLM. This is a **net positive change** with significant improvements in router and refinement tasks, offset by regressions in batch processing and exploration tasks.

### Key Finding

The change from `self.history.events[N].content` to semantic accessors like `self.history[N].prompt`, `.stdout`, `.error` has a **dual effect**:

| Effect | Impact | Tests Affected |
|--------|--------|----------------|
| **Cleaner context** → better task parsing | +12.5% to +8.3% | router_validate, refinement, error_recovery |
| **Removed semantic prefix** → premature termination | -15.0% to -8.3% | calculate_batch, repl_exploration, employee_lookup |

---

## Root Cause Summary by Test Type

### Regressions

#### 1. calculate_batch (-15.0%)

**Root Cause**: Removal of "Execution successful.\nStdout:\n" prefix caused LLMs to generate code with **condition ordering bugs** (separate `if` statements instead of `elif` chains).

**Mechanism**: The old prefix created a more "human-readable" context that encouraged careful pattern matching. The new "machine-like" structured format primed LLMs toward early-return patterns that matched general patterns before specific ones.

**Example**: For "Compute (a integer-divided by b) plus (a modulo b)", the LLM matched `'modulo' in text` first, returning just `a % b` instead of the compound `(a // b) + (a % b)`.

**Impact**: gpt-oss-120b dropped 50%, claude-sonnet unaffected.

#### 2. employee_lookup (-8.3%)

**Root Cause**: Two distinct failure modes:
1. **Introspection skipping** (qwen3-80b, gemini): Agent solved tasks via trial-and-error instead of calling `doc()`, failing the LLM judge
2. **Max iteration exhaustion** (nemotron): Agent got stuck in error recovery loops

**Mechanism**: The history format changes affected how LLMs perceive error recovery options, leading some to skip explicit introspection in favor of runtime debugging.

#### 3. repl_exploration (-8.3%)

**Root Cause**: The removal of semantic framing caused **premature task termination**. LLMs returned intermediate riddle answers instead of completing the multi-step exploration to call `self.secret_message()`.

**Mechanism**: Without "Execution successful.\nStdout:\n", the LLM lacks signals that an execution step completed and more steps may be needed.

**Impact**: gemini-2.5-flash-lite dropped 50%, nemotron improved 30% (model-specific sensitivity).

---

### Improvements

#### 1. router_validate (+12.5%)

**Root Cause**: **Cleaner history context reduced LLM confusion**. The semantic accessors (`.prompt`, `.stdout`) made it easier to parse routing tasks correctly.

**Mechanism**:
- Main: 89 turns, 15 sessions, called wrong agents
- Branch: 8 turns, 2 sessions, called correct agent

The new format reduced cognitive load, enabling correct task parsing on the first attempt.

**Impact**: Efficiency improved 15x (turn count), accuracy improved 12.5%.

#### 2. refinement (+8.3%)

**Root Cause**: **Zero syntax errors** in branch vs 2 in main. The structured API reduced LLM cognitive load, eliminating malformed code blocks (JavaScript-style `}` terminators in Python).

**Mechanism**: Semantic accessor names (`.prompt`, `.error`, `.stdout`) are clearer than generic `.events[N].content`, allowing LLMs to focus on the problem rather than navigating opaque event structures.

**Impact**: All 14 turns productive in branch vs 10/12 in main (2 wasted on syntax errors).

#### 3. error_recovery (+6.7%)

**Root Cause**: Two factors:
1. **Infrastructure (67%)**: 6 of 9 improvements due to transient API/judge failures in main
2. **Context framing (33%)**: Change from `...content` to `...prompt` influenced LLM interpretation

**Mechanism**: "content" suggests data to parse; "prompt" suggests task to execute. Some LLMs in main literally tried to parse the expression path instead of calling the required method.

---

## Consolidated Causal Chain

```
History Context API Change
         ↓
┌────────────────────────────────────────────────────────────┐
│                                                            │
│  ┌─────────────────────┐     ┌──────────────────────────┐ │
│  │ Semantic Accessors  │     │ Removed Prefix           │ │
│  │ (.prompt, .stdout)  │     │ "Execution successful."  │ │
│  └──────────┬──────────┘     └────────────┬─────────────┘ │
│             ↓                              ↓              │
│  Clearer task context          Less "human" framing      │
│             ↓                              ↓              │
│  Better task parsing           Premature termination     │
│  Fewer syntax errors           Condition ordering bugs   │
│             ↓                              ↓              │
│  +12.5% router_validate        -15.0% calculate_batch    │
│  +8.3% refinement              -8.3% repl_exploration    │
│  +6.7% error_recovery          -8.3% employee_lookup     │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## Recommendations

### For This PR

1. **Consider restoring the execution prefix** - The "Execution successful.\nStdout:\n" prefix is load-bearing for multi-step tasks. Options:
   - Restore it in the render function
   - Make status more prominent in structured output (e.g., `<status>complete</status><stdout>...</stdout>`)

2. **Model-specific testing** - gpt-oss-120b shows high sensitivity to formatting (50% drop in calculate_batch). Consider model-specific context formatting.

3. **Keep semantic accessors** - The `.prompt`, `.stdout`, `.error` naming is objectively better than `.events[N].content` and improves most test types.

### For Future Changes

1. **A/B test context formats** before deploying to catch regressions early
2. **Add pattern-matching test coverage** for the condition ordering bug pattern
3. **Make introspection_usage scorer deterministic** instead of using LLM judge
4. **Monitor syntax error rates** as a code quality metric

---

## Trace Explorer Feedback (Consolidated)

### Strengths (Mentioned in 5/6 RCAs)

1. **Hierarchical navigation** (`get_overview()` → `get_session()` → `get_turn()`) - intuitive and effective
2. **Diff functionality** - identified expression path differences automatically
3. **Eval context access** - clear visibility into pass/fail reasons
4. **Call graph visualization** - immediately showed session count differences

### Areas for Improvement (Mentioned in 3+ RCAs)

| Suggestion | Mentioned In |
|------------|--------------|
| Batch comparison mode (`batch_diff(dir1, dir2)`) | calculate_batch, employee_lookup, repl_exploration |
| Content diff (not just expression paths) | repl_exploration, refinement |
| Pattern matching in generated code | employee_lookup, error_recovery |
| Session ID as direct property (not string parsing) | error_recovery, employee_lookup |
| Syntax error detection/highlighting | refinement |

### Analysis Time

Average RCA time with trace_explorer: **15-20 minutes per test type**
Estimated time without tool: **1+ hour** (manual JSONL parsing)

---

## Test-Level Summary Table

| Test Type | Main | Branch | Delta | Root Cause |
|-----------|------|--------|-------|------------|
| **router_validate** | 80.8% | 93.3% | **+12.5%** | Cleaner context → better parsing |
| **refinement** | 38.3% | 46.7% | **+8.3%** | Zero syntax errors |
| **error_recovery** | 70.0% | 76.7% | **+6.7%** | Context framing + infrastructure |
| **sentiment_batch** | 3.3% | 8.3% | **+5.0%** | (not analyzed) |
| **router_analyze** | 88.3% | 91.7% | **+3.3%** | Same as router_validate |
| **large_data_find** | 85.8% | 89.2% | **+3.3%** | (not analyzed) |
| **calculate_batch** | 58.3% | 43.3% | **-15.0%** | Condition ordering bugs |
| **employee_lookup** | 58.3% | 50.0% | **-8.3%** | Introspection skipping |
| **repl_exploration** | 70.0% | 61.7% | **-8.3%** | Premature termination |

---

## Conclusion

The `history-phases-1-3-restack` branch represents a **net improvement** but with trade-offs that should be addressed:

**Ship as-is if**: The router/refinement improvements are more important than batch processing tasks

**Address first if**: The calculate_batch and repl_exploration regressions are blockers

**Recommended fix**: Restore the "Execution successful.\nStdout:\n" prefix while keeping the semantic accessor API. This preserves both benefits.

---

*Generated from 6 individual RCA reports using trace_explorer*
