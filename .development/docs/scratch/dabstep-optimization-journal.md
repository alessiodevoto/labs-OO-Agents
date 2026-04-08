# DABStep E2E Optimization Project Journal

**Started:** 2026-01-20
**Status:** Phase 2 Complete - Ready for Phase 3

---

## Project Goal

Boot up the e2e_optimization system on DABStep benchmark with user-in-the-loop workflow.

**Pareto criteria:** Success rate on 10 training examples only (no token count limits).

---

## Full Plan

### Phase 1: Baseline Evaluation of All Agents

**Objective:** Evaluate all 6 dabstep agents on 10 training examples, rank by success rate.

#### Step 1.1: Run Baseline Evals (Sequential)
Run each agent on DABStep dev (10 samples) using existing run_ablation.py:

```bash
cd experiments/evaluation-ablations

# Agent 000 (baseline)
python run_ablation.py --benchmark dabstep --agent-file agents/dabstep_agent000.py \
  --provider nvidia_internal --model aws/anthropic/bedrock-claude-sonnet-4-5-v1 \
  --reasoning-effort high --concurrent-tasks 5 --limit 10

# Repeat for agents 001-005...
```

#### Step 1.2: Collect Results & Create Pareto Table

**USER CHECKPOINT 1:** Review baseline rankings. Decide which agent(s) to optimize.

---

### Phase 2: Setup E2E Optimizer for DABStep

#### Step 2.1: Create DABStep Training Data File
Generate `train_data.jsonl` with 10 samples from DABStep dev set.

#### Step 2.2: Create DABStep Config
Create `config.yaml` for the e2e optimizer.

**USER CHECKPOINT 2:** Review config before initializing optimizer.

---

### Phase 3: User-in-the-Loop Optimization Loop

#### Step 3.1: Initialize & Evaluate (Single Run)
Since training set is small (10 samples), we use n_runs=1 (no repeated runs per sample).

```python
from e2e_optimization.optimizer import Optimizer
opt = Optimizer("util/e2e_optimization/src/e2e_optimization/examples/dabstep/config.yaml")
await opt.run_eval(n_runs=1)  # Single run, no repeats
```

#### Step 3.2: Analyze Differential Results
Select **differential samples** - where one agent succeeded and another failed.

**USER CHECKPOINT 3:** Review differential samples - what did passing agents do differently?

#### Step 3.3: Reflect on Patterns
```python
proposed_changes = await opt.reflect()
# Shows proposed code modifications
```

**USER CHECKPOINT 4:** Review proposed changes. Accept, modify, or reject.

#### Step 3.4: Test Proposal
```python
result = await opt.accept_or_reject()
print(f"Parent: {result.parent_score:.1%} → Proposed: {result.proposed_score:.1%}")
```

**USER CHECKPOINT 5:** Review results. Iterate or stop.

#### Step 3.5: Repeat
Loop Steps 3.2-3.4 until:
- Pass rate meets target
- User decides to stop
- Max iterations reached

---

## Todo List

| # | Task | Status |
|---|------|--------|
| 1 | Run baseline eval for dabstep_agent000 | COMPLETED |
| 2 | Run baseline eval for dabstep_agent001 | COMPLETED |
| 3 | Run baseline eval for dabstep_agent002 | COMPLETED |
| 4 | Run baseline eval for dabstep_agent003 | COMPLETED |
| 5 | Run baseline eval for dabstep_agent004 | COMPLETED |
| 6 | Run baseline eval for dabstep_agent005 | COMPLETED |
| 7 | Run baseline eval for dabstep_agent006 | COMPLETED |
| 8 | Run baseline eval for dabstep_agent007 | COMPLETED |
| 9 | Create pareto table comparing all agents | COMPLETED |
| 10 | Create e2e optimizer config for DABStep | COMPLETED |
| 11 | Generate train_data.jsonl from DABStep dev | COMPLETED |
| 12 | Verify optimizer can load config | COMPLETED |
| 13 | Initialize optimizer and start user-in-the-loop iteration | PENDING |

---

## Journal Entries

### 2026-01-20 ~14:00 - Baseline Evaluations Complete

Ran all 6 DABStep agents on 10 training samples with Claude Sonnet 4.5 + reasoning_effort=high.

**Results:**

| Rank | Agent | Architecture | Pass Rate | Notes |
|------|-------|-------------|-----------|-------|
| 1 | 000 | Single agent, basic CodeAct | **5/10 (50%)** | Best baseline |
| 1 | 001 | Multi-step workflow | **5/10 (50%)** | Tied with 000 |
| 1 | 003 | Single agent, 3p-style prompts | **5/10 (50%)** | Tied with 000, 001 |
| 4 | 002 | 3-subagent (RulesLawyer, Verifier) | 3/10 (30%) | |
| 4 | 005 | 3-subagent + improved prompts | 3/10 (30%) | |
| 6 | 004 | 3-subagent + regex | 2/10 (20%) | Worst performer |

**Key Finding:** Simpler agents outperform complex multi-agent architectures!

**Per-Sample Results:**

| Sample | 000 | 001 | 002 | 003 | 004 | 005 | Notes |
|--------|-----|-----|-----|-----|-----|-----|-------|
| dabstep_5_easy | PASS | PASS | PASS | PASS | PASS | PASS | All pass |
| dabstep_49_easy | FAIL | PASS | PASS | FAIL | PASS | PASS | Differential |
| dabstep_70_easy | FAIL | FAIL | **PASS** | FAIL | FAIL | FAIL | Only 002 passes! |
| dabstep_1273_hard | PASS | FAIL | FAIL | PASS | FAIL | PASS | Differential |
| dabstep_1305_hard | PASS | PASS | FAIL | PASS | FAIL | FAIL | Differential |
| dabstep_1464_hard | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | Only 001 passes |
| dabstep_1681_hard | PASS | PASS | FAIL | PASS | FAIL | FAIL | Differential |
| dabstep_1753_hard | PASS | FAIL | FAIL | PASS | FAIL | FAIL | Differential |
| dabstep_1871_hard | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | All fail |
| dabstep_2697_hard | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | All fail |

**Unique Wins:**
- Agent 002 is the ONLY agent to pass dabstep_70_easy ("Not Applicable" question)
- Agent 001 is the ONLY agent to pass dabstep_1464_hard (fee IDs question with 448 IDs!)

**Decision:** Selected agent000 as baseline for optimization (simplest, tied for best).

---

### 2026-01-20 ~14:30 - E2E Optimizer Setup Complete

Created the optimizer configuration:

**Files Created:**
1. `util/e2e_optimization/src/e2e_optimization/examples/dabstep/config.yaml`
2. `util/e2e_optimization/src/e2e_optimization/examples/dabstep/train_data.jsonl`
3. `util/e2e_optimization/src/e2e_optimization/examples/dabstep/agent.py` (copy of agent000)
4. `docs/e2e-optimization-process.md` (mermaid diagrams)
5. `docs/dabstep-pareto-baseline.md` (baseline results)

**Config Summary:**

| Setting | Value |
|---------|-------|
| Target Agent | agent.py (copy of agent000) |
| Training Data | 10 samples from DABStep dev |
| Scorer | ExactMatchScorer (case insensitive) |
| Objective | Accuracy only (no token limits) |
| Model | Claude Sonnet 4.5 + reasoning_effort=high |
| n_runs | 1 (single run per sample) |

**Verification:** Optimizer loads and initializes successfully.

---

### 2026-01-20 ~15:00 - At USER CHECKPOINT 2

Setup complete. Ready to proceed with Phase 3 (optimization loop).

**Next Steps:**
1. Run `opt.run_eval(n_runs=1)` to evaluate agent on all 10 samples
2. Analyze differential samples
3. Run reflection to propose changes
4. User review and accept/reject cycle

---

### 2026-01-20 ~16:05 - User Created Agents 006 and 007

User built two new agents using 8-phase decomposition approach:
- **Agent 006**: Soft decomposition (guidance-based)
- **Agent 007**: Hard decomposition (Pydantic-enforced)

Ran evaluations for both in parallel.

**Results:**

| Rank | Agent | Architecture | Pass Rate | Notes |
|------|-------|-------------|-----------|-------|
| **1** | **007** | 8-phase hard decomposition | **6/10 (60%)** | **NEW LEADER** |
| 2 | 000 | Single agent, basic CodeAct | 5/10 (50%) | Previous best |
| 2 | 001 | Multi-step workflow | 5/10 (50%) | Tied |
| 2 | 003 | Single agent, 3p-style prompts | 5/10 (50%) | Tied |
| 2 | 006 | 8-phase soft decomposition | 5/10 (50%) | Tied |
| 6 | 002 | 3-subagent (RulesLawyer, Verifier) | 3/10 (30%) | |
| 6 | 005 | 3-subagent + improved prompts | 3/10 (30%) | |
| 8 | 004 | 3-subagent + regex | 2/10 (20%) | Worst |

**Agent 007 Per-Sample:**

| Sample | Result |
|--------|--------|
| dabstep_5_easy | PASS |
| dabstep_49_easy | PASS |
| dabstep_70_easy | PASS |
| dabstep_1273_hard | PASS |
| dabstep_1305_hard | PASS |
| dabstep_1464_hard | PASS |
| dabstep_1681_hard | FAIL |
| dabstep_1753_hard | FAIL |
| dabstep_1871_hard | FAIL |
| dabstep_2697_hard | FAIL |

**Key Breakthroughs:**
1. Both 006 and 007 pass dabstep_70_easy ("Not Applicable" - previously only 002)
2. Both 006 and 007 pass dabstep_1464_hard (448 fee IDs - previously only 001)
3. Agent 007 uniquely passes 1273_hard and 1305_hard among 8-phase agents

**Interesting Trade-off:**
- Agent 006 passes 1753_hard but 007 fails
- Agent 007 passes 1273_hard/1305_hard but 006 fails
- Suggests potential for combining strengths

Updated pareto baseline document with all 8 agents.

---

### 2026-01-21 ~14:00 - Agent Directory Structure Implementation

Implemented the new agent directory structure for organized e2e optimization.

**Problem:** Results were scattered in timestamped directories with no lineage tracking.

**Solution:** Each agent gets its own directory with:
- Source code
- Append-only event log (`.006opt.jsonl`) with Pydantic-backed types
- Minibatch results (overwritten per run)
- Fullbatch results (accumulated history)
- Trace analysis output

**Files Created:**

| File | Purpose |
|------|---------|
| `docs/agent-directory-structure.md` | Full specification |
| `util/e2e_optimization/src/e2e_optimization/agent_types.py` | Pydantic event types |
| `util/e2e_optimization/src/e2e_optimization/agent_directory.py` | Helper class |

**Event Types:**
- `AgentCreated` - First event, tracks parents and method (baseline/mutation/merge/manual)
- `AgentMutation` - Logged when source code changes, includes source_hash
- `MinibatchEval` - Quick validation on targeted tests
- `FullbatchEval` - Complete evaluation with per_test results
- `TraceAnalysis` - Linked to source_hash, regenerated on source change

**run_ablation.py Update:**
Added `--output-dir` flag for explicit result placement:
```bash
python run_ablation.py --benchmark dabstep --agent-file agents/dabstep_agent008.py \
  --output-dir results/dabstep/agent008/fullbatch/
```

**Next:** Run e2e optimization with agent006 as baseline.

---

### 2026-01-21 ~14:40 - 9-Run Reliability Results

Completed 9-run reliability testing for all 8 agents. **Surprise: agent006 is the leader!**

**Overall Results (9 runs × 10 tests = ~90 results per agent):**

| Rank | Agent | Passed/Total | Rate | Single-Run Rate |
|------|-------|--------------|------|-----------------|
| 1 | **agent006** | 60/92 | **65.2%** | 50% |
| 2 | agent005 | 56/92 | 60.8% | 30% |
| 3 | agent007 | 54/92 | 58.6% | 60% |
| 4 | agent001 | 37/92 | 40.2% | 50% |
| 5 | agent002 | 36/92 | 39.1% | 30% |
| 6 | agent003 | 26/92 | 28.2% | 50% |
| 7 | agent000 | 25/92 | 27.1% | 50% |
| 8 | agent004 | 16/92 | 17.3% | 20% |

**Key Insight:** Multi-run reliability differs significantly from single-run results!
- agent006 went from 50% → 65.2% (consistent performer)
- agent007 went from 60% → 58.6% (less reliable)
- agent005 went from 30% → 60.8% (surprisingly good with more runs)

**Per-Test Reliability Comparison (Top 3 Agents):**

| Test | agent006 | agent005 | agent007 |
|------|----------|----------|----------|
| dabstep_5_easy | 9/9 (100%) | 9/9 (100%) | 9/9 (100%) |
| dabstep_49_easy | 9/9 (100%) | 9/9 (100%) | 9/9 (100%) |
| dabstep_70_easy | 9/9 (100%) | 9/9 (100%) | 7/9 (77%) |
| dabstep_1273_hard | 9/9 (100%) | 5/9 (55%) | 8/9 (88%) |
| dabstep_1305_hard | 7/9 (77%) | 8/9 (88%) | 8/9 (88%) |
| dabstep_1464_hard | 9/9 (100%) | 9/9 (100%) | 9/9 (100%) |
| dabstep_1681_hard | 4/9 (44%) | 2/9 (22%) | 1/9 (11%) |
| dabstep_1753_hard | 4/9 (44%) | 5/9 (55%) | 3/9 (33%) |
| dabstep_1871_hard | 0/9 (0%) | 0/9 (0%) | 0/9 (0%) |
| dabstep_2697_hard | 0/9 (0%) | 0/9 (0%) | 0/9 (0%) |

**Analysis:**
- **Stable (100%):** 5_easy, 49_easy, 1464_hard (all top agents)
- **agent006 unique strength:** 70_easy (100%), 1273_hard (100%)
- **Unstable but solvable:** 1681_hard, 1753_hard (need optimization)
- **Unsolved:** 1871_hard, 2697_hard (0% across all agents)

**Decision:** Use **agent006** as baseline for e2e optimization (highest reliability).

---

### 2026-01-21 ~15:00 - Iteration Setup for Merge Approach

Set up the agent directory structure retroactively and prepared for the first merge iteration.

**Directory Structure Created:**

```
experiments/evaluation-ablations/results/dabstep/
├── agent006/
│   ├── agent006.py                    # 8-phase soft decomposition
│   ├── agent006.006opt.jsonl          # Event log
│   └── fullbatch/
│       └── 9run_reliability.006eval.jsonl
├── agent007/
│   ├── agent007.py                    # 8-phase hard decomposition
│   ├── agent007.006opt.jsonl          # Event log
│   └── fullbatch/
│       └── 9run_reliability.006eval.jsonl
└── agent008/
    ├── agent008.py                    # Placeholder for merge
    ├── agent008.006opt.jsonl          # parents: [agent006, agent007], method: merge
    ├── minibatch/traces/
    ├── fullbatch/traces/
    └── analysis/
```

**Event Logs Created:**

agent006.006opt.jsonl:
```jsonl
{"type": "created", "parents": [], "method": "baseline", "description": "8-phase soft decomposition (guidance-based)"}
{"type": "fullbatch_eval", "passed": 6, "total": 10, "pass_rate": 0.6, "per_test": {...}}
```

agent007.006opt.jsonl:
```jsonl
{"type": "created", "parents": [], "method": "baseline", "description": "8-phase hard decomposition (Pydantic-enforced)"}
{"type": "fullbatch_eval", "passed": 6, "total": 10, "pass_rate": 0.6, "per_test": {...}}
```

agent008.006opt.jsonl:
```jsonl
{"type": "created", "parents": ["agent006", "agent007"], "method": "merge", "description": "GEPA crossover of agent006 (soft) + agent007 (hard)"}
```

**Iteration Plan:**

The merge iteration will proceed step-by-step:

1. **Generate Merge** - Use LLM to analyze both agents and produce merged code
   - Input: agent006.py, agent007.py, per-test results
   - Output: Updated agent008.py with merged implementation
   - Log: AgentMutation event with source_hash

2. **Run Minibatch** - Quick validation on target tests
   - Tests: dabstep_1681_hard, dabstep_1753_hard (unstable)
   - Command: `python run_ablation.py --agent-file results/dabstep/agent008/agent008.py --benchmark dabstep --task-ids dabstep_1681_hard dabstep_1753_hard --output-dir results/dabstep/agent008/minibatch/`
   - Log: MinibatchEval event

3. **Analyze Traces** (if minibatch fails)
   - Run trace analyzer on failing tests
   - Log: TraceAnalysis event with summary

4. **Run Fullbatch** (if minibatch passes)
   - Full 10-test evaluation
   - Command: `python run_ablation.py --agent-file results/dabstep/agent008/agent008.py --benchmark dabstep --limit 10 --output-dir results/dabstep/agent008/fullbatch/`
   - Log: FullbatchEval event

5. **Compare & Decide**
   - Compare agent008 to parents (agent006: 65.2%, agent007: 58.6%)
   - Target: >70% reliability (7/10 tests)
   - Decision: Accept, iterate, or abandon

**Next:** Run trace analysis first, then generate merge.

---

### E2E Optimization Loop - Full Sketch

```
┌─────────────────────────────────────────────────────────────────────┐
│                     E2E OPTIMIZATION LOOP                           │
└─────────────────────────────────────────────────────────────────────┘

INPUTS:
  - Parent agents (agent006.py, agent007.py)
  - Per-test reliability data (9-run results)
  - Traces from failing runs
  - Target tests (1681_hard, 1753_hard)

┌──────────────────────────────────────────────────────────────────────┐
│ STEP 0: TRACE ANALYSIS                                               │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Input:  Traces from failed runs on target tests                     │
│          - results/.../traces/dabstep_1681_hard_*.006trace.jsonl     │
│          - results/.../traces/dabstep_1753_hard_*.006trace.jsonl     │
│                                                                      │
│  Process:                                                            │
│    1. Load traces with TraceExplorer.from_file_with_eval()          │
│    2. Run trace_analyzer.analyze_trace_failure() on each             │
│    3. Aggregate findings: root causes, patterns, improvement hints   │
│                                                                      │
│  Output:                                                             │
│    - analysis/trace_analysis.json (structured findings)              │
│    - TraceAnalysis event in .006opt.jsonl                            │
│    - Summary for merge prompt                                        │
│                                                                      │
│  Model: Claude Haiku (fast, cheap) or Nemotron Nano (target)         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 1: GENERATE MERGE                                               │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Input:                                                              │
│    - Parent agent sources (agent006.py, agent007.py)                 │
│    - Per-test results (what each parent passes/fails)                │
│    - Trace analysis summary (why they fail)                          │
│                                                                      │
│  Process:                                                            │
│    1. Build merge prompt with:                                       │
│       - Both agent sources                                           │
│       - Complementarity analysis (A wins X, B wins Y)                │
│       - Failure analysis summary                                     │
│    2. Call LLM to generate merged code                               │
│    3. Validate syntax (ast.parse)                                    │
│    4. Write to agent008.py                                           │
│                                                                      │
│  Output:                                                             │
│    - agent008/agent008.py (merged implementation)                    │
│    - AgentMutation event with source_hash                            │
│                                                                      │
│  Model: Claude Opus (best for code generation)                       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 2: MINIBATCH EVAL                                               │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Input:                                                              │
│    - agent008/agent008.py                                            │
│    - Target tests: [dabstep_1681_hard, dabstep_1753_hard]            │
│                                                                      │
│  Command:                                                            │
│    python run_ablation.py \                                          │
│      --agent-file results/dabstep/agent008/agent008.py \             │
│      --benchmark dabstep \                                           │
│      --task-ids dabstep_1681_hard dabstep_1753_hard \                │
│      --output-dir results/dabstep/agent008/minibatch/                │
│                                                                      │
│  Output:                                                             │
│    - minibatch/latest.006eval.jsonl                                  │
│    - minibatch/traces/*.006trace.jsonl                               │
│    - MinibatchEval event                                             │
│                                                                      │
│  Decision:                                                           │
│    - If PASS: Proceed to fullbatch                                   │
│    - If FAIL: Go to Step 3 (re-analyze) or iterate                   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
              [MINIBATCH PASS]               [MINIBATCH FAIL]
                    │                               │
                    │                               ▼
                    │         ┌─────────────────────────────────────────┐
                    │         │ STEP 3: RE-ANALYZE (if failed)          │
                    │         ├─────────────────────────────────────────┤
                    │         │                                         │
                    │         │  Input: New traces from failed minibatch│
                    │         │                                         │
                    │         │  Process:                               │
                    │         │    1. Analyze new failure traces        │
                    │         │    2. Compare to previous analysis      │
                    │         │    3. Update improvement hints          │
                    │         │                                         │
                    │         │  Output:                                │
                    │         │    - Updated trace_analysis.json        │
                    │         │    - TraceAnalysis event (new hash)     │
                    │         │                                         │
                    │         │  Decision:                              │
                    │         │    - ITERATE: Go back to Step 1         │
                    │         │    - ABANDON: Stop, try different pair  │
                    │         │                                         │
                    │         └─────────────────────────────────────────┘
                    │                               │
                    │                               │ (iterate)
                    │                               └──────────► Step 1
                    │
                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 4: FULLBATCH EVAL                                               │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Input:                                                              │
│    - agent008/agent008.py                                            │
│    - All 10 tests                                                    │
│                                                                      │
│  Command:                                                            │
│    python run_ablation.py \                                          │
│      --agent-file results/dabstep/agent008/agent008.py \             │
│      --benchmark dabstep \                                           │
│      --limit 10 \                                                    │
│      --output-dir results/dabstep/agent008/fullbatch/                │
│                                                                      │
│  Output:                                                             │
│    - fullbatch/<timestamp>.006eval.jsonl                             │
│    - fullbatch/traces/*.006trace.jsonl                               │
│    - FullbatchEval event with per_test results                       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 5: COMPARE & DECIDE                                             │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Compare agent008 to parents:                                        │
│    - agent006: 65.2% (6/10 per-test, 60/92 per-run)                  │
│    - agent007: 58.6% (6/10 per-test, 54/92 per-run)                  │
│    - agent008: ???                                                   │
│                                                                      │
│  Target: >70% (7/10 tests passing reliably)                          │
│                                                                      │
│  Decision:                                                           │
│    - ACCEPT: agent008 beats both parents → new baseline              │
│    - ITERATE: Close but not there → refine merge                     │
│    - ABANDON: Regression or no improvement → try different approach  │
│                                                                      │
│  If ACCEPT:                                                          │
│    - agent008 becomes a potential parent for agent009                │
│    - Run 9-run reliability test for robust scoring                   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘

KEY FILES:
  - agent008/agent008.py            The merged agent source
  - agent008/agent008.006opt.jsonl  Event log (lineage, evals, analysis)
  - agent008/minibatch/             Quick validation results
  - agent008/fullbatch/             Full evaluation results (accumulated)
  - agent008/analysis/              Trace analysis output

EVENT TYPES:
  - AgentCreated    parents=["agent006", "agent007"], method="merge"
  - TraceAnalysis   source_hash=..., failing_tests=[...], summary=...
  - AgentMutation   source_hash=..., description="Merged from parents"
  - MinibatchEval   tests=[...], passed=N, total=M
  - FullbatchEval   passed=N, total=10, pass_rate=..., per_test={...}
```

**Current Position:** Ready to start Step 0 (Trace Analysis).

---

## Training Samples

| ID | Level | Question Preview | Expected |
|----|-------|-----------------|----------|
| dabstep_5_easy | easy | Which issuing country has highest transactions... | NL |
| dabstep_49_easy | easy | Top country for fraud? A. NL, B. BE... | B. BE |
| dabstep_70_easy | easy | Is Martinis_Fine_Steakhouse in danger... | **Not Applicable** |
| dabstep_1273_hard | hard | Average fee for credit GlobalCard 10 EUR... | 0.120132 |
| dabstep_1305_hard | hard | Fee for account H, MCC Eating Places... | 0.123217 |
| dabstep_1464_hard | hard | Fee IDs for account_type=R, aci=B... | **448 fee IDs!** |
| dabstep_1681_hard | hard | Fee IDs for Belles_cookbook_store on 10th... | 10 fee IDs |
| dabstep_1753_hard | hard | Fee IDs for Belles_cookbook_store in March... | 34 fee IDs |
| dabstep_1871_hard | hard | Delta if fee 384 changed to 1... | -0.94... |
| dabstep_2697_hard | hard | Preferred ACI for lowest fees... | E:13.57 |

---

## Key Observations

1. **Simpler is better** - Single-agent architectures (000, 001, 003) all hit 50%, while complex 3-subagent architectures (002, 004, 005) performed worse (20-30%).

2. **Unique capabilities exist** - Agent 002 uniquely handles "Not Applicable" cases. Agent 001 uniquely solves the fee ID enumeration problem.

3. **Hard questions are hard** - dabstep_1871_hard and dabstep_2697_hard defeat all agents. These may require specific domain knowledge or calculation approaches not present in any current agent.

4. **Differential analysis opportunity** - 7 samples show differential behavior across agents, providing rich material for understanding what works.

---

## Files Reference

| File | Purpose |
|------|---------|
| `docs/dabstep-pareto-baseline.md` | Baseline evaluation results |
| `docs/e2e-optimization-process.md` | Mermaid diagrams of optimization flow |
| `docs/agent-directory-structure.md` | Agent directory structure spec |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/config.yaml` | Optimizer config |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/train_data.jsonl` | 10 training samples |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/agent.py` | Agent to optimize |
| `util/e2e_optimization/src/e2e_optimization/agent_types.py` | Pydantic event types for .006opt.jsonl |
| `util/e2e_optimization/src/e2e_optimization/agent_directory.py` | AgentDirectory helper class |
| `experiments/evaluation-ablations/agents/dabstep_agent000.py` | Original agent000 |
| `experiments/evaluation-ablations/agents/dabstep_agent006.py` | 8-phase soft decomposition |
| `experiments/evaluation-ablations/agents/dabstep_agent007.py` | 8-phase hard decomposition |

---

## Solutions Directory (Optional)

**Optional** per-sample context files for trace analysis. Many benchmarks have this information baked into the `.006eval` file or directly in the trace, making these files unnecessary.

Use solutions files when:
- The benchmark doesn't include solution explanations
- You want to provide domain-specific hints to the analyzer
- The correct algorithm is non-obvious (e.g., "collect ALL vs select BEST")

**Location:** `util/e2e_optimization/src/e2e_optimization/examples/dabstep/solutions/`

**Naming:** `<base_test_id>.md` (e.g., `dabstep_1681.md` for `dabstep_1681_hard`)

**Contents:**
- Question and expected answer
- Verified solution code with correct algorithm
- Key insights (e.g., "empty list = applies to all", "collect ALL matching fees")
- Data files used and their purpose

**Usage:**
```bash
# Run trace analyzer with solution context
python -m e2e_optimization.analyzer_agent <trace_file> \
  --solution solutions/dabstep_1681.md \
  --eval <eval_file>
```

**Configured in:** `config.yaml` with `solutions_dir: solutions/`

**Files:**
| File | Sample | Key Insight |
|------|--------|-------------|
| dabstep_5.md | Easy issuing country | Simple aggregation |
| dabstep_49.md | Easy fraud country | Rate vs count distinction |
| dabstep_70.md | Easy "Not Applicable" | Missing data handling |
| dabstep_1273.md | Hard avg fee calculation | Multi-step fee formula |
| dabstep_1305.md | Hard specific fee | Constraint matching |
| dabstep_1464.md | Hard 448 fee IDs | Null/empty list semantics |
| dabstep_1681.md | Hard 10 fee IDs | ALL applicable, not lowest |
| dabstep_1753.md | Hard 34 fee IDs | ALL applicable, not lowest |
| dabstep_1871.md | Hard delta calculation | Counterfactual analysis |
| dabstep_2697.md | Hard ACI optimization | Multi-level aggregation |

---

## Agent Architectures Reference

| Agent | Architecture Description | Pass Rate |
|-------|-------------------------|-----------|
| 007 | 8-phase hard decomposition (Pydantic-enforced) | **60%** |
| 000 | Basic CodeActStrategy single agent | 50% |
| 001 | Multi-step workflow with rule finding and validation | 50% |
| 003 | Single agent with 3p data explorer style prompts | 50% |
| 006 | 8-phase soft decomposition (guidance-based) | 50% |
| 002 | 3-subagent: RulesLawyer + DABStepAgent + SolutionVerifier | 30% |
| 005 | 3-subagent + improved prompts from solution analysis | 30% |
| 004 | 3-subagent + regex-based section finding | 20% |

---

## Files Organization

This section documents where all produced files go for reproducibility and organization.

### Directory Structure

```
/Volumes/dev/dev/fix/
├── experiments/evaluation-ablations/
│   ├── agents/                          # Agent implementations
│   │   ├── dabstep_agent000.py          # Basic CodeAct agent
│   │   ├── dabstep_agent001.py          # Multi-step workflow
│   │   ├── dabstep_agent002.py          # 3-subagent architecture
│   │   ├── dabstep_agent003.py          # 3p-style prompts
│   │   ├── dabstep_agent004.py          # 3-subagent + regex
│   │   ├── dabstep_agent005.py          # 3-subagent + improved prompts
│   │   ├── dabstep_agent006.py          # 8-phase soft decomposition
│   │   └── dabstep_agent007.py          # 8-phase hard decomposition
│   │
│   ├── results/                         # Evaluation outputs
│   │   ├── <timestamp>_<model>_<uuid>/  # Per-run directory
│   │   │   ├── *.006eval.jsonl          # Per-task results (streaming)
│   │   │   ├── *.006eval.json           # Aggregated summary
│   │   │   └── traces/                  # OpenTelemetry traces
│   │   │
│   │   └── reliability/                 # Reliability test outputs
│   │       ├── agent000/run_1/ ... run_9/  # Per-run results
│   │       ├── agent001/run_1/ ... run_9/
│   │       └── ...
│   │
│   ├── run_ablation.py                  # Main evaluation script
│   └── run_reliability_test.py          # Multi-run reliability script
│
├── util/e2e_optimization/src/e2e_optimization/examples/dabstep/
│   ├── config.yaml                      # Optimizer configuration
│   ├── train_data.jsonl                 # 10 training samples
│   ├── agent.py                         # Target agent (copy of 000)
│   ├── agent006.py                      # Copy for Pareto frontier
│   ├── agent007.py                      # Copy for Pareto frontier
│   ├── setup_pareto_frontier.py         # Populate frontier script
│   └── merge_agents.py                  # GEPA crossover script
│
└── docs/
    ├── dabstep-pareto-baseline.md       # Baseline results table
    ├── e2e-optimization-process.md      # Mermaid diagrams
    └── scratch/
        └── dabstep-optimization-journal.md  # This file
```

### Output File Types

| File Pattern | Description | Location |
|--------------|-------------|----------|
| `<agent>_<benchmark>.006eval.jsonl` | Streaming per-task results | `results/<run_dir>/` |
| `<agent>_<benchmark>.006eval.json` | Aggregated run summary | `results/<run_dir>/` |
| `<timestamp>.006trace.jsonl` | Session-level trace | `results/<run_dir>/traces/` |
| `<task_id>_<uuid>.006trace.jsonl` | Per-task trace | `results/<run_dir>/traces/` |
| `reliability_report.md` | Multi-run analysis | `results/reliability/` |
| `reliability_results.json` | Raw reliability data | `results/reliability/` |

### File Naming Convention Details

The `.006eval` and `.006trace` suffixes are **format version identifiers** (not related to agent numbers):

**Evaluation files (`.006eval`)**:
```
<agent_name>_<benchmark>.006eval.jsonl   # Streaming results (one JSON object per line)
<agent_name>_<benchmark>.006eval.json    # Aggregated summary (single JSON object)

# Examples:
agent006_dabstep.006eval.jsonl    # Agent 006 results on DABStep
agent000_dabstep.006eval.jsonl    # Agent 000 results on DABStep
```

**Trace files (`.006trace`)**:
```
<timestamp>.006trace.jsonl              # Session-level trace (run start)
<task_id>_<uuid>.006trace.jsonl         # Per-task execution trace

# Examples:
20260121_045033.006trace.jsonl              # Session started at 04:50:33
dabstep_1273_hard_0cbacf3a.006trace.jsonl   # Task trace with unique ID
dabstep_5_easy_7b2c4d1e.006trace.jsonl      # Another task trace
```

**Structure inside a result directory:**
```
results/20260121_045031/
├── agent006_dabstep.006eval.json        # Summary: pass rate, duration, etc.
├── agent006_dabstep.006eval.jsonl       # Per-task: 90 result objects (9 runs × 10 tests)
└── traces/
    ├── 20260121_045033.006trace.jsonl   # Session trace
    ├── dabstep_5_easy_7b2c4d1e.006trace.jsonl
    ├── dabstep_5_easy_8a3f5c2d.006trace.jsonl   # Multiple runs of same task
    ├── dabstep_1273_hard_0cbacf3a.006trace.jsonl
    └── ... (one trace per task execution)
```

### run_ablation.py Outputs

Each run creates a timestamped directory. **IMPORTANT:** The directory name does NOT include the agent - you must check the metadata inside the file to identify which agent was used.

```
results/20260121_045031/                      # Timestamped run directory
├── agent006_dabstep.006eval.jsonl            # Streaming results (all runs in one file)
├── agent006_dabstep.006eval.json             # Aggregated summary (pass rate, duration)
└── traces/
    ├── 20260121_045033.006trace.jsonl        # Session-level trace
    ├── dabstep_5_easy_7b2c4d1e.006trace.jsonl
    ├── dabstep_5_easy_8a3f5c2d.006trace.jsonl    # 9 runs = 9 traces per task
    ├── dabstep_1273_hard_0cbacf3a.006trace.jsonl
    └── ... (one .006trace.jsonl per task execution)
```

**Identifying which agent produced a result:**
```bash
# Check the config_file in metadata (first line of jsonl)
head -1 results/20260121_045031/agent006_dabstep.006eval.jsonl | jq '.metadata.config_file'
# Returns: "agents/dabstep_agent006.py"

# Bulk check all directories:
for dir in results/2026*/; do
  file=$(ls "$dir"*.006eval.jsonl 2>/dev/null | head -1)
  [ -n "$file" ] && echo "$dir → $(head -1 "$file" | jq -r '.metadata.config_file')"
done
```

### Multi-Run Results (--runs N)

When using `--runs 9`, all 90 results (10 tests × 9 runs) go into a **single file** in one timestamped directory. Results are NOT organized by run number.

**Current behavior (scattered):**
```
results/
├── 20260120_204643/   # agent000 × 9 runs (90 results)
├── 20260120_211059/   # agent001 × 9 runs (90 results)
├── 20260120_221055/   # agent002 × 9 runs (90 results)
└── ...                # One directory per agent
```

**RECOMMENDED: Use descriptive output directories**

Future improvement: Add `--output-dir` flag to run_ablation.py to specify destination:
```bash
# Proposed usage (not yet implemented):
python run_ablation.py --benchmark dabstep --agent-file agents/dabstep_agent006.py \
  --runs 9 --output-dir results/reliability/agent006/

# For now, manually move after running:
mkdir -p results/reliability/agent006
mv results/20260121_045031 results/reliability/agent006/9runs_20260121
```

### Reliability Test Wrapper (run_reliability_test.py)

The wrapper script was designed to organize results by agent, but using `--runs` directly on run_ablation.py is simpler. The wrapper creates:
```
results/reliability/
├── agent000/
│   └── agent000_run01_20260120.006eval.jsonl
│   └── agent000_run02_20260120.006eval.jsonl
├── agent001/
│   └── ...
├── reliability_report.md           # Summary with stable/unstable
└── reliability_results.json        # Raw JSON data
```

### E2E Optimizer Outputs

The optimizer writes to:
```
util/e2e_optimization/results/dabstep/
├── generation_0/                   # Initial population
│   ├── strategy_001.py             # Generated variants
│   ├── strategy_002.py
│   └── scores.json                 # Evaluation scores
├── generation_1/                   # Mutated variants
│   └── ...
├── pareto_frontier.json            # Current Pareto frontier
└── optimization_log.jsonl          # Full optimization history
```

### Naming Conventions

- **Timestamps:** `YYYYMMDD_HHMMSS` (e.g., `20260120_143052`)
- **Run directories:** `<timestamp>_<model>_<uuid>` for uniqueness
- **Task IDs:** `<benchmark>_<id>_<difficulty>` (e.g., `dabstep_1273_hard`)
- **Multi-run tasks:** `<task_id>:run<N>` (e.g., `dabstep_5_easy:run1`)
- **Agent files:** `dabstep_agent<NNN>.py` (3-digit zero-padded)

### Current 9-Run Reliability Results (2026-01-21)

Quick reference for the 9-run reliability test results:

| Directory | Agent | Results | Status |
|-----------|-------|---------|--------|
| `20260120_204643` | agent000 | 90 | ✓ Complete |
| `20260120_211059` | agent001 | 90 | ✓ Complete |
| `20260120_221055` | agent002 | 90 | ✓ Complete |
| `20260121_010055` | agent003 | 90 | ✓ Complete |
| `20260121_012800` | agent004 | 90 | ✓ Complete |
| `20260121_025643` | agent005 | 90 | ✓ Complete |
| `20260121_045031` | agent006 | 90 | ✓ Complete |
| `20260121_070251` | agent007 | 90 | ✓ Complete |

**Note:** These are in `experiments/evaluation-ablations/results/`

### Cleanup Notes

- **Safe to delete:** `results/` directories (can regenerate)
- **Keep:** Agent files in `agents/`, config files, documentation
- **Traces:** Can grow large; consider archiving after analysis

---

## Trace Analyzer Eval Baseline

**Date:** 2026-01-21
**Eval Run:** trace_analyzer_20260121_162210

### Test Set
- 2 annotated traces:
  1. `JsonQAAgent` - failed-imports, overthinking
  2. `DABStep #49` - wrong-computation (count vs rate)

### Results

| Trace | Model | Passed | Scorers |
|-------|-------|--------|---------|
| JsonQAAgent | claude-sonnet | ✗ | Phase 1 failed |
| JsonQAAgent | nemotron-nano | ✗ | Phase 1 failed |
| DABStep #49 | claude-sonnet | ✓ | 3/4 (75%) |
| DABStep #49 | nemotron-nano | ✗ | Phase 1 failed |

**Overall: 1/4 (25%)**

### Scorer Breakdown (DABStep #49 / claude-sonnet)

| Scorer | Score | Notes |
|--------|-------|-------|
| outcome_match | 1.0 ✓ | Correctly identified FAILURE |
| method_identification | 0.0 ✗ | Found phase 4/5, expected phase_7_compute |
| root_cause_quality | 1.0 ✓ | Identified missing_context/manual.md |
| suggestions_useful | 1.0 ✓ | Good suggestions about reading docs |

### Issues Found

1. **nemotron-nano Phase 1 failures**: Model consistently fails to complete Phase 1 (overview) after 5 iterations. Needs investigation - likely prompt/format issues for smaller models.

2. **Template substitution for failed runs**: When analyzer returns `output=null` (execution error), judge prompts show literal `{output_outcome}` instead of handling the null case gracefully.

3. **Method identification is challenging**: Even with successful analysis, identifying the exact failing session/method is hard. Analyzer found phases 4-5, but human annotated phase_7_compute.

### Next Steps

1. Investigate nemotron-nano Phase 1 failures
2. Add null handling for template substitution when output is null
3. Consider relaxing method_identification scorer to accept phase-level matches
4. Add more annotated traces for better signal
