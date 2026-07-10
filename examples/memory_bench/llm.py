# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LLM + embedding builders for the memory benchmark.

Real models (NVIDIA gateway), configured purely from the environment so no keys
are committed:

* **LLM (gpt-5.4)** — mirrors ``examples/arc_agi/llm.py``::

      ARC_LLM_MODEL=openai/openai/gpt-5.4         # gateway model id
      ARC_LLM_BASE_URL=https://inference-api.nvidia.com/v1
      ARC_LLM_API_KEY=...

  litellm wants the ``openai/`` provider prefix on top of the gateway id, so the
  final model string becomes ``openai/openai/openai/gpt-5.4``.

* **Embeddings (text-embedding-3-large)** — via the same gateway::

      MEM_EMBED_MODEL=openai/azure/openai/text-embedding-3-large
      MEM_EMBED_BASE_URL=https://inference-api.nvidia.com/v1/embeddings
      MEM_EMBED_API_KEY=...
      MEM_EMBED_DIMS=1024        # optional; text-embedding-3-large supports reduction

When the credentials are absent the builders fall back to a ``FakeLLMClient`` and
the deterministic ``hashing`` embedder, so the benchmark still runs offline.
"""

from __future__ import annotations

import os
from pathlib import Path

from nooa_memory.config import EmbeddingConfig

from nooa.unifiedllm import CompletionClient, FakeLLMClient, RetryConfig

# Defaults requested for this benchmark. The gateway uses the OpenAI-compatible
# route, so litellm appends "/embeddings" to the base — the base is ".../v1".
_DEFAULT_EMBED_MODEL = "openai/azure/openai/text-embedding-3-large"
_DEFAULT_EMBED_BASE = "https://inference-api.nvidia.com/v1"


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency): real environment wins via setdefault."""
    for d in [Path.cwd(), *Path(__file__).resolve().parents]:
        f = d / ".env"
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
            return


_load_dotenv()


def has_llm_creds() -> bool:
    return all(os.environ.get(k) for k in ("ARC_LLM_MODEL", "ARC_LLM_BASE_URL", "ARC_LLM_API_KEY"))


def has_embedding_creds() -> bool:
    return bool(os.environ.get("MEM_EMBED_API_KEY"))


def build_llm(
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    request_timeout: float | None = 90.0,
):
    """Real gpt-5.4 client when ARC_LLM_* is set, else a FakeLLMClient."""
    if not has_llm_creds():
        return FakeLLMClient()
    extra: dict[str, object] = {}
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    if reasoning_effort:
        extra["reasoning_effort"] = reasoning_effort
    if request_timeout is not None:
        # HARD per-request HTTP timeout passed through to litellm. Without it, a gateway
        # request that hangs (no response, no error) blocks forever — an asyncio.wait_for
        # wrapper can't cancel it because the underlying call isn't cancellation-responsive,
        # so hung requests permanently occupy concurrency slots and deadlock the run. With a
        # timeout, litellm aborts the request and retry_config retries; slots always free up.
        extra["timeout"] = request_timeout
    model = "openai/" + os.environ["ARC_LLM_MODEL"]
    # Some reasoning models (e.g. Nemotron) return the final answer in `reasoning_content`
    # with an EMPTY `content` field. `retry_on_empty_content=True` treats that as a transient
    # failure and exhausts its retries (then raises) *before* the runtime's built-in
    # reasoning-as-content fallback can parse the structured answer. For such models the empty
    # content is persistent, so disable the retry and let the fallback do its job.
    reasoning_model = "nemotron" in model.lower()
    return CompletionClient(
        model=model,
        api_base=os.environ["ARC_LLM_BASE_URL"],
        api_key=os.environ["ARC_LLM_API_KEY"],
        retry_config=RetryConfig(retry_on_empty_content=not reasoning_model),
        drop_params=True,  # the gateway rejects some params on the reasoning route
        **extra,
    )


def build_embedding_config(force: str = "auto") -> EmbeddingConfig:
    """text-embedding-3-large via the gateway when keys are set, else hashing.

    ``force`` ∈ {"auto", "litellm", "hashing"}.
    """
    want_real = force == "litellm" or (force == "auto" and has_embedding_creds())
    if want_real:
        dims = os.environ.get("MEM_EMBED_DIMS")
        return EmbeddingConfig(
            backend="litellm",
            model=os.environ.get("MEM_EMBED_MODEL", _DEFAULT_EMBED_MODEL),
            endpoint=os.environ.get("MEM_EMBED_BASE_URL", _DEFAULT_EMBED_BASE),
            api_key=os.environ.get("MEM_EMBED_API_KEY"),
            dimensions=int(dims) if dims else 1024,
        )
    return EmbeddingConfig(backend="hashing", dim=256)
