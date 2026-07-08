# nemo-oo-agents-cli

CLI and TUI for [nemo-oo-agents](https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents). Ships the `nemo-oo` command (subcommands like `nemo-oo tui`, `nemo-oo term`, `nemo-oo eval`, `nemo-oo traces`) and the agent TUI (a REPL-driven CodeAct frontend).

## Install

```bash
# CLI + TUI
uv add nemo-oo-agents-cli

# ...with numpy/pandas/plotly/scipy/sklearn pre-loaded into the LLM REPL
uv add "nemo-oo-agents-cli[datascience]"

# ...with web-served TUI (uvicorn + ptyprocess + fastapi, plus datascience)
uv add "nemo-oo-agents-cli[web]"
```

`nemo-oo-agents-cli` automatically pulls in matching `nemo-oo-agents` (the core framework). The `[datascience]` extra adds libraries the LLM can use in REPL-generated code; the `[web]` extra adds the `nemo-oo term` web frontend.

## Usage

```bash
nemo-oo --help
nemo-oo tui                  # interactive agent REPL
nemo-oo term                 # web terminal
nemo-oo eval ...             # eval pipeline runner
```

See the main repo [README](https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/blob/main/README.md) for the framework documentation.
