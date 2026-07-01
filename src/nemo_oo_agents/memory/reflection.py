# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reflection — offline consolidation after a task.

A pure-Python orchestrator running ordered ops that mirror the biological
sequence (clean -> abstract -> renormalise -> forget). The deterministic steps
need no LLM and run by default:

  1. dedup / merge      — fold near-identical memories into a canonical record
  2. edge formation     — link memories whose embeddings are close
  3. re-score importance — bump salient/frequently-recalled memories
  4. prune / forget      — archive memories that have decayed (ForgettingEngine)

An optional ``reasoner`` callable enables the generative abstraction step (abstract
episodes -> skills); when absent that step is skipped so reflection stays fully
offline-testable. See ``docs/design/memory-system/design.md`` §4.2.4.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from pydantic import BaseModel

from nemo_oo_agents.memory.config import ForgetPolicy, ReflectionPolicy
from nemo_oo_agents.memory.embeddings import Embedder
from nemo_oo_agents.memory.forgetting import ForgettingEngine
from nemo_oo_agents.memory.retrieval import _sigmoid, base_level_activation
from nemo_oo_agents.memory.schema import EdgeType, Memory, _now
from nemo_oo_agents.memory.store import MemoryStore


class ReflectionReport(BaseModel):
    """Summary of what a consolidation pass changed (for logs/auditability)."""

    merged: int = 0
    edges_added: int = 0
    rescored: int = 0
    pruned: int = 0
    created: int = 0
    reconciled: int = 0  # clusters where an updated/current value superseded older ones
    superseded: int = 0  # memories archived because they were outdated


class ReflectionEngine:
    """Runs the offline consolidation ops over a memory store."""

    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        config: ReflectionPolicy,
        forget_config: ForgetPolicy,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.config = config
        self._forgetting = ForgettingEngine(store, forget_config)

    def consolidate(
        self,
        *,
        reasoner: Callable[[list[Memory]], list[Memory]] | None = None,
        reconciler: Callable[[list[Memory]], tuple[Memory | None, list[str]]] | None = None,
    ) -> ReflectionReport:
        report = ReflectionReport()
        report.merged = self._merge_duplicates()
        if reconciler is not None:
            report.reconciled, report.superseded = self._reconsolidate(reconciler)
        report.edges_added = self._form_edges()
        report.rescored = self._rescore_importance()
        if reasoner is not None:
            report.created = self._abstract(reasoner)
        report.pruned = len(self._forgetting.prune())
        return report

    # ------------------------------------------------------------------
    # NREM: dedup / merge
    # ------------------------------------------------------------------
    def _merge_duplicates(self) -> int:
        merged: set[str] = set()
        count = 0
        # Stable order so merges are deterministic; the iteration anchor is canonical.
        anchors = sorted(self.store.all_memories(), key=lambda m: (m.created_at, m.id))
        for m in anchors:
            if m.id in merged:
                continue
            emb = self.store.get_embedding(m.id)
            if emb is None:
                continue
            dirty = False
            for nid, cos in self.store.knn(emb, 6):
                if nid == m.id or nid in merged or cos < self.config.merge_threshold:
                    continue
                dup = self.store.get(nid)
                if dup is None or dup.type != m.type:
                    continue
                # Fold the duplicate into the canonical memory.
                m.reinforcement_count += 1 + dup.reinforcement_count
                m.strength += dup.strength
                m.importance = max(m.importance, dup.importance)
                m.salience = max(m.salience, dup.salience)
                m.confidence = max(m.confidence, dup.confidence)
                for e in dup.edges:
                    if e.target_id != m.id:
                        m.add_edge(e.target_id, e.type, e.weight)
                m.add_edge(dup.id, EdgeType.REFINES, 1.0)  # provenance
                self.store.archive(dup.id)
                merged.add(nid)
                dirty = True
                count += 1
            if dirty:
                self.store.save(m)
        return count

    # ------------------------------------------------------------------
    # reconsolidation: resolve updated/contradicted facts (keep the current one)
    # ------------------------------------------------------------------
    def _reconsolidate(
        self, reconciler: Callable[[list[Memory]], tuple[Memory | None, list[str]]]
    ) -> tuple[int, int]:
        """Cluster related memories and let ``reconciler`` resolve outdated values.

        ``reconciler(cluster)`` (cluster sorted oldest->newest) returns
        ``(consolidated_current_memory_or_None, ids_to_archive)``. We archive the
        superseded memories and store the consolidated current one — so retrieval
        stops surfacing stale values. Mirrors memory reconsolidation.
        """
        reconciled = 0
        superseded = 0
        visited: set[str] = set()
        for m in sorted(self.store.all_memories(), key=lambda x: (x.created_at, x.id)):
            if m.id in visited:
                continue
            emb = self.store.get_embedding(m.id)
            if emb is None:
                continue
            cluster = [m]
            visited.add(m.id)
            for nid, cos in self.store.knn(emb, self.config.recon_max_cluster):
                if nid in visited or cos < self.config.recon_threshold:
                    continue
                c = self.store.get(nid)
                if c is not None:
                    cluster.append(c)
                    visited.add(nid)
            if len(cluster) < 2:
                continue
            cluster.sort(key=lambda x: (x.created_at, x.id))  # oldest -> newest
            try:
                consolidated, archive_ids = reconciler(cluster)
            except Exception:
                continue
            valid = {c.id for c in cluster}
            archived_here = [aid for aid in (archive_ids or []) if aid in valid]
            if not archived_here:
                continue
            for aid in archived_here:
                self.store.archive(aid)
                superseded += 1
            if consolidated is not None:
                for c in cluster:
                    consolidated.add_edge(c.id, EdgeType.REFINES, 1.0)
                self.store.add(consolidated, self.embedder.embed(consolidated.embedding_text()))
            reconciled += 1
        return reconciled, superseded

    # ------------------------------------------------------------------
    # form associative edges between close memories
    # ------------------------------------------------------------------
    def _form_edges(self) -> int:
        added = 0
        for m in self.store.all_memories():
            emb = self.store.get_embedding(m.id)
            if emb is None:
                continue
            existing = {e.target_id for e in m.edges}
            new = 0
            for nid, cos in self.store.knn(emb, self.config.max_edges_per_node + 1):
                if nid == m.id or nid in existing:
                    continue
                if cos < self.config.edge_threshold or cos >= self.config.merge_threshold:
                    continue
                m.add_edge(nid, EdgeType.RELATED, weight=round(cos, 4))
                existing.add(nid)
                new += 1
                if new >= self.config.max_edges_per_node:
                    break
            if new:
                self.store.save(m)
                added += new
        return added

    # ------------------------------------------------------------------
    # renormalise importance (salience + access-frequency aware)
    # ------------------------------------------------------------------
    def _rescore_importance(self) -> int:
        now = _now()
        n = 0
        for m in self.store.all_memories():
            base = _sigmoid(base_level_activation(m.access_log, now, 0.5))
            new_imp = 0.5 * m.importance + 3.0 * m.salience + 2.0 * base
            new_imp = max(0.0, min(10.0, new_imp))
            # Never drop explicitly-protected memories below the forgetting boundary.
            if m.importance >= 8.0:
                new_imp = max(new_imp, 8.0)
            if not math.isclose(new_imp, m.importance, abs_tol=1e-6):
                m.importance = new_imp
                self.store.save(m)
                n += 1
        return n

    # ------------------------------------------------------------------
    # REM: optional generative abstraction (needs an LLM-backed reasoner)
    # ------------------------------------------------------------------
    def _abstract(self, reasoner: Callable[[list[Memory]], list[Memory]]) -> int:
        episodes = [m for m in self.store.all_memories() if m.type.value == "episode"]
        if not episodes:
            return 0
        sliced = episodes[: self.config.max_episodes_per_reflection]
        try:
            new_memories = reasoner(sliced)
        except Exception:
            return 0
        created = 0
        for nm in new_memories or []:
            emb = self.embedder.embed(nm.embedding_text())
            # Link the abstraction back only to episodes the reasoner saw.
            for ep in sliced:
                nm.add_edge(ep.id, EdgeType.DERIVED_FROM, 1.0)
            self.store.add(nm, emb)
            created += 1
        return created
