# Issue gl-138 — LLM-registry discovery via `paths.py` + entry-point bundled defaults

Source: [gl-138](https://gitlab-master.nvidia.com/interactive-agents/nooa/-/issues/138). Folds in the bundled-defaults work originally tracked as MR !223.

## Problem

`unifiedllm/registry.py` did its own LLM-config discovery (only the
`UNIFIEDLLM_CONFIG` env var + bare `./llm_config.yaml` in CWD), with
the load happening at import time. This diverged from the project's
`paths.py` conventions (`get_user_dir` / `get_project_dir`), forced
the project YAML to sit at the wrong location, and meant any new
discovery layer had to be added inside `unifiedllm` even though
`unifiedllm` lives downstream of `paths.py`.

Internal NVIDIA users also had to manually copy
`configs/llm_config_nvidia.yaml` and set `UNIFIEDLLM_CONFIG` — high
friction and the cause of recurring "alias not found" surprises.

## Design

### Discovery: `nooa.llm_config.llm_config_chain()`

New module that returns YAML paths in priority order (lowest first):

1. **Bundled** — every package that registers under the
   `nooa.bundled_configs` entry-point group. Install
   `nemo-oo-agents-nvidia` (the new sibling package) for the
   NVIDIA-gateway aliases; install nothing for an OSS-only registry.
   No env var to remember.
2. **User** — `get_user_dir("llm_config.yaml")`
   (`~/.config/nat/oo/llm_config.yaml`).
3. **Project** — `get_project_dir("llm_config.yaml")`
   (`<project-root>/.nooa/llm_config.yaml`).
4. **Env var** — `NEMO_OO_LLM_CONFIG` (comma-separated paths). The
   **global override**: highest priority so a shell session can
   override project / user files without editing them.

Paths are `Path.resolve()`-canonicalised so symlinks and repeats
collapse to the *highest-priority* occurrence.

A bare `./llm_config.yaml` in CWD is no longer auto-loaded. Alpha
software — no transitional warning; users move to one of the four
layers.

### Entry-point bundled defaults

The bundled YAML lives in its own workspace package:

```
packages/nemo-oo-agents-nvidia/
  src/nooa_nvidia/
    __init__.py           # get_default_config_path() callable
    data/
      __init__.py
      llm_config_default.yaml
  pyproject.toml          # registers nvidia = "...:get_default_config_path"
                          # under nooa.bundled_configs
  README.md
```

Adding another provider is purely additive: drop a package that
registers under the same entry-point group and core's chain picks it
up. There is no fixed alias namespace and no env-var toggle.

### Loading: `unifiedllm/registry.py` stays discovery-agnostic

- `MODELS` starts empty at import time (no filesystem side effects).
- `reload_registry(*paths)` — load only these files, last wins.
- `reload_registry()` (no args) — re-discover via `llm_config_chain`
  and load. Callers can edit a YAML and call `reload_registry()` to
  pick changes up.
- `ensure_loaded()` — idempotent; calls `reload_registry()` the first
  time it's invoked. Triggered automatically by `get_llm_client()`
  so standalone scripts work without ceremony.
- Module-level `threading.RLock` serialises the `_loaded` check +
  `MODELS.clear()/update()` mutation, so concurrent async tool calls
  can't observe a half-cleared registry.
- Non-mapping YAML entries (`models.foo: "bar"`) are logged and
  dropped, not stored.

### Shared helper: `resolve_api_key_from_config()`

Public function in `registry.py` that resolves `api_key_env` → env
var value, with a **WARN** when the env var is set in YAML but
missing from the environment. Used by `get_llm_client`, the NAT
plugin, and the viewer's `/inference` route — replaces the silent
fall-through-to-OPENAI_API_KEY footgun. Validates `api_key_env` is a
string before calling `os.getenv` (so a malformed `api_key_env: 12345`
logs and returns `None` rather than raising `TypeError`).

`get_llm_client` skips the resolution when the caller passes
`api_key=` directly, so programmatic callers don't see spurious
warnings.

### Wiring

- **TUI bootstrap** calls `reload_registry(*llm_config_chain())`
  early so `MODELS` is hot before the health check and TUI commands
  (`/model`, completer) see populated state. Wrapped in try/except
  so a malformed YAML doesn't abort startup.
- **NAT plugin** (`packages/nat_oo_agents/.../llm.py`) calls
  `ensure_loaded()` before reading `MODELS`. Wrapped to catch any
  registry failure and proceed with NAT-only config.
- **`eval_pipeline`** and the `UnifiedLLM.context_window` property
  also call `ensure_loaded()`; both are entry points reachable
  without TUI bootstrap.
- **Health-check helper** `_has_llm_config_yaml()` checks
  `llm_config_chain()` excluding any bundled-provider paths —
  bundled defaults are an install-time concern, not a user
  customisation, so including them would steer diagnostics toward
  "check your `llm_config.yaml`" hints for users who haven't written
  one.

### `nemo oo config` CLI

New subcommand at `commands/config.py`:

- `show` — prints the resolved chain (each layer + alias count),
  flags candidates dropped by dedup, prints the total alias count
  and every referenced `api_key_env` var's set/unset state.
- `path` — prints where `eject` would write.
- `eject [--force]` — copies the bundled YAML to the user-level path.
  Refuses to overwrite symlinks/directories even with `--force`.
  Refuses to pick one when multiple bundled providers are
  registered.

## Tests

- **`tests/test_llm_config_chain.py`** — chain ordering, dedup
  (env+user collision, env self-duplicate, symlink alias),
  missing-path warning, bundled entry-point inclusion (multiple
  providers, empty-providers, end-to-end check that
  `nemo-oo-agents-nvidia` registers).
- **`tests/unifiedllm/test_model_registry.py`** — uses
  `NEMO_OO_USER_DIR` / `NEMO_OO_PROJECT_DIR` instead of CWD;
  exercises the new `reload_registry(*paths)` API, the lazy
  auto-load, the no-args re-discover contract, the `_loaded`
  invariant, non-mapping validation, non-string `api_key_env`
  validation, and the explicit-`api_key`-skips-warning case.
- **`packages/nemo-labs-oo-agents-cli/tests/cli/test_config_command.py`**
  — `show` / `path` / `eject` behaviour, including dedup
  annotation, no-provider / multi-provider eject refusal, and
  symlink/directory refusal.
- **`packages/nemo-labs-oo-agents-cli/tests/cli/test_health_check.py`** —
  chain + bundled-exclusion contract.

Autouse fixtures stub
`nooa.llm_config.bundled_config_paths` to return `[]` so
tests are insensitive to whether `nemo-oo-agents-nvidia` is
installed in the dev environment.

## Out of scope

- Scrubbing NVIDIA-internal endpoints from the bundled YAML (gl-18
  content cleanup) — orthogonal; the YAML is now in its own opt-in
  package so external users aren't exposed by default.
- Per-call `drop_params` (gl-143).
- Scaffolding an `llm_config.yaml` template on first project run.
- Folding `eval_pipeline`'s separate `models.yaml` schema into the
  same registry — distinct consumer, different shape.
