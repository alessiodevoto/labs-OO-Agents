# Three-package split + cleanup (gl-131 follow-on)

Branch `feat/gl-131-two-package-split` shipped the two-package version (core + benchmarks). User wants to extend to three packages **in this same MR** so end users absorb a single round of breaking changes:

- `uv add nemo-oo-agents` — core (framework + context_blocks + unifiedllm)
- `uv add nemo-labs-oo-agents-cli` — CLI + TUI (`nemo` command)
- `uv add nemo-labs-oo-agents-cli[tui]` — same plus np/pd/plotly/scipy/sklearn for the LLM REPL
- `uv add nemo-labs-oo-agents-cli[term]` — same plus web-terminal (uvicorn/ptyprocess/fastapi)
- `uv add nemo-oo-agents-benchmarks` — eval harness

Plus opportunistic cleanup of detritus accumulated during the prior phases.

## Pre-flight review findings (incorporated below)

A sub-agent audit caught four blockers and several mitigations:

- **B1**: `tests/integration/test_sandbox_integration.py` is fully CLI-internal (7 imports of `nooa_cli.commands.sandbox` private helpers, nothing else) — move with CLI, not "stays in root". `test_quick_wins.py::TestTemplateCommand` is CLI-targeted but the rest of the file is core tests, and the CLI test works fine via `CliRunner` once CLI is installed in the dev workspace, so keep the file in root.
- **B2**: CLI deps list omits `pydantic` (used directly in `src/nooa_cli/tui/agent.py:10` and `tui/models.py:7`). Currently transitive via core; declare explicitly to avoid fragility.
- **D1**: Existing `.gitlab-ci.yml:551` sed pattern uses `"nooa>=..."` (underscore). The CLI pyproject's dep on core must use the same underscore form so the version-bump regex matches.
- **X5**: `[tool.coverage.run] source` currently includes `src/nooa_cli`; after the move, replace with `packages/nemo-labs-oo-agents-cli/src/nooa_cli` so coverage on CLI tests still gets recorded.
- **B4**: `examples/tools_agent_tui/example.py` imports CLI at module top — add a docstring note that it requires `pip install nemo-labs-oo-agents-cli`.
- **E2**: `THIRD_PARTY_NOTICES.md:24` has an `## Optional: [tui]` section listing TUI deps and licenses — update wording to reference `nemo-labs-oo-agents-cli[tui]`.
- **F2**: When dropping `[project.scripts] nemo` from root pyproject, leave `trace-explorer` (it's `nooa.trace_explorer:main` — core).
- **D3**: `test-core` `rules.changes` should add `packages/nemo-labs-oo-agents-cli/**/*` so MRs that touch CLI also trigger the cross-cutting tests still housed in root `tests/`.
- **F1**: Add a 5th install scenario to verify `uv add "nemo-oo-agents-benchmarks[bigcodebench]"` resolves the 50-dep stack (post-relocation).

## Phase A — Cleanup (one commit)

Mechanical, all stand-alone.

1. **Lingering empty package dirs**
   - `packages/context-blocks/` and `packages/unifiedllm/` contain only `.ruff_cache/` (gitignored). Delete the directories.

2. **Dead extras in root pyproject.toml**
   - `[tui-optional]` — duplicate of `[tui]` with newer pins; **0** references in repo. Delete.
   - `[nemo-oo-agents-benchmarks]` — `["nemo-oo-agents-benchmarks"]` self-ref; redundant once benchmarks is its own published package and users `uv add` it directly. Delete.

3. **Misplaced extra**
   - `[bigcodebench]` (50+ scientific deps for "1140 BigCodeBench tasks") has **0** references outside its own pyproject definition. Move to `packages/nemo-oo-agents-benchmarks/pyproject.toml` as `[project.optional-dependencies] bigcodebench`. That's its real owner.

## Phase B — Move CLI source (commits with Phase C)

Source moves with `git mv` so blame survives.

```
git mv src/nooa_cli  packages/nemo-labs-oo-agents-cli/src/nooa_cli
git mv tests/cli               packages/nemo-labs-oo-agents-cli/tests/cli
```

Plus this CLI-internal test (per review B1):
```
git mv tests/integration/test_sandbox_integration.py  packages/nemo-labs-oo-agents-cli/tests/integration/test_sandbox_integration.py
```

Cross-cutting tests that genuinely touch *core+CLI* stay in root `tests/`:
- `tests/test_event_auto_registration.py:262` (one TUI events import in one fn; rest is event-registration test)
- `tests/integration/test_import_roundtrip.py:125` (one CLI import in an end-to-end pipeline)
- `tests/unit/test_quick_wins.py::TestTemplateCommand` (uses `CliRunner` against CLI; rest of file is core tests)

Their imports continue to work because `nooa_cli` is still importable when `nemo-labs-oo-agents-cli` is installed (which it is in dev).

### New `packages/nemo-labs-oo-agents-cli/pyproject.toml`

```toml
[project]
name = "nemo-labs-oo-agents-cli"
version = "0.2.0"  # lockstep with core
description = "CLI and TUI for nemo-oo-agents (`nemo` command, web terminal, agent REPL)"
license = {text = "Apache-2.0"}
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    # Underscore form so the CI version-bump sed regex matches (see .gitlab-ci.yml:551)
    "nooa>=0.2.0",
    "click>=8.1.0",
    "ipython>=9.12.0",
    "prompt-toolkit>=3.0.41,<3.1.0",  # was transitive via ipython; declare explicitly
    "rich>=13.0.0",                    # was transitive via litellm; declare explicitly
    "pydantic>=2.5.0",                 # used in tui/agent.py, tui/models.py
    "pyyaml>=6.0",
]

[project.scripts]
nemo = "nooa_cli:main"

[project.entry-points."nooa_tui.skills_dirs"]
sw_skills = "nooa_cli.tui:_get_sw_skills_dir"

[project.optional-dependencies]
# Pre-load scientific stack into the TUI agent's REPL execution namespace.
# Soft-imported via try/except — TUI works without these (LLM convenience only).
tui = [
    "numpy>=1.24.0",
    "pandas>=2.0.0",
    "plotly>=5.0.0",
    "scipy>=1.10.0",
    "scikit-learn>=1.3.0",
]
# Web-terminal mode (`nemo oo term`)
term = [
    "nemo-labs-oo-agents-cli[tui]",
    "uvicorn[standard]>=0.34.0",
    "ptyprocess>=0.7.0",
    "fastapi>=0.123.4",
]

[tool.uv.sources]
nemo-oo-agents = { workspace = true }

[build-system]
requires = ["uv_build>=0.9.11,<0.10.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = ["nooa_cli"]
```

### New `packages/nemo-labs-oo-agents-cli/README.md`

Brief — three sentences plus a usage block.

## Phase C — Rewire root pyproject.toml (commits with Phase B)

- Drop `click>=8.1.0` and `ipython>=9.12.0` from `[project] dependencies`.
- Drop `[project.scripts] nemo` line ONLY (keep `trace-explorer` — core).
- Drop `[project.entry-points."nooa_tui.skills_dirs"]`.
- Drop `[project.optional-dependencies]`: `tui`, `term`, `tui-optional`, `nemo-oo-agents-benchmarks`, `bigcodebench`.
- Add `packages/nemo-labs-oo-agents-cli` to `[tool.uv.workspace] members`.
- Update `[tool.uv.build-backend] module-name`: `["nooa", "nooa_cli"]` → `["nooa"]`.
- Update `[tool.pytest.ini_options]`:
  - `pythonpath`: keep `src`, add `packages/nemo-labs-oo-agents-cli/src`.
  - `testpaths`: replace `tests/cli` references with `packages/nemo-labs-oo-agents-cli/tests/cli`.
- Update `[tool.coverage.run] source`: replace `src/nooa_cli` with `packages/nemo-labs-oo-agents-cli/src/nooa_cli` (keep CLI coverage tracked).
- Update `[tool.pyright] extraPaths`: keep `src`, add `packages/nemo-labs-oo-agents-cli/src`.

Phases B + C land as one commit because the tree isn't buildable in between.

### `packages/nemo-oo-agents-benchmarks/pyproject.toml`

Add the `[project.optional-dependencies] bigcodebench = [...]` block (50+ deps) lifted from root pyproject.

## Phase D — CI (`.gitlab-ci.yml`)

- Add `build-package-cli` job (extends `.build-package-base`): `uv build --package nemo-labs-oo-agents-cli --out-dir dist-cli`.
- Add `publish-package-cli` job (extends `.publish-package-base`): `uv publish dist-cli/*`, depends on `build-package-cli`.
- Update version-bump for-loop to also bump `packages/nemo-labs-oo-agents-cli/pyproject.toml`.
- Add a sed line that pins cli's `nemo-oo-agents>=...` constraint to `==$VERSION` (mirrors benchmarks).
- Add `test-cli` job for symmetry with `test-context-blocks`/`test-unifiedllm`/`test-benchmarks`.
- Add `packages/nemo-labs-oo-agents-cli/**/*` to `test-core` `rules.changes` so MRs touching CLI re-run the cross-cutting tests still in root.

## Phase E — Docs

- README.md install section: replace single command with the four-line block.
- README.md `### What's included` callout.
- REFERENCE.md "Key Paths" table: add `packages/nemo-labs-oo-agents-cli/`.
- AGENTS.md / CONTRIBUTING.md: scan + update.
- THIRD_PARTY_NOTICES.md: rename `## Optional: [tui]` section to `## Optional: nemo-labs-oo-agents-cli[tui]`.
- `examples/tools_agent_tui/example.py`: docstring note that this example requires `pip install nemo-labs-oo-agents-cli`.

## Phase F — Verification

Local:
```bash
uv lock --check
uv sync --all-extras --dev
uv run pytest -q                # full suite
uv run ruff check . && uv run ruff format --check .
uv run pyright src
uv build && uv build --package nemo-labs-oo-agents-cli --out-dir dist-cli && uv build --package nemo-oo-agents-benchmarks --out-dir dist-bench
unzip -l dist/*.whl | grep -E '/__init__'         # expect nooa/ only, NO nooa_cli
unzip -l dist-cli/*.whl | grep -E '/__init__'     # expect nooa_cli/
```

Clean-env install (the "user pattern" check):
- Core only → no `nemo` command, no click installed
- Core + CLI (no extras) → `nemo` works, no pandas
- Core + CLI[tui] → pandas/numpy/plotly importable
- Core + benchmarks → no CLI, no `nemo` command
- Core + benchmarks[bigcodebench] → resolves the 50-dep scientific stack
- End-to-end quickstart against `claude-haiku` from a clean cli install

Capability tests (haiku) on the worktree as the final smoke.

## Critical files

- root `pyproject.toml`
- `.gitlab-ci.yml`
- `packages/nemo-oo-agents-benchmarks/pyproject.toml`
- `packages/nemo-labs-oo-agents-cli/pyproject.toml` (new)
- `packages/nemo-labs-oo-agents-cli/README.md` (new)
- `README.md`, `REFERENCE.md`
- `uv.lock`

## Out of scope

- Backcompat shims for `nemo-oo-agents[tui]` / `nemo-oo-agents[term]`. MR description calls these out as breaking; new install pattern documented.
- Splitting `util/eval_pipeline` or `util/harbor` (dev-only / loose scripts; not published).

## Commit sequence

1. **`refactor(gl-131): cleanup empty dirs + dead extras + relocate bigcodebench`** — Phase A only.
2. **`refactor(gl-131): split CLI/TUI into nemo-labs-oo-agents-cli package`** — Phases B + C.
3. **`ci(gl-131): three-package lockstep build+publish`** — Phase D.
4. **`docs(gl-131): three-package install instructions`** — Phase E.

Each commit independently green so reviewers can bisect cleanly.
