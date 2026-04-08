# Agent006 Reference

Quick reference for paths, examples, commands, and configuration.

## Key Paths

| Path | What |
|------|------|
| `src/agent006/` | Framework source |
| `src/agent006_cli/` | Command line interface source and TUI agent |
| `packages/` | Workspace packages (UnifiedLLM, AgentDoc, ContextBlocks, etc.) |
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

## Working Documentation

`.development/docs/` is the working docs tree — not `docs/` (which is stable guides).

| Content type | Path |
|---|---|
| Architecture, proposals, API specs | `.development/docs/design/` |
| RCAs, session notes, implementation logs | `.development/docs/scratch/` |
| Benchmark results, experiment outcomes | `.development/docs/evaluation/` |

When creating significant designs (architecture, API specs, data models), write to `.development/docs/design/` before presenting in chat. Include ASCII/mermaid diagrams.

## Logging

Agent006 uses Python's standard `logging` module throughout.  Every module
creates a logger with `logging.getLogger(__name__)`, producing a hierarchy
rooted at `agent006`.

### Quick start

```python
from agent006 import enable_logging

enable_logging()                                     # everything at DEBUG
enable_logging(level=logging.INFO)                   # calmer overview
enable_logging(name="agent006.strategies")           # just strategies
enable_logging(name="agent006.runtime.actor")        # just the executor
```

### Logger hierarchy

| Logger name | What it covers |
|-------------|---------------|
| `agent006` | Root — catches everything below |
| `agent006.agent` | Agent lifecycle, configuration |
| `agent006.runtime.actor` | Execution engine, code execution |
| `agent006.runtime.hooks` | Hook dispatch |
| `agent006.runtime.code_validator` | Code validation / safety checks |
| `agent006.strategies.codeact` | CodeAct strategy |
| `agent006.strategies.predict` | Predict strategy |
| `agent006.strategies.pure_python` | PurePython strategy |
| `agent006.strategies.reflexion` | Reflexion strategy |
| `agent006.tools.*` | Individual tool modules |
| `agent006.storage.*` | Storage backends |
| `agent006.library_manager` | Library / skill loading |
| `agent006.skill_manager` | Skill discovery |

### For application developers

`enable_logging()` is a convenience for development.  In production, use
`logging.config.dictConfig()` as usual — agent006 loggers are standard
`logging.Logger` instances.  The library adds only a `NullHandler` to the
root logger, so no output appears unless you configure handlers.

## Environment Variables

See `.env` for API keys:
- `OPENAI_API_KEY` — OpenAI
- `NVIDIA_API_KEY` — NVIDIA NIM (integrate.api.nvidia.com)
- `NVIDIA_INTERNAL_API_KEY` — NVIDIA internal (inference-api.nvidia.com)

| Provider | Endpoint | Key |
|----------|----------|-----|
| `openai` | OpenAI API | `OPENAI_API_KEY` |
| `nvidia` | integrate.api.nvidia.com | `NVIDIA_API_KEY` |
| `nvidia_internal` | inference-api.nvidia.com | `NVIDIA_INTERNAL_API_KEY` |
