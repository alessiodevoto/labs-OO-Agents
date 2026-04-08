# MiniMax M2.5 Slurm SFT Data Generation

## Overview

Generate NeMo RL SFT training data from DABStep evaluations using **MiniMax M2.5** as
the LLM backend, self-hosted on CW Slurm via vLLM.

Two agent varieties → two JSONL files:
- `dabstep_sft_nemo_oo_agents_minimax_m2.5_YYYYMMDD.jsonl` — opt63 (nemo_oo_agents variety)
- `dabstep_sft_agent009_minimax_m2.5_YYYYMMDD.jsonl` — agent009 (skill-accumulating solver)

---

## Step 1 — Deploy MiniMax on Slurm

See `util/slurm/README.md` for full details.

```bash
# From the login node:
sbatch util/slurm/host_minimax.sbatch

# Monitor the job:
squeue -j <JOBID> -o "%.18i %.2t %R"

# Tail logs:
tail -f minimax-m25-vllm-<JOBID>.out
```

The sbatch script:
- Account: `llmservice_nemo_reasoning`
- Partition: `batch_short` (2h max)
- Checkpoint: `/lustre/fs1/portfolios/coreai/projects/coreai_dlalgo_ci/artifacts/model/minimaxai_minimax-m2_5/safetensors_mode-instruct/hf-3040beaf-nim`
- Served model name: `MiniMaxAI/MiniMax-M2.5`
- Port: 8000, TP=4

---

## Step 2 — Open SSH Tunnel

Find the node running the job from `squeue` output (e.g. `pool0-01767`), then:

```bash
# From your laptop / evaluation machine:
ssh -N -L 127.0.0.1:18000:pool0-01767:8000 rcabral@cw-dfw-cs-001-login-02
```

Verify:
```bash
curl http://127.0.0.1:18000/v1/models
# Should return: {"data": [{"id": "MiniMaxAI/MiniMax-M2.5", ...}]}
```

---

## Step 3 — Run Evaluations

Both scripts verify the endpoint before starting and write traces for SFT conversion.

### opt63 (nemo_oo_agents variety)

```bash
cd experiments/evaluation-ablations
source ../../.venv/bin/activate
bash run_minimax_opt63.sh
```

Results: `experiments/evaluation-ablations/results/dabstep/minimax_m2.5_opt63_YYYYMMDD/`

### agent009 (skill-accumulating solver)

```bash
cd experiments/evaluation-ablations
source ../../.venv/bin/activate
bash run_minimax_agent009.sh
```

Results: `experiments/evaluation-ablations/results/dabstep/minimax_m2.5_agent009_YYYYMMDD/`

**Tip:** Run both in parallel in separate terminals (each uses 4 concurrent tasks, 8 concurrent LLM calls).

---

## Step 4 — Monitor Progress

```bash
# opt63 progress
python3 -c "
import json
f='experiments/evaluation-ablations/results/dabstep/minimax_m2.5_opt63_$(date +%Y%m%d)/nemo_oo_agents_dabstep_full.noo-eval.jsonl'
results = [json.loads(l) for l in open(f) if json.loads(l).get('_type')=='result']
print(f'opt63 progress: {len(results)}/450')
"

# agent009 progress
python3 -c "
import json
f='experiments/evaluation-ablations/results/dabstep/minimax_m2.5_agent009_$(date +%Y%m%d)/nemo_oo_agents_dabstep_full.noo-eval.jsonl'
results = [json.loads(l) for l in open(f) if json.loads(l).get('_type')=='result']
print(f'agent009 progress: {len(results)}/450')
"
```

---

## Step 5 — Generate SFT JSONL

After runs complete (or with partial results):

```bash
cd experiments/evaluation-ablations

# opt63 SFT data
bash generate_sft_minimax_opt63.sh
# Output: sft_data/dabstep_agent009/dabstep_sft_nemo_oo_agents_minimax_m2.5_YYYYMMDD.jsonl(.gz)

# agent009 SFT data
bash generate_sft_minimax_agent009.sh
# Output: sft_data/dabstep_agent009/dabstep_sft_agent009_minimax_m2.5_YYYYMMDD.jsonl(.gz)
```

---

## Step 6 — Commit to Branch

```bash
git add sft_data/dabstep_agent009/dabstep_sft_*minimax*.jsonl.gz
git commit -m "feat(sft-data): add DABStep MiniMax M2.5 SFT datasets (opt63 + agent009)"
```

---

## Notes

- **Endpoint env var**: Override with `SLURM_ENDPOINT_URL=http://127.0.0.1:18000/v1`
- **Job time limit**: `batch_short` partition → 2h max. Resubmit with `--resume` if needed.
- **Concurrent settings**: 4 tasks × 8 LLM calls = 32 in-flight requests. Reduce if the
  single-node vLLM instance shows high latency (`--concurrent-tasks 2 --concurrent-llm 4`).
- **Model name**: The sbatch serves it as `MiniMaxAI/MiniMax-M2.5`. The run scripts match this.
- **Tool call support**: The sbatch enables `--enable-auto-tool-choice --tool-call-parser minimax_m2`
  which is required for the agentic opt63/agent009 workflows.
