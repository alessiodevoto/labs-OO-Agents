# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in long-term memory subsystem for NeMo OO Agents.

A brain-inspired, fully *additive* memory add-on: spontaneous association
(similarity recall injected each turn), deliberate recall (conscious tools),
encoding (write on important events), reflection (offline consolidation), and
forgetting (online decay + offline pruning).

Quick start::

    from nemo_oo_agents import Agent
    from nemo_oo_agents.memory import MemoryConfig, MemoryManager, MemoryToolsMixin

    class MyAgent(MemoryToolsMixin, Agent, llm=my_llm):
        async def work(self, task: str) -> str:
            # Do {task}. Use self.recall(...) to consult memory.
            ...

    agent = MyAgent()
    MemoryManager.install(agent, config=MemoryConfig(enabled=True))
    agent.remember("Deploy with `make ship`.", type="skill", importance="HIGH")

See ``docs/design/memory-system/design.md`` for the full design.
"""

from nemo_oo_agents.memory.config import (
    EmbeddingConfig,
    ForgetPolicy,
    MemoryConfig,
    ReflectionPolicy,
    RetrievalConfig,
    ScoringWeights,
    SpontaneousConfig,
    VectorConfig,
    WritePolicy,
)
from nemo_oo_agents.memory.descriptors import ladder, to_label, to_numeric
from nemo_oo_agents.memory.embeddings import (
    Embedder,
    HashingEmbedder,
    LiteLLMEmbedder,
    get_embedder,
)
from nemo_oo_agents.memory.manager import MemoryManager, MemoryToolsMixin
from nemo_oo_agents.memory.monitoring import (
    MemoryInjected,
    MemoryRecalled,
    MemoryStats,
    MemoryWritten,
    ReflectionCompleted,
)
from nemo_oo_agents.memory.schema import Edge, EdgeType, Memory, MemoryType
from nemo_oo_agents.memory.store import MemoryStore
from nemo_oo_agents.memory.vector_backends import (
    ChromaVectorIndex,
    NumpyVectorIndex,
    SqliteVecVectorIndex,
    VectorIndex,
    make_vector_index,
)

__all__ = [
    # schema
    "Memory",
    "MemoryType",
    "Edge",
    "EdgeType",
    # config
    "MemoryConfig",
    "RetrievalConfig",
    "SpontaneousConfig",
    "ScoringWeights",
    "EmbeddingConfig",
    "VectorConfig",
    "WritePolicy",
    "ReflectionPolicy",
    "ForgetPolicy",
    # embeddings
    "Embedder",
    "HashingEmbedder",
    "LiteLLMEmbedder",
    "get_embedder",
    # store
    "MemoryStore",
    # vector backends
    "VectorIndex",
    "NumpyVectorIndex",
    "SqliteVecVectorIndex",
    "ChromaVectorIndex",
    "make_vector_index",
    # verbal descriptors (agent-facing vocabulary)
    "ladder",
    "to_numeric",
    "to_label",
    # manager / integration
    "MemoryManager",
    "MemoryToolsMixin",
    # monitoring
    "MemoryStats",
    "MemoryWritten",
    "MemoryRecalled",
    "MemoryInjected",
    "ReflectionCompleted",
]
