# Unified config layout + first-run install UX

Branch: `feat/setup-and-credentials`. No GitLab issue; thematic design doc.

## Problem

1. **First run has friction.** After `curl … | sh`, getting `nemo oo tui`
   into a working chat required hand-editing a shell rc to export
   `NVIDIA_INTERNAL_API_KEY` — the installer wrote a `~/.config/nat/oo/env`
   file that no Python code ever read.

2. **Three unrelated config mechanisms.** LLM aliases were layered YAML,
   TUI settings were a project-only TOML, and credentials were ad-hoc
   `os.getenv`. Different formats, locations, and precedence rules for
   what is conceptually one concern.

## Proposed Design

One layout, one precedence chain, one loader for all three config files.

### Filesystem layout

```text
~/.config/nooa/      # user-global (honors XDG_CONFIG_HOME; all platforms)
├── settings.yaml       # TUI settings
├── secrets.yaml        # API keys; chmod 600
└── llm_config.yaml     # LLM aliases

.nooa/               # project-local (per repo)
├── settings.yaml
├── secrets.yaml        # gitignore-d (.nooa/ is ignored)
└── llm_config.yaml
```

No file is required; absence means "use the layer below." Base dirs are
overridable via `NEMO_OO_USER_DIR` / `NEMO_OO_PROJECT_DIR`
(`src/nooa/paths.py` is the single source of truth).

### Precedence (low → high, last wins)

1. **Bundled defaults** — entry-point group `nooa.bundled_configs`
   (LLM config only); in-code defaults for settings; nothing for secrets.
2. **User** file: `~/.config/nooa/<name>.yaml`.
3. **Project** file: `.nooa/<name>.yaml`.
4. **Env-var path override** — `NEMO_OO_{LLM_CONFIG,SETTINGS,SECRETS}`, each
   a comma-separated list of YAML *file paths* to load as the top file layer.

Within a file, keys merge last-wins and `null` deletes; resolved-path dedup
keeps the highest-priority occurrence.

For `secrets.yaml` there is one extra, distinct rule:

5. **Process env wins over file values.** Loaded keys are pushed into
   `os.environ` *non-clobbering* — if `K` is already set in the environment,
   the file value is ignored.

> 4 vs 5 are different axes. (4) selects *which file* to read (a path in
> `NEMO_OO_SECRETS`); (5) is about a single already-exported variable (e.g.
> `NVIDIA_INTERNAL_API_KEY`) beating whatever any file — including the one
> from (4) — would have set. A shell `export` always wins.

### File schemas

`secrets.yaml` — an `env:` name→value map, mirroring the existing
`api_key_env: NVIDIA_INTERNAL_API_KEY` pattern (YAML names the var, the var
holds the secret):

```yaml
env:
  NVIDIA_INTERNAL_API_KEY: sk-...
  ANTHROPIC_API_KEY: sk-ant-...
```

`settings.yaml` — a direct serialisation of the `Config` Pydantic-model tree
(`tui:` / `agent:` sections, field names); round-trips with
`dump_settings()` / `load_settings()`.

`llm_config.yaml` — unchanged (existing model-alias format).

### Config as typed objects

Everything is reachable through one namespace, **`nooa.config`**,
and file-based config flows as typed objects (not raw dicts), matching the
in-code configs (`CodeActConfig`, …):

- `ModelConfig` (Pydantic, `extra="allow"`) — typed view of one
  `llm_config.yaml` entry. `get_model_config(name)` returns it. The registry
  `MODELS` stays a `dict` internally (litellm passthrough + external readers);
  long-term it should hold `ModelConfig` objects.
- `Secrets` — typed `{env: dict[str, str]}` view of `secrets.yaml`.
- `resolved_config() -> ResolvedConfig` — the programmatic `config show`:
  typed `models` / `secrets` / `settings` (dict; the rich `Config` object is
  CLI-owned) + the resolved `sources`. Secret values redacted by default.
- Loader helpers (`load_layered_yaml`, `layered_paths`, `load_secrets_into_env`)
  are re-exported here too, so `nooa.config` is the single door.
- TUI `Config`/`TUIConfig`/`AgentConfig` are Pydantic models living in the CLI
  package (following the main-package convention; not moved to core).

### Code

- `src/nooa/layered_config.py` — the shared engine.
  `layered_paths(filename, env_var, prepend=…)` returns the resolved file
  chain; `load_layered_yaml(filename, env_var, prepend=…)` returns the merged
  dict. Re-exported via `nooa.config`.
- `src/nooa/secrets.py::load_secrets_into_env()` — applies
  `secrets.yaml`'s `env:` map to `os.environ` non-clobbering; idempotent.
  Called by the TUI bootstrap before `reload_registry()` and by the `oo`
  CLI group before subcommands (skipped for thin launchers like `term` /
  `completion` that don't use keys in-process and shouldn't pay the core
  import cost).
- `nooa_cli/tui/settings.py` — `settings.yaml` ↔ `Config`
  (de)serialisation, layered via the engine above. (Lives in the CLI
  package because it binds to `Config`/`TUIConfig`; core can't import those.)
- `install.sh` — writes `~/.config/nooa/secrets.yaml` (chmod 600, value
  written as a quoted YAML scalar); no shell-rc edit. Next step is just
  `nemo oo tui`.
- `nemo oo config show` — prints the resolved layers for all three files;
  secret **values are redacted** (key names only).

## Out of scope

- `nemo oo doctor` / `nemo oo init` commands (deferred).
- Renaming `llm_config.yaml` → `models.yaml` (pure churn).
- Keyring / OS-credential-store integration (deferred).
- Project-level `secrets.yaml` lint that warns if not gitignored
  (nice-to-have; not blocking — `.nooa/` is already gitignored).
