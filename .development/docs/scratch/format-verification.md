# Format Verification - Concrete Examples

## Test Case 1: Single Model, Single Variant (Simplest)

**Use case**: Basic evaluation run with one model

**File**: `sentiment_simple.006eval.jsonl`
```jsonl
{"metadata": {"timestamp": "2025-12-11T14:00:00", "suite_name": "sentiment", "status": "completed", "config": {"models": ["gpt-4o-mini"], "variants": ["v1"]}}, "results": []}
{"test_id": "sent_001", "test_name": "sentiment", "model": "gpt-4o-mini", "variant": "v1", "output": "positive", "expected": "positive", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}, "trace_file": "traces/sent_001.jsonl"}
{"test_id": "sent_002", "test_name": "sentiment", "model": "gpt-4o-mini", "variant": "v1", "output": "negative", "expected": "negative", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}, "trace_file": "traces/sent_002.jsonl"}
```

**Aggregation**:
- Total tests: 2
- Pass rate: 100% (2/2)
- Models: ["gpt-4o-mini"]
- Variants: ["v1"]

✅ **Simple and clear**

---

## Test Case 2: Multiple Models, Single Variant (Model Comparison)

**Use case**: Compare GPT-4 vs Claude on same tests

**File**: `sentiment_models.006eval.jsonl`
```jsonl
{"metadata": {"timestamp": "2025-12-11T14:00:00", "suite_name": "sentiment", "status": "completed", "config": {"models": ["gpt-4o-mini", "claude-sonnet-4"], "variants": ["v1"]}}, "results": []}
{"test_id": "sent_001", "test_name": "sentiment", "model": "gpt-4o-mini", "variant": "v1", "output": "positive", "expected": "positive", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "sent_001", "test_name": "sentiment", "model": "claude-sonnet-4", "variant": "v1", "output": "positive", "expected": "positive", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "sent_002", "test_name": "sentiment", "model": "gpt-4o-mini", "variant": "v1", "output": "negative", "expected": "negative", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "sent_002", "test_name": "sentiment", "model": "claude-sonnet-4", "variant": "v1", "output": "negative", "expected": "negative", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
```

**Aggregation by Model**:
```
gpt-4o-mini:      2/2 (100%)
claude-sonnet-4:  2/2 (100%)
```

✅ **Each (test, model) pair is a separate line**

---

## Test Case 3: Single Model, Multiple Variants (Prompt Optimization)

**Use case**: Compare different prompt versions

**File**: `sentiment_variants.006eval.jsonl`
```jsonl
{"metadata": {"timestamp": "2025-12-11T14:00:00", "suite_name": "sentiment", "status": "completed", "config": {"models": ["gpt-4o-mini"], "variants": ["v1_baseline", "v2_cot", "v3_reflexion"]}}, "results": []}
{"test_id": "sent_001", "test_name": "sentiment", "model": "gpt-4o-mini", "variant": "v1_baseline", "output": "positive", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "sent_001", "test_name": "sentiment", "model": "gpt-4o-mini", "variant": "v2_cot", "output": "positive", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "sent_001", "test_name": "sentiment", "model": "gpt-4o-mini", "variant": "v3_reflexion", "output": "positive", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
```

**Aggregation by Variant**:
```
v1_baseline:   1/1 (100%)
v2_cot:        1/1 (100%)
v3_reflexion:  1/1 (100%)
```

✅ **Each (test, variant) pair is a separate line**

---

## Test Case 4: Multiple Judges (Code Quality Analysis)

**Use case**: Evaluate both correctness and code quality

**File**: `codegen_judges.006eval.jsonl`
```jsonl
{"metadata": {"timestamp": "2025-12-11T14:00:00", "suite_name": "codegen", "status": "completed", "config": {"models": ["gpt-4o-mini"], "variants": ["v1"], "judges": ["exact_match", "llm_judge", "code_quality"]}}, "results": []}
{"test_id": "code_001", "model": "gpt-4o-mini", "variant": "v1", "output": "def add(a,b):\n  return a+b", "expected": "def add(a, b):\n    return a + b", "passed": false, "scores": {"exact_match": {"passed": false, "score": 0.0, "reasoning": "Whitespace differs"}, "llm_judge": {"passed": true, "score": 1.0, "reasoning": "Functionally correct"}, "code_quality": {"passed": false, "score": 0.6, "reasoning": "Missing spaces around operators"}}}
```

**Aggregation by Judge**:
```
exact_match:   0/1 (0%)   - Strictest
llm_judge:     1/1 (100%) - Semantic
code_quality:  0/1 (0%)   - Style
```

**Questions**:
1. What is the "passed" field at the top level?
   - **Proposed**: Overall pass/fail (AND of all judges? OR? User configurable?)
   - **Alternative**: Remove top-level "passed", only in scores

2. Should we support judge-specific configurations?
   - Example: `{"judges": [{"name": "llm_judge", "model": "claude-4", "temperature": 0}]}`

---

## Test Case 5: Full Matrix (2 models × 3 variants × 2 tests)

**Total lines**: 1 metadata + (2 × 3 × 2) = **13 lines**

```jsonl
{"metadata": {"timestamp": "2025-12-11T14:00:00", "suite_name": "sentiment", "status": "completed", "config": {"models": ["gpt-4o-mini", "claude-4"], "variants": ["v1", "v2", "v3"]}}, "results": []}
{"test_id": "t1", "model": "gpt-4o-mini", "variant": "v1", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t1", "model": "gpt-4o-mini", "variant": "v2", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t1", "model": "gpt-4o-mini", "variant": "v3", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t1", "model": "claude-4", "variant": "v1", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t1", "model": "claude-4", "variant": "v2", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t1", "model": "claude-4", "variant": "v3", "passed": false, "scores": {"exact_match": {"passed": false, "score": 0.0}}}
{"test_id": "t2", "model": "gpt-4o-mini", "variant": "v1", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t2", "model": "gpt-4o-mini", "variant": "v2", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t2", "model": "gpt-4o-mini", "variant": "v3", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t2", "model": "claude-4", "variant": "v1", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t2", "model": "claude-4", "variant": "v2", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
{"test_id": "t2", "model": "claude-4", "variant": "v3", "passed": true, "scores": {"exact_match": {"passed": true, "score": 1.0}}}
```

**Heatmap: Model × Variant**
```
              v1    v2    v3
gpt-4o-mini  2/2   2/2   2/2
claude-4     2/2   2/2   1/2  ← One failure in (claude-4, v3)
```

✅ **Clean aggregation, easy to spot patterns**

---

## Resolved Specifications

### 1. Top-level "passed" field semantics ✅

**Decision**: `passed` = **AND of all judges**

A test passes only if ALL judges pass. This is the most conservative approach.

Example:
```json
"passed": false,  // false because code_quality failed
"scores": {
  "exact_match": {"passed": true, "score": 1.0, "reason": "Correct output"},
  "llm_judge": {"passed": true, "score": 0.95, "reason": "Semantically correct"},
  "code_quality": {"passed": false, "score": 0.6, "reason": "Missing type hints"}
}
```

The viewer will show:
- Overall: ❌ Failed (because not all judges passed)
- exact_match: ✅ Passed
- llm_judge: ✅ Passed
- code_quality: ❌ Failed (reason: "Missing type hints")

### 2. Required vs Optional Fields ✅

**Required**:
- `test_id`, `model`, `variant`, `passed`, `scores`
- Each judge in `scores` must have: `passed`, `score`, `reason`

**Optional**:
- Everything else (`input`, `output`, `expected`, `metrics`, `trace_file`, `error`)

**Important**: `variant` is **mandatory** even with only one variant. Use descriptive names like "v1_baseline".

### 3. Field naming: `test_name` vs `test`

Current spec has both:
```json
"test_id": "sentiment_006",
"test_name": "sentiment",
```

**Decision**: Keep both ✅
- `test_id`: Unique identifier (e.g., "sentiment_006")
- `test_name`: Test type/category (e.g., "sentiment") - optional, defaults to test_id
- `display_name`: Human-readable UI label - optional

---

## Complete Reference Example

Valid test result with all required fields and judge details:

```json
{
  "test_id": "sentiment_006",
  "model": "gpt-4o-mini",
  "variant": "v1_baseline",
  "passed": false,
  "scores": {
    "exact_match": {
      "passed": true,
      "score": 1.0,
      "reason": "Output 'positive' matches expected 'positive'"
    },
    "llm_judge": {
      "passed": true,
      "score": 0.95,
      "reason": "Semantically correct with high confidence"
    },
    "reasoning_quality": {
      "passed": false,
      "score": 0.4,
      "reason": "No reasoning provided, expected chain-of-thought"
    }
  },
  "test_name": "sentiment",
  "display_name": "Classify sentiment: I'm so grateful...",
  "input": {"text": "I'm so grateful for all the support from my friends."},
  "output": "positive",
  "expected": "positive",
  "metrics": {"iterations": 1, "execution_time_ms": 1250},
  "trace_file": "traces/sentiment_006.jsonl",
  "error": null
}
```

**Key observations**:
- `passed` = false (because reasoning_quality failed, even though output was correct)
- Each judge has detailed `reason` explaining their verdict
- Viewer can show which specific judges failed and why

---

## Viewer Aggregation Code

```python
def aggregate_by_model(tests):
    by_model = {}
    for test in tests:
        model = test["model"]
        if model not in by_model:
            by_model[model] = {"total": 0, "passed": 0}
        by_model[model]["total"] += 1
        if test.get("passed"):
            by_model[model]["passed"] += 1
    return by_model

def aggregate_by_variant(tests):
    by_variant = {}
    for test in tests:
        variant = test.get("variant", "default")
        if variant not in by_variant:
            by_variant[variant] = {"total": 0, "passed": 0}
        by_variant[variant]["total"] += 1
        if test.get("passed"):
            by_variant[variant]["passed"] += 1
    return by_variant

def aggregate_by_judge(tests):
    by_judge = {}
    for test in tests:
        for judge_name, judge_result in test.get("scores", {}).items():
            if judge_name not in by_judge:
                by_judge[judge_name] = {"total": 0, "passed": 0}
            by_judge[judge_name]["total"] += 1
            if judge_result.get("passed"):
                by_judge[judge_name]["passed"] += 1
    return by_judge
```

✅ **Simple, clear aggregation logic**

---

## Final Verdict

**Format is complete and ready for implementation** ✅

1. ✅ Supports multiple models
2. ✅ Supports multiple variants (mandatory field)
3. ✅ Supports multiple judges with detailed reasons
4. ✅ Flat structure, easy to parse
5. ✅ Simple aggregation logic
6. ✅ Top-level "passed" = AND of all judges (clarified)
7. ✅ Required vs optional fields documented

**Specification files**:
- `docs/evaluation-file-format.md` - Complete format specification
- `docs/format-verification.md` - Concrete examples and validation (this file)
- `docs/viewer-architecture.md` - System architecture

**Next step**: Implement format in evaluation/runner.py
