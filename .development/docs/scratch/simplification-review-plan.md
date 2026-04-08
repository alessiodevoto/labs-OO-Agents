# Agent006 Simplification Review Plan

Hand review of all files in `src/agent006/`, organized by **dependency layers**.

Each layer only depends on layers before it. Review in order to understand the dependency chain.

## Overview

- **Total Files**: ~56 core files (excluding `__init__.py`)
- **Total LOC**: ~17,756
- **Layers**: 4 main layers + 1 semi-independent (evaluation)

---

## Layer 1: Foundation & Primitives (~13 files, ~1,700 LOC)

Data definitions, configuration, and low-level utilities with no/minimal internal dependencies.

### 1A. Core Data (No Internal Dependencies)

| # | File | LOC | Description |
|---|------|-----|-------------|
| 1 | [config.py](src/agent006/config.py) | 45 | Configuration defaults |
| 2 | [types.py](src/agent006/types.py) | 265 | Pydantic data models (15 classes) |
| 3 | [errors/__init__.py](src/agent006/errors/__init__.py) | 178 | Error hierarchy (16 exception classes) |
| 4 | [errors/developer_messages.py](src/agent006/errors/developer_messages.py) | 279 | DeveloperError with error catalog |

### 1B. Primitives (Stdlib Only)

| # | File | LOC | Description |
|---|------|-----|-------------|
| 5 | [util/_context.py](src/agent006/util/_context.py) | 47 | Context variables for agent/runtime |
| 6 | [tracing/ids.py](src/agent006/tracing/ids.py) | 22 | Trace/span ID generators |
| 7 | [tracing/protocol.py](src/agent006/tracing/protocol.py) | 152 | Tracer/span protocol (abstract) |
| 8 | [context/formats.py](src/agent006/context/formats.py) | 142 | JSON/XML/Markdown formatters |
| 9 | [context/scoped.py](src/agent006/context/scoped.py) | 118 | Scoped context manager |
| 10 | [util/context_blocks.py](src/agent006/util/context_blocks.py) | 207 | Context block manipulation (8 funcs) |
| 11 | [util/prompt.py](src/agent006/util/prompt.py) | 114 | Prompt construction helpers |
| 12 | [context/prompts.py](src/agent006/context/prompts.py) | 384 | Prompts context manager |
| 13 | [runtime/validator.py](src/agent006/runtime/validator.py) | 89 | AST-based code validator |

**Review focus**:
- Are all 16 error types needed?
- Can types.py be split by domain?
- context_blocks.py has 8 standalone functions - could be a class

---

## Layer 2: Services (~24 files, ~4,500 LOC)

LLM clients, tracing, context rendering, and runtime support. Depends on Layer 1.

### 2A. Logging & Context Helpers

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 14 | [util/message.py](src/agent006/util/message.py) | 51 | util/_context | Message printing to agent |
| 15 | [util/logger.py](src/agent006/util/logger.py) | 85 | util/_context | Agent logging wrapper |
| 16 | [util/context.py](src/agent006/util/context.py) | 113 | util/_context | Context dict helpers |

### 2B. LLM Clients

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 17 | [unifiedllm/fake.py](packages/unifiedllm/src/unifiedllm/fake.py) | 212 | unifiedllm | Fake LLM for testing (moved to unifiedllm package) |
| 18 | [llm/client.py](src/agent006/llm/client.py) | 262 | types, errors | LiteLLM wrapper |
| 19 | [llm/human.py](src/agent006/llm/human.py) | 132 | llm/client, types | Human-in-the-loop LLM |
| 20 | [llm/model_tester.py](src/agent006/llm/model_tester.py) | 224 | llm/*, types | Model compatibility testing |

### 2C. Tracing Implementations

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 21 | [tracing/noop.py](src/agent006/tracing/noop.py) | 90 | tracing/protocol | No-op tracer |
| 22 | [tracing/pretty.py](src/agent006/tracing/pretty.py) | 85 | tracing/protocol | Console colored output |
| 23 | [tracing/jsonl_exporter.py](src/agent006/tracing/jsonl_exporter.py) | 176 | tracing/protocol | JSONL trace export |
| 24 | [tracing/otel.py](src/agent006/tracing/otel.py) | 478 | tracing/protocol | OpenTelemetry integration |
| 25 | [tracing/__init__.py](src/agent006/tracing/__init__.py) | 134 | tracing/* | Tracer factory |

### 2D. Runtime Support

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 26 | [runtime/variable_expansion.py](src/agent006/runtime/variable_expansion.py) | 197 | ast | Variable substitution |
| 27 | [runtime/task_wrapper.py](src/agent006/runtime/task_wrapper.py) | 198 | errors | Task metadata wrapper |
| 28 | [runtime/history.py](src/agent006/runtime/history.py) | 359 | dataclasses | Conversation history |
| 29 | [runtime/executor.py](src/agent006/runtime/executor.py) | 272 | validator | SimpleExecutor (exec) |
| 30 | [runtime/stub.py](src/agent006/runtime/stub.py) | 125 | inspect | Method stubs |
| 31 | [runtime/errors/messages/formatter.py](src/agent006/runtime/errors/messages/formatter.py) | 122 | errors | Error message formatter |

### 2E. Context & Prompt Building

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 32 | [context/renderer.py](src/agent006/context/renderer.py) | 301 | util/context_blocks | Block renderer |
| 33 | [runtime/prompts.py](src/agent006/runtime/prompts.py) | 456 | context/prompts | Prompt builder |

### 2F. Tools

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 34 | [tools/file_tools.py](src/agent006/tools/file_tools.py) | 69 | pathlib | File system ops |
| 35 | [tools/subprocess_tool.py](src/agent006/tools/subprocess_tool.py) | 193 | subprocess | Shell execution |
| 36 | [tools/gitlab_tool.py](src/agent006/tools/gitlab_tool.py) | 342 | python-gitlab | GitLab API |
| 37 | [tools/slack_tool.py](src/agent006/tools/slack_tool.py) | 240 | slack_sdk | Slack API |

**Review focus**:
- Can message.py and logger.py be merged?
- Is tracing over-abstracted? (4 implementations) - consolidate to noop + otel?
- executor.py has direct exec() - security review needed
- Tool wrappers have repetitive patterns - extract base class?

---

## Layer 3: Execution Engine (~5 files, ~3,000 LOC)

**Critical infrastructure** - executor base, strategies, and orchestration. Depends on Layers 1-2.

### 3A. Executor Infrastructure

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 38 | [runtime/executors/registry.py](src/agent006/runtime/executors/registry.py) | 92 | types | Executor dispatch |
| 39 | [runtime/executors/base.py](src/agent006/runtime/executors/base.py) | **1016** | 8+ modules | **ExecutorBase** - shared infrastructure |

### 3B. Execution Strategies

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 40 | [runtime/executors/pure_python.py](src/agent006/runtime/executors/pure_python.py) | 443 | executors/base | PURE_PYTHON strategy |
| 41 | [runtime/executors/structured_output.py](src/agent006/runtime/executors/structured_output.py) | 360 | executors/base | STRUCTURED_OUTPUT strategy |

### 3C. Orchestration

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 42 | [runtime/actor.py](src/agent006/runtime/actor.py) | **1171** | executors, task_wrapper | **ActorRuntime** - main loop |

**Review focus**:
- **ExecutorBase (1016 LOC)** is the architectural bottleneck. Imports from 8+ modules. Consider splitting:
  - Validation logic → separate module
  - Retry logic → separate module
  - Error formatting → separate module
  - Event emission → separate module
- **ActorRuntime (1171 LOC)** contains: task serialization, lifetime caching, LLM client management, generation locking. Could benefit from state machine pattern.
- How much code is duplicated between strategies?

---

## Layer 4: Public API & Documentation (~4 files, ~1,000 LOC)

Top-level classes and introspection. Uses lazy imports. Depends on Layers 1-3.

### 4A. Public API

| # | File | LOC | Lazy Imports | Description |
|---|------|-----|--------------|-------------|
| 43 | [decorators.py](src/agent006/decorators.py) | 190 | types, errors | @agent and @plan decorators |
| 44 | [agent.py](src/agent006/agent.py) | 214 | runtime, context, llm | Base Agent class |
| 45 | [introspection.py](src/agent006/introspection.py) | 72 | runtime | Agent introspection helpers |

### 4B. Documentation Generation

| # | File | LOC | Key Imports | Description |
|---|------|-----|-------------|-------------|
| 46 | [util/doc.py](src/agent006/util/doc.py) | 515 | inspect, agent | Self-documentation (15 funcs) |

**Review focus**:
- These are the entry points users see - review decorator complexity, Agent.__init__ flow
- util/doc.py at 515 LOC with 15 functions could split into method_inspector.py, variable_inspector.py

---

## Layer 5: Evaluation Framework (~10 files, ~4,500 LOC)

Semi-independent subsystem. Minimal dependencies on core agent framework. Could be a separate package.

### 5A. Protocol & Utilities

| # | File | LOC | Description |
|---|------|-----|-------------|
| 47 | [evaluation/protocol.py](src/agent006/evaluation/protocol.py) | 312 | Evaluation protocol types |
| 48 | [evaluation/llm/retry.py](src/agent006/evaluation/llm/retry.py) | 205 | LLM call retry logic |
| 49 | [evaluation/llm/batching_client.py](src/agent006/evaluation/llm/batching_client.py) | 314 | Batch LLM client |

### 5B. Core Evaluation

| # | File | LOC | Description |
|---|------|-----|-------------|
| 50 | [evaluation/metrics.py](src/agent006/evaluation/metrics.py) | 664 | Metrics calculator |
| 51 | [evaluation/trace_analyzer.py](src/agent006/evaluation/trace_analyzer.py) | 458 | Trace inspection |
| 52 | [evaluation/runner.py](src/agent006/evaluation/runner.py) | 527 | Experiment runner |

### 5C. Benchmark Adapters

| # | File | LOC | Description |
|---|------|-----|-------------|
| 53 | [evaluation/adapters/tau_bench.py](src/agent006/evaluation/adapters/tau_bench.py) | 657 | TAU-Bench |
| 54 | [evaluation/adapters/intercode.py](src/agent006/evaluation/adapters/intercode.py) | 663 | InterCode |
| 55 | [evaluation/adapters/livecodebench.py](src/agent006/evaluation/adapters/livecodebench.py) | 693 | LiveCodeBench |
| 56 | [evaluation/adapters/bfcl.py](src/agent006/evaluation/adapters/bfcl.py) | 854 | BFCL (largest) |

**Review focus**:
- Could evaluation be extracted to separate package?
- Adapters share common patterns (657-854 LOC each) - extract shared base class for 30-40% reduction

---

## Package Init Files (Review Last)

| File | LOC | Purpose |
|------|-----|---------|
| [__init__.py](src/agent006/__init__.py) | 104 | Public API exports |
| [tools/__init__.py](src/agent006/tools/__init__.py) | 10 | Tool re-exports |
| [context/__init__.py](src/agent006/context/__init__.py) | 11 | Context re-exports |
| [llm/__init__.py](src/agent006/llm/__init__.py) | 19 | LLM re-exports |
| [util/__init__.py](src/agent006/util/__init__.py) | 22 | Util re-exports |
| [runtime/__init__.py](src/agent006/runtime/__init__.py) | 5 | Runtime re-exports |
| + evaluation `__init__.py` files | ~150 | Evaluation re-exports |

---

## Dependency Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Foundation & Primitives (~1,700 LOC)              │
│  config, types, errors, context vars, protocols, formatters │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Services (~4,500 LOC)                             │
│  LLM clients, tracing, context rendering, tools, prompts    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: Execution Engine (~3,000 LOC) ← CRITICAL          │
│  ExecutorBase (1016), Strategies, ActorRuntime (1171)       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: Public API & Docs (~1,000 LOC)                    │
│  Agent, decorators, introspection, doc generation           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Layer 5: Evaluation (~4,500 LOC) ← SEMI-INDEPENDENT        │
│  Protocol, metrics, runner, benchmark adapters              │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Simplification Opportunities

### High Impact (Layer 3)

1. **ExecutorBase (1016 LOC)** - Split into focused modules
2. **ActorRuntime (1171 LOC)** - Extract state machine, reduce coupling

### Medium Impact (Layers 1-2)

3. **Tracing (4 implementations)** - Consolidate to 2 (noop + otel)
4. **Context utilities** - Merge context_blocks.py, context.py, prompt.py
5. **util/doc.py (515 LOC)** - Split into sub-modules

### Lower Impact (Layer 5)

6. **Evaluation adapters** - Extract shared base class (30-40% reduction)
7. **Tool wrappers** - Generic base tool class

---

## Progress Tracking

- [ ] **Layer 1**: Foundation & Primitives (13 files, ~1,700 LOC)
- [ ] **Layer 2**: Services (24 files, ~4,500 LOC)
- [ ] **Layer 3**: Execution Engine (5 files, ~3,000 LOC) ← **Critical**
- [ ] **Layer 4**: Public API & Docs (4 files, ~1,000 LOC)
- [ ] **Layer 5**: Evaluation (10 files, ~4,500 LOC)
- [ ] Package Init Files
