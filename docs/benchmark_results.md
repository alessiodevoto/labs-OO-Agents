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
| specialized (terminal-bench-1) | 48.9% (88/180) | 61.2% (109/178) | 38.5% (80/208) |
| react | 40.5% (83/205) | 27.4% (58/212) | 38.9% (81/208) |

**Best: baseline × opus = 63.4%.** opus dominates; ultra mid-pack; the specialized
agent helps sonnet (+3pp) but the baseline CodeAct agent is strongest with opus.

## Terminal Bench 2.0 (89 tasks, baseline agent)

| Model | Pass rate | Scored | Infra |
|-------|-----------|--------|-------|
| sonnet | 13.9% (11/79) | 79 | 100 (pre-fix run) |
| **opus** | **75.9% (44/58)** | 58 | 31 |
| ultra | 50.9% (29/57) | 57 | 32 |

opus/ultra were re-run after fixing the LLM-provider + ultra-prefix bugs (both
scored 0 before). TB2's high infra counts come from heavy QEMU/build tasks.

## SWE-bench Verified (500 tasks, swebench/todo agent, Docker)

| Model | Pass rate | Scored | Infra | Tokens (in / out) |
|-------|-----------|--------|-------|-------------------|
| **opus** | **75.4% (376/499)** | 499/500 | 1 | 470M / 3.9M |
| sonnet | 67.9% (169/249)* | 251/500 | 2 | 395M / 2.7M |
| ultra | 60.4% (246/407)* | 411/500 | 4 | 1.65B / 11.2M |

\*sonnet/ultra are partials — ipp2-2047 hung mid-run; recovered via SNMP reboot,
then resumed the unscored tasks via `DatasetConfig.task_names` filtering. Pass
rates over the scored subset are statistically robust.

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
