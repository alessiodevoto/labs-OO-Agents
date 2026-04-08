

# GEPA vs Agent0 vs Agent006 Optimization: Comparative Analysis

**Date:** 2025-12-12
**Purpose:** Compare approaches from 3p/gepa and 3p/Agent0 with our OPTIMIZATION_PLAN.md

---

## Executive Summary

| Aspect | GEPA | Agent0 | Our Plan (agent006) |
|--------|------|--------|---------------------|
| **Core Approach** | Prompt evolution via reflection | Co-evolution with curriculum learning | Prompt evolution (GEPA-inspired) |
| **What Gets Optimized** | Text components (prompts, code) | Model weights via RL | Strategy templates (prompts) |
| **Optimization Method** | LLM reflection + Pareto selection | PPO/GRPO reinforcement learning | LLM reflection + Pareto selection |
| **Curriculum** | No curriculum learning | Curriculum Agent proposes harder tasks | ✅ LLM-based curriculum evolution (Step 4) |
| **Judge Evolution** | No | No | ✅ We evolve judges too |
| **Data Source** | User-provided train/val sets | Self-generated (zero data) | User-provided + LLM-generated test cases |
| **Integration** | Adapter-based (pluggable) | Requires full training pipeline | Adapter-based (eval_pipeline) |

---

## GEPA: Key Insights

### What GEPA Does Well

1. **Clean Adapter Abstraction**

```python
class GEPAAdapter(Protocol[DataInst, Trajectory, RolloutOutput]):
    def evaluate(self, batch, candidate, capture_traces=False) -> EvaluationBatch
    def make_reflective_dataset(self, candidate, eval_batch, components) -> Mapping
    propose_new_texts: ProposalFn | None = None
```

This is **exactly what we need**. The adapter:
- Evaluates candidates (our agents) on batches
- Captures traces for reflection
- Builds reflective datasets from traces
- Optionally proposes new texts

2. **Pareto-Aware Multi-Objective Selection**

GEPA maintains a **Pareto frontier** of candidates that excel on different subsets of the validation set:

```python
def select_program_candidate_from_pareto_front(
    program_at_pareto_front_valset,  # Dict[DataId, Set[ProgramIdx]]
    program_full_scores_val_set,     # List[float]
    rng,
) -> int
```

**Why this matters:** A candidate that achieves 100% on sentiment but 50% on compute is still valuable—it may contain insights that help other candidates improve.

3. **Merge Proposer for Combining Strengths**

GEPA's merge mechanism is clever: it finds two candidates with a **common ancestor** where each has improved on different components, then combines the improvements:

```python
# If prompt A evolved predictor_1 and candidate B evolved predictor_2
# Merge creates: ancestor + A's predictor_1 + B's predictor_2
```

**This is missing from our plan.** We should add it.

4. **Reflective Dataset Construction**

GEPA requires the adapter to build a "reflective dataset" from traces:

```python
{
    "Inputs": {...},
    "Generated Outputs": {...},
    "Feedback": "...",  # Error messages, scores, correct answer
}
```

This structured format gives the reflection LLM **high-signal context** for proposing improvements.

5. **Component-Level Mutation**

GEPA can update individual components (e.g., just `strategy_instructions` vs `error_empty`) using:
- **Round-robin:** Cycle through components
- **All:** Update all components at once

**Our plan only targets whole strategies.** Component-level evolution may be more efficient.

### What GEPA Lacks

1. **No Curriculum Learning** - Tests are static; model must improve on fixed distribution
2. **No RL** - Pure LLM-based evolution, no gradient updates
3. **No Self-Generated Data** - Requires human-provided train/val sets

---

## Agent0: Key Insights

### What Agent0 Does Differently

1. **Zero-Data Self-Evolution**

Agent0 uses **co-evolution** between two agents:
- **Curriculum Agent:** Generates progressively harder tasks
- **Executor Agent:** Solves tasks with tools

The reward for the Curriculum Agent includes:
- Difficulty (is the Executor struggling?)
- Diversity (not repeating similar problems)
- Solvability (must be answerable)

```python
# From curriculum_reward.py
# Uses clustering to penalize similar questions
penalty = cluster_share_per_problem(questions, distance_threshold=0.5)

# Final score includes tool usage reward
final_score = (difficulty_score - penalty + tool_reward)
```

2. **Tool-Integrated Reasoning**

Agent0 rewards **tool usage** explicitly:

```python
def calculate_tool_reward(predict: str, weight: float = 0.05, cap: int = 4) -> float:
    tool_call_count = len(re.findall(r"```output", predict))
    capped_calls = min(tool_call_count, cap)
    return capped_calls * weight
```

This encourages the Executor to **use tools** rather than pure reasoning.

3. **Reinforcement Learning with PPO/GRPO**

Agent0 uses actual RL algorithms:
- **PPO:** Proximal Policy Optimization with clipped objectives
- **GRPO:** Group Relative Policy Optimization
- **RLOO:** REINFORCE Leave-One-Out
- **DAPO:** Dual-clip Advantage Policy Optimization

```python
def compute_policy_loss(
    old_log_probs, log_probs, advantages, response_mask,
    clip_ratio_low, clip_ratio_high, clip_ratio_dual
) -> tuple[loss, clipfrac_high, clipfrac_low, kl]
```

**Key difference:** Agent0 modifies model weights; GEPA/we modify prompts.

4. **Self-Consistency Filtering**

For training data curation, Agent0 uses self-consistency:

```bash
# Filter training data by executor's self-consistency score
python question_evaluate/upload.py --max_score 0.8 --min_score 0.3
```

Only questions where the executor achieves 30-80% consistency are used for training—not too easy, not too hard.

### What Agent0 Lacks

1. **High Resource Requirements** - Needs GPU cluster, sandbox service, multi-day training
2. **Not Prompt-Focused** - Optimizes weights, not prompts
3. **Complex Setup** - VeRL framework, SandboxFusion, vLLM service
4. **No Judge Evolution** - Reward functions are fixed; we evolve judges too

---

## Our Curriculum vs Agent0's Curriculum: Deep Dive

**We already have curriculum evolution in Step 4!** Here's how it compares:

### Agent0's Curriculum Agent

Agent0 uses a **dedicated RL-trained agent** that generates increasingly hard tasks:

```
Curriculum Agent (RL-trained)
    ↓
Generates questions with target difficulty
    ↓
Executor Agent solves them
    ↓
Self-consistency filters (30-80% pass rate)
    ↓
Both agents trained via PPO
```

**Key features:**
- **Competition:** Curriculum Agent tries to stump Executor
- **Diversity penalty:** Uses BLEU clustering to avoid repetitive questions
- **Self-consistency:** Filters questions by pass rate variance

### Our Curriculum Evolution (Step 4)

We use **LLM-based analysis** that evolves tests AND judges:

```
EvalResults + Traces
    ↓
PerResultAnalysis (per test)
    ↓
ReflectionSynthesis (aggregate patterns)
    ↓
CurriculumEvolution
    ├── tests_to_add (new tests from failure patterns)
    ├── tests_to_modify (adjust difficulty/edge cases)
    ├── tests_to_remove (too easy or duplicative)
    └── judge_changes (new judges, weight adjustments)
```

### Comparison

| Feature | Agent0 Curriculum | Our Curriculum (Step 4) |
|---------|------------------|------------------------|
| **Who generates** | RL-trained Curriculum Agent | LLM analyzer |
| **What evolves** | Only tests | Tests + Judges |
| **Difficulty control** | Self-consistency score | Implicit from failure analysis |
| **Diversity** | BLEU-based penalty | LLM reasoning about coverage |
| **Training** | PPO/GRPO | No training needed |
| **Resource cost** | GPU days | API calls only |

### What to Add from Agent0

1. **Explicit Self-Consistency Scoring:**

   ```python
   # Run each test N times to measure consistency
   def calculate_test_difficulty(test_id: str, n_runs: int = 5) -> float:
       results = [run_test(test_id) for _ in range(n_runs)]
       pass_rate = sum(results) / n_runs
       return pass_rate

   # Focus curriculum evolution on "learnable" tests (30-80%)
   learnable_tests = [t for t in tests if 0.3 <= calculate_test_difficulty(t.id) <= 0.8]
   ```

2. **Diversity Penalty for Curriculum:**

   ```python
   # When proposing new tests, penalize similar ones
   def score_test_proposal(new_test: TestAddition, existing_tests: list) -> float:
       similarity = max(bleu_similarity(new_test.input, t.input) for t in existing_tests)
       novelty_bonus = 1.0 - similarity
       return new_test.expected_value * novelty_bonus
   ```

### What We're Doing Better

1. **Judge Evolution:** Agent0 has fixed reward functions; we evolve judges
2. **LLM Flexibility:** Can add arbitrary test types without retraining
3. **Trace-Driven:** Our proposals come from actual failure patterns, not random generation
4. **Lower Cost:** API calls vs GPU training

---

## Comparative Analysis: What We Should Learn

### From GEPA: ADOPT

| Feature | How to Adopt | Priority |
|---------|--------------|----------|
| **Pareto Frontier** | Track per-test performance, select diverse parents | High |
| **Merge Proposer** | Combine best components from two successful variants | High |
| **Component-Level Evolution** | Evolve individual prompts, not whole strategies | Medium |
| **Reflective Dataset Format** | Structured {Input, Output, Feedback} for reflection | High |
| **Candidate Selection Strategies** | Add epsilon-greedy, current-best, pareto options | Medium |

### From Agent0: LEARN (but not copy)

| Feature | What to Learn | Why Not Copy |
|---------|---------------|--------------|
| **Curriculum Learning** | Could generate harder test cases over time | We don't have curriculum agent |
| **Diversity Penalty** | Penalize similar generated code patterns | Applicable to prompt evolution |
| **Tool Usage Reward** | Reward direct returns over keyword classifiers | Already in LLM judge |
| **Self-Consistency Filtering** | Only train on "goldilocks" difficulty tests | Could apply to test selection |

### What We Should Do Differently

1. **Don't Copy Agent0's RL Approach**
   - Too resource-intensive
   - Prompt optimization is more tractable
   - GEPA achieves 93% on MATH via prompt evolution alone

2. **Add Curriculum-Lite**
   - Instead of curriculum agent, use **LLM judge to rate test difficulty**
   - Progressively introduce harder tests as baseline improves

3. **Hybrid Scoring Like Agent0**
   - Don't just score correctness
   - Include: approach quality, code elegance, tool usage

4. **Structured Trace Analysis (GEPA-style)**
   - Build reflective dataset from OpenInference traces
   - Format: `{Input, LLM_Output, Score, Failure_Reason, Trace_Summary}`

---

## Recommended Architecture Changes

### 1. Adopt GEPA's Adapter Pattern

```python
class Agent006Adapter(GEPAAdapter):
    """Adapter connecting agent006 to GEPA-style optimization."""

    def evaluate(self, batch, candidate, capture_traces=False):
        # Apply candidate (strategy variant) to agent
        # Run on batch, return EvaluationBatch with scores and traces
        pass

    def make_reflective_dataset(self, candidate, eval_batch, components):
        # Extract from OpenInference traces:
        # - LLM inputs/outputs per turn
        # - Error messages
        # - Final score and reasoning
        return {
            "strategy_instructions": [...],
            "error_empty": [...],
        }
```

### 2. Implement Pareto Selection

```python
@dataclass
class CandidateScore:
    candidate_id: int
    per_test_scores: dict[str, float]  # test_id -> score
    aggregate_score: float

def update_pareto_frontier(
    frontier: dict[str, set[int]],  # test_id -> set of candidate_ids
    new_candidate: CandidateScore,
    all_scores: list[CandidateScore],
) -> dict[str, set[int]]:
    """Add candidate to Pareto frontier if it dominates on any test."""
    # Build lookup for candidates that exist in all_scores
    scores_by_id = {c.candidate_id: c for c in all_scores}

    for test_id, score in new_candidate.per_test_scores.items():
        # Only consider candidates that exist in both frontier and all_scores
        valid_frontier_ids = frontier[test_id] & scores_by_id.keys()
        if valid_frontier_ids:
            current_best = max(scores_by_id[cid].per_test_scores[test_id] for cid in valid_frontier_ids)
            if score >= current_best:
                frontier[test_id].add(new_candidate.candidate_id)
        else:
            # No valid candidates in frontier, add new candidate
            frontier[test_id].add(new_candidate.candidate_id)
    return frontier
```

### 3. Add Merge Proposer

```python
def propose_merge(parent_a: Strategy, parent_b: Strategy, ancestor: Strategy) -> Strategy:
    """Combine improvements from two successful variants."""
    merged = {}
    for component in ancestor.components:
        a_changed = parent_a[component] != ancestor[component]
        b_changed = parent_b[component] != ancestor[component]

        if a_changed and not b_changed:
            merged[component] = parent_a[component]
        elif b_changed and not a_changed:
            merged[component] = parent_b[component]
        elif a_changed and b_changed:
            # Both changed - use higher-scoring parent's version
            merged[component] = parent_a[component] if parent_a.score > parent_b.score else parent_b[component]
        else:
            merged[component] = ancestor[component]

    return Strategy(**merged)
```

### 4. Structured Reflective Dataset

```python
def build_reflective_dataset(trace: EvalTrace, result: EvalResult) -> dict:
    """Build GEPA-style reflective dataset from agent006 trace."""
    return {
        "Inputs": {
            "task": result.input,
            "method": result.metadata.get("method"),
        },
        "Generated Outputs": {
            "code": trace.turns[-1].llm_output if trace.turns else "",
            "result": str(result.output),
        },
        "Feedback": _build_feedback(result),
    }

def _build_feedback(result: EvalResult) -> str:
    lines = []
    if not result.passed:
        lines.append(f"❌ Test failed: expected {result.expected}, got {result.output}")

    for scorer_name, scorer_result in result.scores.items():
        if not scorer_result.passed:
            lines.append(f"- {scorer_name}: {scorer_result.reason}")

    if result.trace and result.trace.error:
        lines.append(f"- Error: {result.trace.error}")

    return "\n".join(lines) or "✅ Test passed"
```

---

## Updated Loop Design

Based on GEPA and Agent0 insights, here's the revised optimization loop:

```
┌─────────────────────────────────────────────────────────────────────┐
│                       GENERATION N                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  [1. EVALUATE] - Run population on benchmark                       │
│      NEW: Track per-test scores for Pareto                         │
│                                                                     │
│  [2. PARETO UPDATE] - Update Pareto frontier                       │
│      NEW: Identify candidates that dominate on specific tests      │
│                                                                     │
│  [3. SELECT PARENT] - Choose candidate for mutation                │
│      NEW: Sample from Pareto frontier (not just best)              │
│                                                                     │
│  [4. BUILD REFLECTIVE DATASET]                                     │
│      NEW: GEPA-style structured format from traces                 │
│                                                                     │
│  [5. PROPOSE MUTATIONS] - LLM generates variants                   │
│      NEW: Component-level targeting (not whole strategy)           │
│                                                                     │
│  [6. OPTIONAL: MERGE] - Combine two Pareto-front candidates        │
│      NEW: If both improved different components, merge them        │
│                                                                     │
│  [7. EVALUATE NEW CANDIDATES] - Quick subsample check              │
│      Accept if subsample_score_new > subsample_score_old           │
│                                                                     │
│  [8. SELECTION] - Add to population if improved                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Key Differences from Current Plan

| Current Plan | Recommended Change | Rationale |
|--------------|-------------------|-----------|
| Single best strategy selection | Pareto frontier sampling | Diversity leads to better exploration |
| Whole-strategy evolution | Component-level mutation | More targeted improvements |
| No merge mechanism | Add GEPA-style merge | Combine successful innovations |
| Unstructured trace analysis | Structured reflective dataset | Higher-signal reflection context |
| Static test difficulty | Consider test difficulty weighting | Focus on frontier of capability |
| 50/50 exact_match + llm_judge | Multi-objective with more dimensions | Token cost, latency, approach style |

---

## Implementation Priority

### Phase 1: Core GEPA Features (Week 1)
1. ✅ Implement `Agent006Adapter` following `GEPAAdapter` protocol
2. ✅ Add per-test score tracking
3. ✅ Implement Pareto frontier selection
4. ✅ Build structured reflective dataset from traces

### Phase 2: Evolution Enhancements (Week 2)
1. Add component-level mutation targeting
2. Implement merge proposer
3. Add epsilon-greedy exploration

### Phase 3: Curriculum-Lite (Week 3)
1. Rate test difficulty with LLM
2. Implement progressive difficulty weighting
3. Add diversity penalty for generated code patterns

---

## Conclusion

**GEPA gives us a battle-tested framework** for prompt optimization that we should adopt:
- Pareto selection for diversity
- Merge for combining innovations
- Structured reflective datasets

**Agent0 shows what's possible with RL** but is too heavy for our use case. We can borrow:
- Diversity penalties
- Tool usage rewards
- Self-consistency for test selection

**Our unique advantage** is the agent006 tracing infrastructure—we have rich OpenInference traces that GEPA's adapter pattern can leverage for high-quality reflection.
