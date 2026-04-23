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
| MemBench | `~/.cache/membench/` | rsync from DFW or Google Drive (see below) |

**MemBench data** (~1.1 GB) is available via rsync from rcabral's DFW homedir:

```bash
rsync -av rcabral@cw-dfw-cs-001-login-02.cw-dfw-cs-001.hpc.nvidia.com:~/.cache/membench/ ~/.cache/membench/
```

Or download from Google Drive and unzip to `~/.cache/membench/`:
`https://drive.google.com/file/d/112Zraj4pTPH4Idph6i1uMOLA_LPFdGr0/view`

Auto-downloads go to `~/.cache/<benchmark>/`. Never embed raw data in the
repo or generate tasks with external retrieval at runtime.

### Verified smoke test results

| Benchmark | Method | Model | Tasks | Result |
|-----------|--------|-------|-------|--------|
| LoCoMo single-hop | eval_pipeline debug | openai/gpt-4o | 5 | 4/5 = 80% (F1) |
| LoCoMo single-hop | Harbor Apptainer | bedrock claude-sonnet-4-5 | 3/5 completed | mean F1=0.87, all pass |
| DABStep | Harbor Apptainer | bedrock claude-sonnet-4-5 | 5 | 1/5 (baseline, no pipeline) |
| MemBench | — | — | — | pending data download |
