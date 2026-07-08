# nemo-oo-agents-nvidia

NVIDIA-gateway model aliases (`claude-haiku`, `claude-haiku-azure`, `nemotron3-nano-30b`, `gpt-5.2`, …) for the NeMo OO Agents LLM registry.

Installing this package makes the bundled aliases available to the
core framework automatically — discovery happens via the
`nemo_oo_agents.bundled_configs` entry-point group, so there's no env
var to set and no path to copy. Set `NVIDIA_INTERNAL_API_KEY` (or
`NVIDIA_API_KEY` for the public NIM endpoint) and the aliases Just
Work in `get_llm_client()`.

To customize, run `nemo-oo config eject` to write a per-user copy at
`~/.config/nemo_oo/llm_config.yaml`, drop an `llm_config.yaml` into
your project's `.nemo_oo/` directory, or point
`NEMO_OO_LLM_CONFIG` at one or more YAML files. Run
`nemo-oo config show` to inspect which layers are loading.

External users who don't want the NVIDIA aliases simply don't install
this package.

Adding more bundled-config providers is purely additive: any package
that registers a callable under the `nemo_oo_agents.bundled_configs`
entry-point group is picked up automatically by the core framework's
`llm_config_chain()`.
