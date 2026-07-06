#!/bin/bash
#SBATCH --job-name=nemotron-ultra-vllm
#SBATCH --account=nemotron_agents_dev
#SBATCH --partition=batch
#SBATCH --nodes=2
#SBATCH --gres=gpu:8
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --output=nemotron-ultra-vllm-%j.out
#SBATCH --error=nemotron-ultra-vllm-%j.out

set -euo pipefail

# ---- Nemotron-3-Ultra-550B-A55B vLLM (2 nodes x 8 H100-80G = 16 GPU, TP8 x PP2 over Ray) ----
# 550B BF16 (~1.1TB) -> ~69GB weights/GPU at 1/16. KV/workspace must fit the remaining ~10GB:
# small max-model-len + fp8 KV + enforce-eager + small batch.
MODEL_CKPT=/lustre/fs1/portfolios/coreai/projects/coreai_dlalgo_ci/artifacts/model/nemotron-3-ultra/hf/rl-052726-590d0da
SERVED_NAME=nvidia/nemotron-3-ultra
PORT=8000
CONTAINER_IMAGE="vllm/vllm-openai:v0.22.0"
RAY_PORT=6379

export ENROOT_CACHE_PATH=/tmp/$USER/enroot/cache
export ENROOT_DATA_PATH=/tmp/$USER/enroot/data
export ENROOT_TEMP_PATH=/tmp/$USER/enroot/tmp
mkdir -p "$ENROOT_CACHE_PATH" "$ENROOT_DATA_PATH" "$ENROOT_TEMP_PATH"

NODES=( $(scontrol show hostnames "$SLURM_JOB_NODELIST") )
HEAD_NODE="${NODES[0]}"
WORKER_NODE="${NODES[1]}"
HEAD_IP=$(getent hosts "$HEAD_NODE" | awk '{print $1}' | head -1)

echo "=== Nemotron-3-Ultra vLLM (2-node Ray TP8 x PP2, host-net pyxis/enroot) ==="
echo "Head: $HEAD_NODE ($HEAD_IP)  Worker: $WORKER_NODE  Port: $PORT"
echo "Tunnel: ssh -N -L 127.0.0.1:18000:${HEAD_NODE}:${PORT} rcabral@cw-dfw-cs-001-login-02"
echo "=================================================================="

# Shared bringup script run in-container on BOTH nodes (same --container-name -> reused on each node),
# with HOST networking so the worker can reach the head's Ray GCS across nodes.
read -r -d '' BRINGUP <<'EOS' || true
set -e
export PATH=/usr/local/bin:$PATH PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export VLLM_WORKER_MULTIPROC_METHOD=spawn SAFETENSORS_FAST_GPU=1 NVIDIA_TF32_OVERRIDE=1 VLLM_LOGGING_LEVEL=INFO
pip install -q 'ray[default]' 2>&1 | tail -1
MYIP=$(hostname -i | awk '{print $1}')
export VLLM_HOST_IP=$MYIP
if [ "$(hostname -s)" = "$HEAD_HOSTNAME" ]; then
  ray start --head --node-ip-address="$HEAD_IP" --port=__RAY_PORT__ --num-gpus=8 --disable-usage-stats
  for i in $(seq 1 60); do
    G=$(ray status 2>/dev/null | grep -oE '[0-9]+\.0/[0-9]+\.0 GPU' | head -1 | sed 's#.*/##; s#\.0 GPU##')
    echo "ray GPUs: ${G:-0}"; [ "${G:-0}" = "16" ] && break; sleep 5
  done
  exec vllm serve /model --host 0.0.0.0 --port __PORT__ --served-model-name __SERVED__ \
    --trust-remote-code --tensor-parallel-size 8 --pipeline-parallel-size 2 --distributed-executor-backend ray \
    --dtype bfloat16 --max-model-len 32768 --gpu-memory-utilization 0.90 \
    --max-num-seqs 8 --max-num-batched-tokens 8192 --enforce-eager --enable-chunked-prefill \
    --reasoning-parser nemotron_v3 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
    --mamba-ssm-cache-dtype float16 --kv-cache-dtype fp8 \
    --model-loader-extra-config '{"enable_multithread_load": true, "num_threads": 96}' \
    --distributed-timeout-seconds 3600
else
  sleep 25
  ray start --address="$HEAD_IP:__RAY_PORT__" --num-gpus=8 --disable-usage-stats --block
fi
EOS
BRINGUP="${BRINGUP//__RAY_PORT__/$RAY_PORT}"
BRINGUP="${BRINGUP//__PORT__/$PORT}"
BRINGUP="${BRINGUP//__SERVED__/$SERVED_NAME}"

export HEAD_HOSTNAME=$(echo "$HEAD_NODE" | cut -d. -f1)

# ONE srun across BOTH nodes; host networking so Ray GCS is reachable cross-node.
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 \
  --container-image="docker://${CONTAINER_IMAGE}" \
  --container-mounts="${MODEL_CKPT}:/model:ro" \
  --container-remap-root \
  --no-container-mount-home \
  --export=ALL,HEAD_IP="$HEAD_IP",HEAD_HOSTNAME="$HEAD_HOSTNAME" \
  bash -c "$BRINGUP"
