# Capability Test Fix Plan

**Created**: 2026-02-11
**Status**: Active
**Goal**: Fix prompts and capability tests — make the foundation solid before scaling back up.

---

## Overview

The capability test suite has 24 tests covering 9 categories. Many tests have accumulated complexity in prompts, judges, and scoring that needs to be cleaned up. The plan is a 3-phase approach: nail a small trusted subset, minimize prompts for good performance, then expand coverage.

## Current State

### Test Categories (24 total)

| Category | Tests | Tier |
|---|---|---|
| Scale awareness (answer vs code) | `sentiment_single`, `sentiment_batch`, `calculate_simple`, `calculate_complex`, `calculate_batch`, `json_qa`, `json_extract` | 5 stable, 2 frontier |
| REPL / needle | `needle_in_haystack` | stable |
| Error recovery & refinement | `error_recovery`, `repl_exploration`, `refinement`, `task_decomposition` | 3 stable, 1 frontier |
| Subagent routing | `router_analyze`, `router_validate`, `router_transform`, `router_multi_analyze_validate`, `router_multi_transform_validate` | all stable |
| Stateful multi-turn | `fast_food_order`, `fast_food_cancel` | 1 frontier, 1 stable |
| Introspection | `employee_lookup` | stable |
| Context API | `context_notes` | frontier |
| Large data / truncation | `large_data_find`, `large_data_extract`, `large_data_count` | all stable |
| Structured output | `structured_combined_extraction` | stable |

### Key Files

- **Strategy (prompts)**: `tests/capability/agents/strategy.py` — overrides `strategy_instructions` and `_build_task_message`
- **Base strategy**: `src/nemo_oo_agents/strategies/codeact.py` — default CodeAct prompts we inherit from
- **Config**: `tests/capability/config.yaml` — test suite definition, scorers, models
- **Agents**: `tests/capability/agents/*.py` — 22 agent files
- **Data**: `tests/capability/data/*.jsonl` — test case data

---

## Phase 1: Trusted Subset — Get the Foundation Right

**Goal**: Pick 5-7 simple, deterministic tests. Make sure every piece works perfectly: test data, agents, scorers/judges, and expected outputs.

### Selected Tests for Phase 1

These are chosen because they're simple, deterministic, and cover the core "does the agent produce the right answer" question without complex methodology scoring:

| Test | Why | Scorers | Notes |
|---|---|---|---|
| `sentiment_single` | Simplest possible test — single text → label | ExactMatch + ModeSelection (internal) | Should answer directly, no code needed |
| `calculate_simple` | Basic arithmetic from natural language | ExactMatch only | Verify numeric answers work |
| `calculate_complex` | Multi-step computation requiring code | ExactMatch + ModeSelection (code) | Verify code execution path works |
| `json_qa` | Direct JSON field lookup | ExactMatch + ModeSelection (internal) | Verify structured data understanding |
| `error_recovery` | Simple retry on transient error | ExactMatch + LLMMethodology | First test with LLM judge — verify it works |

**Optional additions** (if the above 5 go quickly):
- `json_extract` — code path for nested data
- `large_data_find` — tests truncation system with LLM judge

### Phase 1 Tasks

1. **Audit test data**: Read every JSONL for the 5 selected tests. Verify expected values are correct. Fix any issues.
2. **Audit agents**: Read each agent file. Verify docstrings (prompts) are clear and minimal. Verify return types match expected data.
3. **Audit scorers**:
   - Verify `ExactMatchScorer` handles type coercion correctly (e.g., `8` vs `8.0` vs `"8"`)
   - Verify `ModeSelectionScorer` correctly detects internal vs code execution
   - Verify `LLMMethodologyScorer` rubric for `error_recovery` is clear and produces consistent results
4. **Run the subset**: Execute just these 5 tests across 2-3 models. Collect baseline pass rates.
5. **Fix any issues found**: Broken expected values, unclear rubrics, scorer edge cases.
6. **Re-run and confirm**: All 5 tests should produce consistent, explainable results.

### Success Criteria (Phase 1)

- All 5 tests pass consistently (>90% across 3+ runs) on at least 2 models
- Every failure is explainable (model limitation, not test/infrastructure bug)
- LLM judge for `error_recovery` agrees with manual inspection

---

## Phase 2: Minimize Prompts

**Goal**: Strip the strategy prompt (`strategy_instructions`) and task message (`_build_task_message`) down to the minimum that still produces good results on the Phase 1 subset.

### Current Prompt Analysis

The `CapabilityStrategy.strategy_instructions` in `tests/capability/agents/strategy.py` currently duplicates much of the base `CodeActStrategy.strategy_instructions` from `src/nemo_oo_agents/strategies/codeact.py`. The capability strategy should ideally add **nothing** — or only test-specific guidance — on top of the base.

Key areas to minimize:

1. **Strategy instructions**: The base CodeAct strategy already explains `execute_python` and `return_result` thoroughly. The capability override repeats most of this. Options:
   - **Option A**: Remove the capability override entirely — just use the base strategy
   - **Option B**: Keep a minimal override that adds only test-specific value (e.g., "prefer direct answers for simple tasks")

2. **Task message**: Currently `"# Task: {docstring}\nUse execute_python(code) or return_result(value). You MUST call a tool."` — this is already fairly minimal. Consider whether the tool reminder is redundant with strategy instructions.

3. **Prefill**: `InspectInputsPrefill` auto-generates code to inspect input parameters. Evaluate whether this helps or hurts on simple tasks (e.g., sentiment classification probably doesn't benefit from a prefill that prints the input).

### Phase 2 Tasks

1. **Baseline**: Run Phase 1 tests with current prompts. Record scores.
2. **Experiment A — No capability override**: Remove `strategy_instructions` override from `CapabilityStrategy`, inherit base CodeAct directly. Run Phase 1 tests. Compare.
3. **Experiment B — Minimal override**: Keep only the delta that adds value. Run Phase 1 tests. Compare.
4. **Experiment C — Prefill ablation**: Disable `InspectInputsPrefill` for simple tests. Compare.
5. **Choose best configuration**: Pick the most minimal prompt that maintains >90% pass rate.
6. **Update strategy.py**: Apply the winning configuration.

### Success Criteria (Phase 2)

- Strategy prompt is as short as possible while maintaining Phase 1 pass rates
- No unnecessary repetition between capability strategy and base CodeAct strategy
- Clear documentation of what was removed and why

---

## Phase 3: Expand Test Coverage

**Goal**: Add more capability tests back one at a time, fixing issues as they surface.

### Expansion Order (easiest → hardest)

**Tier 1 — Direct expansions of Phase 1 patterns:**
- `sentiment_batch` — batch version of sentiment (code path)
- `calculate_batch` — batch version of calculate (code path)
- `json_extract` — code path for nested JSON

**Tier 2 — REPL and multi-step:**
- `repl_exploration` — multi-step REPL with riddles
- `needle_in_haystack` — find needle in large data
- `large_data_find`, `large_data_extract`, `large_data_count` — truncation system

**Tier 3 — Complex orchestration:**
- `task_decomposition` — helper method reuse
- `refinement` — iterative refinement
- `employee_lookup` — introspection / doc()

**Tier 4 — Subagent routing (most complex):**
- `router_analyze`, `router_validate`, `router_transform`
- `router_multi_analyze_validate`, `router_multi_transform_validate`

**Tier 5 — Stateful / advanced:**
- `fast_food_order`, `fast_food_cancel`
- `context_notes`
- `structured_combined_extraction`

### Phase 3 Tasks (per tier)

1. Add tier's tests to the run
2. Run and collect results
3. For each failing test:
   - Is the test data correct?
   - Is the scorer appropriate?
   - Is the agent prompt clear?
   - Does the strategy prompt need refinement?
4. Fix issues, re-run, confirm
5. Move to next tier

### Success Criteria (Phase 3)

- Each tier's tests pass at >80% before moving to the next tier
- No prompt changes regress earlier tiers
- All LLM judges produce consistent, explainable results

---

## Running Tests

```bash
# Activate environment
source .venv/bin/activate

# Run full suite
python -m eval_pipeline --config tests/capability/config.yaml --runs 3

# Run Phase 1 subset only
python -m eval_pipeline --config tests/capability/config.yaml \
  --test sentiment_single,calculate_simple,calculate_complex,json_qa,error_recovery \
  --runs 3

# Run single test for debugging
python -m eval_pipeline --config tests/capability/config.yaml \
  --test sentiment_single --limit 1

# Debug LLM judge
export DEBUG_JUDGE_INPUT=./tmp/judge_debug
python -m eval_pipeline --config tests/capability/config.yaml \
  --test error_recovery --limit 1
```

---

## Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-02-11 | Start with 5 simple tests | Build confidence in infrastructure before expanding |
| 2026-02-11 | Include `error_recovery` in Phase 1 | Earliest possible validation of LLM judge |
| 2026-02-11 | Phase 2 before Phase 3 | Minimize prompt complexity before adding test complexity |
