# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Retrieval: hybrid candidate recall, ACT-R-style scoring, multi-hop spread.

Score per candidate (each base term min-max normalised across the candidate set
first, then weighted) plus an associative-spread term over the graph:

    rel(m,q) = lambda*cos + (1-lambda)*ctxOverlap            (encoding specificity)
    rec(m)   = sigmoid( ln sum_k (t_now - access_k)^-d )      (ACT-R base-level)
    imp(m)   = importance / 10
    S_base   = a_rel*rel^ + a_rec*rec^ + a_imp*imp^
    Act(n)   = S_base(n) + spread over k hops (decayed per hop)

See ``docs/design/memory-system/design.md`` §4.3.
"""

from __future__ import annotations

import math

import numpy as np

from nemo_oo_agents.memory.config import RetrievalConfig
from nemo_oo_agents.memory.embeddings import _TOKEN_RE, Embedder
from nemo_oo_agents.memory.schema import CAUSAL_EDGE_TYPES, Memory, _now
from nemo_oo_agents.memory.store import MemoryStore


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def base_level_activation(access_log: list[float], now: float, d: float) -> float:
    """ACT-R base-level: ln( sum_k (now - t_k)^-d ). Higher = more recent/frequent."""
    total = 0.0
    for t in access_log:
        dt = max(now - t, 1.0)  # epsilon = 1s, avoids div-by-zero / future stamps
        total += dt ** (-d)
    if total <= 0.0:
        return -10.0
    return math.log(total)


def _minmax(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi - lo < 1e-12:
        return dict.fromkeys(values, 0.0)  # all equal -> term contributes nothing
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class RetrievalEngine:
    """Recalls memories from a store given a text query."""

    def __init__(self, store: MemoryStore, embedder: Embedder, config: RetrievalConfig) -> None:
        self.store = store
        self.embedder = embedder
        self.config = config

    def recall(
        self,
        query: str,
        *,
        k: int | None = None,
        hops: int | None = None,
        query_cues: set[str] | None = None,
        touch: bool = True,
    ) -> list[Memory]:
        cfg = self.config
        k = cfg.top_k if k is None else k
        hops = cfg.hops if hops is None else hops
        if self.store.count() == 0 or not query.strip():
            return []

        qvec = self.embedder.embed(query)

        # ---- stage 1: hybrid candidate pool (dense ∪ sparse) ----
        dense = self.store.knn(qvec, cfg.n_dense)
        cos: dict[str, float] = {mid: c for mid, c in dense if c >= cfg.min_similarity}
        for mid in self.store.keyword_search(query, cfg.n_sparse):
            if mid not in cos:
                emb = self.store.get_embedding(mid)
                cos[mid] = float(np.dot(emb, qvec)) if emb is not None else 0.0
        if not cos:
            return []

        memories = {mid: self.store.get(mid) for mid in cos}
        memories = {mid: m for mid, m in memories.items() if m is not None}

        # ---- stage 2: base score (relevance + recency + importance) ----
        now = _now()
        if query_cues is None:
            query_cues = {t.lower() for t in _TOKEN_RE.findall(query.lower())}
        rel_raw: dict[str, float] = {}
        rec_raw: dict[str, float] = {}
        imp_raw: dict[str, float] = {}
        w = cfg.weights
        for mid, m in memories.items():
            ctx = _jaccard(query_cues, m.cue_set())
            rel_raw[mid] = w.lambda_embed * cos[mid] + (1.0 - w.lambda_embed) * ctx
            rec_raw[mid] = _sigmoid(base_level_activation(m.access_log, now, w.base_level_decay))
            imp_raw[mid] = m.importance / 10.0
        rel_n, rec_n, imp_n = _minmax(rel_raw), _minmax(rec_raw), _minmax(imp_raw)
        s_base = {
            mid: w.relevance * rel_n[mid] + w.recency * rec_n[mid] + w.importance * imp_n[mid]
            for mid in memories
        }

        # ---- stage 3: associative spread over the graph ----
        activation = dict(s_base)
        if hops >= 1:
            spread = self._spread(s_base, hops)
            for mid, extra in spread.items():
                if mid not in memories:
                    m = self.store.get(mid)
                    if m is None:
                        continue
                    memories[mid] = m
                activation[mid] = activation.get(mid, 0.0) + w.spread_gamma * extra

        ranked = sorted(activation.items(), key=lambda kv: kv[1], reverse=True)
        result: list[Memory] = []
        for mid, _score in ranked[:k]:
            m = memories.get(mid) or self.store.get(mid)
            if m is None:
                continue
            if touch:
                m.touch(when=now)
                self.store.save(m)
            result.append(m)
        return result

    def _spread(self, seed_activation: dict[str, float], hops: int) -> dict[str, float]:
        """Propagate activation outward over edges, decaying ``per_hop_decay`` per hop."""
        cfg = self.config
        spread: dict[str, float] = {}
        frontier = dict(seed_activation)
        for h in range(1, hops + 1):
            decay = cfg.per_hop_decay**h
            nxt: dict[str, float] = {}
            for node, act in frontier.items():
                edges = self.store.neighbors(node)
                edges.sort(key=lambda e: e.weight, reverse=True)
                for e in edges[: cfg.per_hop_fanout]:
                    type_w = 1.0 if e.type in CAUSAL_EDGE_TYPES else 0.6
                    contrib = decay * act * e.weight * type_w
                    if contrib < cfg.activation_floor:
                        continue
                    spread[e.target_id] = spread.get(e.target_id, 0.0) + contrib
                    nxt[e.target_id] = nxt.get(e.target_id, 0.0) + contrib
            frontier = nxt
            if not frontier:
                break
        return spread


# ---------------------------------------------------------------------------
# Query strategies — derive the per-turn 'spontaneous association' query from
# the agent's recent events (pluggable + composable; see design §4.3.1).
# ---------------------------------------------------------------------------

_USER_TEXT_EVENTS = ("Task", "Message", "Notification")


def _event_text(event: object) -> str:
    for attr in ("prompt", "content", "text"):
        val = getattr(event, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _recent_events(agent: object, n: int) -> list[object]:
    em = getattr(agent, "event_manager", None)
    if em is None:
        return []
    try:
        tags = list(em.keys())
    except Exception:
        return []
    out = []
    for tag in reversed(tags):
        try:
            out.append(em[tag])
        except Exception:
            continue
        if len(out) >= n:
            break
    return out


def _strategy_last_message(agent: object, cfg) -> list[str]:
    for ev in _recent_events(agent, 50):
        if type(ev).__name__ in _USER_TEXT_EVENTS:
            txt = _event_text(ev)
            if txt:
                return [txt]
    return []


def _strategy_recent_events(agent: object, cfg) -> list[str]:
    texts = [_event_text(ev) for ev in _recent_events(agent, cfg.recent_events_n)]
    joined = "\n".join(t for t in texts if t)
    return [joined] if joined else []


def _strategy_working_state(agent: object, cfg) -> list[str]:
    texts = []
    for ev in _recent_events(agent, cfg.recent_events_n * 2):
        if type(ev).__name__ in ("PythonOutput", "LLMOutput"):
            txt = _event_text(ev) or getattr(ev, "output", "")
            if isinstance(txt, str) and txt:
                texts.append(txt)
    joined = "\n".join(texts[: cfg.recent_events_n])
    return [joined] if joined else []


_STRATEGIES = {
    "last_message": _strategy_last_message,
    "recent_events": _strategy_recent_events,
    "working_state": _strategy_working_state,
    # 'distilled' would use an LLM; without one it falls back to recent_events.
    "distilled": _strategy_recent_events,
}


def derive_queries(agent: object, cfg) -> list[str]:
    """Run the configured query strategies and return de-duplicated query strings."""
    queries: list[str] = []
    for name in cfg.query_strategies:
        fn = _STRATEGIES.get(name)
        if fn is None:
            continue
        for q in fn(agent, cfg):
            if q and q not in queries:
                queries.append(q)
    return queries
