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

## System-specific Apptainer settings (this machine)

These settings apply on the current host and are already baked into every
`*_baseline.yaml` in this directory. If you move to a different machine,
check them again.

```yaml
environment:
  type: apptainer
  kwargs:
    apptainer_binary: apptainer
    apptainer_fakeroot: false          # IPC namespace unavailable without CAP_SYS_ADMIN
    apptainer_image_cache_dir: ~/.cache/harbor/sif   # reuse pulled SIFs across runs
    env_passthrough: "NVIDIA_INTERNAL_API_KEY,NEMO_OO_AGENTS_GIT_URL,NEMO_OO_AGENTS_GIT_REF"
```

**`apptainer_fakeroot: false`** — The default is `true`, which requires IPC
namespace support (`CAP_SYS_ADMIN`). On this host that fails with
`Failed to create ipc namespace: ipc namespace requires privileges`. Always
set `false` here.

**`apptainer_image_cache_dir`** — Without this, each trial pulls the Docker
image to a throwaway temp dir. With it, the SIF is written once and reused.
Put pre-built SIFs here too (e.g. `dabstep.sif`).

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
| LoCoMo | `~/.cache/locomo/locomo10.json` | Auto-downloaded from GitHub |
| MemBench | wherever `--data-dir` points | Google Drive (manual, one-time) |

Auto-downloads go to `~/.cache/<benchmark>/`. Manual downloads go wherever
the user puts them and are passed via CLI flag. Never embed raw data in the
repo or generate tasks with external retrieval at runtime.

### Verified smoke test results

| Benchmark | Method | Model | Tasks | Result |
|-----------|--------|-------|-------|--------|
| LoCoMo single-hop | eval_pipeline debug | openai/gpt-4o | 5 | 4/5 = 80% (F1) |
| LoCoMo single-hop | Harbor Apptainer | bedrock claude-sonnet-4-5 | 3/5 completed | mean F1=0.87, all pass |
| DABStep | Harbor Apptainer | bedrock claude-sonnet-4-5 | 5 | 1/5 (baseline, no pipeline) |
| MemBench | — | — | — | pending data download |
