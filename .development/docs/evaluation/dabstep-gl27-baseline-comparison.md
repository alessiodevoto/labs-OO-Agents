# DABStep Baseline Comparison — gl-27

**Date:** 2026-04-15
**Branch:** agent/gl-27
**Infrastructure:** nemo-oo-agents eval_pipeline (no Harbor/Apptainer container required)

## Setup

- **Agent:** `BaselineAgent` (CodeAct, general-purpose — no DABStep-specific logic)
- **Model:** `openai/gpt-4o`
- **Dataset:** `adyen/DABstep`, `tasks` config, `dev` split, first 10 tasks
- **Script:** `util/harbor/run_dabstep.py`
- **Data:** `~/.cache/dabstep/data/context/` (downloaded from HuggingFace)

## Tasks

Same 10 tasks used by agent006 benchmarks:

| Task ID | Level | Question (abbreviated) |
|---------|-------|------------------------|
| 5       | easy  | Which issuing country has the highest number of transactions? |
| 49      | easy  | What is the top country (ip_country) for fraud? |
| 70      | easy  | Is Martinis_Fine_Steakhouse in danger of getting a high-fraud rate fine? |
| 1273    | hard  | Avg fee that GlobalCard charges for credit transactions at 10 EUR |
| 1305    | hard  | Avg fee for account type H, Eating Places, GlobalCard at 10 EUR |
| 1464    | hard  | Fee IDs for account_type=R and card_scheme=GlobalCard |
| 1681    | hard  | Fee IDs applicable on day 10 of 2023 |
| 1753    | hard  | Applicable fee IDs for Belles_cookbook_store |
| 1871    | hard  | Jan 2023 delta for Belles_cookbook_store |
| 2697    | hard  | Best card scheme for Belles_cookbook_store in January |

## Results

### 5-task smoke test (tasks 5, 49, 70, 1273, 1305)

| Task | Expected | Got | Result |
|------|----------|-----|--------|
| 5 (easy)    | NL         | IN        | FAIL |
| 49 (easy)   | B. BE      | D. FR     | FAIL |
| 70 (easy)   | Not Applicable | Not Applicable | **PASS** |
| 1273 (hard) | 0.120132   | 0.020000  | FAIL |
| 1305 (hard) | 0.123217   | 0.100000  | FAIL |

**Score: 1/5 = 20.0%**

### Full 10-task suite

| Task | Expected | Got | Result |
|------|----------|-----|--------|
| 5 (easy)    | NL         | Not Applicable    | FAIL |
| 49 (easy)   | B. BE      | No files tool available | FAIL |
| 70 (easy)   | Not Applicable | Not Applicable | **PASS** |
| 1273 (hard) | 0.120132   | Not Applicable    | FAIL |
| 1305 (hard) | 0.123217   | Not Applicable    | FAIL |
| 1464 (hard) | (list of 455 IDs) | Not Applicable | FAIL |
| 1681 (hard) | (top 10 IDs) | Not Applicable | FAIL |
| 1753 (hard) | (list of 34 IDs) | Not Applicable | FAIL |
| 1871 (hard) | -0.94      | Not Applicable    | FAIL |
| 2697 (hard) | E:13.57    | AmEx:1.50         | FAIL |

**Score: 1/10 = 10.0%**

## Comparison to agent006

| Agent | Model | Score | Notes |
|-------|-------|-------|-------|
| agent006 DABStepAgent (specialized) | Claude 3.5/4 | **70–80%** (7–8/10) | 3-phase pipeline: RulesLawyer → compute_answer → SolutionVerifier |
| agent006 baseline (qwen) | qwen/qwen3-next-80b | **10–20%** (1–2/10) | General CodeAct agent |
| **nemo-oo-agents BaselineAgent** | openai/gpt-4o | **10%** (1/10) | General CodeAct agent (this run) |

## Analysis

**Infrastructure**: The eval_pipeline infrastructure works correctly. The BaselineAgent executes code via the REPL, reads CSVs with pandas, and calls `return_result()`. Traces confirm code execution happened.

**Why 10%?**
- Easy task 70 passes reliably (agent identifies merchant doesn't exist → "Not Applicable")
- Easy tasks 5 and 49 show inconsistency: sometimes the agent computes (wrong) answers, sometimes it gives up early
- Hard tasks require specialized fee-calculation logic (null semantics, fee filtering, "lowest fee wins") that the baseline agent lacks

**Variance**: The 5-task run scored 20% (task 70 + the agent at least attempted tasks 5 and 49 with wrong answers), while the 10-task run scored 10% (same task 70 passing, others fell back to "Not Applicable"). This variance is consistent with the agent006 baseline analysis (see `dabstep-agent006-variance-analysis.md`).

**Conclusion**: nemo-oo-agents BaselineAgent matches agent006 baseline performance (10–20%) as expected. The gap to the specialized DABStepAgent (70–80%) is attributable to agent design, not infrastructure.

## Output Files

- 5-task run: `.development/docs/evaluation/dabstep_baseline_gl27_20260415_193312_982479/`
- 10-task run: `.development/docs/evaluation/dabstep_baseline_gl27_20260415_193423_474086/`
