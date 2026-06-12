# NeMo OO Agents Reference

Quick reference for paths, examples, commands, and configuration.

## Key Paths

| Path | What |
|------|------|
| `src/nemo_oo_agents/` | Core framework source (the `nemo-oo-agents` published package) |
| `src/nemo_oo_agents/context_blocks/` | Context block rendering subpackage |
| `src/nemo_oo_agents/unifiedllm/` | LLM client subpackage |
| `packages/nemo-oo-agents-cli/` | CLI + TUI (separately-published `nemo-oo-agents-cli`) |
| `packages/nemo-oo-agents-benchmarks/` | Eval harness (separately-published `nemo-oo-agents-benchmarks`) |
| `packages/nat_oo_agents/` | NeMo Agent Toolkit plugin (external; not published from this repo) |
| `examples/` | Example agents (see below for details) |
| `evaluation/` | Benchmark-agnostic evaluation framework (adapters, metrics) — see its `README.md` and `AGENTS.md` |
| `docs/guides/` | Detailed framework guides |

## Examples

**Quickstart** (`examples/quickstart/`) — step-by-step tutorial sequence. Start here. Each example builds on the previous one and can be run standalone with `uv run python examples/quickstart/XX_name.py`.

| Example | File |
|---------|------|
| Simplest agent | `examples/quickstart/01_first_generation_method.py` |
| Structured output | `examples/quickstart/02_structured_outputs.py` |
| Deterministic + generative methods | `examples/quickstart/03_codeact_tools.py` |
| Strategies compared | `examples/quickstart/04_strategies.py` |
| Progressive disclosure | `examples/quickstart/05_progressive_disclosure.py` |
| Tracing (all method types) | `examples/quickstart/06_tracing.py` |
| Dynamic prompts | `examples/quickstart/07_dynamic_prompts.py` |
| Context blocks | `examples/quickstart/08_context_blocks.py` |
| Summarization | `examples/quickstart/09_summarization.py` |
| Skills | `examples/quickstart/10_skills.py` |
| MCP tools | `examples/quickstart/11_mcp.py` |
| Multimodal (images) | `examples/quickstart/12_multimodal.py` |
| NeMo Flow integration | `examples/quickstart/13_nemo_flow.py` |

**Advanced** (`examples/advanced/`) — deeper dives into specific features and integrations. Each example is self-contained; read the file docstring for prerequisites.

| Example | File |
|---------|------|
| Agent memory | `examples/advanced/memory.py` |
| CodeAct event flow | `examples/advanced/codeact_event_sequence.py` |
| Pre-ellipsis prefill | `examples/advanced/prefill.py` |
| OTLP tracing (Langfuse/Phoenix) | `examples/advanced/tracing_otlp.py` |
| Swappable execution engines | `examples/advanced/swappable_execution_engines.py` |

## Detailed Guides

For deeper understanding, see these topic-specific docs:

| Topic | File | When to Read |
|-------|------|-------------|
| How prompts work | `docs/guides/prompt-mechanics.md` | Building any agent |
| Strategies in depth | `docs/guides/strategies.md` | Choosing/configuring strategies |
| Context blocks | `docs/guides/context-blocks.md` | Managing agent state and memory |
| Single vs multi-agent | `docs/guides/single-vs-multi-agent.md` | Architectural decisions |
| Structured output | `docs/guides/structured-output.md` | Return types, Pydantic validation, PythonSource pattern |
| Method design | `docs/guides/writing-generation-methods.md` | Writing effective generation methods |
| Config migration (0.4.x → 0.5.0) | `docs/guides/config-migration.md` | Moving existing config to the unified `nemo_oo` layout (settings/secrets/llm_config YAML) — breaking changes in 0.5.0 |

## Working Documentation

`.development/docs/` is the working docs tree — not `docs/` (which is stable guides).

| Content type | Path |
|---|---|
| Architecture, proposals, API specs | `.development/docs/design/` |
| RCAs, session notes, implementation logs | `.development/docs/scratch/` |
| Benchmark results, experiment outcomes | `.development/docs/evaluation/` |

When creating significant designs (architecture, API specs, data models), write to `.development/docs/design/` before presenting in chat. Include ASCII/mermaid diagrams.

## Logging

NeMo OO Agents uses Python's standard `logging` module throughout.  Every module
creates a logger with `logging.getLogger(__name__)`, producing a hierarchy
rooted at `nemo_oo_agents`.

### Quick start

```python
from nemo_oo_agents import enable_logging

enable_logging()                                     # everything at DEBUG
enable_logging(level=logging.INFO)                   # calmer overview
enable_logging(name="nemo_oo_agents.strategies")           # just strategies
enable_logging(name="nemo_oo_agents.runtime.actor")        # just the executor
```

### Logger hierarchy

| Logger name | What it covers |
|-------------|---------------|
| `nemo_oo_agents` | Root — catches everything below |
| `nemo_oo_agents.agent` | Agent lifecycle, configuration |
| `nemo_oo_agents.runtime.actor` | Execution engine, code execution |
| `nemo_oo_agents.runtime.hooks` | Hook dispatch |
| `nemo_oo_agents.runtime.code_validator` | Code validation / safety checks |
| `nemo_oo_agents.strategies.codeact` | CodeAct strategy |
| `nemo_oo_agents.strategies.predict` | Predict strategy |
| `nemo_oo_agents.strategies.pure_python` | PurePython strategy |
| `nemo_oo_agents.strategies.reflexion` | Reflexion strategy |
| `nemo_oo_agents.tools.*` | Individual tool modules |
| `nemo_oo_agents.storage.*` | Storage backends |
| `nemo_oo_agents.library_manager` | Library / skill loading |
| `nemo_oo_agents.skill_manager` | Skill discovery |

### For application developers

`enable_logging()` is a convenience for development.  In production, use
`logging.config.dictConfig()` as usual — nemo_oo_agents loggers are standard
`logging.Logger` instances.  The library adds only a `NullHandler` to the
root logger, so no output appears unless you configure handlers.

## NeMo Flow Integration

Optional integration with [NeMo Flow](https://github.com/NVIDIA/NeMo-Flow) (`nemo_flow`) — a multi-language agent runtime providing guardrails, intercepts, event subscribers, and ATIF trajectory export.

### Quick start

```bash
# Install with NeMo Flow support (public PyPI wheels)
uv sync --extra nemo-flow
```

```python
from nemo_oo_agents.nemo_flow_middleware import nemo_flow_scope

async with nemo_flow_scope(agent, "my-agent") as handle:
    result = await agent.my_method(...)
    # handle.uuid available for ATIF export
```

### Key files

| File | Purpose |
|------|---------|
| `src/nemo_oo_agents/nemo_flow_middleware.py` | Middleware functions + `install_nemo_flow()` / `nemo_flow_scope()` |
| `tests/test_nemo_flow_middleware.py` | Integration tests (requires `nemo_flow`) |
| `examples/quickstart/13_nemo_flow.py` | Full quickstart: guardrails, intercepts, ATIF export |

### Middleware hooks

The integration installs three middleware via `event_manager.intercept()`:

| Middleware | Hook | What it does |
|------------|------|-------------|
| `nemo_flow_llm_middleware` | `llm_call` | Routes LLM calls through NeMo Flow LLM pipeline |
| `nemo_flow_tool_middleware` | `execute_python` | Routes code execution through NeMo Flow tool pipeline |
| `nemo_flow_agent_call_middleware` | `agent_call` | Wraps each agent method in a NeMo Flow Function scope |

## Config files

Three config files share one layout, one precedence chain, and one loader
(`nemo_oo_agents.layered_config`). All are optional — absence means "use the
layer below."

| File | What | Bundled defaults | Env-var override |
|------|------|------------------|------------------|
| `llm_config.yaml` | LLM model aliases | entry-point group `nemo_oo_agents.bundled_configs` | `NEMO_OO_LLM_CONFIG` |
| `settings.yaml` | TUI settings (`tui:` / `agent:` sections, dataclass field names) | in-code defaults | `NEMO_OO_SETTINGS` |
| `secrets.yaml` | API keys (`env:` name→value map, pushed into `os.environ` non-clobbering) | — | `NEMO_OO_SECRETS` |

```text
~/.config/nemo_oo/           # user-global   (XDG_CONFIG_HOME aware; override base with NEMO_OO_USER_DIR)
├── settings.yaml
├── secrets.yaml             # chmod 600
└── llm_config.yaml

.nemo_oo/                    # project-local (override base with NEMO_OO_PROJECT_DIR)
├── settings.yaml
├── secrets.yaml             # gitignore strongly recommended
└── llm_config.yaml
```

Precedence (low → high, last wins): bundled defaults → user → project →
env-var path(s). For `secrets.yaml`, an env var already set in the process
always wins over a file value. `null` deletes a key inherited from a lower
layer. Run `nemo oo config show` to see which layers are loading (secret
values redacted).

`secrets.yaml`:

```yaml
env:
  NVIDIA_INTERNAL_API_KEY: sk-...
  ANTHROPIC_API_KEY: sk-ant-...
```

## Environment Variables

See `.env` for API keys (library use); the CLI/TUI reads `secrets.yaml`:
- `OPENAI_API_KEY` — OpenAI
- `NVIDIA_API_KEY` — NVIDIA NIM (integrate.api.nvidia.com)
- `NVIDIA_INTERNAL_API_KEY` — NVIDIA internal (inference-api.nvidia.com)

| Provider | Endpoint | Key |
|----------|----------|-----|
| `openai` | OpenAI API | `OPENAI_API_KEY` |
| `nvidia` | integrate.api.nvidia.com | `NVIDIA_API_KEY` |
| `nvidia_internal` | inference-api.nvidia.com | `NVIDIA_INTERNAL_API_KEY` |

### `NEMO_OO_*` variables

All framework-owned env vars use the `NEMO_OO_` prefix:

| Variable | What |
|----------|------|
| `NEMO_OO_USER_DIR` | Override the user config base (default `~/.config/nemo_oo`, XDG-aware) |
| `NEMO_OO_PROJECT_DIR` | Override the project config dir (default `<root>/.nemo_oo`) |
| `NEMO_OO_LLM_CONFIG` | Comma-separated YAML path(s) — highest-priority `llm_config.yaml` layer |
| `NEMO_OO_SETTINGS` | Comma-separated YAML path(s) — highest-priority `settings.yaml` layer |
| `NEMO_OO_SECRETS` | Comma-separated YAML path(s) — highest-priority `secrets.yaml` layer |
| `NEMO_OO_TRACE_VIEWER_PORT` | Port for the trace viewer (`nemo oo start-dev`; default 5001) |
| `NEMO_OO_TRACE_DB` | SQLite trace-store path for the viewer (default `~/.config/nemo_oo/traces.db`) |
| `NEMO_OO_RICH_URL` | Rich-content POST endpoint, set by `nemo oo term` for the web frontend (internal) |

Any var named under a `secrets.yaml` `env:` map (e.g. `NVIDIA_INTERNAL_API_KEY`)
is pushed into the process env non-clobbering — an already-exported value always wins.
