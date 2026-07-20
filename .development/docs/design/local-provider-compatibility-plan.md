# Local provider compatibility plan

## Context

MR 470 is a narrow self-hosted vLLM change, not a general local-provider
compatibility effort. The source diff adds:

- `nemotron-3-ultra-selfhost` and `nemotron-3-ultra-selfhost-nothink` aliases to
  `packages/nemo-oo-agents-nvidia/src/nemo_oo_agents_nvidia/data/llm_config_default.yaml`.
- `extra_body` passthrough in the registry.
- `util/slurm/host_nemotron_ultra.sbatch` for hosting Nemotron Ultra via vLLM.

The useful direction is right: self-hosted OpenAI-compatible endpoints need
provider-specific passthrough and reproducible launch instructions. It is not
enough to prove compatibility with Ollama, vLLM, llama.cpp, or other local
servers.

## Findings from MR 470 review

1. The active checkout's registry already preserves `extra_body`,
   `allowed_openai_params`, and `additional_drop_params` in
   `src/nooa/unifiedllm/registry.py`, so the MR's `extra_body` registry change is
   already effectively covered in this line of development.

2. The proposed aliases use:

   ```yaml
   api_base_env: NEMO_OO_SELFHOST_ULTRA_BASE
   api_key: EMPTY
   ```

   This adds another config indirection for a value that users already need to
   choose at runtime. Prefer the simpler public contract: pass the model name and
   base URL explicitly, usually from `NOOA_MODEL` and `NOOA_API_BASE`. A local
   verification with MR-style YAML produced:

   ```text
   api_base=None
   api_key=None
   extra_body={'chat_template_kwargs': {'enable_thinking': False}}
   ```

   So the alias shape in MR 470 would not currently route calls to the tunneled
   vLLM endpoint unless another branch has already added that support.

3. Direct TUI startup has its own model construction path, but TUI behavior is
   out of scope for this plan. The work here is limited to core library behavior
   and non-TUI integrations that already consume registry defaults.

4. `tests/external/test_providers.py` is mostly config assertion coverage. It does
   not exercise the framework through `Agent`, `CompletionClient`, tool calls,
   structured outputs, streaming, or real local endpoint behavior.

5. Literal `api_key` in `llm_config.yaml` is a safety-sensitive addition. The CLI
   and viewer currently reason about `api_key_env` and redacted `secrets.yaml`;
   they do not consistently redact a literal `api_key` carried inside a model
   config. Prefer `api_key_env` for real secrets, and only add literal `api_key`
   support if all display and API surfaces redact it.

## External provider facts checked

Checked on 2026-07-10:

- LiteLLM recommends `ollama_chat` for Ollama and shows `api_base` pointing at the
  Ollama server: <https://docs.litellm.ai/docs/providers/ollama>
- LiteLLM's OpenAI-compatible route requires an API key or `OPENAI_API_KEY`, and
  suggests provider-specific routes such as `hosted_vllm` or `llamafile` when a
  fake key is undesirable:
  <https://docs.litellm.ai/docs/providers/openai_compatible>
- LiteLLM documents vLLM through `hosted_vllm/<model>` plus `api_base`:
  <https://docs.litellm.ai/docs/providers/vllm>
- vLLM serves OpenAI-compatible chat completions, responses, embeddings, and
  tools, with tool calling requiring server flags such as
  `--enable-auto-tool-choice` and `--tool-call-parser`:
  <https://docs.vllm.ai/en/stable/serving/online_serving/> and
  <https://docs.vllm.ai/en/latest/features/tool_calling/>
- Ollama documents `/v1/chat/completions` with tools and reasoning controls, and
  `/v1/responses` with caveats:
  <https://docs.ollama.com/api/openai-compatibility>
- llama.cpp `llama-server` documents OpenAI-compatible chat completions,
  responses, and embeddings:
  <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>

## Target contract

Support hosted and local providers through the same public surface users already
use:

```python
import os

from nooa.unifiedllm import get_llm_client

llm = get_llm_client("gpt-4o-mini")
llm = get_llm_client("claude-sonnet-4-5-20250514")
llm = get_llm_client(os.environ["NOOA_MODEL"], api_base=os.environ["NOOA_API_BASE"])
```

A provider is compatible when the following all work through NeMo OO Agents, not
just through raw LiteLLM:

- Plain chat generation.
- CodeAct tool call round trip.
- PredictStrategy structured output with a Pydantic return model.
- Streaming or stream consumption path, if the provider route supports it.
- Context-window metadata and provider usage handling degrade cleanly when usage
  is absent or approximate.

## Local provider patterns

Recommended default examples:

```bash
export NOOA_MODEL=ollama_chat/qwen3:8b
export NOOA_API_BASE=http://localhost:11434
```

```bash
export NOOA_MODEL=hosted_vllm/Qwen/Qwen3-1.7B
export NOOA_API_BASE=http://localhost:8000/v1
```

```bash
export NOOA_MODEL=openai/local-model
export NOOA_API_BASE=http://localhost:8080/v1
export OPENAI_API_KEY=EMPTY
```

Notes:

- Ollama's base URL should usually be `http://localhost:11434`.
- vLLM's base URL should usually include `/v1`, for example
  `http://localhost:8000/v1`.
- llama.cpp's base URL should usually include `/v1`, for example
  `http://localhost:8080/v1`.
- For llama.cpp or generic `openai/` local endpoints, set
  `OPENAI_API_KEY=EMPTY` or another dummy value unless the server enforces a
  real key.
- For vLLM, prefer `hosted_vllm/` because LiteLLM resolves a fake API key for that
  route; use `openai/<served-model-name>` only when there is a reason to exercise
  the generic OpenAI-compatible path.

## Implementation plan

### 1. Registry and config resolution

- Keep `get_llm_client(model, api_base=...)` as the primary local-provider
  surface.
- Keep existing static `api_base` support for stable aliases and gateways.
- Do not add `api_base_env`; let applications and examples read `NOOA_MODEL` and
  `NOOA_API_BASE` and pass values explicitly.
- Decide explicitly on literal `api_key`:
  - Conservative option: do not support literal `api_key` from YAML; update MR 470
    aliases to use `api_key_env` with a documented dummy env var.
  - If literal `api_key` is supported, first update `resolved_config()`, viewer
    playground model APIs, `nooa config show`, and any `model_dump()` paths to
    redact it. Add regression tests proving no literal key appears in printed or
    JSON output.
- Keep `extra_body`, `allowed_openai_params`, and `additional_drop_params`
  passthrough tests. Add provider-oriented examples using `extra_body` for vLLM
  thinking/tool-parser cases.

### 2. Keep non-TUI registry consumers consistent

- Any integration that consumes registry defaults directly, such as the NAT
  bridge, should preserve the same precedence: explicit integration config wins,
  then static registry `api_base`, then provider defaults.
- Leave TUI startup behavior out of scope for now.

### 3. Compatibility harness

Create `tests/provider_compat/` with a small reusable test agent:

- `EchoAgent.summarize()` for plain generation.
- `ToolAgent.answer_with_tool()` using one deterministic helper.
- `ExtractAgent.extract()` returning a simple Pydantic model via
  `PredictStrategy`.

Add a `ProviderSpec` table:

```python
ProviderSpec(
    id="ollama",
    model_env="NOOA_TEST_OLLAMA_MODEL",
    base_url_env="NOOA_TEST_OLLAMA_BASE_URL",
    default_model="ollama_chat/qwen3:8b",
    supports_tools=True,
    supports_structured_output=True,
)
```

Run tests only when the required env vars are present. Skipped tests should say
which provider/env var is missing. This keeps normal CI fast and deterministic.

### 4. Local smoke servers for scheduled CI

Add an optional scheduled/manual CI job, separate from merge-blocking unit tests:

- Ollama: start from a service image or documented runner image, pull a small
  model only in scheduled CI, and run the provider harness.
- vLLM: use a tiny model on a GPU runner, or keep this as manual/Nightly on the
  internal GPU pool. Verify `/v1/models` before tests.
- llama.cpp: use a small GGUF checked into an internal cache or downloaded in a
  scheduled job; start `llama-server` and run the generic OpenAI-compatible tests.

Do not make live provider tests block ordinary MRs at first. Promote individual
providers to blocking only after the job has been stable for several weeks.

### 5. Examples and docs

Add `examples/local_providers/`:

- `ollama_quickstart.py`
- `vllm_openai_server.py`
- `llamacpp_server.py`
- `README.md` with copy-paste `llm_config.yaml`, server start commands, env vars,
  and known limitations.

Each example should be a real agent example, not a raw LiteLLM curl. Use:

- one generation method;
- one deterministic helper invoked by CodeAct;
- one Pydantic structured-output method with `PredictStrategy`;
- an explicit `uv run python ...` command.

Update `README.md` and `REFERENCE.md` to point to these examples from the LLM
section.

### 6. MR 470 follow-up

Before merging MR 470 or a successor:

- Rebase or retarget it onto the active package layout for the branch being
  merged.
- Replace `api_base_env` with either a static `api_base` alias for fixed
  endpoints or explicit `api_base=` at the call site for local/runtime endpoints.
- Replace `api_key: EMPTY` with `api_key_env` unless literal-key redaction lands
  first.
- Add a unit test that loads the self-host alias and asserts `api_base`,
  `api_key` or dummy-key behavior, `extra_body`, `temperature`, `top_p`, and
  `max_tokens` are present on the constructed client.
- Add a README next to `util/slurm/host_nemotron_ultra.sbatch` with the tunnel,
  `/v1/models` health check, expected model id, and the exact
  `llm_config.yaml` alias.

## Verification checklist

Unit:

- `uv run pytest tests/unifiedllm/test_model_registry.py`
- `uv run pytest tests/config/test_resolved_config.py`
- New `tests/provider_compat/test_registry_aliases.py`

Local live:

- `NOOA_TEST_OLLAMA_BASE_URL=http://localhost:11434 uv run pytest tests/provider_compat -m provider_compat_ollama`
- `NOOA_TEST_VLLM_BASE_URL=http://localhost:8000/v1 uv run pytest tests/provider_compat -m provider_compat_vllm`
- `NOOA_TEST_LLAMACPP_BASE_URL=http://localhost:8080/v1 LLAMACPP_API_KEY=EMPTY uv run pytest tests/provider_compat -m provider_compat_llamacpp`

## Risks

- Local model quality can make agent tests flaky. Keep assertions behavioral and
  bounded, not semantic beyond simple exact tasks.
- Tool calling support depends on server flags and model templates, especially
  for vLLM. The harness should mark tool support per provider/model combination.
- Literal `api_key` support is convenient but easy to leak through config display
  and viewer APIs. Prefer `api_key_env` unless redaction is implemented first.
- Responses API support differs by provider. Default local examples should use
  Chat Completions clients; add Responses-specific tests only for providers that
  explicitly support them or when using LiteLLM's chat-completions bridge.
