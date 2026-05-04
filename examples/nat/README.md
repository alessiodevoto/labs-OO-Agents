# NeMo OO Agents + NAT Integration Examples

Run NeMo OO Agents agents through NVIDIA NeMo Agent Toolkit (NAT) with zero modifications to agent code.

## Prerequisites

- Python 3.12+ with the project venv activated
- API keys configured in `.env` at the repo root (see below)

## Setup

From the repo root, install with the `nat-dev` dependency group:

```bash
uv sync --group nat-dev
```

This installs NAT core (from `3p/NeMo-Agent-Toolkit/`) and the `nvidia-nat-nemo_oo_agents` plugin (from `packages/nvidia_nat_nemo_oo_agents/`). The plugin is automatically discovered by NAT via Python entry points.

Verify it works:

```bash
source .venv/bin/activate
nat validate --config_file examples/nat/config_full.yml
```

You should see: `Configuration file is valid!`

## API Keys

The examples use the NVIDIA internal gateway. Set these in your `.env`:

```
NVIDIA_INTERNAL_API_KEY=sk-...   # For the full integration example (NAT-provided LLM)
OPENAI_API_KEY=sk-...            # For the standalone example (agent's own LLM)
```

## Running the Examples

### Full Integration (NAT-provided LLM + Tools)

NAT configures the LLM **and** tools, injecting both into the agent. The agent code has zero NAT dependencies.

```bash
cd examples/nat
nat run --config_file config_full.yml --input "What time is it right now?"
```

What happens:
1. NAT reads `config_full.yml` and discovers the `nemo_oo_agents_wrapper` plugin
2. The LLM bridge creates a `CompletionClient` from the YAML `llms:` config
3. The OTel bridge sets up a shared TracerProvider + NeMo OO Agents JSONL tracing
4. NAT builds the `current_datetime` function and injects it onto the agent as `self.current_datetime`
5. NAT imports `DemoAgent` from `example_agent.py` and injects the LLM
6. NAT routes input to `DemoAgent.chat()`
7. NeMo OO Agents's CodeAct strategy generates code that calls `await self.current_datetime.invoke()`
8. NAT displays the result and a JSONL trace is written to `traces/`

### Standalone Agent (own LLM)

The agent defines its own LLM at class level. NAT wraps and serves it without providing an LLM.

```bash
cd examples/nat
nat run --config_file config_standalone.yml --input "Hello, what can you do?"
```

Note: The standalone example uses `gpt-4o-mini` directly, so `OPENAI_API_KEY` must be a valid OpenAI key (not an NVIDIA key).

### Integration Tests (no API keys needed)

Tests the tool bridge, OTel bridge, input conversion, and agentdoc introspection using mocks:

```bash
cd examples/nat
python test_integration.py
```

Expected output: `7 passed, 0 failed, 7 total`

## Files

| File | Description |
|------|-------------|
| `example_agent.py` | `DemoAgent` -- no LLM or tools defined, everything injected by NAT |
| `example_agent_standalone.py` | `StandaloneAgent` -- brings its own `CompletionClient` |
| `config_full.yml` | YAML config: NAT provides LLM + `current_datetime` tool, agent code unchanged |
| `config_standalone.yml` | Minimal YAML config: agent uses its own LLM |
| `test_integration.py` | 7 automated tests covering tool bridge, OTel, input conversion, agentdoc |

## Config Reference

The `nemo_oo_agents_wrapper` workflow type accepts these fields:

```yaml
workflow:
  _type: nemo_oo_agents_wrapper
  agent: path/to/module.py:ClassName   # Required: agent module and class
  method: chat                          # Required: async method to invoke
  dependencies:                         # Directories to add to sys.path
    - ../../src
  tools:                                # Optional: list of NAT function names to inject
    - current_datetime
  llm_name: agent                       # Optional: name from llms: section
  env: ../../.env                       # Optional: .env file to load
  enable_tracing: true                  # Optional: OTel tracing (default: true)
  description: "My agent"              # Optional: display name
```

NAT functions are declared in the top-level `functions:` section:

```yaml
functions:
  current_datetime:
    _type: current_datetime
```

The tool bridge generates native Python classes from NAT function schemas and injects them as agent attributes (e.g., `self.current_datetime`). These classes are fully introspectable by `agentdoc`.

## Architecture

See [docs/scratch/nemo_oo_agents-nat-integration.md](../../docs/scratch/nemo_oo_agents-nat-integration.md) for the full design document.
