# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""MemoryManager — the additive integration layer.

``MemoryManager.install(agent, config=...)`` wires an opt-in memory subsystem
onto any existing agent with **zero core edits**, mirroring
``agents/summarization.py``:

* pre-turn **spontaneous association** — a dynamic context block refreshed on
  ``BeforeTurn`` (cadence configurable) via ``ContextManager.set_dynamic``.
* **write-on-event** — ``on(<event>)`` subscriptions encode salient events.
* post-task **reflection** — ``intercept("agent_call")`` gated to the top-level
  call runs consolidation after the method returns.

``MemoryToolsMixin`` exposes the conscious tools (``recall``/``search``/
``remember``/``associate``) so they render in ``doc(self)``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from nemo_oo_agents.agent import Agent
from nemo_oo_agents.memory.config import MemoryConfig
from nemo_oo_agents.memory.descriptors import to_numeric
from nemo_oo_agents.memory.embeddings import Embedder, get_embedder
from nemo_oo_agents.memory.forgetting import ForgettingEngine
from nemo_oo_agents.memory.monitoring import (
    MemoryInjected,
    MemoryRecalled,
    MemoryStats,
    MemoryWritten,
    ReflectionCompleted,
)
from nemo_oo_agents.memory.reflection import ReflectionEngine, ReflectionReport
from nemo_oo_agents.memory.retrieval import RetrievalEngine, derive_queries
from nemo_oo_agents.memory.schema import EdgeType, Memory, MemoryType
from nemo_oo_agents.memory.store import MemoryStore

logger = logging.getLogger(__name__)
# Dedicated logger for memory-usage monitoring/debug (raise to DEBUG to trace ops).
mem_logger = logging.getLogger("nemo_oo_agents.memory")

# Salience/importance defaults per auto-written event type.
_EVENT_SALIENCE: dict[str, tuple[float, float]] = {
    # event_type: (salience, importance)
    "Error": (0.9, 7.0),
    "Notification": (0.5, 5.0),
    "Message": (0.4, 5.0),
}

# The instruction injected into the agent's context when memory is installed. This
# is the heart of the design: the AGENT owns and curates its memory (writes +
# refines it per the schema) — the framework does NOT silently extract it for you.
MEMORY_SCHEMA_GUIDE = """\
## Your long-term memory

You have a persistent long-term memory that YOU own and curate. Other systems
extract memories for the agent behind its back; here, *you* decide what to keep
and how to structure it. As you work, deliberately maintain it:

- WRITE durable, reusable knowledge — `self.remember(content, type=..., importance=..., tags=[...])`.
  Store distilled facts/skills/decisions, ONE self-contained item each — never raw
  transcripts or chit-chat.
- REFINE — when a memory becomes more accurate, `self.update_memory(id, ...)`; when it
  is wrong or obsolete, `self.forget(id)`; link related memories with
  `self.associate(id_a, id_b, relation)`.
- RECALL before acting — `self.recall(query)` to reuse what you already know.

Memory schema (set the fields deliberately):
  type:        info       — a durable fact, preference, rule, or convention
               skill      — a reusable, verified procedure / how-to
               episode    — what happened in a specific task or event
               intent     — a future intention / reminder / TODO
               reflection — an insight distilled from several episodes
  importance:  CRITICAL | HIGH | MEDIUM | LOW | TRIVIAL — how much this should influence future decisions
  tags:        salient entities/keywords for retrieval (names, files, dates, topics)
Near-duplicate writes are auto-merged, so prefer writing over worrying about overlap.
"""


class MemoryManager:
    """Owns the store + engines and the agent hooks for one agent."""

    def __init__(
        self,
        agent: Agent,
        config: MemoryConfig | None = None,
        *,
        embedder: Embedder | None = None,
        reasoner: Callable[[list[Memory]], list[Memory]] | None = None,
        reconciler: Callable[[list[Memory]], tuple[Memory | None, list[str]]] | None = None,
    ) -> None:
        self.agent = agent
        self.config = config or MemoryConfig()
        self.embedder = embedder or get_embedder(self.config.embedding)
        self.store = self._make_store(agent)
        self.retrieval = RetrievalEngine(self.store, self.embedder, self.config.retrieval)
        self.reflection_engine = ReflectionEngine(
            self.store, self.embedder, self.config.reflection, self.config.forget
        )
        self.forgetting = ForgettingEngine(self.store, self.config.forget)
        self._reasoner = reasoner
        self._reconciler = reconciler
        self.stats = MemoryStats()

        # hook state
        self._unsubs: list[Callable[[], None]] = []
        self._pending: list[asyncio.Task[Any]] = []
        self._call_depth = 0
        self._reflecting = False
        self._last_query_hash: int | None = None
        self._primed = False

        self._install_hooks()

    def _make_store(self, agent: Agent) -> MemoryStore:
        path = self.config.path or self._default_path(agent)
        return MemoryStore(path, vector_config=self.config.vector, embedding_dim=self.embedder.dim)

    # ------------------------------------------------------------------
    # install / uninstall
    # ------------------------------------------------------------------
    @classmethod
    def install(
        cls, agent: Agent, *, config: MemoryConfig | None = None, **kwargs: Any
    ) -> MemoryManager:
        """Attach memory to ``agent``. Stored as ``agent._memory`` (lifetime tied)."""
        existing = getattr(agent, "_memory", None)
        if isinstance(existing, MemoryManager):
            existing.uninstall()
        manager = cls(agent, config=config, **kwargs)
        agent._memory = manager  # type: ignore[attr-defined]
        return manager

    @staticmethod
    def _default_path(agent: Agent) -> str:
        from pathlib import Path

        return str(Path(".nemo_oo") / "memory" / "memory.sqlite")

    def _install_hooks(self) -> None:
        em = self.agent.event_manager
        if not self.config.enabled:
            return  # additive guarantee: install is inert when disabled
        # Tell the agent it owns its memory + the schema to write it with.
        if self.config.instruct:
            try:
                self.agent.context_manager.set_static(
                    self.config.instruct_block_key, MEMORY_SCHEMA_GUIDE
                )
            except Exception:
                mem_logger.debug("memory: could not inject instruction block", exc_info=True)
        # reset per-task injection state on task boundaries
        self._unsubs.append(em.on("Task", self._on_task))
        if self.config.spontaneous.enabled:
            self._unsubs.append(em.on("BeforeTurn", self._on_before_turn))
        for evt in self.config.write.on_events:
            self._unsubs.append(em.on(evt, self._on_write_event))
        if self.config.reflection.enabled and self.config.reflection.trigger == "post_task":
            self._unsubs.append(em.intercept("agent_call", self._reflect_middleware))

    def uninstall(self) -> None:
        """Remove all hooks and cancel pending background work."""
        if self.config.instruct:
            try:
                del self.agent.context_manager[self.config.instruct_block_key]
            except Exception:
                pass
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs.clear()
        for task in self._pending:
            if not task.done():
                task.cancel()
        self._pending.clear()
        self.store.close()
        if getattr(self.agent, "_memory", None) is self:
            del self.agent._memory  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # conscious operations (used by MemoryToolsMixin + internally)
    # ------------------------------------------------------------------
    def remember(
        self,
        content: str,
        *,
        type: MemoryType = MemoryType.INFO,
        importance: float | None = None,
        salience: float = 0.0,
        title: str | None = None,
        tags: list[str] | None = None,
        source_task_ref: str | None = None,
        dedup: bool = True,
    ) -> str:
        """Encode a memory (dedup-on-write). Returns the memory id."""
        mem = Memory(
            content=content,
            type=type,
            title=title,
            importance=self.config.write.default_importance if importance is None else importance,
            salience=salience,
            tags=tags or [],
            source_task_ref=source_task_ref,
        )
        emb = self.embedder.embed(mem.embedding_text())

        if dedup and self.store.count() > 0:
            for nid, cos in self.store.knn(emb, self.config.write.dedup_top_k):
                if cos >= self.config.write.dedup_threshold:
                    existing = self.store.get(nid)
                    if existing is not None and existing.type == type:
                        existing.touch()
                        existing.reinforcement_count += 1
                        existing.importance = max(existing.importance, mem.importance)
                        existing.salience = max(existing.salience, mem.salience)
                        self.store.save(existing)
                        self.stats.reinforced += 1
                        self._emit(
                            MemoryWritten(
                                memory_id=existing.id,
                                mem_type=type.value,
                                op="reinforce",
                                importance=existing.importance,
                            )
                        )
                        mem_logger.debug(
                            "memory.reinforce id=%s type=%s", existing.id[:8], type.value
                        )
                        return existing.id  # NOOP: reinforced the duplicate

        self.store.add(mem, emb)
        self.stats.writes += 1
        self._emit(
            MemoryWritten(
                memory_id=mem.id, mem_type=type.value, op="add", importance=mem.importance
            )
        )
        mem_logger.debug(
            "memory.write id=%s type=%s imp=%.1f", mem.id[:8], type.value, mem.importance
        )
        return mem.id

    def recall(self, query: str, k: int | None = None, *, hops: int | None = None) -> list[Memory]:
        """Associative + keyword recall, scored and ranked."""
        res = self.retrieval.recall(query, k=k, hops=hops)
        eff_hops = self.config.retrieval.hops if hops is None else hops
        self.stats.recalls += 1
        self.stats.recalled_items += len(res)
        self._emit(MemoryRecalled(query=query[:200], n_results=len(res), hops=eff_hops))
        mem_logger.debug("memory.recall q=%r hops=%d -> %d results", query[:60], eff_hops, len(res))
        return res

    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        importance: float | None = None,
        type: MemoryType | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Refine an existing memory in place (re-embeds if content changed)."""
        m = self.store.get(memory_id)
        if m is None:
            return False
        content_changed = content is not None and content != m.content
        if content is not None:
            m.content = content
        if importance is not None:
            m.importance = max(0.0, min(10.0, importance))
        if type is not None:
            m.type = type
        if tags is not None:
            m.tags = tags
        m.reinforcement_count += 1
        m.touch(reinforce=False)
        if content_changed:
            self.store.add(m, self.embedder.embed(m.embedding_text()))  # re-embed + replace
        else:
            self.store.save(m)
        self._emit(
            MemoryWritten(
                memory_id=m.id, mem_type=m.type.value, op="update", importance=m.importance
            )
        )
        mem_logger.debug("memory.update id=%s", m.id[:8])
        return True

    def forget(self, memory_id: str) -> bool:
        """Archive (tombstone) a memory the agent judges wrong or obsolete."""
        m = self.store.get(memory_id)
        if m is None:
            return False
        self.store.archive(memory_id)
        self.stats.pruned += 1
        self._emit(
            MemoryWritten(
                memory_id=memory_id, mem_type=m.type.value, op="forget", importance=m.importance
            )
        )
        mem_logger.debug("memory.forget id=%s", memory_id[:8])
        return True

    def _emit(self, event: Any) -> None:
        """Emit a runtime memory event on the agent's bus (never enters LLM context)."""
        try:
            self.agent.event_manager.add(event, record=False)
        except Exception:
            pass

    def memory_stats(self) -> MemoryStats:
        """Snapshot of how the agent has used its memory (also refreshes store_size)."""
        self.stats.store_size = self.store.count()
        return self.stats

    def log_summary(self) -> None:
        """Log a one-line memory-usage summary at INFO."""
        mem_logger.info("memory stats: %s", self.memory_stats().summary())

    def associate(self, a_id: str, b_id: str, relation: str = "related") -> None:
        """Add a directed graph edge ``a_id -> b_id``."""
        try:
            etype = EdgeType(relation)
        except ValueError:
            etype = EdgeType.RELATED
        self.store.add_edge(a_id, b_id, etype)

    def reflect(self) -> ReflectionReport:
        """Run a consolidation pass synchronously (also callable manually)."""
        report = self.reflection_engine.consolidate(
            reasoner=self._reasoner, reconciler=self._reconciler
        )
        self.stats.reflections += 1
        self.stats.merged += report.merged
        self.stats.edges_added += report.edges_added
        self.stats.pruned += report.pruned + report.superseded
        self._emit(
            ReflectionCompleted(
                merged=report.merged,
                edges_added=report.edges_added,
                rescored=report.rescored,
                pruned=report.pruned,
                created=report.created,
            )
        )
        mem_logger.info("memory.reflect %s", report.model_dump())
        return report

    # ------------------------------------------------------------------
    # spontaneous association (pre-turn injection)
    # ------------------------------------------------------------------
    def recall_for_context(self) -> str:
        """Derive a query from agent state and format recalled memories."""
        if not self.config.enabled or not self.config.spontaneous.enabled:
            return ""
        queries = derive_queries(self.agent, self.config.spontaneous)
        return self._format_recall(queries)

    def _format_recall(self, queries: list[str]) -> str:
        if not queries:
            return ""
        sp = self.config.spontaneous
        k = sp.top_k or self.config.retrieval.top_k
        seen: dict[str, Memory] = {}
        for q in queries:
            for m in self.retrieval.recall(q, k=k, touch=False):
                seen.setdefault(m.id, m)
        if not seen:
            return ""
        lines = ["## Recalled memories (associative)"]
        for m in list(seen.values())[:k]:
            head = m.title or m.content
            head = head.replace("\n", " ").strip()
            lines.append(f"- [{m.type.value}] {head}")
        text = "\n".join(lines)
        if len(text) > sp.context_char_budget:
            text = text[: sp.context_char_budget].rstrip() + " …"
        return text

    def _on_task(self, event: object) -> None:
        self._primed = False
        self._last_query_hash = None

    def _on_before_turn(self, event: object) -> None:
        sp = self.config.spontaneous
        if not self.config.enabled or not sp.enabled:
            return
        if sp.inject_cadence == "per_task" and self._primed:
            return
        queries = derive_queries(self.agent, sp)
        if not queries:
            return
        qhash = hash(tuple(queries))
        if sp.inject_cadence == "self_gated" and qhash == self._last_query_hash:
            return
        text = self._format_recall(queries)
        cm = self.agent.context_manager
        key = sp.context_block_key
        if text:
            cm.set_dynamic(key, value=text)
            self.stats.injections += 1
            self.stats.injected_chars += len(text)
            self._emit(MemoryInjected(n_memories=text.count("\n- "), chars=len(text)))
            mem_logger.debug("memory.inject %d chars (%d memories)", len(text), text.count("\n- "))
        elif key in cm:
            del cm[key]
        self._last_query_hash = qhash
        self._primed = True

    # ------------------------------------------------------------------
    # write-on-event
    # ------------------------------------------------------------------
    def _on_write_event(self, event: object) -> None:
        if not self.config.enabled:
            return
        text = ""
        for attr in ("content", "prompt", "text"):
            val = getattr(event, attr, None)
            if isinstance(val, str) and val.strip():
                text = val
                break
        if not text:
            return
        salience, importance = _EVENT_SALIENCE.get(type(event).__name__, (0.3, 5.0))
        if salience < self.config.write.salience_min:
            return
        try:
            self.remember(
                text,
                type=MemoryType.INFO,
                importance=importance,
                salience=salience,
                source_task_ref=type(event).__name__,
            )
        except Exception:
            logger.warning("memory: write-on-event failed", exc_info=True)

    # ------------------------------------------------------------------
    # post-task reflection
    # ------------------------------------------------------------------
    async def _reflect_middleware(self, ctx: Any, nxt: Any) -> Any:
        self._call_depth += 1
        try:
            ctx = await nxt(ctx)
        finally:
            self._call_depth -= 1

        if self._reflecting:
            return ctx
        is_top = self._call_depth == 0
        if self.config.reflection.only_top_level and not is_top:
            return ctx
        methods = self.config.reflection.entrypoint_methods
        if methods and getattr(ctx, "method_name", None) not in methods:
            return ctx

        if self.config.reflection.background:
            task = asyncio.create_task(self._async_reflect(ctx))
            self._pending.append(task)
            task.add_done_callback(
                lambda t: self._pending.remove(t) if t in self._pending else None
            )
        else:
            self._consolidate_after_task(ctx)
        return ctx

    async def _async_reflect(self, ctx: Any) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._consolidate_after_task, ctx)

    def _consolidate_after_task(self, ctx: Any) -> None:
        if self._reflecting:
            return
        self._reflecting = True
        try:
            if self.config.write.write_episodic:
                self._write_episode(ctx)
            self.reflect()  # counts + emits + logs the consolidation
        except Exception:
            logger.warning("memory: reflection failed", exc_info=True)
        finally:
            self._reflecting = False

    def _write_episode(self, ctx: Any) -> None:
        from nemo_oo_agents.runtime.middleware import _AGENT_RESULT_NOT_SET

        method = getattr(ctx, "method_name", "") or "task"
        result = getattr(ctx, "result", _AGENT_RESULT_NOT_SET)
        result_str = "" if result is _AGENT_RESULT_NOT_SET else str(result)
        content = f"Episode: {method}\nResult: {result_str[:500]}"
        self.remember(
            content,
            type=MemoryType.EPISODE,
            importance=5.0,
            salience=0.5,
            title=f"episode:{method}",
            source_task_ref=method,
        )


# ---------------------------------------------------------------------------
# Conscious tools mixin
# ---------------------------------------------------------------------------
class MemoryToolsMixin:
    """Mixes the conscious memory tools into an Agent so they show in ``doc(self)``.

    Usage::

        class MyAgent(MemoryToolsMixin, Agent, llm=llm): ...
        MemoryManager.install(agent, config=MemoryConfig(enabled=True))
    """

    def _memory_or_raise(self) -> MemoryManager:
        mem = getattr(self, "_memory", None)
        if mem is None or not mem.config.enabled:
            raise RuntimeError(
                "Memory is not installed/enabled. Call MemoryManager.install(agent, "
                "config=MemoryConfig(enabled=True))."
            )
        return mem

    def _tool_enabled(self, name: str) -> MemoryManager:
        mem = self._memory_or_raise()
        if name not in mem.config.tools:
            raise RuntimeError(f"Memory tool {name!r} is disabled in MemoryConfig.tools.")
        return mem

    @staticmethod
    def _as_type(type: str) -> MemoryType:
        return MemoryType(type)  # raises ValueError on an unknown type (no silent fallback)

    def remember(
        self,
        content: str,
        *,
        type: str = "info",
        importance: str = "MEDIUM",
        tags: list[str] | None = None,
        title: str | None = None,
    ) -> str:
        """Write a NEW long-term memory you author: {content}

        Record durable, reusable knowledge (NOT raw transcripts), one item per call.
        type: info | skill | episode | intent | reflection.
        importance: CRITICAL | HIGH | MEDIUM | LOW | TRIVIAL.
        tags: salient keywords/entities for later retrieval. Returns the memory id.
        """
        mem = self._tool_enabled("remember")
        return mem.remember(
            content,
            type=self._as_type(type),
            importance=to_numeric("importance", importance),
            tags=tags,
            title=title,
        )

    def update_memory(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        importance: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Refine one of your existing memories {memory_id} (correct or sharpen it).

        Pass only the fields to change. importance: CRITICAL | HIGH | MEDIUM | LOW | TRIVIAL.
        Returns True if the memory was found.
        """
        mem = self._tool_enabled("update_memory")
        return mem.update(
            memory_id,
            content=content,
            importance=to_numeric("importance", importance) if importance is not None else None,
            type=self._as_type(type) if type else None,
            tags=tags,
        )

    def forget(self, memory_id: str) -> bool:
        """Forget (archive) a memory {memory_id} you judge wrong or obsolete. Returns True if found."""
        mem = self._tool_enabled("forget")
        return mem.forget(memory_id)

    def recall(self, query: str, k: int = 5) -> list[Memory]:
        """Recall memories associatively related to {query} (similarity + graph)."""
        mem = self._tool_enabled("recall")
        return mem.recall(query, k=k)

    def search(self, query: str, k: int = 5) -> list[Memory]:
        """Search memories by keyword/term for {query} (deliberate recall)."""
        mem = self._tool_enabled("search")
        # term-focused recall: 0 hops, keyword + dense, no graph spread
        return mem.recall(query, k=k, hops=0)

    def associate(self, a_id: str, b_id: str, relation: str = "related") -> None:
        """Link memory {a_id} to {b_id} with a directed {relation} edge."""
        mem = self._tool_enabled("associate")
        mem.associate(a_id, b_id, relation)
