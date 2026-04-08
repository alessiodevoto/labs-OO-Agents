# DABStep Self-Optimization - Session Handoff

## Task

Complete the DABStep self-optimization TODO at `experiments/evaluation-ablations/TODO dabstep self optimize.md`.

### Phase I: Optimize on 10 Training Tasks (Target: 9/10)
1. Re-enable seed strategies in config (they were disabled due to VPN/network issues - should be fine now)
2. Run the optimization loop with tournament selection
3. Target: 9/10 on 10 training tasks, or complete 10 improvement iterations
4. Mark tasks with 'x' in the .md file and commit changes every iteration

### Phase II: Evaluate on Full 450 Tasks
- Evaluate best agent on full 450 tasks
- Target: Match or surpass opt63's 55.6% (250/450)

## Run Command

```bash
cd util/e2e_optimization
python -m e2e_optimization run --config src/e2e_optimization/examples/dabstep/config.yaml
```

## Key Files

| File | Purpose |
|------|---------|
| `experiments/evaluation-ablations/TODO dabstep self optimize.md` | Master TODO with full context |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/config.yaml` | Optimization config |
| `util/e2e_optimization/src/e2e_optimization/optimizer.py` | Core optimizer (fixed seed eval API) |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/agents/agent.py` | Target file being optimized (simple CodeAct, 50%) |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/agents/agent006.py` | Seed: 8-phase soft decomposition (50%) |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/agents/agent007.py` | Seed: 8-phase hard decomposition (60%) |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/agents/agent_opt63.py` | Seed: Best manual opt (90% train, 55.6% full) |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/solutions/` | Ground truth solutions for 10 training tasks |
| `util/e2e_optimization/src/e2e_optimization/examples/dabstep/solutions/README.md` | Key insights from manual optimization |

## What Was Done (Previous Sessions)

### Code Fixes Applied
1. **TraceExplorer import** - Fixed `analyzer_agent.py` to not use non-existent `from_file_with_eval`
2. **Seed strategy eval API** - Fixed `_evaluate_seed_strategies()` in `optimizer.py` (line ~784) to use `Agent006Evaluator` instead of non-existent `run_eval` from `eval_pipeline`
3. **Seed strategies** - Currently commented out in config.yaml due to network issues. **Re-enable them before running.**

### Optimization Runs (3 attempts, all crashed due to VPN/network)
- Baseline consistently achieves 5/9 (55.6%) with simple agent.py
- Passing: dabstep_5_easy, dabstep_49_easy, dabstep_1273_hard, dabstep_1305_hard, dabstep_1464_hard
- Failing: dabstep_70_easy, dabstep_1753_hard, dabstep_1871_hard, dabstep_2697_hard
- Seed strategies evaluated as 0% (all failed due to network errors, NOT real results)
- Diagnostics phase works (trace analysis runs Phase 1 & 2) but only ~1/5 samples fully analyzed
- Process crashes in `reflect()` phase (litellm.acompletion connection error) - no retry logic for network errors

### Known Issues
1. **`reflect()` has no retry on connection errors** - Only catches `ImportError`, not `InternalServerError`. Located at `optimizer.py:1391`. Adding retry with backoff would make the loop resilient to transient network issues.
2. **Seed strategy evaluations are slow** - 3 seeds × 10 tasks = 30 extra evaluations before optimization starts. If network is unstable, these all fail.
3. **Trace analysis sometimes fails** - "Diagnostics complete: 1/5 samples analyzed" - traces may be too large or analysis times out.

## End-to-End Optimization Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    e2e_optimization Loop                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. INITIALIZE                                              │
│     ├── Load config.yaml                                    │
│     ├── Copy agent.py to iteration_000/agents/              │
│     └── If seed_strategies defined:                         │
│         └── Evaluate each seed (agent006, agent007, opt63)  │
│             on all 10 training tasks → add to Pareto front  │
│                                                             │
│  2. EVALUATE BASELINE (iteration 1)                         │
│     ├── Run agent.py on 10 training tasks (1 run each)      │
│     ├── Score with ExactMatchScorer                         │
│     └── Record: 5/9 passed (55.6%)                          │
│                                                             │
│  3. ANALYZE                                                 │
│     ├── Select 5 samples: 4 failed + 1 passing (regression) │
│     ├── Run mechanical checks (automated pattern detection)  │
│     └── Run TraceAnalyzerAgent on each failed sample:       │
│         ├── Phase 1: Overview (read trace, summarize)       │
│         ├── Phase 2: Generation Analysis (deep dive)        │
│         └── Produce diagnostic_report per sample            │
│                                                             │
│  4. REFLECT (LLM call)                                      │
│     ├── Build prompt with:                                  │
│     │   ├── Current agent.py source                         │
│     │   ├── Failed test inputs/outputs/expected             │
│     │   ├── Diagnostic reports from trace analysis          │
│     │   ├── Mechanical check findings                       │
│     │   └── Solution hints from solutions/ directory        │
│     ├── Call Claude (reflect_model) to propose improved code │
│     └── Save reflection_response.md                         │
│                                                             │
│  5. APPLY & EVALUATE PROPOSAL                               │
│     ├── Extract new agent.py from LLM response              │
│     ├── Run proposed agent on sampled subset (7 of 10)      │
│     └── Compare vs parent on same subset                    │
│                                                             │
│  6. ACCEPT/REJECT                                           │
│     ├── If proposed > parent: accept, add to Pareto frontier │
│     ├── If proposed <= parent: reject                       │
│     └── Tournament selection: compete against Pareto front  │
│                                                             │
│  7. LOOP                                                    │
│     ├── If 9/10 reached → DONE (target met)                 │
│     ├── If 10 iterations → DONE (max reached)               │
│     ├── If 3 consecutive rejections → CONVERGED (early stop) │
│     └── Otherwise → go to step 2 with best agent            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## First Actions for New Session

1. **Re-enable seed strategies** in `config.yaml` (uncomment lines 82-96)
2. **Consider adding retry logic** to `reflect()` in `optimizer.py:1377-1393` for network resilience
3. **Run the optimization**: `cd util/e2e_optimization && python -m e2e_optimization run --config src/e2e_optimization/examples/dabstep/config.yaml`
4. **Monitor progress** - baseline eval takes ~15min, seed eval ~45min, each iteration ~20-30min
5. After optimization completes, update `TODO dabstep self optimize.md` and commit
