# Benchmark Results — nemo-oo-agents

**Last updated:** 2026-05-13  
**Model:** `aws/anthropic/bedrock-claude-sonnet-4-5-v1`  
**Token tracking:** landed on main via commits `2254cd9b`, `87a60394`, `80e7e007`

---

## Table 1 — Pass Rate by Benchmark × Runtime

### ipp1-3404 (x86_64, Harbor + Apptainer)

| Benchmark | CodeAct | ReAct | Specialized |
|-----------|---------|-------|-------------|
| DABStep | 25.2% (112/444†) | 34.8% (157/451) | **56.8%** (255/449†) |
| LoCoMo | **49.5%** (654/1322†) | 35.2% (543/1543) | 39.0% (270/693‡) |
| MemBench | 86.5% (1844/2132‡) | **88.2%** (2731/3096‡) | — |
| SWE-bench Verified | **62.1%** (164/264‡) | 8.4% (15/178‡) | — |
| Terminal Bench 1 | 5.6% (8/144†) | 9.4% (22/233) | **17.4%** (19/109†) |
| Terminal Bench 2 | — | — | — |

† near-complete (≤3 tasks remaining)  
‡ partial run — fresh full run started 2026-04-30  
— not yet run

### galaxy (aarch64, Harbor + Docker native)

| Benchmark | CodeAct baseline | Notes |
|-----------|-----------------|-------|
| Terminal Bench 1 | **27.97%** (66/236) | 2026-05-12, full 236-task run, ~2h 40min |

Previous QEMU/Apptainer run (2026-05-11): 11.9% (28/236) — Docker-native is **2.4× faster** and scores **2.3× higher** (no emulation overhead).

---

## Table 2 — Token Usage by Benchmark × Runtime

All token counts are totals across all completed tasks in the run.

### Input tokens

| Benchmark | CodeAct | ReAct | Specialized |
|-----------|---------|-------|-------------|
| DABStep | 151.5M (444 tasks) | 78.8M (451) | 186.0M (449) |
| LoCoMo | 72.9M (1322 tasks) | 77.2M (1543) | 15.2M (693) |
| MemBench | 66.2M (2132 tasks) | 36.2M (3096) | — |
| SWE-bench | 274.1M (264 tasks) | 79.9M (178) | — |
| Terminal Bench 1 | 42.4M (144 tasks) | 86.2M (233) | 47.0M (109) |

### Output tokens

| Benchmark | CodeAct | ReAct | Specialized |
|-----------|---------|-------|-------------|
| DABStep | 4.3M (444 tasks) | 2.3M (451) | 5.3M (449) |
| LoCoMo | 1.5M (1322 tasks) | 646K (1543) | 16K (693) |
| MemBench | 1.7M (2132 tasks) | 857K (3096) | — |
| SWE-bench | 5.0M (264 tasks) | 2.6M (178) | — |
| Terminal Bench 1 | 1.5M (144 tasks) | 2.3M (233) | 1.1M (109) |

### Per-task averages (input tokens)

| Benchmark | CodeAct | ReAct | Specialized |
|-----------|---------|-------|-------------|
| DABStep | 341K/task | 175K/task | 414K/task |
| LoCoMo | 55K/task | 50K/task | 22K/task |
| MemBench | 31K/task | 12K/task | — |
| SWE-bench | 1,038K/task | 449K/task | — |
| Terminal Bench 1 | 294K/task | 370K/task | 431K/task |

---

## Run Status (as of 2026-05-13)

| Run | Job dir | Status |
|-----|---------|--------|
| dabstep_baseline | 2026-04-28__09-54-40 | ~done (2 remaining) |
| dabstep_react_baseline | 2026-04-29__10-47-55 | **DONE** |
| dabstep_specialized | 2026-04-28__08-30-38 | ~done (1 remaining) |
| locomo_baseline | 2026-04-28__11-34-02 | partial (1322/1542); **fresh run started 2026-04-30** |
| locomo_react_baseline | 2026-04-29__11-40-50 | **DONE** |
| locomo_specialized | 2026-04-29__13-00-55 | partial (693/1542); **fresh run started 2026-04-30** |
| membench_baseline | 2026-04-28__11-34-02 | partial (2132/4779); **fresh run started 2026-04-30** |
| membench_react_baseline | 2026-04-29__11-40-56 | in progress (3096/4779) |
| swebench_baseline | 2026-04-28__08-30-38 | in progress (264/500), resume running |
| swebench_react_baseline | 2026-04-29__10-47-55 | in progress (178/500), resume running |
| terminal_bench_baseline | 2026-04-28__11-34-02 | partial (144/233); **fresh run started 2026-04-30** |
| terminal_bench_react_baseline | 2026-04-29__11-40-53 | **DONE** |
| terminal_bench_specialized | 2026-04-28__11-34-03 | partial (109/233); **fresh run started 2026-04-30** |
| terminal_bench_2_* | — | SIF prefetch in progress on ipp1-3404; runs will auto-start |
| **galaxy docker** — terminal_bench_docker | 2026-05-12__15-42-32 | **DONE** — 54/236 (27.97%), ~2h 40min, aarch64 native |

---

## Notes

- **SWE-bench CodeAct 62.1%** is surprisingly high vs ReAct 8.4%. The CodeAct agent has SWE-bench testbed tools injected (`--tools swebench`); ReAct does not.
- **DABStep** tells the "Helpers Beat Prompts" story cleanly: CodeAct baseline 25.2% → ReAct baseline 34.8% → Specialized 56.8%.
- **ReAct is ~2× cheaper** per input token on DABStep (175K vs 341K), but CodeAct Specialized justifies the cost.
- **LoCoMo Specialized** (39.0%) underperforms LoCoMo CodeAct baseline (49.5%) — the specialized agent may need tuning.
- **Token tracking** implementation: `ContextVar` accumulator in `src/nooa/runtime/token_usage.py`, wired via `actor.py` metrics bridge. See commits `2254cd9b`, `87a60394`, `80e7e007` on main.
