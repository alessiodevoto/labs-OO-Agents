# Curriculum Evolution Design

**Status:** Draft
**Created:** 2025-12-12
**Context:** E2E Optimization Loop Phase 2

---

## Overview

After the EVOLVE step generates improved strategies, we add a CURRICULUM step that evolves the evaluation apparatus itself:

1. **Test Evolution** - Add/remove/modify test cases
2. **Judge Evolution** - Add/update scoring criteria

Both produce LLM-proposed changes that require human review before applying.

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│                       GENERATION N                                  │
├─────────────────────────────────────────────────────────────────────┤
│  [1. EVALUATE]  → Results, traces                                  │
│  [2. ANALYZE]   → TraceAnalysis, patterns                          │
│  [3. REFLECT]   → Diagnosis                                        │
│  [4. EVOLVE]    → Strategy mutations                               │
│  [5. CURRICULUM]                                                    │
│      ├─ [5a. TestEvolver]  → Proposed test changes                 │
│      └─ [5b. JudgeEvolver] → Proposed judge changes                │
│  [6. HUMAN REVIEW] → Approve/reject/modify proposals               │
│  [7. SELECT]    → Pareto selection (on updated curriculum)         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Judge Evolution

### Problem Statement

Current judges miss important failure modes. Examples from capability tests:

| Example | What Happened | What Judge Should Catch |
|---------|--------------|------------------------|
| `sentiment_batch_001` | Called `classify_sentiment()` in loop instead of `classify_batch()` | Wrong approach for scale |
| Input echo | LLM re-outputs input variable before processing | Wasteful redundancy |
| LLM for math | Loop calls LLM method to calculate `a*b` | LLM abuse for trivial ops |

### Judge Categories

We need judges that detect **anti-patterns** beyond just correctness:

#### 1. Scale Awareness Judge

**Detects:** Using wrong method for task scale

```yaml
scale_judge:
  type: llm_judge
  model: aws/anthropic/claude-haiku-4-5-v1
  criteria: |
    Evaluate if the code uses the appropriate method for the task scale:

    FAIL conditions:
    - Single item task uses batch method (e.g., wraps item in list)
    - Batch task uses single method in a loop
    - Creates unnecessary list/array when input is scalar

    PASS conditions:
    - Single item task calls single-item method directly
    - Batch task calls batch method or uses proper vectorization

    Return: {"pass": bool, "reason": str, "anti_pattern": str|null}
```

#### 2. Redundancy Judge

**Detects:** Wasteful patterns that don't affect correctness

```yaml
redundancy_judge:
  type: llm_judge
  model: aws/anthropic/claude-haiku-4-5-v1
  criteria: |
    Evaluate if the code has unnecessary redundancy:

    FAIL conditions:
    - Re-outputs/re-assigns input variable before using it
    - Converts type then converts back (e.g., str→int→str)
    - Stores intermediate result that's used only once
    - Duplicates logic that could be a single operation

    PASS conditions:
    - Direct use of input parameters
    - Minimal intermediate variables
    - Clean data flow

    Return: {"pass": bool, "reason": str, "pattern": str|null}
```

#### 3. LLM Abuse Judge

**Detects:** Using LLM for trivial operations

```yaml
llm_abuse_judge:
  type: llm_judge
  model: aws/anthropic/claude-haiku-4-5-v1
  criteria: |
    Evaluate if code uses LLM/agent methods for trivially computable operations:

    FAIL conditions:
    - Calls LLM method for arithmetic (a*b, sum, etc.)
    - Calls LLM method for string manipulation (concat, split, etc.)
    - Calls LLM method for list operations (len, sort, filter with simple predicate)
    - Uses LLM when Python builtin would suffice

    PASS conditions:
    - LLM used for semantic understanding (sentiment, classification, etc.)
    - LLM used for generation (text, code, etc.)
    - LLM used for reasoning over complex data

    Return: {"pass": bool, "reason": str, "trivial_op": str|null}
```

#### 4. Approach Correctness Judge

**Detects:** Fundamentally wrong approach (even if output is correct)

```yaml
approach_judge:
  type: llm_judge
  model: aws/anthropic/claude-haiku-4-5-v1
  criteria: |
    Evaluate if the approach is fundamentally correct, not just the output:

    Given the TASK DESCRIPTION and the GENERATED CODE, determine if the
    approach matches what was asked.

    Examples of FAIL:
    - Task asks for batch processing, code processes items one-by-one
    - Task asks for parallel execution, code is sequential
    - Task asks for specific method, code uses different method
    - Task expects direct return, code builds complex pipeline

    Return: {"pass": bool, "reason": str, "expected_approach": str, "actual_approach": str}
```

### Judge Configuration Schema

```yaml
# In config.yaml
scorers:
  - name: exact_match
    type: exact_match
    weight: 0.4

  - name: scale_judge
    type: llm_judge
    weight: 0.2
    model: aws/anthropic/claude-haiku-4-5-v1
    criteria: |
      [scale awareness criteria]

  - name: redundancy_judge
    type: llm_judge
    weight: 0.1
    model: aws/anthropic/claude-haiku-4-5-v1
    criteria: |
      [redundancy criteria]

  - name: llm_abuse_judge
    type: llm_judge
    weight: 0.2
    model: aws/anthropic/claude-haiku-4-5-v1
    criteria: |
      [llm abuse criteria]

  - name: approach_judge
    type: llm_judge
    weight: 0.1
    model: aws/anthropic/claude-haiku-4-5-v1
    criteria: |
      [approach criteria]
```

### JudgeEvolver Agent

```python
@dataclass
class JudgeProposal:
    """Proposed change to a judge."""
    action: Literal["add", "modify", "remove", "reweight"]
    judge_name: str
    reason: str  # Why this change is needed
    evidence: list[str]  # Trace IDs that motivated this

    # For add/modify
    new_criteria: str | None = None
    new_weight: float | None = None

    # For remove
    removal_reason: str | None = None

class JudgeEvolver:
    """Proposes judge improvements based on trace analysis."""

    async def analyze(
        self,
        traces: list[TraceAnalysis],
        current_judges: list[JudgeConfig],
        diagnosis: Diagnosis,
    ) -> list[JudgeProposal]:
        """
        Analyze traces to find:
        1. False positives: Tests passed but approach was wrong
        2. False negatives: Tests failed but approach was reasonable
        3. Missing criteria: Patterns that no judge catches
        4. Weight imbalances: Some judges too influential
        """
        ...
```

---

## Part 2: Test Evolution

### Problem Statement

Current test suite may be:
- **Incomplete**: Missing edge cases discovered in traces
- **Redundant**: Multiple tests covering same scenario
- **Broken**: Expected values incorrect
- **Mis-specified**: Test doesn't test what we think

### Test Evolution Categories

#### 1. Edge Case Discovery (Fuzzing)

Generate variants of existing tests that probe boundaries:

```python
# Original test
{"input": "I love this!", "expected": "positive"}

# Fuzzing variants
{"input": "I love this.", "expected": "positive"}  # Punctuation
{"input": "i love this!", "expected": "positive"}  # Lowercase
{"input": "I LOVE THIS!", "expected": "positive"}  # Uppercase
{"input": "I love this!!!", "expected": "positive"}  # Multiple punctuation
{"input": " I love this! ", "expected": "positive"}  # Whitespace
{"input": "I love this", "expected": "positive"}  # No punctuation
```

#### 2. Creative Expansion

Generate entirely new test scenarios:

```python
# Existing: Simple sentiment
{"input": "I love this!", "expected": "positive"}

# Creative: Sarcasm
{"input": "Oh great, another Monday.", "expected": "negative"}

# Creative: Mixed signals
{"input": "The food was terrible but the service was excellent.", "expected": "mixed"}

# Creative: Subtle
{"input": "It's fine, I guess.", "expected": "neutral"}

# Creative: Domain-specific
{"input": "Bull market continues, stocks rally.", "expected": "positive"}
```

#### 3. Test Repair

Fix tests where expected values are wrong:

```python
# Before (broken)
{"input": "Not bad at all", "expected": "negative"}  # Wrong! This is positive

# After (fixed)
{"input": "Not bad at all", "expected": "positive"}
```

#### 4. Test Consolidation

Remove redundant tests:

```python
# Redundant set
{"input": "I love this!", "expected": "positive"}
{"input": "I love it!", "expected": "positive"}
{"input": "I love this product!", "expected": "positive"}

# Keep one representative
{"input": "I love this!", "expected": "positive"}

# Add a distinct variant instead
{"input": "I really really love this!", "expected": "positive"}  # Emphasis
```

### TestEvolver Agent

```python
@dataclass
class TestProposal:
    """Proposed change to test suite."""
    action: Literal["add", "modify", "remove", "consolidate"]
    test_id: str | None  # For modify/remove
    reason: str
    evidence: list[str]  # Trace IDs or analysis that motivated this

    # For add
    new_test: dict | None = None  # {"input": ..., "expected": ..., "metadata": ...}
    generation_method: Literal["fuzzing", "creative", "edge_case"] | None = None

    # For modify
    field_changes: dict | None = None  # {"expected": "new_value"}

    # For consolidate
    tests_to_merge: list[str] | None = None
    representative_test: str | None = None

class TestEvolver:
    """Proposes test suite improvements."""

    async def analyze(
        self,
        traces: list[TraceAnalysis],
        current_tests: list[Task],
        diagnosis: Diagnosis,
        judge_results: dict[str, JudgeResult],
    ) -> list[TestProposal]:
        """
        Analyze to find:
        1. Edge cases from trace failures
        2. Creative expansions based on patterns
        3. Broken tests (consistent failures with good approach)
        4. Redundant tests (identical behavior)
        """
        ...
```

---

## Part 3: Human Review Interface

### Proposal Format

All proposals are written to a review file:

```yaml
# experiments/gen_001/curriculum_proposals.yaml
generation: 1
timestamp: 2025-12-12T19:00:00Z

judge_proposals:
  - id: jp_001
    action: add
    judge_name: scale_judge
    reason: "sentiment_batch_001 passed but used loop instead of batch method"
    evidence:
      - trace_id: sentiment_batch_001_gpt-oss-20b
        observation: "Model called classify_sentiment() 5 times instead of classify_batch()"
    criteria: |
      Evaluate if code uses appropriate method for task scale...
    weight: 0.2
    status: pending  # pending | approved | rejected | modified

  - id: jp_002
    action: modify
    judge_name: llm_judge
    reason: "Current judge doesn't catch input echo pattern"
    evidence:
      - trace_id: calculate_single_003
        observation: "Model wrote: x = a; y = b; return x * y"
    criteria_addition: |
      Also FAIL if code re-assigns input parameters to new variables
      before using them.
    status: pending

test_proposals:
  - id: tp_001
    action: add
    reason: "No test for sarcasm detection"
    generation_method: creative
    new_test:
      id: sentiment_sarcasm_001
      input: "Oh wonderful, another bug in production."
      expected: negative
      metadata:
        category: sarcasm
        difficulty: hard
    status: pending

  - id: tp_002
    action: modify
    test_id: sentiment_003
    reason: "Expected value incorrect - 'not bad' is positive"
    field_changes:
      expected: positive
    evidence:
      - manual_review: "Linguistic analysis confirms double negative = positive"
    status: pending

  - id: tp_003
    action: remove
    test_id: sentiment_007
    reason: "Duplicate of sentiment_002"
    evidence:
      - similarity_score: 0.95
      - same_expected: true
    status: pending
```

### Review CLI

```bash
# Show pending proposals
python -m e2e_optimization curriculum review --experiment gen_001

# Approve specific proposal
python -m e2e_optimization curriculum approve jp_001

# Reject with reason
python -m e2e_optimization curriculum reject tp_003 --reason "Tests different edge case"

# Approve all judge proposals
python -m e2e_optimization curriculum approve --type judge --all

# Apply approved changes
python -m e2e_optimization curriculum apply --experiment gen_001
```

### Review Web UI (Optional)

Integrate into existing viewer at port 5005:
- Show proposal diffs
- One-click approve/reject
- Edit proposals before approving
- Show evidence traces inline

---

## Part 4: Canonical vs Evolved Test Sets

### Problem

If tests change every generation, we lose ability to compare across generations.

### Solution: Two Test Sets

```yaml
test_sets:
  canonical:
    description: "Fixed test set for cross-generation comparison"
    source: experiments/capability_eval/canonical.jsonl
    mutable: false

  evolved:
    description: "Evolving test set for comprehensive coverage"
    source: experiments/capability_eval/evolved.jsonl
    mutable: true
    history: experiments/capability_eval/evolved_history/
```

### Metrics Tracking

```json
{
  "generation": 5,
  "canonical_metrics": {
    "accuracy": 0.85,
    "first_try_rate": 0.45,
    "avg_iterations": 1.3
  },
  "evolved_metrics": {
    "accuracy": 0.72,
    "first_try_rate": 0.38,
    "avg_iterations": 1.8,
    "test_count": 45,
    "new_tests_this_gen": 3,
    "removed_tests_this_gen": 1
  }
}
```

---

## Part 5: Implementation Plan

### Phase 1: Judge Evolution (This Sprint)

1. **Define initial judge set** based on examples:
   - `scale_judge` - for sentiment_batch issue
   - `redundancy_judge` - for input echo pattern
   - `llm_abuse_judge` - for trivial computation

2. **Add judges to capability_eval config**
   - Weight: 50% exact_match, 50% combined judges

3. **Run baseline with new judges**
   - Measure how many tests now fail that passed before

4. **Implement JudgeEvolver agent**
   - Takes traces + current judges → proposals

5. **Implement review CLI**
   - `curriculum review`, `approve`, `reject`, `apply`

### Phase 2: Test Evolution (Next Sprint)

1. **Implement TestEvolver agent**
   - Fuzzing logic for variants
   - Creative expansion via LLM

2. **Canonical/evolved split**
   - Move current tests to canonical
   - Create evolved copy for mutation

3. **History tracking**
   - Version control for test changes

4. **Integrate into optimization loop**
   - After EVOLVE, before SELECT

### Phase 3: Full Integration

1. **Web UI for review**
2. **Automated proposal scoring** (confidence levels)
3. **Batch approval workflows**

---

## Open Questions

1. **Judge model:** Should all judges use same model, or can we use cheaper models for simpler checks?

2. **Proposal limits:** How many proposals per generation? (Avoid overwhelming human reviewer)

3. **Approval latency:** Should optimization loop block on human review, or continue with pending proposals?

4. **Rollback:** If applied changes make things worse, how do we revert?

---

## Appendix: Concrete Judge Examples

### Example 1: sentiment_batch_001 (Scale Judge)

**Task:** Classify sentiment of 5 reviews

**Bad Code (should fail scale_judge):**
```python
results = []
for review in reviews:
    result = await self.classify_sentiment(review)
    results.append(result)
return results
```

**Good Code (should pass):**
```python
return await self.classify_batch(reviews)
```

### Example 2: Input Echo (Redundancy Judge)

**Bad Code (should fail redundancy_judge):**
```python
text = input_text
processed = text
sentiment = analyze(processed)
return sentiment
```

**Good Code (should pass):**
```python
return analyze(input_text)
```

### Example 3: LLM for Math (LLM Abuse Judge)

**Bad Code (should fail llm_abuse_judge):**
```python
for i in range(len(numbers)):
    for j in range(len(numbers)):
        product = await self.calculate(numbers[i], numbers[j])
        results.append(product)
```

**Good Code (should pass):**
```python
results = [[a * b for b in numbers] for a in numbers]
```
