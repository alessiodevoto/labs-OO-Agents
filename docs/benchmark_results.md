# Benchmark Results — Helpers Beat Prompts (nemo-oo-agents)

Full benchmark campaign across Terminal Bench 1.0, Terminal Bench 2.0, and
SWE-bench Verified, evaluating three agent strategies × three models.

Models: `sonnet` = bedrock-claude-sonnet-4-5-v1, `opus` = bedrock-claude-opus-4-6,
`ultra` = nvidia/nvidia/nemotron-3-ultra-preview. All runs Docker mode, post-MR!317.

## Terminal Bench 1.0 (241 tasks, full 3×3 matrix)

Pass rate (passed / scored). **react is the baseline-to-beat**; the Δ column shows
how each agent compares to react on the same model:

| Agent | sonnet | Δ vs react | opus | Δ vs react | ultra | Δ vs react | infra |
|-------|--------|-----------:|------|-----------:|-------|-----------:|------:|
| baseline (CodeAct) | 45.7% (85/186) | +5.2 | **63.4% (135/213)** | **+36.0** | 46.2% (96/208) | +7.3 | 28–56 |
| specialized (terminal-bench-1) | 48.9% (88/180) | +8.4 | 61.5% (131/213) | +34.1 | 38.5% (80/208) | −0.4 | 28–61 |
| react (baseline-to-beat) | 40.5% (83/205) | — | 27.4% (58/212) | — | 38.9% (81/208) | — | 29–36 |

**Best: baseline × opus = 63.4% (+36.0pp over react).** Both the baseline CodeAct
and the specialized agent beat react on sonnet and opus by a wide margin; on opus
the gap is huge (react collapses to 27.4%). On ultra the picture is mixed — baseline
beats react (+7.3) but the specialized agent is flat (−0.4).

> **Infra exceptions (TB1) — root cause (corrected):** 28–61 per run. The dominant
> cause (~70–85%) is **agent-setup failures**, *not* task timeouts. TB1 task
> containers ship varied Python versions (`cp36`, `cp310`, `cp311`, `cp312`,
> `cp313`). The agent-setup fast-path only had a venv tarball for `cp312` (with a
> `cp313`→`cp312` symlink), so `cp311`/`cp310`/`cp36` containers fell to a slow path
> that pip-installs `cp312`-only wheels → ABI mismatch → `exit 1/2/127` during setup.
> This is the **same family** as the TB2 cp312 bug, just broader. Only **~1 task per
> run** is a genuine agent solve-timeout; ~5 are task-container exit-2; ~2–6 are
> verifier timeouts. **Doubling the per-task timeout would not fix the bulk** — the
> setup failures fail in seconds. **Fix** (harbor `a61ddaa4`): on x86_64, use the
> `/opt/harbor/cpython312` overlay interpreter for PYVER (mirroring the existing
> aarch64 path) so *every* container resolves to `cp312` and the fast-path always
> wins. Validated: PYVER → `cp312` on x86_64 with the overlay present. The ~20–50
> setup-failure tasks per run were re-run with this fix to confirm recovery.
>
> **Validation (baseline-opus, the 28 infra tasks):** before the fix all 28 were
> infra (0 scored); after the fix **17 scored / 6 passed** and **0 cp311-wheel-mismatch
> setup failures remained** (was 13). The residual ~11 failures are a *different* class —
> ~5 task-container-exit-2 (the task's own image fails to start, unrelated to agent
> setup), ~1 agent solve-timeout, ~1 verifier-timeout, and a few exit-1 inside the agent
> *solve* (not setup). So the fix recovers the bulk of TB1 infra; the remainder is not the
> Python-version bug and would not be fixed by a longer timeout either.

## Terminal Bench 1.0 (241 tasks) — CONVERGED (post-cp312-fix reruns folded in)

After the cp312 agent-setup fix (harbor `a61ddaa4`), each run's unscored tasks were
re-run and folded in, so every cell now evaluates a near-identical ~224–230-task set
(residual infra = the deterministic task-image container-exit-2 set + a few solve/verifier
timeouts, consistent across runs). Pass rate over scored:

| Agent | sonnet | opus | ultra |
|-------|--------|------|-------|
| baseline (CodeAct) | 45.4% (103/227) | **62.2% (143/230)** | 44.6% (100/224) |
| specialized (tb-1) | 51.5% (117/227) | 60.9% (140/230) | 38.5% (80/208)* |
| react | 39.8% (90/226) | 27.5% (63/229) | 38.9% (81/208)* |

\*specialized-ultra and react-ultra were not re-run (they live on z590-0140, busy with
the react×TB2 runs) — shown at their original pre-fix values.

**vs the original (pre-fix, survivorship-inflated) numbers:** the converged rates are
slightly *lower* over a *larger* scored set (e.g. baseline-opus 63.4%→62.2% but over
230 vs 213 tasks; specialized-sonnet 48.9%→51.5% — sonnet recovered the most). Infra
dropped from 28–61/run to **11–17/run** (the residual deterministic task-image failures).
The ordering is unchanged: **opus > sonnet ≈ ultra for baseline; helper agents beat react.**


## Terminal Bench 2.0 (89 tasks, baseline agent)

> react/specialized were not run on TB2 — only the baseline agent. The react-vs-baseline comparison is TB1-only.

| Model | Pass rate | Scored | Infra |
|-------|-----------|--------|-------|
| **opus** | **64.4% (56/87)** | 87/89 | 2 |
| sonnet | 40.4% (36/89) | 89/89 | 0 |
| ultra | 34.8% (31/89) | 89/89 | 0 |

These are clean full-89-task runs after the cp312-PATH fix (which eliminated the
~28 `python3: command not found` infra failures). The earlier scored-subset rates
(opus 75.9%, ultra 50.9%, sonnet 13.9%) were inflated by survivorship — the ~28
failing tasks were disproportionately the harder ones, so the honest pass rates
over all 89 tasks are lower.

## SWE-bench Verified (500 tasks, swebench/todo agent, Docker)

> SWE-bench uses the dedicated `swebench/todo` agent for all models — there is no react/baseline split here, so no Δ-vs-react column.

| Model | Pass rate | Scored | Infra | Tokens (in / out) |
|-------|-----------|--------|-------|-------------------|
| **opus** | **75.4% (376/499)** | 499/500 | 1 | 470M / 3.9M |
| sonnet | 66.5% (330/496) | 496/500 | 4 | 303M / 2.2M |
| ultra | 60.2% (301/500) | 500/500 | 0 | 1.65B+ / 11.2M+ |

sonnet was completed to **496/500** by resuming the 245 unscored tasks via the same `DatasetConfig.task_names` recipe. **ultra was completed to
500/500** by resuming the 93 unscored tasks via `DatasetConfig.task_names`
filtering after recovering the machine with a Colossus SNMP reboot. (sonnet's
remaining ~249 can be finished the same way if needed.)

**opus is the standout at 75.4%** — a strong result for the todo-driven agent.
ultra is competitive (60%) but burns **~3.5× opus's input tokens** (1.65B vs 470M);
a per-task cost flag given its long reasoning traces.

## Infrastructure notes & fixes (all on `main`)

Three silent SWE-bench-Docker bugs (each produced reward 0 for every task):
1. **cp312 PATH** — eval containers ship conda python3.11; the agent-setup probe
   picked it (`PYVER=cp311`), found no matching venv tarball, fell back to a pip
   editable install that fails (`Cannot import hatchling.build`). Fix: prepend
   `/opt/harbor/cpython312/bin` to the container `PATH` env.
2. **uv missing** — the verifier's `tests/test.sh` runs `uv run parser.py`; `uv`
   wasn't on PATH → `uv: command not found` → reward 0 even on solved tasks. Fix:
   bundle `uv` into the overlay's `cpython312/bin`.
3. **stale installed-agent** — the overlay's `.pth`-imported agent src predated
   MR!320 → `Unknown agent_type: swebench/todo`. Fix: refresh installed-agent from repo.

The same cp312-PATH bug caused TB2's `python3: command not found` (exit 127)
infra exceptions. The ultra runs were initially misdiagnosed as "no key access";
the real bug was the `openai/` model-name prefix (HTTP 401 vs 200).

4. **TB1 agent-setup Python-version mismatch** (harbor `a61ddaa4`) — TB1 task
   containers ship varied Python (`cp36`/`cp310`/`cp311`/`cp312`/`cp313`); the
   x86_64 agent-setup path used the *container* python for `PYVER`, so non-cp312
   containers found no matching venv tarball and fell to a slow path that
   pip-installs cp312-only wheels → ABI mismatch → `exit 1/2/127` during setup
   (~20–50 tasks/run, ~70–85% of TB1 infra). Fix: on x86_64 use the
   `/opt/harbor/cpython312` overlay interpreter for `PYVER` (mirroring the
   aarch64 path), so every container resolves to `cp312` and the fast-path tarball
   always applies. This is *not* a timeout — doubling the per-task timeout would
   not recover these (they fail in seconds).

Reproducible on a fresh Colossus machine via `git pull` + `util/harbor/setup_colossus.sh`
+ `build_venv_tarballs.sh`. See `util/harbor/README.md`.


## Complete results matrix (final)

| Benchmark | Agent | sonnet | opus | ultra |
|-----------|-------|--------|------|-------|
| TB1 (241) | baseline | 45.7% | **63.4%** | 46.2% |
| TB1 (241) | specialized | 48.9% | 61.5% | 38.5% |
| TB1 (241) | react | 40.5% | 27.4% | 38.9% |
| TB2 (89)  | baseline | 40.4% | **64.4%** | 34.8% |
| SWEBench (500) | swebench/todo | 66.5% | **75.4%** | 60.2% |

\* All SWEBench cells are full 500-task runs (sonnet completed via task-id resume: 330/496 = 66.5%, 4 infra). TB2 numbers are honest full-89 runs (cp312 fix). opus is the strongest model across all three benchmarks.