# Can We Reuse NeMo Skills' Slurm Backend?

## TL;DR

**YES - we could reuse it, and it would be VERY valuable for large-scale evaluation!**

**Key finding**: They use `nemo_run`, an NVIDIA library that abstracts execution (local, Docker, Slurm). We could adopt this for distributed agent evaluation.

---

## What is nemo_run?

**NVIDIA's distributed execution library**: https://github.com/NVIDIA/NeMo-Run

Provides unified API for:
- **Local execution** - Run directly
- **Docker execution** - Run in container locally
- **Slurm execution** - Submit to cluster
- **SSH tunnel** - Submit from laptop to remote cluster

**Key abstraction**:
```python
import nemo_run as run

# Create experiment
with run.Experiment("my_eval") as exp:
    # Add tasks
    exp.add(
        run.Script(command="python evaluate.py --task 1"),
        executor=slurm_executor,  # or local_executor, or docker_executor
    )

    exp.add(
        run.Script(command="python evaluate.py --task 2"),
        executor=slurm_executor,
    )

# Run (automatically distributes based on executor)
exp.run()
```

**Seamless transition**: Change executor, same code runs local → Docker → Slurm.

---

## NeMo Skills' Slurm Architecture

### 1. Configuration System

**Cluster config** (`cluster_configs/*.yaml`):

```yaml
# Example: cluster_configs/my_cluster.yaml
executor: slurm                    # or "local" or "none"
account: my_account
partition: gpu_partition
cpu_partition: cpu_partition
job_dir: /remote/path/to/jobs      # Where to store job files

# Environment variables to pass
required_env_vars:
  - OPENAI_API_KEY
  - NVIDIA_API_KEY
env_vars:
  - WANDB_API_KEY
  - HF_TOKEN

# SSH tunnel (optional - submit from laptop)
ssh_tunnel:
  host: cluster.nvidia.com
  user: $USER
  identity: ~/.ssh/id_rsa
  job_dir: /home/$USER/nemo_jobs

# Container images
containers:
  vllm: nvcr.io/nvidia/vllm:latest
  default: nvcr.io/nvidia/pytorch:24.01

# Timeouts per partition
timeouts:
  gpu_partition: "4-00:00:00"      # 4 days
  cpu_partition: "1-00:00:00"      # 1 day
```

### 2. Executor Factory

**`get_executor()` function** creates appropriate executor:

```python
def get_executor(
    cluster_config,
    container,
    num_nodes=1,
    tasks_per_node=1,
    gpus_per_node=0,
    job_name="my_job",
    log_dir="logs/",
    partition=None,
    dependencies=None,          # Wait for these jobs
    sbatch_kwargs=None,
):
    if cluster_config["executor"] == "none":
        return LocalExecutor()

    elif cluster_config["executor"] == "local":
        return DockerExecutor(
            container_image=container,
            num_gpus=-1,  # All GPUs
            network="host",
            volumes=mounts,
            env_vars=env_vars,
        )

    elif cluster_config["executor"] == "slurm":
        return SlurmExecutor(
            account=cluster_config["account"],
            partition=partition or cluster_config["partition"],
            nodes=num_nodes,
            ntasks_per_node=tasks_per_node,
            gpus_per_node=gpus_per_node,
            time=timeout,
            tunnel=get_tunnel(cluster_config),  # SSH tunnel!
            container_image=container,
            container_mounts=mounts,
            dependencies=dependencies,         # Job dependencies!
            env_vars=env_vars,
            packager=packager,                 # Code packaging!
        )
```

### 3. SSH Tunnel Support

**Submit from laptop to remote cluster**:

```python
# SSH tunnel config
tunnel = run.SSHTunnel(
    host="cluster.nvidia.com",
    user="username",
    identity="~/.ssh/id_rsa",
    job_dir="/home/username/jobs"
)

# Executor uses tunnel
executor = run.SlurmExecutor(
    tunnel=tunnel,
    ...
)
```

**Benefits**:
- Submit from laptop
- Monitor jobs remotely
- Download results
- No need to SSH in manually

### 4. Code Packaging

**Automatic code shipping**:

```python
packager = get_packager(extra_package_dirs=["my_package/"])

# Packager bundles:
# - Main code directory
# - Extra packages
# - Dependencies
# → Ships to cluster as tarball
# → Extracts in job directory
```

**Run latest code** without manual syncing.

### 5. Job Dependencies

**Chain jobs**:

```python
# Run generation
gen_task = exp.add(
    run.Script("python generate.py"),
    executor=slurm_executor,
)

# Run evaluation after generation completes
eval_task = exp.add(
    run.Script("python evaluate.py"),
    executor=slurm_executor,
    dependencies=[gen_task],  # Wait for gen_task
)
```

**Slurm handles waiting** (afterany, afterok, etc.).

### 6. Container Management

**Per-task containers**:

```python
# Generation uses vLLM container
gen_executor = get_executor(
    cluster_config,
    container=cluster_config["containers"]["vllm"],
    gpus_per_node=8,
)

# Evaluation uses default container
eval_executor = get_executor(
    cluster_config,
    container=cluster_config["containers"]["default"],
    gpus_per_node=0,  # CPU only
)
```

### 7. Environment Variable Management

**Automatic env var forwarding**:

```python
env_vars = get_env_variables(cluster_config)
# Returns:
# {
#   "OPENAI_API_KEY": "...",
#   "NVIDIA_API_KEY": "...",
#   "WANDB_API_KEY": "...",
#   "HF_TOKEN": "...",
# }

# Passed to executor
executor = SlurmExecutor(env_vars=env_vars, ...)
```

**Keys always available** in cluster jobs.

---

## How NeMo Skills Uses It

**Example: Distributed evaluation**

```python
import nemo_run as run
from nemo_skills.pipeline.utils import get_executor, get_cluster_config

# Load cluster config
cluster_config = get_cluster_config(cluster="my_cluster")

# Create experiment
with run.Experiment("bfcl_eval") as exp:

    # Submit 100 evaluation tasks in parallel
    for i in range(100):
        executor = get_executor(
            cluster_config,
            container=cluster_config["containers"]["vllm"],
            num_nodes=1,
            gpus_per_node=1,
            job_name=f"eval_task_{i}",
            log_dir=f"logs/eval_{i}",
        )

        exp.add(
            run.Script(f"python evaluate.py --task-id {i}"),
            executor=executor,
        )

# Submit all 100 jobs at once
exp.run()
```

**Slurm schedules** based on availability.

---

## Could We Use This?

### ✅ YES - Here's How

**Scenario**: Run 10,000 BFCL tasks across 100 GPUs on a cluster.

#### Option 1: Task-Level Distribution

**Each Slurm job = 1 evaluation task**:

```python
import nemo_run as run
from evaluation.runner import EvaluationRunner
from evaluation.adapters import Agent006Adapter

# Load tasks
tasks = load_bfcl_tasks(limit=10000)

# Create experiment
cluster_config = get_cluster_config("my_cluster")
with run.Experiment("bfcl_10k") as exp:

    for task in tasks:
        # Create executor for this task
        executor = get_executor(
            cluster_config,
            container="nvcr.io/nvidia/pytorch:24.01",
            num_nodes=1,
            gpus_per_node=1,
            job_name=f"bfcl_{task.task_id}",
            log_dir=f"logs/{task.task_id}",
        )

        # Add task to experiment
        exp.add(
            run.Script(
                f"python -m evaluation.run_single_task --task-id {task.task_id}"
            ),
            executor=executor,
        )

# Submit all jobs
exp.run()
```

**Slurm manages**:
- Scheduling (based on GPU availability)
- Retries (if jobs fail)
- Dependencies (if needed)
- Resource allocation

#### Option 2: Batch-Level Distribution

**Each Slurm job = batch of N tasks**:

```python
# Split 10,000 tasks into 100 batches of 100
batches = chunk_tasks(tasks, batch_size=100)

with run.Experiment("bfcl_10k") as exp:
    for batch_id, batch in enumerate(batches):
        executor = get_executor(
            cluster_config,
            num_nodes=1,
            gpus_per_node=1,
            job_name=f"batch_{batch_id}",
        )

        # Save batch to file
        batch_file = f"batch_{batch_id}.jsonl"
        save_tasks(batch, batch_file)

        # Run our evaluation runner on this batch
        exp.add(
            run.Script(
                f"python -m evaluation.cli --tasks {batch_file} --output results_{batch_id}.jsonl"
            ),
            executor=executor,
        )

exp.run()
```

**Benefits**:
- Better resource utilization
- Fewer Slurm jobs (less scheduler overhead)
- Still parallel (100 GPUs simultaneously)

#### Option 3: Hybrid (Our Runner + Slurm Distribution)

**Each Slurm job runs our concurrency engine**:

```python
# Split into 10 large batches (1000 tasks each)
large_batches = chunk_tasks(tasks, batch_size=1000)

with run.Experiment("bfcl_10k") as exp:
    for batch_id, batch in enumerate(large_batches):
        # Each job gets 10 GPUs and runs 100 tasks concurrently
        executor = get_executor(
            cluster_config,
            num_nodes=1,
            gpus_per_node=10,
            job_name=f"batch_{batch_id}",
        )

        exp.add(
            run.Script(
                f"python -m evaluation.cli "
                f"--tasks batch_{batch_id}.jsonl "
                f"--parallel 100 "  # Our concurrency engine!
                f"--output results_{batch_id}.jsonl"
            ),
            executor=executor,
        )

exp.run()
```

**Best of both worlds**:
- Slurm handles machine allocation
- Our concurrency engine handles task parallelism within each machine
- 10 jobs × 100 concurrent tasks = 1000 parallel executions

---

## Integration with Our Architecture

**How it fits**:

```
┌─────────────────────────────────────────────────────────┐
│                    USER INTERFACE                        │
│  "python -m evaluation.cli --cluster my_cluster ..."    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│               SLURM DISTRIBUTION LAYER                   │
│             (nemo_run + nemo_skills utils)               │
│                                                          │
│  - Split tasks into batches                              │
│  - Create Slurm executors                                │
│  - Submit jobs via nemo_run                              │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼ (Each Slurm job runs)
┌─────────────────────────────────────────────────────────┐
│                 OUR EVALUATION BACKEND                   │
│                                                          │
│  Layer 0: ConcurrencyEngine (asyncio + semaphore)       │
│  Layer 1: Protocols                                      │
│  Layer 2: EvaluationRunner                               │
│  Layer 3: Adapters                                       │
└─────────────────────────────────────────────────────────┘
```

**Clean separation**:
- **Distribution layer**: Slurm job submission (optional)
- **Execution layer**: Our 5-layer architecture (always used)

**Local vs cluster**:
```python
# Local execution
python -m evaluation.cli --tasks bfcl.jsonl --parallel 10

# Cluster execution
python -m evaluation.cli --tasks bfcl.jsonl --parallel 10 --cluster my_cluster
```

**Same runner**, different distribution strategy.

---

## What We Would Need to Add

### 1. Cluster Config Support

```python
# evaluation/cluster.py

from nemo_skills.pipeline.utils.cluster import (
    get_cluster_config,
    get_executor,
    get_tunnel,
)

def create_distributed_runner(
    tasks: list[EvaluationTask],
    cluster: str,
    batch_size: int = 100,
) -> None:
    """Distribute evaluation across Slurm cluster."""

    cluster_config = get_cluster_config(cluster)

    # Split tasks into batches
    batches = chunk_tasks(tasks, batch_size)

    with run.Experiment("distributed_eval") as exp:
        for batch_id, batch in enumerate(batches):
            # Create executor for this batch
            executor = get_executor(
                cluster_config,
                container="our_eval_container",
                num_nodes=1,
                gpus_per_node=8,
                job_name=f"eval_batch_{batch_id}",
                log_dir=f"logs/batch_{batch_id}",
            )

            # Save batch to file
            batch_file = f"batch_{batch_id}.jsonl"
            save_tasks(batch, batch_file)

            # Run our evaluation CLI
            exp.add(
                run.Script(
                    f"python -m evaluation.cli "
                    f"--tasks {batch_file} "
                    f"--output results_{batch_id}.jsonl "
                    f"--parallel {batch_size}"
                ),
                executor=executor,
            )

    # Submit and wait
    exp.run()
```

### 2. CLI Flag

```python
# evaluation/cli.py

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--parallel", type=int, default=10)
    parser.add_argument("--cluster", default=None)  # NEW!
    parser.add_argument("--batch-size", type=int, default=100)  # NEW!
    args = parser.parse_args()

    tasks = load_tasks(args.tasks)

    if args.cluster:
        # Distributed execution via Slurm
        create_distributed_runner(
            tasks,
            cluster=args.cluster,
            batch_size=args.batch_size,
        )
    else:
        # Local execution (our runner)
        runner = EvaluationRunner(...)
        results = await runner.run_evaluation(tasks)
```

### 3. Dependencies

```yaml
# pyproject.toml (add)
dependencies = [
    "nemo-run>=1.0.0",
    # ... existing deps
]
```

---

## Benefits of Adopting Slurm Backend

### ✅ 1. Scale to Thousands of GPUs

**Run massive evaluations**:
- 10,000 BFCL tasks across 100 GPUs
- Complete in hours instead of days
- No manual job management

### ✅ 2. SSH Tunnel Support

**Submit from laptop**:
```bash
# From laptop (not on cluster!)
python -m evaluation.cli \
    --tasks bfcl.jsonl \
    --cluster my_cluster \  # Automatically SSHs and submits
    --parallel 1000
```

**No need** to:
- SSH into cluster
- Copy files manually
- Monitor jobs manually

### ✅ 3. Automatic Code Packaging

**Always run latest code**:
- Local changes → automatically shipped to cluster
- No manual rsync
- No version mismatches

### ✅ 4. Container Management

**Consistent environments**:
- Use same container everywhere
- No dependency issues
- Reproducible results

### ✅ 5. Job Dependencies

**Chain evaluations**:
```python
# Run generation first
gen_jobs = submit_generation(...)

# Then evaluation (waits automatically)
eval_jobs = submit_evaluation(..., dependencies=gen_jobs)
```

### ✅ 6. Resource Flexibility

**Easy to change resources**:
```bash
# Small test run (1 GPU)
python -m evaluation.cli --cluster test --gpus-per-job 1

# Large production run (100 GPUs)
python -m evaluation.cli --cluster prod --gpus-per-job 8 --num-jobs 12
```

### ✅ 7. Unified API

**Same code, different executors**:
```python
# Development: Local
python -m evaluation.cli --tasks test.jsonl

# CI: Docker
python -m evaluation.cli --tasks test.jsonl --cluster local-docker

# Production: Slurm
python -m evaluation.cli --tasks bfcl.jsonl --cluster prod-cluster
```

---

## Costs/Considerations

### ⚠️ 1. Additional Dependency

**Adds `nemo-run` dependency**:
- NVIDIA library (well-maintained)
- Apache 2.0 license
- Actively developed

**Not a blocker** - many NVIDIA users already have it.

### ⚠️ 2. Cluster Config Required

**Users need to create config**:
```yaml
# ~/.nemo_skills/cluster_configs/my_cluster.yaml
executor: slurm
account: my_account
partition: gpu
ssh_tunnel:
  host: cluster.nvidia.com
  user: myuser
```

**But**: Only needed for cluster execution, not local.

### ⚠️ 3. Learning Curve

**Users need to understand**:
- Cluster configs
- Resource allocation
- Job monitoring

**But**: Standard Slurm knowledge, not specific to our tool.

---

## Recommendation

### ✅ YES - Adopt Slurm Backend (Optional Layer)

**Implement as optional distribution layer**:

```
Layer 5 (Optional): Slurm Distribution
        ↓ (orchestrates)
Layer 4: Frontends
        ↓ (creates)
Layer 3: Adapters
        ↓ (uses)
Layer 2: Runner
        ↓ (uses)
Layer 1: Protocols
        ↓ (uses)
Layer 0: Concurrency Engine
```

**Layer 5 = Cluster distribution** (optional):
- Uses `nemo_run` for job submission
- Reuses NeMo Skills' cluster utilities
- Distributes our evaluation jobs across Slurm
- Falls back to local execution if not configured

**Benefits**:
- ✅ Scale to massive evaluations (10K+ tasks)
- ✅ Submit from laptop via SSH tunnel
- ✅ Automatic code packaging
- ✅ Container management
- ✅ Job dependencies
- ✅ Unified local/cluster API
- ✅ Optional (doesn't break local execution)

**Implementation**:
1. Add Layer 5 (cluster distribution) in `evaluation/cluster.py`
2. Add `--cluster` flag to CLI
3. Reuse NeMo Skills' cluster utilities
4. Keep our 5-layer backend unchanged

**Timeline**: 1-2 days to integrate (reusing their utils)

---

## Example Usage

### Local Execution (No Change)

```bash
python -m evaluation.cli \
    --tasks bfcl.jsonl \
    --parallel 10 \
    --output results.jsonl
```

### Cluster Execution (New!)

```bash
python -m evaluation.cli \
    --tasks bfcl.jsonl \
    --parallel 100 \
    --output results.jsonl \
    --cluster my_cluster \      # NEW!
    --batch-size 1000 \         # NEW!
    --gpus-per-job 8            # NEW!
```

**Same CLI, different scale!**

---

## Conclusion

**Slurm backend from NeMo Skills is highly reusable** and would be valuable for:
- Large-scale benchmark evaluation
- CI/CD on clusters
- Research experiments at scale

**We should**:
- ✅ Add optional Layer 5 (cluster distribution)
- ✅ Reuse NeMo Skills' cluster utilities
- ✅ Keep our backend unchanged
- ✅ Make cluster execution opt-in

**This gives users**:
- Local execution (fast iteration)
- Cluster execution (large scale)
- Same API for both

**Recommended**: Add after Phase 1 (core backend) is complete.
