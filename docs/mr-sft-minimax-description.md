# MR: feat(sft-data): MiniMax M2.5 self-hosted Slurm SFT datasets

## What this MR does

Adds MiniMax M2.5 (self-hosted via Slurm + vLLM) as an inference backend for SFT data generation,
matching what was already done for nemotron/deepseek/sonnet45 backends.

## Commits

| Commit | Description |
|--------|-------------|
| `494c1b6` | Merge SFT tooling (`feat/sft-data-generation`) + slurm deployment files (`docs/slurm-self-hosting-instructions`) into this branch |
| `d66c232` | Add `slurm` provider to `run_ablation.py`; add run scripts (`run_minimax_opt63.sh`, `run_minimax_agent009.sh`) and SFT generate scripts |
| `230d796` | Fix: login node (`cw-dfw-cs-001-login-02`) has direct network access to `pool0:8000` — no SSH `-L` port forwarding needed from the login node |
| `fa471ab` | Fix: switch sbatch to `batch_large_long` partition with 8h walltime (2h `batch_short` was too short for 450 tasks) |
| `a0e9603` | Add MiniMax M2.5 SFT datasets (opt63 + agent009) and update `sft_data/README.md` |

## Benchmark Evaluation Results

Both agent varieties were evaluated against the **DABStep full split (450 tasks)** using MiniMax M2.5
served on a Slurm `pool0` node (4×H100, TP=4) via `vllm/vllm-openai:v0.15.1`.

The 8h Slurm job (`batch_large_long`, job `9781725`, node `pool0-00218`) completed before all 450
tasks could be evaluated. Valid results are from tasks completed within the job window only.

| Agent | Tasks Evaluated | Exact-Match Pass Rate | SFT Examples Generated |
|-------|----------------|-----------------------|------------------------|
| **opt63** (`rsc_dab_hard_opt63`) | 63 / 450 | 22 / 63 = **34.9%** | 22 |
| **agent009** (`dabstep_agent009.py`) | 205 / 450 | 49 / 205 = **23.9%** | 49 |

> **Note on harness reporting**: The eval output files show 450/450 tasks "complete" with `passed=True`
> for all. This is a harness bug: after the Slurm job expired, the evaluation process continued
> running against a dead endpoint, producing empty responses that were marked as passed. The SFT
> datasets are clean — `build_sft_dataset.py` requires valid OTel trace files, which only exist for
> tasks that ran during the valid window.

## New Files

```
util/slurm/
  host_minimax.sbatch                          # Submit MiniMax M2.5 vLLM job on batch_large_long (8h, 4×H100)
  README.md                                    # Deployment docs: login-node direct access + laptop tunnel

experiments/evaluation-ablations/
  run_minimax_opt63.sh                         # Auto-submit sbatch, wait for vLLM, run opt63 eval
  run_minimax_agent009.sh                      # Same for agent009
  generate_sft_minimax_opt63.sh               # Build SFT JSONL from opt63 eval results
  generate_sft_minimax_agent009.sh            # Same for agent009

sft_data/dabstep_agent009/
  dabstep_sft_nooa_minimax_m2.5_20260303.jsonl.gz   # 22 SFT examples, opt63 agent
  dabstep_sft_agent009_minimax_m2.5_20260303.jsonl.gz   # 49 SFT examples, agent009

docs/minimax-slurm-sft-plan.md                # End-to-end guide for reproducing
```

## Key implementation notes

- **`slurm` provider** in `run_ablation.py`: reads `SLURM_ENDPOINT_URL` env var (set automatically
  by the run scripts), routes via litellm `openai/` prefix
- **Direct endpoint access**: Login node has network visibility to `pool0` nodes on port 8000.
  Run scripts auto-discover the compute node with `squeue -j <JOBID> -h -o "%N"` and set
  `SLURM_ENDPOINT_URL=http://<node>:8000/v1` — no SSH tunnel needed from the login node
- **SFT generation**: exact-match against `dabstep-full-solutions.jsonl` (stricter than harness
  LLM-based eval), yielding 35% (opt63) and 24% (agent009) pass rates
