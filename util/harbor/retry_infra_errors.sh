#!/bin/bash
# Retry infra-failed trials (AgentSetupTimeoutError + EnvironmentStartTimeoutError)
# Run this AFTER all active orchestrators have exited.
# Usage: bash retry_infra_errors.sh [benchmark1 benchmark2 ...]

set -euo pipefail

JOBS_DIR=/raid/rcabral/home/harbor_jobs
AGENT_SETUP="AgentSetupTimeoutError"
ENV_START="EnvironmentStartTimeoutError"
CANCELLED="CancelledError"

BENCHMARKS=(
    dabstep_baseline
    dabstep_specialized
    locomo_baseline
    locomo_react_baseline
    locomo_specialized
    membench_baseline
    membench_react_baseline
    swebench_baseline
    swebench_react_baseline
    terminal_bench_baseline
    terminal_bench_react_baseline
    terminal_bench_specialized
)

# If args specified, use those instead
if [ $# -gt 0 ]; then
    BENCHMARKS=("$@")
fi

cd /raid/rcabral/home/nemo_oo_agents

for BENCH in "${BENCHMARKS[@]}"; do
    JOB_DIR="${JOBS_DIR}/${BENCH}"
    if [ ! -d "$JOB_DIR" ]; then
        echo "SKIP: $BENCH (no dir)"
        continue
    fi

    # Get latest run dir
    LATEST=$(ls -1t "$JOB_DIR" | head -1)
    RUN_DIR="${JOB_DIR}/${LATEST}"
    if [ ! -f "${RUN_DIR}/config.json" ]; then
        echo "SKIP: $BENCH (no config.json in $LATEST)"
        continue
    fi

    echo "Retrying $BENCH ($LATEST) ..."
    LOG="${JOB_DIR}/retry_infra_$(date +%Y%m%d_%H%M%S).log"
    nohup uv run harbor job resume \
        --job-path "$RUN_DIR" \
        --filter-error-type "$AGENT_SETUP" \
        --filter-error-type "$ENV_START" \
        --filter-error-type "$CANCELLED" \
        > "$LOG" 2>&1 &
    echo "  PID $! → $LOG"
    sleep 2  # stagger starts to avoid disk contention
done

echo "All retries launched."
