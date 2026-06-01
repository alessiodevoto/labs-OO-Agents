# Benchmark Results — Helpers Beat Prompts (nemo-oo-agents)

Full benchmark campaign across Terminal Bench 1.0, Terminal Bench 2.0, and
SWE-bench Verified, evaluating three agent strategies × three models.

Models: `sonnet` = bedrock-claude-sonnet-4-5-v1, `opus` = bedrock-claude-opus-4-6,
`ultra` = nvidia/nvidia/nemotron-3-ultra-preview. All runs Docker mode, post-MR!317.

## Terminal Bench 1.0 (241 tasks, full 3×3 matrix)

Pass rate (passed / scored; ~30–60 infra exceptions per run from py3.6-base tasks
that fail agent setup — the same set across agents):

| Agent | sonnet | opus | ultra |
|-------|--------|------|-------|
| baseline (CodeAct) | 45.7% (85/186) | **63.4% (135/213)** | 46.2% (96/208) |
| specialized (terminal-bench-1) | 48.9% (88/180) | 61.5% (131/213) | 38.5% (80/208) |
| react | 40.5% (83/205) | 27.4% (58/212) | 38.9% (81/208) |

**Best: baseline × opus = 63.4%.** opus dominates; ultra mid-pack; the specialized
agent helps sonnet (+3pp) but the baseline CodeAct agent is strongest with opus.


> **Infra exceptions (TB1):** 28-61 per run, consistent across all three models. These are **task timeouts** on heavy build/install tasks (kernel/initramfs builds, install-windows-xp, leelachess0-pytorch-conversion, mteb-eval, magsac-install) that exceed harbor's per-task wall-clock limit. This is a *different* failure mode from TB2's infra (the cp312 `python3: command not found` / exit-127 bug, since fixed): verified `python3-127=0` across all TB1 runs -- the agent runs fine, the task is just slow.

## Terminal Bench 2.0 (89 tasks, baseline agent)

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

| Model | Pass rate | Scored | Infra | Tokens (in / out) |
|-------|-----------|--------|-------|-------------------|
| **opus** | **75.4% (376/499)** | 499/500 | 1 | 470M / 3.9M |
| sonnet | 67.9% (169/249)* | 251/500 | 2 | 395M / 2.7M |
| ultra | 60.2% (301/500) | 500/500 | 0 | 1.65B+ / 11.2M+ |

\*sonnet is partial (251/500) — ipp2-2047 hung mid-run. **ultra was completed to
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

Reproducible on a fresh Colossus machine via `git pull` + `util/harbor/setup_colossus.sh`
+ `build_venv_tarballs.sh`. See `util/harbor/README.md`.


## Complete results matrix (final)

| Benchmark | Agent | sonnet | opus | ultra |
|-----------|-------|--------|------|-------|
| TB1 (241) | baseline | 45.7% | **63.4%** | 46.2% |
| TB1 (241) | specialized | 48.9% | 61.5% | 38.5% |
| TB1 (241) | react | 40.5% | 27.4% | 38.9% |
| TB2 (89)  | baseline | 40.4% | **64.4%** | 34.8% |
| SWEBench (500) | swebench/todo | 67.9%* | **75.4%** | 60.2% |

\* SWEBench sonnet partial (251/500); TB2 numbers are honest full-89 runs (cp312 fix);
SWEBench sonnet partial (251/500). All other cells are full runs. opus is the
strongest model across all three benchmarks.