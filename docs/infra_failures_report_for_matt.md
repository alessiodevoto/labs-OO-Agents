# Benchmark Infrastructure Failures — Field Report

*For Matt, designing a benchmark-execution system at scale. Distilled from the
"Helpers Beat Prompts" campaign: TB1 (241 tasks) × 3 agents × 3 models, TB2 (89)
× 3 models, SWEBench Verified (500) × 3 models, run on Colossus bare-metal via
Harbor (Apptainer/Docker).*

## TL;DR for system design

Every silent-zero we hit came from **one of two root classes**:
1. **Agent-environment Python/ABI mismatch** — the eval container's interpreter
   ≠ the interpreter our prebuilt venv/wheels target.
2. **Missing tool/credential on the in-container PATH/env** — a binary or key the
   agent or verifier needs at runtime that wasn't forwarded/installed.

A scale system should treat both as **first-class preflight checks** that fail
loud *before* a 500-task run burns hours producing reward=0.

---

## The failures, each with symptom → root cause → fix → design lesson

### 1. Model-name provider prefix (silent 401, looked like "no model access")
- **Symptom:** ultra runs scored 0 everywhere; error "key not allowed to access model".
- **Root cause:** model name `openai/nvidia/nvidia/nemotron-3-ultra-preview` — the
  `openai/` provider prefix made the gateway reject it (HTTP 401). Correct name
  (`nvidia/nvidia/...`) returns 200. The key *did* have access; the prefix lied.
- **Fix:** drop the `openai/` prefix in the LLM config.
- **Design lesson:** **preflight every (model, key) pair with a 1-token live call**
  and assert HTTP 200 + echoed model id before launching. A 401 misread as
  "no access" cost us a full misdiagnosis cycle.

### 2. SWEBench is x86_64-only — don't try arm64
- **Symptom:** considered running SWEBench on arm64 (Galaxy) hardware.
- **Root cause:** 496/500 SWEBench-Verified instances are in `USE_X86_PY` (compiled
  C-extension deps); only ~6% of images have arm64 builds; the Harbor adapter
  force-rewrites `arm64→x86_64`.
- **Fix:** run SWEBench on x86_64 hosts in Docker mode.
- **Design lesson:** **bake arch constraints into the scheduler.** A task suite
  should declare required arch; the orchestrator should refuse to place it on
  incompatible hardware rather than emulate (QEMU TCG is 10–50× slower).

### 3. cp312 PATH — `python3: command not found` / `Cannot import hatchling.build`
- **Symptom:** SWEBench reward=0 on every task; TB2 ~28 exit-127 infra/run.
- **Root cause:** eval containers ship conda **python3.11**; our agent-setup probe
  picked it (`PYVER=cp311`), found no matching prebuilt venv tarball, and fell back
  to a pip editable install that fails (`hatchling.build` missing). On TB2 the same
  containers had no `python3` on the agent-staging PATH at all → exit 127.
- **Fix:** prepend `/opt/harbor/cpython312/bin` to the container `PATH`.
- **Design lesson:** **pin and inject a known-good interpreter into the container
  env; never trust the container's default `python3`.** Make "which interpreter
  will the agent use" an explicit, logged decision, not an implicit `which python3`.

### 4. `uv` missing in the overlay (verifier failure, not agent failure)
- **Symptom:** reward=0 even on tasks the agent *solved correctly*.
- **Root cause:** the SWEBench verifier's `tests/test.sh` runs `uv run parser.py`;
  `uv` wasn't on PATH → `uv: command not found` → no reward written.
- **Fix:** bundle `uv` into the overlay's `cpython312/bin`.
- **Design lesson:** **the verifier has its own runtime dependency closure,
  separate from the agent's.** Preflight the verifier env too. A solved task
  scoring 0 because the *scorer* couldn't run is the most expensive failure mode —
  it silently corrupts the headline number.

### 5. Stale installed-agent overlay (`Unknown agent_type`)
- **Symptom:** `Unknown agent_type: swebench/todo` after adding a new agent.
- **Root cause:** the overlay's `.pth`-imported agent *source snapshot* predated the
  new agent's merge.
- **Fix:** refresh `installed-agent` from the repo after agent-code changes.
- **Design lesson:** **content-address the agent code** (commit SHA in the run
  manifest) and verify the deployed overlay matches the intended SHA at launch.
  We now record the agent commit (e.g. swebench/todo = `53d6a5bf`) per run.

### 6. Container Python-version diversity (the big TB1 infra source)
- **Symptom:** 28–61 "infra exceptions" per TB1 run (no reward.txt).
- **Root cause:** TB1's 241 task images ship **cp36/cp310/cp311/cp312/cp313**. Our
  agent-setup fast-path only had a prebuilt venv tarball for cp312 (with a
  cp313→cp312 symlink); other versions fell to a slow path that pip-installs
  cp312-only wheels → ABI mismatch → exit 1/2/127 *during agent setup*. This is the
  same class as #3 but broader. **~70–85% of TB1 "infra" was this, NOT timeouts.**
- **Fix:** on x86_64, use the `/opt/harbor/cpython312` overlay interpreter for the
  agent (mirroring the existing aarch64 path) so *every* container resolves to
  cp312 regardless of its shipped python. **Validated:** a 28-task rerun that was
  100% infra before → 17 scored / 6 passed, 0 cp311-wheel failures after.
- **Design lesson:** **the agent's runtime must be hermetic and arch/version-pinned,
  injected over the task container — never assembled from the task container's
  interpreter.** Ship one interpreter + one venv, mount it, and force the agent to
  use it. Don't pip-install at task time (slow, flaky, ABI-fragile).

### 7. LLM-provider config not found inside the container
- **Symptom:** `LLM Provider NOT provided` transient on opus/ultra.
- **Root cause:** harbor writes `llm_config.yaml` to
  `/installed-agent/nooa/`, but the agent looked elsewhere; and the model
  had to be present in harbor's hardcoded LLM-config string.
- **Fix:** set `NEMO_OO_LLM_CONFIG` to the path harbor writes + list the model.
- **Design lesson:** **config discovery inside the sandbox must be explicit and
  logged.** Env-var-driven config path, asserted-present at startup.

### 8. Partial-run recovery (machine hung mid-benchmark)
- **Symptom:** ipp2-2047 hung at 407/500 SWEBench tasks; harbor's `--job-name`
  resume goes *idle* (won't re-run unscored tasks).
- **Fix:** recover the machine (`colossus bm reboot`, SNMP power-cycle), then
  **filter by task id**: compute unscored = all − {has reward.txt}, write a fresh
  config with `datasets[].task_names = [unscored]`, run into a new jobs_dir, merge
  results. Validated 3× (ultra, sonnet SWEBench; TB1 reruns).
- **Design lesson:** **make runs resumable by construction.** Per-task idempotent
  output dirs + a "what's unscored" query + task-id-filtered re-dispatch. Don't
  rely on a monolithic job-name resume. Also: **checkpoint scoring incrementally**
  (we lost no scored work because reward.txt is per-task, written as we go).

### 9. Config drift across near-identical per-arm files (re-broke #3 + #4, and silently ran the WRONG agent)

- **Symptom:** a fresh 3-model run (opus/sonnet/ultra) where **every task scored
  reward=0** — *and*, separately, the run wasn't even exercising the agent variant
  it was supposed to be measuring.
- **Root cause:** the per-arm configs (`swebench_mr359_{opus,sonnet,ultra}.yaml`)
  were hand-written as a "lean" copy of the working config and **silently dropped
  the entire `environment.env` block** that the proven config had. That single
  omission re-triggered **two** already-solved failures at once:
    - missing `PATH=/opt/harbor/cpython312/bin` → verifier's `uv run` not found →
      reward=0 for all 500 tasks (regression of #3 + #4); and
    - missing `SHELL_VARIANT=3` → the agent's shell-selector **fell back to its
      default variant (v1)** instead of the v3 under test. The harness *tagged*
      the run as the intended experiment while *executing* a different code path.
- **Fix:** restore the full `env` block; **generate** the per-arm configs from one
  source of truth instead of hand-copying (a ~80-line generator that emits all
  arms with a `# GENERATED — DO NOT EDIT` header and a comment on *why* each shared
  key exists). Verified with a 1-task smoke (reward flips 0→1, and the trial config
  shows `SHELL_VARIANT=3`) before committing the 1500-task relaunch.
- **Design lesson:** **never hand-maintain N near-identical configs.** Differences
  that matter (an interpreter PATH, a behavior-selecting env var) hide in the diff
  noise between 200-line YAMLs. Emit them from a single source, or use a base+matrix
  sweep. And **assert the run is what it claims to be**: the experiment label
  (`SHELL_VARIANT=3`) must be *checked against actual runtime behavior*, not just
  set — a silent fallback that still tags the intended variant corrupts the data
  worse than a crash would, because the numbers look plausible.

---

## Cross-cutting recommendations for a scale system

1. **Preflight gate before every campaign** (fail loud, cheap, < 1 min):
   - (model, key) → live 1-token call, assert 200 + echoed model id.
   - agent interpreter resolves to the pinned version inside a sample container.
   - verifier dependency closure present (`uv`, `python3`, scorer imports).
   - deployed agent SHA == intended SHA.
2. **Hermetic agent runtime**, arch+version-pinned, mounted over the task container.
   Never assemble it from the task container's interpreter at task time.
3. **Per-task idempotent outputs + a task-id resume path** as the *only* recovery
   mechanism. Job-name resume is a trap.
4. **Distinguish failure phases in telemetry**: agent-setup vs container-start vs
   agent-solve-timeout vs verifier-timeout vs verifier-error. We initially
   mislabeled ~70–85% of TB1 setup failures as "timeouts" because we grepped the
   word "timeout" in result.json (a config field). **Read the exception's stack
   phase, not keywords.** This single distinction changes the fix (and whether a
   longer timeout would even help — usually it wouldn't).
5. **Token + infra accounting per run**, always reported alongside pass rate.
   Survivorship bias is real: scored-subset pass rates were inflated 5–15pp vs
   honest full-suite rates because the excluded (infra-failed) tasks were the hard
   ones.
6. **A stable set of task images will deterministically fail to even start**
   (heavy QEMU/VM/build tasks: install-windows-xp, build-initramfs-qemu, etc.).
   These are task-definition bugs, agent-independent, and consistent across all
   model/agent runs. A scale system should **quarantine known-bad task images**
   (track them, exclude or fix at the suite level) rather than counting them as
   per-run agent infra noise.

## Appendix: the numbers these fixes unlocked
- TB1 (241): baseline opus **63.4%**, ultra 46.2%, sonnet 45.7%.
- TB2 (89, clean): opus **64.4%**, sonnet 40.4%, ultra 34.8%.
- SWEBench Verified (500), baseline ShellTools-v1 agent: opus **75.4%**, sonnet 66.5%, ultra 60.2%.
- SWEBench Verified (500), ShellTools3 agent (after fixing #9): opus **76.2%**,
  sonnet 66.6%, ultra 60.0% — a **wash** vs the v1 baseline (all deltas within
  ~0.2%/task noise). Worth noting because the *mid-run partial* looked like a win
  (+1–1.4pp) and only converged to flat at completion: **never call a tooling
  result off a partial run** — early scored tasks aren't a random sample.
- ultra burns ~3.3× opus's input tokens on SWEBench (1.65B vs 494M) — a cost flag
  for reasoning models that a scale system should budget/track explicitly.

## Appendix B: harbor patches + oo-agents-side config (what to build for independence)

**Patches we made to harbor (the eval harness):**
- `adapters/swebench/template/tests/test.sh`: prepend `/opt/harbor/cpython312/bin`
  to the **verifier-phase** PATH so `uv` resolves regardless of the run config
  (defense-in-depth for #4/#9). Commit `d4a45ac3`.
- harbor `a61ddaa4`: on x86_64, use the `/opt/harbor/cpython312` overlay interpreter
  for agent setup so every container's Python resolves to cp312 (#3/#6).
- `.pth`-based agent install (no `pip install -e` at task time) to avoid ABI/race
  failures under concurrency (#6).

**oo-agents-side configuration required to run on a fresh Colossus box:**
- A **bootstrap overlay** mounted into every container at `/opt/harbor` +
  `/installed-agent`, containing: pinned `cpython312` interpreter, a prebuilt venv
  tarball with `.pth` files, `uv`, and the agent source snapshot at the intended SHA.
- Per-run config `environment.env` MUST set (this is the block whose omission caused
  #9): `PATH` (with cpython312/bin first), `NEMO_OO_LLM_CONFIG`
  (=`/installed-agent/nooa/llm_config.yaml`), `NEMO_OO_AGENTS_GIT_URL`,
  `OTLP_ENDPOINT`, the API key, and any agent-behavior selector (e.g. `SHELL_VARIANT`).
- Configs are **generated from one source of truth**, never hand-copied per arm.
- Model names carry **no `openai/` prefix** for the ultra endpoint (#1).
- SWEBench runs in **Docker mode on x86_64** only (#2); SIF/image cache pre-pulled.

**Minimum preflight before any 500-task launch** (each < 1 min, fail loud):
1. `(model, key)` 1-token live call → assert 200 + echoed model id.
2. In a sample container: agent interpreter resolves to cp312; `uv` + `python3`
   on PATH; `import nooa` succeeds.
3. Deployed agent SHA == intended SHA.
4. **Behavior assertion:** the agent's runtime self-report (e.g. the active shell
   class in its system prompt) matches the experiment label — catches silent
   fallbacks like #9 before they corrupt a run.
