# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Memory record schema — intentionally *loose*.

Only ``id``, ``type``, ``content`` and ``created_at`` are required; every
descriptor, metadata field and edge is optional with a sane default, so a
memory can be a one-line fact or a fully-annotated, graph-linked episode.

Grounding (see ``docs/design/memory-system/research-notes.md``): the type
taxonomy follows Tulving/Squire (episodic/semantic/procedural + prospective +
working); descriptors (importance/salience/confidence/mood) follow the
poignancy/emotional-tagging literature; ``access_log``/``strength`` feed the
ACT-R base-level activation and the Ebbinghaus spaced-repetition decay.
"""

from __future__ import annotations

import re
import time
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field

from nemo_oo_agents.memory.descriptors import to_label


class MemoryType(StrEnum):
    """Taxonomy of memory kinds (extends the user's ``skill`` + ``info``)."""

    INFO = "info"  # semantic: facts, prefs, domain rules, conventions
    SKILL = "skill"  # procedural: reusable verified procedures + applicability
    EPISODE = "episode"  # episodic: a specific task run (goal/actions/outcome)
    INTENT = "intent"  # prospective: future intention / reminder with a trigger
    REFLECTION = "reflection"  # schema/gist: insight distilled from episodes
    SCRATCH = "scratch"  # working memory: transient, never durably consolidated


class EdgeType(StrEnum):
    """Typed, directed edges of the memory graph."""

    DERIVED_FROM = "derived_from"  # CAUSAL provenance: this came from <target>
    CREATED_BY = "created_by"  # CAUSAL: task/event that encoded this
    SUPPORTS = "supports"  # corroborating evidence
    CONTRADICTS = "contradicts"  # conflicting belief
    REFINES = "refines"  # this updates/sharpens a prior memory
    RELATED = "related"  # generic associative/semantic link
    CAUSES = "causes"  # domain causal: A -> outcome B
    PRECEDES = "precedes"  # temporal ordering of episodes
    PART_OF = "part_of"  # composition (skill composed of sub-skills)
    TRIGGERS = "triggers"  # intent -> action (prospective)


# Edge types whose causal/structural meaning warrants a higher default traversal
# weight than a generic ``related`` link.
CAUSAL_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.DERIVED_FROM, EdgeType.CREATED_BY, EdgeType.CAUSES, EdgeType.REFINES}
)


# Max length of a memory's access_log (bounds row size; older accesses drop off).
_ACCESS_LOG_CAP = 64


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


class Edge(BaseModel):
    """A directed edge from one memory to another."""

    target_id: str
    type: EdgeType = EdgeType.RELATED
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: float = Field(default_factory=_now)


class Memory(BaseModel):
    """A single long-term memory record."""

    # --- identity (required: id/type/content) ---
    id: str = Field(default_factory=_new_id)
    type: MemoryType = MemoryType.INFO
    title: str | None = None
    content: str

    # --- structural (auto-derived from content if left at defaults) ---
    size_chars: int = 0
    token_len: int = 0
    sentence_count: int = 0

    # --- descriptors ---
    importance: float = Field(default=5.0, ge=0.0, le=10.0)  # LLM "poignancy" 1-10
    salience: float = Field(default=0.0, ge=0.0, le=1.0)  # outcome/surprise/novelty tag
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)  # belief certainty
    mood: str | None = None
    strength: int = 1  # spaced-repetition counter (+1 on recall)
    reinforcement_count: int = 0  # merge/replay reinforcement tally

    # --- metadata ---
    created_at: float = Field(default_factory=_now)
    last_accessed_at: float = Field(default_factory=_now)
    access_log: list[float] = Field(default_factory=list)  # recent access timestamps
    access_count: int = 0
    source_task_ref: str | None = None
    related_files: list[str] = Field(default_factory=list)
    chat_turn_ref: str | None = None
    valid_from: float | None = None
    valid_to: float | None = None  # bi-temporal: invalidate-don't-delete
    trigger: dict | None = None  # INTENT only: {kind, cue, fire_at}

    # --- encoding context cue (for encoding-specificity overlap) ---
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    place_or_task: str | None = None

    # --- graph + lifecycle ---
    edges: list[Edge] = Field(default_factory=list)
    archived: bool = False  # soft-delete tombstone (forgotten but recoverable)

    def model_post_init(self, __context: object) -> None:  # noqa: D105
        # Derive structural fields when not explicitly provided.
        if not self.size_chars:
            self.size_chars = len(self.content)
        if not self.token_len:
            self.token_len = max(1, len(self.content) // 4)  # ~4 chars/token
        if not self.sentence_count:
            self.sentence_count = max(1, len(re.findall(r"[.!?]+", self.content)) or 1)
        if not self.access_log:
            self.access_log = [self.created_at]

    # ------------------------------------------------------------------
    # behaviour
    # ------------------------------------------------------------------
    def embedding_text(self) -> str:
        """Text to embed: title + content + cue tags/entities (boosts recall)."""
        parts: list[str] = []
        if self.title:
            parts.append(self.title)
        parts.append(self.content)
        if self.tags:
            parts.append(" ".join(self.tags))
        if self.entities:
            parts.append(" ".join(self.entities))
        return "\n".join(parts)

    def touch(self, *, when: float | None = None, reinforce: bool = True) -> None:
        """Record an access: bump recency/frequency and (optionally) strength.

        This is the online half of memory dynamics — retrieval strengthens a
        memory (anti-forgetting) and refreshes its recency.
        """
        ts = _now() if when is None else when
        self.last_accessed_at = ts
        self.access_count += 1
        self.access_log.append(ts)
        if len(self.access_log) > _ACCESS_LOG_CAP:
            self.access_log = self.access_log[-_ACCESS_LOG_CAP:]
        if reinforce:
            self.strength += 1

    def add_edge(
        self, target_id: str, type: EdgeType = EdgeType.RELATED, weight: float = 1.0
    ) -> None:
        """Add (or update the weight of) a directed edge to ``target_id``."""
        for e in self.edges:
            if e.target_id == target_id and e.type == type:
                e.weight = max(e.weight, weight)
                return
        self.edges.append(Edge(target_id=target_id, type=type, weight=weight))

    def cue_set(self) -> set[str]:
        """Lower-cased tag + entity + place cues, for Jaccard context overlap."""
        cues = {t.lower() for t in self.tags} | {e.lower() for e in self.entities}
        if self.place_or_task:
            cues.add(self.place_or_task.lower())
        return cues

    # ------------------------------------------------------------------
    # verbal descriptor rendering (numeric -> ALL-CAPS band, for the agent)
    # ------------------------------------------------------------------
    def importance_label(self) -> str:
        """This memory's importance as a verbal band (CRITICAL..TRIVIAL)."""
        return to_label("importance", self.importance)

    def salience_label(self) -> str:
        """This memory's salience as a verbal band (PIVOTAL..NONE)."""
        return to_label("salience", self.salience)

    def confidence_label(self) -> str:
        """This memory's confidence as a verbal band (CERTAIN..UNCERTAIN)."""
        return to_label("confidence", self.confidence)
