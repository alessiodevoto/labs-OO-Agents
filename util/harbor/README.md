# util/harbor — Harbor benchmark configs and debug scripts

Harbor is the canonical way to run benchmark evaluations. Tasks run inside
Apptainer containers; the agent reads `instruction.md`, writes an answer to
`/app/answer.txt`, and the verifier writes a float score to
`/logs/verifier/reward.txt`.

## Files

| File | Purpose |
|------|---------|
| `*_baseline.yaml` | Harbor run configs (Apptainer + baseline agent) |
| `run_*_debug.py` | In-process debug scripts (no container, fast iteration) |
| `tasks/` | Pre-generated task dirs for small smoke-test runs |

## Running a benchmark

```bash
# Generate task dirs (benchmark-specific; see adapter README)
python 3p/harbor-nemo/adapters/locomo/run_adapter.py \
    --task-dir util/harbor/tasks/locomo --limit 5

# Run via Harbor
harbor run --config util/harbor/locomo_baseline.yaml

# Debug without Harbor (faster, no container)
uv run python util/harbor/run_locomo_debug.py --tasks 5
```

## SIF cache

Harbor converts Docker images to Apptainer SIF files once, caches them in
`apptainer_image_cache_dir`, and reuses them for every subsequent run.
**Pulling is a one-time cost per unique task image**, not per run.

Each SIF is ~500 MB–1 GB. Running a full benchmark (e.g. 500 SWE-bench tasks)
requires ~300 GB of cache space. The shipped `*_baseline.yaml` configs use
`~/3p/sif_cache/` as a generic default for the current host. On DFW hosts,
override `apptainer_image_cache_dir` in your config (or copy from Lustre
into `~/3p/sif_cache/`) — see the "Shared SIF cache on DFW Lustre" section
below.

### Shared SIF cache on DFW Lustre

Pre-built SIFs for Terminal Bench, SWE-bench Verified, DABStep, and MemBench
are stored on the DFW cluster's Lustre filesystem (world-writable,
`hardware` group):

```
/lustre/fs1/portfolios/coreai/projects/coreai_dlalgo_dle/users/agronskiy/apptainer_cache/
```

This is a flat directory: `<name>.sif` + `<name>.sif.lock` pairs — the exact
naming convention Harbor expects for `apptainer_image_cache_dir`.

**To populate your local SIF cache from Lustre** (instead of pulling from
Docker Hub, which is much slower):

```bash
DFW=rcabral@cw-dfw-cs-001-login-02.cw-dfw-cs-001.hpc.nvidia.com
LUSTRE_CACHE=/lustre/fs1/portfolios/coreai/projects/coreai_dlalgo_dle/users/agronskiy/apptainer_cache
LOCAL_CACHE=~/3p/sif_cache

# Sync all available SIFs (or filter by prefix, e.g. terminal_bench_*)
rsync -av "$DFW:$LUSTRE_CACHE/" "$LOCAL_CACHE/"
```

Transfer speed is ~46 MB/s. Contents as of 2026-04-22:

| Benchmark | SIFs | Notes |
|-----------|------|-------|
| SWE-bench Verified | ~500 | `swebench_sweb.eval.x86_64.*_latest.sif` |
| Terminal Bench 1 | ~241 | `terminal_bench_*.sif`, one per task |
| DABStep | 1 | `dabstep.sif` |
| MemBench / LoCoMo | 1 | `ghcr.io_laude-institute_t-bench_ubuntu-24-04_latest.sif` |

SWE-bench Pro (~731 SIFs, ~500 GB) is not yet on Lustre. See gl-13.

### Disk space

SIFs are large. A rough budget:

| Benchmark | # SIFs | ~Size |
|-----------|--------|-------|
| Terminal Bench 1 | 241 | 38 GB |
| SWE-bench Verified | 500 | 300 GB |
| SWE-bench Pro | 731 | ~500 GB |
| DABStep + MemBench/LoCoMo | 2 | < 1 GB |

If running SWE-bench Verified or Pro without a pre-populated cache, Harbor
pulls SIFs on-the-fly per task from Docker Hub (~5 min/SIF). Pre-populating
from Lustre is strongly recommended for any full run.

## System-specific Apptainer settings (rcabral DFW/local hosts)

The `*_baseline.yaml` configs in this directory are tuned for the current
hosts. Key settings to revisit on a new machine:

**`apptainer_fakeroot`** — Required `true` for benchmarks that need `runuser`
inside the container (Terminal Bench, SWE-bench, MemBench, LoCoMo). Requires
AppArmor unprivileged user namespace support (`aa-status | grep userns`).
See gl-35 for the AppArmor fix if it's disabled.

**`apptainer_image_cache_dir`** — Set to `~/3p/sif_cache` on local hosts.
On DFW you can point this directly at the Lustre path (no rsync needed):
```yaml
apptainer_image_cache_dir: /lustre/fs1/portfolios/coreai/projects/coreai_dlalgo_dle/users/agronskiy/apptainer_cache
```

**`TMPDIR` for fakeroot runs** — Fakeroot containers create staging dirs in
`$TMPDIR` (Python `tempfile.mkdtemp`). These dirs are root-owned inside the
user namespace and cannot be deleted by the regular user if a container
crashes. `/tmp` is a 61 GB tmpfs and fills up silently.

Always set before launching harbor on a fakeroot benchmark:
```bash
export TMPDIR=/localhome/$USER/apptainer_tmp   # or any non-tmpfs path
mkdir -p "$TMPDIR"
```

If `/tmp` fills and you see `ENOSPC` errors:
```bash
sudo rm -rf /tmp/apptainer_staging_*/   # root-owned; requires sudo
```

**`env_passthrough`** — The container starts with a clean environment.
API keys must be forwarded explicitly. `OPENAI_API_KEY` on this host is
a LiteLLM proxy token (not a real OpenAI key) and will fail inside the
container. Use `NVIDIA_INTERNAL_API_KEY` with
`aws/anthropic/bedrock-claude-sonnet-4-5-v1` instead.

## Model selection

Use the same model the verified dabstep runs use:

```yaml
agents:
  - name: nemo-oo-agents
    model_name: aws/anthropic/bedrock-claude-sonnet-4-5-v1
    kwargs:
      agent_type: baseline
```

`OPENAI_API_KEY` here is a LiteLLM proxy token — passing it into the
container produces `AuthenticationError: Invalid proxy server token`. The
AWS Bedrock path via `NVIDIA_INTERNAL_API_KEY` is what actually works.


## Setting up a new Colossus machine for benchmarks

This section documents the complete setup process for running Harbor benchmarks
(TB1, TB2, SWEBench) on a new Colossus machine. Follow these steps to get a
reproducible environment.

### 1. Request a Colossus lease

```bash
# Via CLI (requires colossus package)
colossus lease create \
  --os ubuntu-24.04-x86_64-standard-uefi \
  --type Z590-A \
  --duration 7d \
  --justification "SWEBench and TB benchmark evaluation"

# Or via the Colossus web UI: https://colossus.nvidia.com
```

Recommended machine types:
- **Z590-A** (Intel i9-11900K, 128GB RAM, 1TB NVMe) — good for SWEBench
- **4090** (RTX 4090, 64GB RAM) — not needed for agent benchmarks (CPU-bound)

### 2. Initial machine setup

```bash
# SSH to the machine (use FQDN from Colossus lease output)
ssh local-$USER@<fqdn>

# Clone the repo
git clone https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents.git
cd nemo_oo_agents

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env

# Install the project
uv sync

# Create harbor jobs directory
mkdir -p ~/harbor_jobs ~/3p/sif_cache
```

### 3. Install Docker

```bash
# Add Docker's official GPG key
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add user to docker group (logout/login required)
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker run hello-world
```

### 4. Install Apptainer (for SWEBench)

```bash
# Add Apptainer repo
sudo add-apt-repository -y ppa:apptainer/ppa
sudo apt-get update
sudo apt-get install -y apptainer

# Verify fakeroot support (required for Terminal Bench, SWEBench)
apptainer exec --fakeroot docker://alpine echo "fakeroot works"
```

### 5. Rsync SIF cache from DFW (for SWEBench)

```bash
# From the Colossus machine:
DFW=rcabral@cw-dfw-cs-001-login-02.cw-dfw-cs-001.hpc.nvidia.com
LUSTRE_CACHE=/lustre/fs1/portfolios/coreai/projects/coreai_dlalgo_dle/users/agronskiy/apptainer_cache

# Rsync all SIFs (535 files, ~567GB, takes ~3 hours at 46 MB/s)
rsync -av --progress "$DFW:$LUSTRE_CACHE/" ~/3p/sif_cache/

# IMPORTANT: Symlink ~/.apptainer to avoid duplicate cache
mkdir -p ~/3p/sif_cache/.apptainer_cache
ln -sfn ~/3p/sif_cache/.apptainer_cache ~/.apptainer
```

The symlink prevents Apptainer from creating a duplicate cache in `~/.apptainer/`
which wastes ~165GB of disk space.

### 6. Set environment variables

```bash
# Create .env file in the repo root
cat > ~/nemo_oo_agents/.env << 'EOF'
# NVIDIA Inference API (routes to Bedrock for Anthropic models)
NVIDIA_INTERNAL_API_KEY=sk-<your-key>

# Apptainer settings (prevent /tmp from filling up)
export TMPDIR=/localhome/$USER/apptainer_tmp
mkdir -p "$TMPDIR"
EOF

# Source it
source ~/nemo_oo_agents/.env
```

### 7. Clone Harbor (for adapters)

```bash
cd ~/nemo_oo_agents
git clone https://gitlab-master.nvidia.com/interactive-agents/harbor.git 3p/harbor-nemo

# Also need the upstream harbor for registry
git clone https://github.com/codeacme17/harbor.git ~/3p/harbor
```

### 8. Run a benchmark

```bash
cd ~/nemo_oo_agents

# Terminal Bench 1 (baseline agent, ~2 hours for all 241 tasks)
harbor run --config util/harbor/terminal_bench_local_docker.yaml

# Terminal Bench 2 (89 tasks)
harbor run --config util/harbor/terminal_bench_2_local_docker.yaml

# SWEBench Verified (500 tasks, ~8 hours)
harbor run --config util/harbor/swebench_todo.yaml
```

### 9. Monitor progress

```bash
# Count completed tasks
cd ~/harbor_jobs/<benchmark_name>/<run_dir>/
find . -name reward.txt | wc -l

# Check scores
find . -name reward.txt -exec cat {} \; | sort | uniq -c
```

### Disk space requirements

| Benchmark | SIF Cache | Working Space | Total Recommended |
|-----------|-----------|---------------|-------------------|
| Terminal Bench 1 | 38 GB | 50 GB | 100 GB |
| Terminal Bench 2 | 0 (Docker) | 50 GB | 100 GB |
| SWEBench Verified | 300 GB | 100 GB | 450 GB |
| All benchmarks | 567 GB | 200 GB | 800 GB |

### Troubleshooting

**"no space left on device" during Apptainer builds:**
- Check `df -h /tmp` — Apptainer builds to `/tmp` by default
- Set `export TMPDIR=/localhome/$USER/apptainer_tmp` before running
- If `/tmp` is full: `sudo rm -rf /tmp/apptainer_staging_*/`

**"LLM Provider NOT provided" error:**
- Harbor writes `llm_config.yaml` to `/installed-agent/nemo_oo_agents/`
- Ensure `NEMO_OO_LLM_CONFIG` env var is in `env_passthrough` in your YAML config

**SSH authentication failures:**
- Use FQDN from Colossus, not IP
- Run `kinit` if using Kerberos auth


## Writing a debug script for a new benchmark

Follow the pattern in `run_locomo_debug.py` and `run_dabstep_debug.py`:

1. **Add adapter to sys.path** — adapters live in `3p/harbor-nemo/adapters/<name>/`.
   The harbor repo must be cloned at `3p/harbor-nemo` (gitignored; clone manually).

2. **Load tasks from the adapter** — instantiate the adapter class in-memory;
   don't generate task dirs to disk.

3. **Build instruction with `return_result()`** — the Harbor template writes to
   `/app/answer.txt`, which doesn't exist outside a container. Replace it with
   `return_result(answer)` in the debug instruction string.

4. **Inline the scorer** — copy the scoring logic from `harbor-nemo/adapters/<name>/scorer.py`
   as a local `ScoringContext → ScoreResult` class. Don't import scorer.py directly
   (it's designed as a CLI script, not a library).

5. **Wire through eval_pipeline** — use `Evaluator`, `add_test`, and `evaluator.run()`.
   Results go to `.development/docs/evaluation/`. See `run_locomo_debug.py` for the
   exact call sequence.

6. **Print agent_error from output dict** — when the agent fails, `r.output` is
   `{"response": "", "success": False, "error": "..."}`. `r.error` (EvalTestResult
   field) is separate and usually None. Check `r.output.get("error")` to diagnose
   API key / model failures.

## Adding a new benchmark (checklist)

### Harbor adapter (`3p/harbor-nemo/adapters/<name>/`)

- [ ] `adapter.py` — loads data, generates task dirs
- [ ] `scorer.py` — CLI scorer: reads args, prints score, exits 0=pass/1=fail
- [ ] `run_adapter.py` — CLI to generate tasks from data
- [ ] `template/instruction.md` — task prompt template (placeholders filled at generation)
- [ ] `template/task.toml` — timeouts, resources, difficulty/tags placeholders
- [ ] `template/environment/Dockerfile` — use `ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624` unless data must be baked in
- [ ] `template/solution/solve.sh` — oracle answer (for verifying scorer)
- [ ] `template/tests/test.sh` — reads `/app/answer.txt`, calls `scorer.py`, writes reward
- [ ] `adapter_metadata.json` — benchmark provenance and sizes
- [ ] `README.md` — data setup, generation commands, scoring explained

### nemo_oo_agents (`util/harbor/`)

- [ ] `<name>_baseline.yaml` — Harbor run config (copy `locomo_baseline.yaml`, adjust dataset path and concurrency)
- [ ] `run_<name>_debug.py` — debug script (copy `run_locomo_debug.py` pattern)
- [ ] Add a smoke test comment block to the debug script after the first run

### Data caching convention

| Benchmark | Cache location | Download |
|-----------|---------------|----------|
| DABStep | `~/.cache/dabstep/data/context/` | HuggingFace `adyen/DABstep` |
| LoCoMo | `~/.cache/locomo/locomo10.json` | Auto-downloaded from GitHub on first run |
| MemBench | `~/.cache/membench/` | Lustre (see below) or Google Drive |

**MemBench data** (~1.1 GB) and **LoCoMo data** (~3 MB) are stored on Lustre:

```
/lustre/fsw/portfolios/llmservice/projects/llmservice_nemo_reasoning/users/rcabral/benchmark_data/
```

To populate locally from Lustre (run from any DFW node):

```bash
DFW=rcabral@cw-dfw-cs-001-login-02.cw-dfw-cs-001.hpc.nvidia.com
LUSTRE=/lustre/fsw/portfolios/llmservice/projects/llmservice_nemo_reasoning/users/rcabral/benchmark_data

# MemBench (~1.1 GB, needed before running run_adapter.py)
rsync -av "$DFW:$LUSTRE/membench/" ~/.cache/membench/

# LoCoMo (~3 MB; also auto-downloads from GitHub so this is optional)
rsync -av "$DFW:$LUSTRE/locomo/" ~/.cache/locomo/
```

LoCoMo also auto-downloads from GitHub on first adapter run — the Lustre copy is a faster fallback.
MemBench has no auto-download; Lustre is the canonical source (Google Drive as backup:
`https://drive.google.com/file/d/112Zraj4pTPH4Idph6i1uMOLA_LPFdGr0/view`).

Auto-downloads go to `~/.cache/<benchmark>/`. Never embed raw data in the
repo or generate tasks with external retrieval at runtime.

### Pre-generated task directories on Lustre

Running `run_adapter.py` can be slow for large benchmarks (1542 LoCoMo tasks,
4779 MemBench tasks). Pre-generated task dirs are stored on Lustre and can be
rsynced directly, skipping adapter generation:

```
/lustre/fsw/portfolios/llmservice/projects/llmservice_nemo_reasoning/users/rcabral/harbor_tasks/
```

```bash
DFW=rcabral@cw-dfw-cs-001-login-02.cw-dfw-cs-001.hpc.nvidia.com
LUSTRE=/lustre/fsw/portfolios/llmservice/projects/llmservice_nemo_reasoning/users/rcabral/harbor_tasks

# LoCoMo task dirs (1542 tasks, ~176 MB)
rsync -av "$DFW:$LUSTRE/locomo/" util/harbor/tasks/locomo/

# MemBench task dirs (4779 tasks, ~270 MB)
rsync -av "$DFW:$LUSTRE/membench/" util/harbor/tasks/membench/
```

This is the fastest way to get started if you already have the SIF cache populated.

### Verified smoke test results

| Benchmark | Method | Model | Tasks | Result |
|-----------|--------|-------|-------|--------|
| LoCoMo single-hop | eval_pipeline debug | openai/gpt-4o | 5 | 4/5 = 80% (F1) |
| LoCoMo single-hop | Harbor Apptainer | bedrock claude-sonnet-4-5 | 3/5 completed | mean F1=0.87, all pass |
| DABStep | Harbor Apptainer | bedrock claude-sonnet-4-5 | 5 | 1/5 (baseline, no pipeline) |
| MemBench | — | — | — | pending data download |

---

## Infra failure playbook

This section documents every infra-level failure pattern we have hit running
Terminal Bench on an aarch64 host (galaxy, `lab@10.87.108.113`) with x86_64
task containers emulated via kernel binfmt_misc QEMU. Where a fix is general
(applies to any host/architecture) that is noted explicitly.

The fixes described here live in two places:
- **`util/harbor/overlay/`** — files rsync'd to `harbor_bootstrap_overlay_v2`
  on the target machine. Apply with:
  ```bash
  rsync -av util/harbor/overlay/opt/harbor/ \
    lab@10.87.108.113:/home/lab/3p/harbor_bootstrap_overlay_v2/opt/harbor/
  ```
- **harbor MR `rcabral/terminal-bench-adapter`** — changes to
  `src/harbor/environments/apptainer.py` and
  `src/harbor/environments/server.py`. Deploy to galaxy with:
  ```bash
  rsync -av 3p/harbor/src/harbor/environments/apptainer.py \
            3p/harbor/src/harbor/environments/server.py \
    lab@10.87.108.113:/home/lab/rcabral/harbor/src/harbor/environments/
  ```

---

### 1. pip/pip3 installs fail inside containers

**Symptom:** Verifier or task setup calls `pip3 install pytest` (or similar)
and gets:
```
ERROR: Package 'pytest' requires a different Python: 3.6.x not in '>=3.8'
```
or silently installs to the wrong Python's site-packages and the import fails
at runtime.

**Root cause (aarch64-specific):** The task container ships its own x86_64
`pip3` binary linked to the container's Python 3.6 (or whatever old version
the image includes). The harbor bootstrap overlay provides a newer aarch64
Python 3.12 at `/opt/harbor/cpython312-aarch64/`, and the `python3` wrapper
in the overlay shadows the container's Python — but `pip3` still resolved to
the container's old binary, so installs went into the wrong site-packages and
with the wrong version checks.

**Fix:** `util/harbor/overlay/opt/harbor/bin/pip3` (and `pip`) — shell
wrappers that detect whether the aarch64 overlay interpreter is active and, if
so, invoke pip via that interpreter with two adjustments:

1. **`--user` + `PYTHONUSERBASE=/tmp/aarch64-pip-user`** — the overlay Python
   at `/opt/harbor/cpython312-aarch64/` has an `EXTERNALLY-MANAGED` marker
   (it was built by uv and has no system site-packages). Installing to `--user`
   with a per-trial temp dir (`/tmp`, which lives in the container's writable
   overlay) sidesteps the marker without needing root or a venv.
2. **`--break-system-packages`** — required alongside `--user` to suppress the
   EXTERNALLY-MANAGED refusal.

The `python3` wrapper already exports `PYTHONUSERBASE=/tmp/aarch64-pip-user`,
so packages installed by pip are importable without any extra path setup.

Tasks that call bare `pip` (not `pip3`) hit the same problem; the `pip`
wrapper is an identical copy of `pip3`.

**Affected platforms:** aarch64 hosts running x86_64 containers with the
harbor bootstrap overlay. On native x86_64 hosts the container's own pip is
correct and this wrapper is a no-op (it falls through to the container pip).

---

### 2. `uv pip install --system` fails inside containers

**Symptom:** Tasks that use `uv pip install --system <pkg>` (e.g.
`build-cython-ext`, `largest-eigenval`) get:
```
error: unrecognized argument: --system
```

**Root cause (aarch64-specific):** The harbor bootstrap overlay ships a `uv`
shell wrapper at `/opt/harbor/bin/uv` that translates `uv pip install` into
`pip3 install --target <dir>`. The wrapper's argument parser did not recognise
the `--system` flag (which tells the real uv to install into the system Python
rather than a venv) and passed it through to pip, which also rejected it.

**Fix:** Strip `--system` in the uv wrapper's argument loop before forwarding
to pip. Committed to harbor MR in `apptainer.py`. `--system` is implicit when
using `--target`, so dropping it is correct.

**Affected platforms:** Any host using the harbor bootstrap overlay's uv
wrapper. Not relevant on hosts where the real uv binary is available.

---

### 3. Apptainer can't bind-mount over a symlink destination

**Symptom:** Container startup fails with:
```
FATAL: container creation failed: mount hook function failure:
  mount .../ld-linux-aarch64.so.1->/lib/ld-linux-aarch64.so.1 error:
  destination /lib/ld-linux-aarch64.so.1 doesn't exist in container
```
even though `/lib/ld-linux-aarch64.so.1` visibly exists in the container image
(e.g. `qemu-startup`, `qemu-alpine-ssh`).

**Root cause (aarch64-specific):** The harbor aarch64 code bind-mounts the
host's aarch64 dynamic linker into the container so the overlay Python can
find it. Most x86_64 task containers don't have `/lib/ld-linux-aarch64.so.1`
at all, so Apptainer creates the destination automatically. A handful of task
containers (those that bundle their own QEMU or aarch64 runtime) already have
this path — but as a **symlink** (`-> aarch64-linux-gnu/ld-2.31.so`).
Apptainer refuses to bind-mount over a symlink destination.

**Fix:** Before launching Apptainer, pre-create an empty regular file at
`overlay_upper/lib/ld-linux-aarch64.so.1`. The overlay's upper dir shadows the
container image's symlink with a regular file; Apptainer then has a valid
mount target. Committed to harbor MR in `apptainer.py`.

**Current status — known limitation, no fix:** Any approach that pre-creates
files under `overlay_upper/lib/` triggers fuse-overlayfs to treat `/lib/` as
opaque, hiding the container image's `/lib/x86_64-linux-gnu/`. The t-bench
`ubuntu-24-04` image uses an unmerged-usr layout where `/lib64` symlinks to
`/lib/x86_64-linux-gnu`, so hiding that directory breaks the x86_64 ELF
interpreter for every container. The two tasks affected by this symlink issue
(`qemu-startup`, `qemu-alpine-ssh`) are accepted as non-runnable on aarch64
hosts until a non-overlay solution is found.

**Affected platforms:** aarch64 hosts only. The code path is guarded by
`os.uname().machine == "aarch64"`.

---

### 4. Harbor sidecar server crashes on Python < 3.10 containers

**Symptom:** Containers built on Python 3.9 (e.g. `swe-bench-astropy`,
`swe-bench-fsspec`, `swe-bench-langcodes`) time out immediately. The trial.log
shows no agent activity — the server never became ready.

**Root cause (general):** `server.py` used `str | None` as a return type
annotation for `_is_blacklisted()`. The union-type shorthand (`X | Y`) was
introduced in Python 3.10 ([PEP 604](https://peps.python.org/pep-0604/)). On
containers whose Python is 3.9 or older, importing `server.py` raises:
```
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```
The server crashes before binding its socket, so harbor reports a timeout.

**Fix:** Changed to `Optional[str]` (from `typing`, already imported).
Committed to harbor MR in `server.py`.

**Affected platforms:** Any container whose Python interpreter is < 3.10,
regardless of host architecture. On aarch64/QEMU hosts the server runs via
the overlay's Python 3.12 so this would not have manifested — but it affects
x86_64 runs too whenever a task image ships Python 3.9.

---

### 5. Safety filter false-positives block legitimate agent commands

**Symptom:** Agent execution completes but the task was never actually attempted.
`trial.log` shows a line like:
```
Blocked command: nemo-harbor --instruction '...reboot...' — Command blocked
by safety filter (matched: \b(shutdown|reboot|...)\b)
```
The word triggering the filter appeared in the task's instruction text, not as
an actual command.

**Root cause (general):** The `_is_blacklisted()` safety filter in `server.py`
ran its regex patterns against the raw command string, including any quoted
shell arguments. If the task instruction (passed as a quoted argument to
`nemo-harbor --instruction '...'`) contained words like `reboot`, `shutdown`,
`halt`, or `kill`, the filter matched them as if they were commands.

**Fix:** Added `_strip_quoted_strings()` to `server.py` — strips single- and
double-quoted string contents before applying the filter. The regex patterns
are then only matched against actual command tokens, not instruction text.
Committed to harbor MR in `server.py`.

**Affected platforms:** Any platform. The false-positive is purely a function
of what appears in task instruction text.

---

### 6. OOM kills before the agent has a chance to run

**Symptom:** Trial fails with `MemoryLimitExceededError`:
```
Container exceeded memory limit (1946MB > 1945MB)
```
Often the margin is tiny (1–100 MB over) or very large (task genuinely needs
4–8 GB).

**Root cause (general + aarch64-amplified):** Harbor's memory watchdog kills
the container at **95% of `memory_mb`** (the value from `task.toml`). Most
Terminal Bench tasks specify `memory_mb = 2048`, giving an effective kill
threshold of ~1945 MB. On aarch64/QEMU hosts, QEMU's emulation overhead adds
~50–200 MB on top of the task's native footprint, pushing borderline tasks
over the threshold.

**Fix:** Add `override_memory_mb: 8192` to the harbor run config's
`environment:` block. This raises the kill threshold to ~7782 MB (95% of
8192), covering all but the most extreme tasks. Galaxy has 743 GB RAM; with 16
concurrent trials the peak is 16 × 8 GB = 128 GB, well within budget.

```yaml
environment:
  type: apptainer
  override_memory_mb: 8192
  kwargs:
    ...
```

Note the harbor warning: "Overriding memory … alters the task from its
intended configuration. This could disqualify you from leaderboard
submissions." This is intentional for our internal runs; do not use
`override_memory_mb` for official leaderboard submissions.

**Affected platforms:** General, but the 95%-of-2048 threshold is especially
tight on QEMU hosts where emulation adds overhead.

---

### 7. QEMU SIGSEGV in dpkg-deb during apt-get (known unfixable)

**Symptom:** `trial.log` shows repeated:
```
qemu: uncaught target signal 11 (Segmentation fault) - core dumped
dpkg-deb: error: <decompress> subprocess was killed by signal (Segmentation fault)
```
The container either fails during agent setup (server disconnect) or during
the verifier's own package installation (reward file not found).

**Root cause (aarch64-specific):** `dpkg-deb` is an x86_64 binary running
under QEMU emulation. For some `.deb` packages — particularly those that use
complex decompression paths — QEMU hits an unimplemented or mishandled x86_64
instruction and segfaults. This is a QEMU stability issue.

Harbor's bootstrap already detects the apt-get failure and falls back to
Python urllib for cloning and uv for package installation, so the **agent
setup** phase usually recovers. However, some task verifiers run their own
`apt-get` to install test dependencies, and those fail without recovery.

**Known affected tasks:** `build-linux-kernel-qemu`, `fix-ocaml-gc`,
`new-encrypt-command`, `sql-injection-attack`, `spring-messaging-vul`.

**No fix available** without upgrading QEMU. These tasks are expected to
produce no reward on aarch64/QEMU hosts until either:
- The host is upgraded to a newer QEMU version with better x86_64 fidelity, or
- The tasks are run on a native x86_64 machine.

**Affected platforms:** aarch64 hosts running x86_64 containers via QEMU only.


## Multi-model benchmark configs

The `util/harbor/` directory includes configs for running benchmarks across
multiple models. Naming convention: `<benchmark>_<mode>_<model>.yaml`.

| Config | Benchmark | Model | Mode |
|--------|-----------|-------|------|
| `terminal_bench_local_docker.yaml` | TB1 | sonnet-4-5 | Docker |
| `terminal_bench_local_docker_ultra.yaml` | TB1 | nemotron-3-ultra | Docker |
| `terminal_bench_local_docker_specialized.yaml` | TB1 | sonnet-4-5 (specialized agent) | Docker |
| `terminal_bench_local_docker_react.yaml` | TB1 | sonnet-4-5 (react agent) | Docker |
| `terminal_bench_2_local_docker.yaml` | TB2 | sonnet-4-5 | Docker |
| `terminal_bench_2_local_docker_opus.yaml` | TB2 | opus-4-6 | Docker |
| `swebench_todo.yaml` | SWEBench | sonnet-4-5 | Apptainer |
| `swebench_todo_docker_sonnet.yaml` | SWEBench | sonnet-4-5 | Docker |
| `swebench_todo_docker_opus.yaml` | SWEBench | opus-4-6 | Docker |

## Venv tarball and .pth approach

The venv tarball (`nemo-venv-base-cp312-x86_64.tar.gz`) is self-sufficient:

1. **Third-party deps** — all wheels pre-installed (pydantic, litellm, etc.)
2. **`.pth` files** — make first-party packages importable via path manipulation
   (points to `/installed-agent/nemo_oo_agents/src/` etc.)
3. **`nemo-harbor` entry point** — correct shebang + import

This eliminates the need for `pip install -e` at runtime. Harbor's install code
detects `.pth` files and skips the editable install step entirely (see the
`feat/skip-editable-installs-with-pth` patch in the harbor repo).

Rebuild with: `bash util/harbor/build_venv_tarballs.sh`

## SWEBench Docker mode — three critical overlay gotchas

Running SWEBench Verified with the `swebench/todo` agent in Docker mode hit
three separate silent failures (all produced reward 0 for every task). If a
SWEBench run scores 0% across the board, check these:

1. **Container python is 3.11, not 3.12.** SWEBench eval images ship conda
   python3.11. Harbor's agent-setup resolves `PYBIN` via
   `which python3.13||3.12||3.11`, picks 3.11 → `PYVER=cp311` → looks for a
   `nemo-venv-base-cp311` tarball (absent) → falls back to pip editable install
   → fails with `Cannot import 'hatchling.build'` (exit 2). **Fix:** prepend
   the overlay's bundled python to the container `PATH` in the YAML `env:` block:
   ```yaml
   env:
     PATH: "/opt/harbor/cpython312/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
   ```

2. **The verifier needs `uv`.** SWEBench `tests/test.sh` runs
   `uv run parser.py` to compute the reward. `uv` isn't on the container PATH
   by default → `uv: command not found` → reward 0 even when the agent solved
   the task. **Fix:** copy `uv` into the overlay's `opt/harbor/cpython312/bin/`
   (which is on PATH via fix #1):
   ```bash
   cp ~/3p/harbor_bootstrap_overlay/usr/local/bin/uv \
      ~/3p/harbor_bootstrap_overlay/opt/harbor/cpython312/bin/uv
   ```

3. **The overlay's `installed-agent` can be stale.** The `.pth` files point at
   `/installed-agent/nemo_oo_agents/packages/nemo-oo-agents-benchmarks/src`. If
   that tree predates the agent you're using (e.g. `swebench/todo` from MR !320),
   the runner errors `Unknown agent_type: 'swebench/todo'` and writes no patch.
   **Fix:** refresh the agent source in the overlay's `installed-agent` to match
   the repo (at minimum, copy the new agent file + the updated
   `agents/__init__.py`).

Symptom triage: `find <run>/ -path '*/verifier/reward.txt' -exec cat {} \; | sort | uniq -c`
all-zero → check `<task>/agent/nemo_oo_agents_benchmarks.log` (agent_type error)
and `<task>/verifier/test-stdout.txt` (uv error / hatchling error).
