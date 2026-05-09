# Terminal Bench 1.0 Evaluation — Harbor Setup

Instructions for running Terminal Bench 1.0 (241 tasks) locally via Harbor + Apptainer,
on a standard x86\_64 machine or an aarch64 machine with QEMU emulation.

## Prerequisites

1. **Harbor** — clone the NVIDIA harbor repo and install:
   ```bash
   git clone ssh://git@gitlab-master.nvidia.com:12051/dl/JoC/competitive_evaluation/core_evals_frameworks/harbor.git 3p/harbor-nemo
   uv tool install --editable 3p/harbor-nemo
   ```
2. **Apptainer** ≥ 1.4.0
3. **API keys** in `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   NEMO_OO_AGENTS_GIT_URL=https://oauth2:<PAT>@gitlab-master.nvidia.com/...
   ```

## One-Time Setup (x86\_64)

### 1. Build task SIF cache

```bash
# Pull the shared base SIFs (ubuntu-24-04, python-3-13)
bash util/harbor/pull_base_sifs.sh

# Build task-specific SIFs (requires sudo + apptainer)
python util/harbor/build_terminal_bench_sifs.py
```

### 2. Build the bootstrap overlay

The overlay provides Python + uvicorn/fastapi to task containers that ship no Python,
so Harbor's sidecar server can start.

```bash
bash util/harbor/build_bootstrap_overlay.sh ~/3p/harbor_bootstrap_overlay
```

This builds an amd64 Python 3.11 environment with uvicorn/fastapi at `/opt/harbor/`.
It also installs pure-Python wheels into `/opt/harbor/pylib/` so that containers
with a system Python but no pip can still import uvicorn (no root required at runtime).

### 3. Run

```bash
harbor run --config util/harbor/terminal_bench_baseline.yaml
```

Configs available under `util/harbor/`:

| Config | Agent |
|--------|-------|
| `terminal_bench_baseline.yaml` | CodeAct baseline |
| `terminal_bench_react_baseline.yaml` | ReAct baseline |

---

## One-Time Setup (aarch64 — QEMU amd64 emulation)

For ARM64 machines (e.g. galaxy-ts4-100), task containers are still amd64 but run
via QEMU binfmt emulation. Timeouts are multiplied 4–10x in the config.

### 1. Install Apptainer without root

```bash
conda install -c conda-forge apptainer squashfuse
```

Enable user namespaces (Ubuntu 24.04 blocks them by default):
```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
# Persist:
echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/99-apptainer.conf
```

### 2. Register QEMU binfmt handlers

```bash
sudo apt-get install -y qemu-user-static binfmt-support
```

### 3. Pull base SIFs (amd64 via QEMU)

```bash
bash util/harbor/pull_base_sifs.sh
```

### 4. Build task SIFs (no sudo)

```bash
python util/harbor/build_terminal_bench_sifs_nosudo.py
```

Same as the standard script but omits `sudo` from the `apptainer build` call.
Expects apptainer to run as the current user (conda-forge install or setuid setup).

### 5. Build the bootstrap overlay

```bash
bash util/harbor/build_bootstrap_overlay.sh ~/3p/harbor_bootstrap_overlay
```

Uses `docker build --platform linux/amd64` to produce the amd64 Python environment.
Requires docker to be available (or sudo access). Also installs pure-Python
uvicorn/fastapi wheels into `/opt/harbor/pylib/` for containers with system Python
but no pip.

### 6. Run

```bash
harbor run --config util/harbor/terminal_bench_galaxy.yaml
```

---

## Known Issues

- **5 tasks crash harbor if not excluded**: `conda-env-conflict-resolution`,
  `extract-safely`, `simple-sheets-put`, `simple-web-scraper`,
  `tmux-advanced-workflow` have no `Dockerfile` and no `docker_image` in
  `task.toml`. Harbor raises `ValueError` on these, which propagates and kills
  the whole run. Either exclude them from the task dir or fix their `task.toml`.
- **`chem-rf`** has an unparseable Dockerfile FROM line — will fail with an error
  but does not crash the run.
- Containers without Python need the bootstrap overlay with uvicorn (see above).
  If you see `ModuleNotFoundError: No module named 'uvicorn'` in trial logs,
  rebuild the overlay and ensure `apptainer_bootstrap_overlay` is set in the config.
- Use `override_memory_mb: 4096` to prevent watchdog OOM kills on memory-heavy tasks.
