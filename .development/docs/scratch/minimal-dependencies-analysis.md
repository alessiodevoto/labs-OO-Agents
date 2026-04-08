# Minimal Dependencies Analysis (Core 006 + Packages, Excluding Trace Explorer)

**Date:** 2026-02-06

## Method

Imports were collected by grepping `^(import |from )` in:

- `src/nemo_oo_agents/` (core)
- `packages/agentdoc/src/`
- `packages/context-blocks/src/`
- `packages/openinference-instrumentation-nemo-oo-agents/src/`
- `packages/unifiedllm/src/`

Trace explorer was excluded per request.

## Third-party imports in core and packages

### Core (`src/nemo_oo_agents`)

| Import | Used in |
|--------|--------|
| `pydantic` | strategies (codeact, structured_output, reflexion, pure_python, generated_code), events, runtime/pprint |
| `agentdoc` | codeact, actor, events, decorators, history, pprint, agent (TypeInfo, register_provider), generated_code |
| `context_blocks` | codeact, pure_python, actor, agent, events, decorators, history |
| `unifiedllm` | codeact (Tool), llm (CompletionClient, LLMResponse, Tool, ToolCall), __init__ (LLMResponse), strategies |

Optional / example-only (not required for core library):

- `openinference_instrumentation_nemo_oo_agents` — only in `util/quickstart.py` (example)
- `dotenv`, `rich` — only in `util/quickstart.py` (example)

### Packages

- **agentdoc**: stdlib only (dataclasses, typing, enum, inspect, collections.abc).
- **context-blocks**: `pydantic`, `agentdoc` (in formatter: `from agentdoc import pformat`).
- **openinference-instrumentation-nemo-oo-agents**: `opentelemetry-*`, `openinference-*` — tracing only.
- **unifiedllm**: `litellm`, `pydantic`.

## Strictly necessary for core + packages (no trace_explorer)

1. **pydantic** — core, context-blocks, unifiedllm.
2. **litellm** — unifiedllm (and thus nemo_oo_agents via unifiedllm).
3. **unifiedllm** — core (Tool, LLMResponse, CompletionClient, ToolCall).
4. **agentdoc** — core and context-blocks.
5. **context-blocks** — core (Block, events, formatters, history, agent).

So **minimal install** = `pydantic`, `litellm`, `unifiedllm`, `agentdoc`, `context-blocks` (with unifiedllm, agentdoc, context-blocks as workspace packages where applicable).

## Moved to dev / optional

These are **not** required for `import nemo_oo_agents` and running agents with core + packages (excluding trace explorer):

- **openinference-instrumentation-nemo-oo-agents** — tracing; only used in examples/quickstart.
- **viewer_utils**, **eval_pipeline**, **e2e-optimization**, **trace_explorer** — tooling, evaluation, viewer.
- **anyio**, **rich**, **python-dotenv**, **textual** — TUI, examples, dotenv.
- **dspy-ai**, **authlib**, **httpx** — not in core src/nemo_oo_agents.
- **slack-sdk**, **python-gitlab**, **msgraph-sdk**, **azure-identity** — integrations/tools.
- **uvicorn**, **python-multipart**, **pyngrok**, **watchfiles** — server/viewer.
- **pre-commit** — dev tooling.
- **datasets**, **openpyxl**, **pypdf**, **docker** — evaluation/benchmarks.

These live in `[dependency-groups] dev` so that:

- `uv sync` (with dev) installs full environment.
- `uv add git+https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents.git --branch main` gets only the minimal dependencies above.

## Note on context-blocks and agentdoc

`context-blocks` uses `agentdoc` in `formatter.py`. Its `pyproject.toml` now declares `agentdoc` as a dependency and `[tool.uv.sources] agentdoc = { workspace = true }` for workspace resolution.

## Verification

- **`uv sync`** (with dev): installs all dependencies including dev group; full test suite runs (1355 passed, 4 skipped).
- **`uv sync --no-dev`**: installs only minimal dependencies; `from nemo_oo_agents import Agent` and core package imports succeed.
- **`uv add git+https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents.git --branch main`** in another project: will resolve and install only the minimal dependency set (no dev group).
