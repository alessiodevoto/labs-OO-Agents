# Agent006 + NAT Integration Design

## Overview

This document describes the integration between Agent006 (a code-generating agent framework) and NVIDIA NeMo Agent Toolkit (NAT). The integration is implemented as a NAT plugin package (`nvidia-nat-agent006`) that wraps Agent006 agents as NAT workflows, requiring zero modifications to Agent006 agent code.

## Architecture

The integration is implemented as four bridges:

1. **LLM Bridge**: NAT YAML config -> `UnifiedLLM` instance via `register_llm_client`
2. **Tool Bridge**: NAT `Function` objects -> native Python tool classes via dynamic code generation
3. **OTel Bridge**: Shared global `TracerProvider` for dual OTLP + JSONL export
4. **Method Routing**: YAML config specifies which agent method to invoke

## Package Location

- **Development**: `packages/nvidia_nat_agent006/` in the agent006 repo
- **Production**: Submitted as MR to NAT monorepo
- **Discovery**: Standard Python entry points (`[project.entry-points.'nat.components']`)

## Bridge 1: LLM

NAT LLM providers (OpenAI, NIM, etc.) are registered with `wrapper_type="agent006"`. Each registration constructs a `UnifiedLLM` from the NAT config:

```python
@register_llm_client(config_type=OpenAIModelConfig, wrapper_type="agent006")
async def openai_agent006(config, builder):
    yield UnifiedLLM(model=config.model_name, api_key=..., api_base=config.base_url)
```

The wrapper calls `builder.get_llm("agent", wrapper_type="agent006")` to get a native `UnifiedLLM`.

## Bridge 2: Tools

NAT `Function` objects are converted to native Python classes at registration time. Each class has properly-typed async methods derived from the Function's Pydantic `input_schema`. The classes are injected as instance attributes on the agent, making them visible to `agentdoc` via standard `inspect` introspection.

```
NAT Function("current_time", input_schema={}) -> class CurrentTime with async def invoke(self) -> str
```

## Bridge 3: OTel

Agent006 uses the global OTel `TracerProvider`. NAT uses its own event-stream system. The wrapper sets up a shared `TracerProvider` with OTLP export before agent instantiation. Agent006's `enable_tracing()` detects the existing provider and adds its JSONL processor alongside.

## Bridge 4: Method Routing

YAML config specifies `method: chat`. The wrapper resolves `getattr(agent, method)` and calls it with the input text.

## YAML Config Example

```yaml
llms:
  agent:
    _type: openai
    model: gpt-4o

tools:
  current_time:
    _type: current_time

workflow:
  _type: agent006_wrapper
  agent: my_agent.py:MyAgent
  method: chat
  tools:
    - current_time
```

## Files

- `packages/nvidia_nat_agent006/src/nat/plugins/agent006/agent006_wrapper.py` -- Core wrapper
- `packages/nvidia_nat_agent006/src/nat/plugins/agent006/llm.py` -- LLM bridge
- `packages/nvidia_nat_agent006/src/nat/plugins/agent006/tool_bridge.py` -- Tool bridge
- `packages/nvidia_nat_agent006/src/nat/plugins/agent006/otel_bridge.py` -- OTel bridge
- `examples/nat/` -- Example agents and configs
