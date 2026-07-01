# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configuration for the memory subsystem.

Frozen Pydantic configs (mirrors ``config/summarizer_config.py``). Every knob
the design lists is here; nested sub-configs keep the surface legible.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ScoringWeights(BaseModel, frozen=True):
    """Weights for the retrieval scoring function (see ``retrieval.py``)."""

    relevance: float = 1.0  # alpha_rel — embedding/context similarity
    recency: float = 0.5  # alpha_rec — ACT-R base-level activation
    importance: float = 0.5  # alpha_imp — stored poignancy
    lambda_embed: float = 0.7  # blend of cosine vs context-cue overlap in relevance
    base_level_decay: float = 0.5  # ACT-R d
    spread_gamma: float = 0.5  # associative-spread gain (1-hop)
    recency_half_life_hours: float = 168.0  # used by the simple decay fallback


class RetrievalConfig(BaseModel, frozen=True):
    """How candidates are recalled, scored and traversed."""

    top_k: int = 5  # final memories returned per recall
    n_dense: int = 50  # first-stage dense candidate pool
    n_sparse: int = 50  # first-stage keyword candidate pool
    hops: int = 1  # 0 = vector-only; 1 = +1 graph hop; 2+ = multi-hop
    per_hop_decay: float = 0.6  # delta — activation decay per hop
    per_hop_fanout: int = 5  # beam: neighbours expanded per node
    activation_floor: float = 0.05  # theta — stop propagating below this
    min_similarity: float = 0.0  # cosine floor on dense candidates
    weights: ScoringWeights = ScoringWeights()


class SpontaneousConfig(BaseModel, frozen=True):
    """Pre-turn 'spontaneous association' injection."""

    enabled: bool = True
    # which query strategies to derive the per-turn query from (composable)
    query_strategies: tuple[str, ...] = ("last_message",)
    recent_events_n: int = 5  # window for the 'recent_events' strategy
    inject_cadence: Literal["self_gated", "per_task", "every_turn"] = "self_gated"
    context_block_key: str = "recalled_memories"
    context_char_budget: int = 2000  # hard cap on the injected block string
    top_k: int = 5  # memories to inject (defaults to retrieval.top_k if 0)


class EmbeddingConfig(BaseModel, frozen=True):
    """Embedding backend selection."""

    # "hashing" = deterministic, offline, dependency-free (default, great for tests);
    # "litellm" = call an OpenAI-compatible /embeddings endpoint (e.g. NVIDIA gateway).
    backend: Literal["hashing", "litellm"] = "hashing"
    # For the NVIDIA gateway + text-embedding-3-large, use the litellm openai provider
    # prefix, e.g. "openai/azure/openai/text-embedding-3-large".
    model: str = "nvidia/nv-embedqa-e5-v5"  # used when backend == "litellm"
    endpoint: str | None = None  # api_base override for litellm (e.g. the gateway /v1)
    api_key: str | None = None
    dim: int = 256  # hashing dimension (litellm infers from the response)
    dimensions: int | None = None  # OpenAI 'dimensions' param (3-large supports reduction)
    batch_size: int = 64
    timeout: float = 60.0  # per-request HTTP timeout for litellm.embedding (avoid silent hangs)
    num_retries: int = 2  # retry transient embedding failures (incl. timeouts) before raising


class VectorConfig(BaseModel, frozen=True):
    """Vector-index backend selection (swap without changing any other code).

    * ``numpy``          — exact brute-force cosine, no extra deps (default).
    * ``sqlite_vec``     — ANN inside the same SQLite file (needs ``sqlite-vec``).
    * ``chroma_embedded``— Chroma over a local dir beside the store (needs ``chromadb``).
    * ``chroma_http``    — a running Chroma server (needs ``chromadb``).
    """

    backend: Literal["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"] = "numpy"
    collection: str = "agent_memory"
    host: str | None = None  # chroma_http
    port: int | None = None


class WritePolicy(BaseModel, frozen=True):
    """When and how memories are encoded."""

    on_events: tuple[str, ...] = ("Notification", "Error")  # event types -> auto-write
    salience_min: float = 0.0  # gate: only write if salience >= this
    dedup_threshold: float = 0.92  # cosine >= this on write -> NOOP (reinforce instead)
    dedup_top_k: int = 5  # neighbours checked on write
    write_episodic: bool = True  # write a task episode post-task (for reflection)
    default_importance: float = 5.0


class ReflectionPolicy(BaseModel, frozen=True):
    """Offline consolidation after a task."""

    enabled: bool = True
    trigger: Literal["post_task", "manual"] = "post_task"
    only_top_level: bool = True  # nested subagent calls do NOT each reflect
    entrypoint_methods: tuple[str, ...] = ()  # () = any top-level method
    background: bool = False  # False = await inline (deterministic); True = create_task
    max_episodes_per_reflection: int = 50
    merge_threshold: float = 0.95  # cosine >= this -> merge duplicates
    edge_threshold: float = 0.80  # cosine >= this -> add a 'related' edge
    max_edges_per_node: int = 5
    # reconsolidation: cluster related memories (cosine >= recon_threshold) and let a
    # reconciler resolve outdated/contradicted values (keep the current one).
    recon_threshold: float = 0.6
    recon_max_cluster: int = 6


class ForgetPolicy(BaseModel, frozen=True):
    """Online decay + offline pruning."""

    enabled: bool = True
    decay_half_life_hours: float = 336.0  # 14 days; Ebbinghaus-style retention
    fetch_boost: float = 1.0  # strength gain per retrieval (handled in Memory.touch)
    prune_activation_threshold: float = 0.02  # below this (and stale) -> prune
    prune_min_age_hours: float = 24.0  # never prune very young memories
    archive_vs_delete: Literal["archive", "delete"] = "archive"
    protected_types: tuple[str, ...] = ("skill",)  # never auto-forgotten


class MemoryConfig(BaseModel, frozen=True):
    """Top-level memory configuration."""

    enabled: bool = False  # master on/off (additive guarantee when False)
    path: str | None = None  # SQLite file; None -> project memory dir
    tools: tuple[str, ...] = (
        "recall",
        "search",
        "remember",
        "update_memory",
        "forget",
        "associate",
    )
    # Inject the schema + "you own and curate your memory" instruction into the
    # agent's context (the core difference: the AGENT authors its memories).
    instruct: bool = True
    instruct_block_key: str = "memory_system"
    chunk_size: int = 512
    chunk_overlap: int = 64

    spontaneous: SpontaneousConfig = SpontaneousConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    vector: VectorConfig = VectorConfig()
    write: WritePolicy = WritePolicy()
    reflection: ReflectionPolicy = ReflectionPolicy()
    forget: ForgetPolicy = ForgetPolicy()

    def merge_with(self, **overrides: object) -> MemoryConfig:
        """Return a copy with top-level fields overridden."""
        return self.model_copy(update=overrides)
