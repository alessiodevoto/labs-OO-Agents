# Mode Selection Validation Implementation

**Date**: January 7, 2026
**Issue**: WP-1: Mode Selection Validation
**Status**: Implemented

---

## Problem Statement

The agent must correctly decide when to use internal reasoning vs code execution. This decision is visible only in the **execution trajectory** (tool calls in the trace), not in the output itself. An agent can produce the correct answer using the wrong approach.

**Example failure modes**:
- Agent writes naive code for semantic tasks: `if 'happy' in text: return 'positive'`
- Agent generates data instead of exploring: `return random.choice(['positive', 'negative'])`

We need to validate the **how** (execution mode), not just the **what** (output correctness).

---

## Design Decision: Mode Selection as a Scorer

### Why a Scorer?

**Scorers evaluate trajectories, not just outputs.**

Existing scorers already do this:
- `ExactMatchScorer`: Evaluates output correctness
- `LLMJudgeScorer`: Evaluates output quality against a rubric
- **`ModeSelectionScorer`**: Evaluates execution strategy (new!)

Mode selection is a **property of how the agent executed**, visible only in the trace file. It's a natural fit for the scorer abstraction because:

1. **Trace analysis required**: Must parse `.006trace.jsonl` to detect `python_executor` tool calls
2. **Binary evaluation**: Returns score 1.0 if mode matches expectation, 0.0 if wrong
3. **Independent dimension**: Separate from output correctness - an agent can get the right answer the wrong way
4. **Weighted scoring**: Can balance importance vs output correctness (e.g., 60/40 split)

### Why Not a Test-Level Property?

We initially considered adding `expected_mode: internal` as a test-level field, but this would:
- Create a special case outside the scorer framework
- Duplicate logic (we already have scorer instantiation, weighting, metadata)
- Break composability (can't easily combine with other evaluation dimensions)

**By using a scorer**, mode selection becomes just another evaluation dimension that can be:
- Added/removed per test via config
- Weighted differently per test
- Combined with other scorers naturally

---

## When to Use Mode Selection Scorer

### Use When There's a Genuine Choice

Mode selection scoring only makes sense when the agent has **a real decision to make**:

| Test | Mode | Has Choice? | Reason |
|------|------|-------------|--------|
| `sentiment_single` | internal | ✅ Yes | Could answer directly OR write code |
| `sentiment_batch` | code | ✅ Yes | Could loop in code OR guess patterns |
| `calculate_single` | internal | ✅ Yes | Could compute directly OR write loop |
| `calculate_batch` | code | ✅ Yes | Could compute directly OR write loop |
| `json_qa` | internal | ✅ Yes | Could answer directly OR parse |
| `json_extract` | code | ✅ Yes | Could extract directly OR write parser |

### Don't Use When There's No Choice

Some tests **require** code execution by design:

| Test | Why Code is Mandatory | Mode Scorer? |
|------|----------------------|--------------|
| `router_*` | Must instantiate child agents | ❌ No |
| `fast_food_*` | Must manage stateful conversation | ❌ No |
| `needle_in_haystack` | Must explore large dataset | ❌ No |

For these tests, mode selection isn't a meaningful evaluation dimension - code execution is inherent to the task.

---

## What Was Implemented

### 1. ModeSelectionScorer Class

**File**: `util/eval_pipeline/src/eval_pipeline/scoring.py`

A scorer that:
- Parses OpenTelemetry traces (`.006trace.jsonl`)
- Detects real code execution by checking for operations (loops, conditionals, comparisons, arithmetic, function calls)
- Treats trivial code (no operations) as internal mode, not code
- Compares to expected mode (`internal` or `code`)
- Returns score with metadata for reporting

**Trivial Code Detection**:

The scorer uses a simplified approach: code is trivial if it contains **no operations**. Operations that make code non-trivial include:
- **Loops**: `for`, `while`, `async for`
- **Conditionals**: `if`, `elif`, `else`, ternary expressions
- **Comparisons**: `==`, `!=`, `<`, `>`, `in`, `not in`, etc.
- **Arithmetic**: `+`, `-`, `*`, `/`, etc.
- **Function calls**: Any function calls except `print()`, `pprint()`, `return_result()`
- **Comprehensions**: List/dict/set comprehensions, generator expressions

**Trivial code examples** (treated as internal mode):
- `return 'negative'` - simple return
- `sentiment = 'positive'; return sentiment` - assignment + return
- `text_content = text; sentiment = 'positive'; return sentiment` - multiple assignments + return
- `print('debug'); return_result('neutral')` - prints + return_result
- `# comment; sentiment = 'neutral'; return_result(sentiment)` - comments + assignment + return

**Non-trivial code examples** (treated as code execution):
- `if 'love' in text: return 'positive'` - contains comparison operation
- `for word in words: ...` - contains loop
- `result = process(data)` - contains function call
- `pos_count = sum(1 for word in words if word in text)` - contains comprehension and comparison

**Config usage**:
```yaml
scorers:
  - name: mode_check
    class: ModeSelectionScorer
    weight: 0.4
    expected: internal  # or "code"
```

### 2. Implementation Details

**Simplified Detection Logic**:

The scorer uses a simplified approach to detect trivial code:
1. Parse the code into an AST
2. Check if the AST contains any operations (via `_has_operations()`)
3. If operations are found → non-trivial (real code execution)
4. If no operations → trivial (just packaging the answer)

This approach is much simpler than pattern matching and correctly handles:
- Print statements and comments (filtered out before checking)
- Variable assignments (trivial if no operations in the assigned value)
- Multiple statements (all checked for operations)
- Edge cases like unused variables, f-strings in prints, etc.

**Operation Detection**:

The `_has_operations()` method recursively checks the AST for:
- **Control flow**: `for`, `while`, `async for`, `if`, `elif`, `else`, ternary expressions
- **Comparisons**: `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`, `is`, `is not`
- **Arithmetic**: `+`, `-`, `*`, `/`, `//`, `%`, `**`, `<<`, `>>`, `&`, `|`, `^`
- **Boolean operations**: `and`, `or`, `not`
- **Function calls**: Any function call except `print()`, `pprint()`, `return_result()`
- **Comprehensions**: List/dict/set comprehensions, generator expressions

**Benefits of Simplified Approach**:
- **Easier to maintain**: Single check instead of complex pattern matching
- **More correct**: Catches all operations uniformly
- **More flexible**: Handles any combination of statements without special cases
- **Better performance**: Single AST traversal instead of multiple pattern checks

### 3. Test Configuration Updates

**File**: `experiments/capability_eval/config.yaml` (optimization experiments)

Added `ModeSelectionScorer` to tests where mode matters:
- `sentiment_single` (internal) / `sentiment_batch` (code)
- `calculate_single` (internal) / `calculate_batch` (code)
- `json_qa` (internal) / `json_extract` (code)

**File**: `tests/capability/config_ci.yaml` (CI regression tests)

Updated to match experiments config (subset of tests):
- Replaced LLM judge in `sentiment_single` with `ModeSelectionScorer` (faster, deterministic)
- Added `ModeSelectionScorer` to `sentiment_batch`, `calculate_single`, `calculate_batch`
- Note: CI config has 4 tests with mode selection (experiments has 6 including `json_qa` and `json_extract`)

**NOT added to**: Tests where code execution is mandatory (router, fast_food, needle_in_haystack)

### 4. New Test Pair: JSON Processing

**Files created**:
- `experiments/capability_eval/agents/json_qa.py` - Simple JSON field lookups (internal)
- `experiments/capability_eval/agents/json_extract.py` - Extract nested JSON data (code)
- `tests/capability/data/json_qa_lookup.jsonl`
- `tests/capability/data/json_qa_reasoning.jsonl`
- `tests/capability/data/json_extract.jsonl`

### 5. Enhanced Reporting

**File**: `util/ci/parse_capability_results.py`

Added:
- GitLab metrics with `mode_selection_accuracy_percent`
**Example output**:
```
Overall: 45/50 passed (90.0%)

Mode Selection Accuracy: 45/50 (90.0%)

Token Usage:
...

Per-test breakdown:
...
```

**File**: `util/ci/post_mr_comment.py`

Updated to show mode selection accuracy in the MR comment.

**Example output**:
```
Overall: 45/50 passed (90.0%)

Mode Selection Accuracy: 45/50 (90.0%)

Token Usage:
...

Per-test breakdown:
...
---

## Test Coverage

| Test Domain | Internal Mode | Code Mode | Mode Scorer? |
|-------------|---------------|-----------|--------------|
| Sentiment | sentiment_single | sentiment_batch | ✅ |
| Calculate | calculate_single | calculate_batch | ✅ |
| JSON | json_qa | json_extract | ✅ |
| Router | - | router_* (5 tests) | ❌ (no choice) |
| Fast Food | - | fast_food_* (2 tests) | ❌ (no choice) |
| Needle | - | needle_in_haystack | ❌ (no choice) |

**Actual counts by configuration**:
- `experiments/capability_eval/config.yaml`: 6 tests with mode selection out of 14 total
- `tests/capability/config_ci.yaml`: 4 tests with mode selection out of 11 total

*Note: The table above is illustrative. Refer to config files for exact test counts.*

---

## Key Design Principles

1. **Scorers evaluate trajectories**: Mode selection is a trace-level property, making it a natural scorer
2. **Mode selection is optional**: Only applied to tests where the agent has a genuine choice
3. **Separate from correctness**: Tracks execution strategy independently from output quality
4. **Metadata-driven detection**: Reports identify mode scorers by checking for `mode_correct` in metadata, not hardcoded names
5. **Composable evaluation**: Mode selection combines naturally with other scoring dimensions

---

## Acceptance Criteria

| Criteria | Status |
|----------|--------|
| Binary mode metric implemented | ✅ Done |
| Mode scorer added to relevant tests | ✅ Done (6 in experiments, 4 in CI) |
| Mode selection reported separately | ✅ Done |
| ≥90% mode selection accuracy target | ⏳ Pending baseline run |

---

## Next Steps

Run baseline evaluation to measure current mode selection accuracy and validate the 90% target:

```bash
cd experiments/capability_eval
python run_evaluation.py --config config.yaml --runs 3
```

The simplified reporting shows:
- Overall pass rate
- Per-test output and mode accuracy side-by-side
- GitLab metrics for CI/MR tracking
