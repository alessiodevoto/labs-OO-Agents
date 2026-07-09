# Memory System — Addendum & Fix Design

**Three independent changes to the existing memory subsystem:**

- **A. Skill interface layer** — expose the memory system as a registerable *skill* plugin (like those in `nooa-skills/`), as a **thin external adapter** over the existing `MemoryManager`. **No internal architecture change.**
- **B. `DreamEngine` → `ReflectionEngine`** — rename `dream` → `reflection` everywhere (a better term), with no back-compat shims.
- **C. Verbal ordered descriptors** — replace the agent-facing numeric descriptors in `schema.py` (importance, …) with **ALL-CAPS ordered verbal ladders**; internal calculations stay numeric.

Analysis was done with a 4-agent `/workflows` pass over the skill mechanism, the descriptor surface, the rename inventory, and the test plan. File:line citations below are from that pass.

## Engineering guidelines (apply to every change)

1. **No `getattr`** — except when facing agent-generated code.
2. **No fallbacks / back-compat** — raise on failure, never silently recover.
3. **No code duplication** — factor shared bodies once.
4. **Target only the essentials** — nothing else.
5. **Separation of concerns** — between modules.
6. **Minimal changes** — don't add complexity.
7. **Net-neutral LOC** — additions offset by deleting duplicated/dead code.

> Scope note: guideline-driven constraint **"no internal architecture change" applies to Workstream A only** (the skill is an external adapter). Workstreams B (rename) and C (verbal descriptors) deliberately modify the memory package — that *is* the requested change — but still obey guidelines 1–7.

---

## A. Memory as a Skill (external plugin)

### A.1 How the skill mechanism works

A "skill" is a Python package registered via the **`nooa.skills` entry-point group** (not a `SKILL.md`). Contract (`src/nooa/skill.py`, `src/nooa/skill_registry.py`):

- A skill subclasses **`nooa.skill.Skill`** with a **zero-arg `__init__`** (discovery instantiates `skill_cls()`, `skill_registry.py:343`; classes whose `__init__` needs args are skipped, `:84-92`). Do **not** call `Skill.__init__` from a subclass — it raises unless given `obj/content` (`skill.py:303`); existing skills (`agent_mesh`, `TraceExplorerTools`) define their own `__init__` and never call super.
- **`attach(self, agent)`** (`skill.py:314`) is the agent-dependent setup hook — base sets `self._agent = agent`; the registry calls it right after `setattr(agent, attr, skill)` (`skill_registry.py:362-363`). **`detach(self)`** (`skill.py:321`) tears down.
- The registry sets the skill on the agent as `attr = name.split('.')[-1]` (`skill_registry.py:349`), so entry point **`nvzurich.memory` → `self.memory`**, and every public method is reachable as `self.memory.<method>` in agent-generated code and rendered via `doc(self.memory)`. The **class docstring is the LLM-facing description**.
- Lifecycle: **discover** (`entry_points(group="nooa.skills")`, `:170-181`, or `discover_libs` over `libs_dirs` in `.nooa/config.toml`) → **load** (instantiate + `setattr` + `attach`) → **activate** (`_unhide_skill` via `spec(agent, attr, hidden=False)` so it shows in `doc(self)`; resolve `requires`; refresh `@slash_command`s).
- Inside skill code, the agent is reached via `self._agent` (`self._agent.queue_manager`, `.event_manager`, …). Optional: `requires: tuple[str,...]` (hard deps), `context_block` (dynamic context), `@slash_command` (user-invocable).

### A.2 Design — `MemorySkill`

The existing six conscious-tool bodies (`remember`/`recall`/`search`/`update_memory`/`forget`/`associate`) live **once** in `MemoryToolsMixin` (`manager.py:487-575`), with validation (`_tool_enabled`), coercion (`_as_type`) and the disabled-tool guard. The skill **reuses them by inheritance** and overrides only the host-resolution hook — **zero duplication (guideline 3)**:

```python
# nooa-skills/memory/__init__.py
from typing import Any
from nooa.skill import Skill
from nooa.memory import MemoryManager, MemoryConfig
from nooa.memory.manager import MemoryToolsMixin
from nooa.memory.reflection import ReflectionReport   # post-Workstream B
from nooa.memory.monitoring import MemoryStats


class MemorySkill(MemoryToolsMixin, Skill):
    """Long-term memory you own and curate.

    Write durable, reusable knowledge with self.memory.remember(...), retrieve it
    with self.memory.recall(...) / self.memory.search(...), refine with
    self.memory.update_memory(...) / forget(...), link with associate(...), and
    consolidate with self.memory.reflect(). (This docstring is the tool doc.)
    """
    __nosnapshot__ = True

    def __init__(self) -> None:                 # zero-arg: discovery-compatible
        self._mgr: MemoryManager | None = None
        self._config = MemoryConfig(enabled=True)

    def attach(self, agent: Any) -> None:
        super().attach(agent)                   # sets self._agent
        self._mgr = MemoryManager.install(agent, config=self._config)

    def detach(self) -> None:
        if self._mgr is not None:
            self._mgr.uninstall()               # existing API (manager.py:169)
            self._mgr = None
        super().detach()

    # The ONLY override: the inherited tool bodies resolve the manager through this.
    def _memory_or_raise(self) -> MemoryManager:
        mgr = self._mgr
        if mgr is None or not mgr.config.enabled:
            raise RuntimeError("MemorySkill is not attached/enabled.")
        return mgr

    # remember/recall/search/update_memory/forget/associate: INHERITED verbatim.

    def reflect(self) -> ReflectionReport:
        """Consolidate memories (merge duplicates, form links, prune)."""
        return self._memory_or_raise().reflect()        # post-Workstream B

    def stats(self) -> MemoryStats:
        """Snapshot of how this agent has used its memory."""
        return self._memory_or_raise().memory_stats()
```

Why this is correct under the guidelines:

- **No internal change**: the skill only calls the public `MemoryManager.install/uninstall` + the public tool methods. Installing the manager keeps `MEMORY_SCHEMA_GUIDE` injection, spontaneous-association hooks, write-on-event, and post-task reflection working exactly as today (`manager.py:148-167`).
- **No `getattr` in the skill path (g1)**: the skill resolves the manager through `self._mgr`, not `getattr(agent, "_memory")`. (The skill **owns** installation — it never reads a pre-installed `agent._memory`; "use the skill *or* install manually, not both".)
- **No fallback (g2)**: `_memory_or_raise` raises; there is no silent recovery.
- **No duplication (g3)** / **minimal (g6)**: six tool bodies inherited; the skill adds `__init__`/`attach`/`detach`/`_memory_or_raise` + two 1-line aliases.

`reflect`/`stats` map to always-available manager methods, so they are intentionally **not** gated by `MemoryConfig.tools` (which gates the six conscious tools via `_tool_enabled`).

### A.3 Packaging

> **Implemented as (supersedes the submodule plan below):** the skill ships **in‑core**
> as a built‑in, alongside the other `nemo.*` skills — `src/nooa/memory/memory_skill/`
> registered in the **core `pyproject.toml`** as `"nemo.memory" = "nooa.memory.memory_skill:MemorySkill"`
> → attr `self.memory`, activated with `self.skills.activate(["nemo.memory"])`. This avoids a
> submodule/gitlink dependency; the skill is still a thin external adapter (no memory‑internal
> change). The original "external submodule package" plan is kept below for context.

In the **`nooa-skills` submodule** (separation of concerns — every distributable skill lives there; the core `src/` ships the *architecture*, not skill packages):

```
nooa-skills/memory/
├── __init__.py        # MemorySkill (above)
└── pyproject.toml
```

```toml
# nooa-skills/memory/pyproject.toml
[project]
name = "memory"
version = "0.1.0"
description = "Long-term memory skill: remember/recall/search/update_memory/forget/associate + reflect."
dependencies = []                       # nooa.memory ships in core

[project.entry-points."nooa.skills"]
"nvzurich.memory" = "memory:MemorySkill"
```

Matches the `nvzurich.<name> = "<module>:<Class>"` convention (`deep_research/pyproject.toml:7-8`). Registry name `nvzurich.memory` → attr `self.memory`. Activate with `self.skills.activate(["nvzurich.memory"])`. Add the README skills-table row.

### A.4 Resolved decisions

- **Config**: zero-arg ctor → `MemoryConfig(enabled=True)` defaults. Custom config uses the manual path `self.skills.register("nvzurich.memory", MemorySkill, ...)` — but since `__init__` is zero-arg, custom config is supplied by **subclassing** or by setting `_config` before `attach`. (We deliberately do **not** add a `config.toml` section unless a concrete need appears — guideline 4.)
- **Resume / hot-reload**: skills are `__nosnapshot__`; a fresh skill instance re-`attach`es and re-installs. File-backed stores reload; `:memory:` stores don't survive resume regardless. No idempotency `getattr` needed because the skill owns its single `self._mgr`.

---

## B. `DreamEngine` → `ReflectionEngine`

A **hard, mechanical rename** with **no aliases** (guideline 2) — net-neutral on LOC (guideline 7).

### B.1 Symbol renames

| from | to | kind |
|---|---|---|
| `DreamEngine` | `ReflectionEngine` | class |
| `DreamPolicy` | `ReflectionPolicy` | class |
| `DreamReport` | `ReflectionReport` | class |
| `DreamCompleted` | `ReflectionCompleted` | event class |
| `MemoryManager.dream()` | `MemoryManager.reflect()` | public method |
| `MemoryManager.dream_engine` | `MemoryManager.reflection_engine` | public attr |
| `MemoryConfig.dream` (field) / `dream=DreamPolicy(...)` kwarg | `MemoryConfig.reflection` / `reflection=ReflectionPolicy(...)` | config key |
| `DreamPolicy.max_episodes_per_dream` | `ReflectionPolicy.max_episodes_per_reflection` | config key |
| `MemoryStats.dreams` + `summary()` token `dreams=` | `MemoryStats.reflections` / `reflections=` | field + label |
| `_dream_middleware`, `_async_dream`, `self._dreaming` | `_reflect_middleware`, `_async_reflect`, `self._reflecting` | internal |
| log labels `memory.dream`, `dreaming failed` | `memory.reflect`, `reflection failed` | strings |
| examples/tests: `dream=`/`dream_on=`/`run_dream_ablation`/`_report_dream`/`--dream`/`'DREAM'` | `reflect=`/`reflect_on=`/`run_reflect_ablation`/`_report_reflect`/`--reflect`/`'REFLECT'` | vars/CLI |

### B.2 File renames

- `src/nooa/memory/dream.py` → `reflection.py`
- `tests/memory/test_memory_dream.py` → `test_memory_reflection.py`
- `examples/memory_bench/dreaming.py` → `reflecting.py` (update `from dreaming import make_llm_reasoner` → `from reflecting import …` in `locomo.py`, `longmemeval.py`, `test_memory_dreaming_reasoner.py`)

### B.3 The `MemoryType.REFLECTION` coherence note (NOT a collision)

`MemoryType.REFLECTION = "reflection"` already exists as a **memory record type** (`schema.py:33`, "insight distilled from episodes"). The engine rename lives in a **different namespace** (class/method/field vs. enum member) so there is no symbol clash — and it is **semantically coherent**: `reflect()` *produces* `MemoryType.REFLECTION` records via its abstraction step. **Document this; do not touch `MemoryType.REFLECTION` or its string value.** Never bind a bare variable named `reflection` to a `Memory` of that type — keep engine concepts as `ReflectionEngine`/`reflect()`/`config.reflection` and record-type usage as `MemoryType.REFLECTION` / `m.type`.

### B.4 Do **not** rename

`consolidate()` (already neutral and accurate), `reasoner` / `reconciler` / `_reconsolidate` / `reconciled` / `superseded` / `recon_threshold` / `recon_max_cluster` (no `dream` substring — orthogonal functional terms), `make_llm_reasoner` (the reasoner survives the engine rename), `asyncio.sleep(0)` (cooperative yield). REM/sleep metaphor *prose* in comments/docstrings may be softened to plain "reflection/abstraction" language but is optional polish, not required.

### B.5 Files to edit

Code: `memory/{dream.py→reflection.py, config.py, manager.py, __init__.py, monitoring.py, forgetting.py, README.md}`. Tests: `test_memory_{dream→reflection}.py, test_memory_manager.py, test_memory_monitoring.py, test_memory_bench_smoke.py` (the `assert 'dreams=' in out` → `'reflections='`). Examples: `bench.py, dreaming→reflecting.py, locomo.py, locomo_scaling.py, longmemeval.py, memory_effect.py, recall_qa.py, README.md`, `examples/quickstart/11_memory.py`. Docs: the memory-system `.md`s (rename **symbol** references; the neuroscience narrative in `research-notes.md` is historical and lowest-priority).

**Excluded (data text, never rename):** `examples/memory_bench/data/{locomo10,lme_oracle}.json` (the word "dream" appears in the conversations).

**Completion check:** after the pass, `grep -rnE 'Dream(Engine|Report|Policy|Completed)|\.dream\(|dream_engine|dreams=|dream=|--dream' src tests examples` must return **zero** hits.

---

## C. Verbal ordered descriptors

### C.1 What the agent actually defines

Of the numeric descriptors on `Memory`, **only `importance` is set by the agent today** (`MemoryToolsMixin.remember/update_memory`, `manager.py:518-553`). `salience`, `confidence`, and `Edge.weight` are **not on the agent tool surface** — they take defaults set by framework paths (`_EVENT_SALIENCE`, `_write_episode`, `reflect` merges, auto-formed edges). **Counters** (`strength`, `reinforcement_count`, `access_count`) are monotonic ACT-R/Ebbinghaus tallies — **they stay numeric** (a verbal ladder would destroy their additive semantics).

We design a ladder for **all four float descriptors** (the requested deliverable), **replace** the live one (`importance`) with verbal, and make the other three available to surface verbally on the agent interface when desired.

### C.2 The verbal ladders (ALL-CAPS, ordered high→low, ≤5)

| descriptor | range | ladder → numeric | default | rationale (behavior-preserving) |
|---|---|---|---|---|
| **importance** | 0–10 | `CRITICAL`=10 · `HIGH`=8 · `MEDIUM`=5 · `LOW`=3 · `TRIVIAL`=1 | `MEDIUM` (5.0) | MEDIUM=5 reproduces the `remember` default; **HIGH=8 lands exactly on `forgetting.is_protected` ≥8.0** (HIGH/CRITICAL are protected from auto-forgetting), so the protection boundary is unchanged. |
| **salience** | 0–1 | `PIVOTAL`=1.0 · `NOTABLE`=0.5 · `ROUTINE`=0.3 · `NONE`=0.0 | `NONE` (0.0) | NONE=0.0 preserves the schema default; agent never sets it today. |
| **confidence** | 0–1 | `CERTAIN`=1.0 · `CONFIDENT`=0.75 · `TENTATIVE`=0.5 · `UNCERTAIN`=0.25 | `TENTATIVE` (0.5) | TENTATIVE=0.5 preserves the default; sole consumer is the reflect-merge max-fold (ordering preserved). |
| **edge weight** | 0–1 | `STRONG`=1.0 · `MODERATE`=0.6 · `WEAK`=0.3 | `STRONG` (1.0) | STRONG=1.0 preserves the default `associate()` edge weight; auto-formed cosine edges keep their float and are **not** verbalized. |

Every mapping is chosen so the **default float round-trips to a label** and all downstream scoring/decay/consolidation math is **byte-for-byte unchanged**. Internal consumers that keep reading raw floats: `retrieval.recall` (importance, edge weight), `forgetting.is_protected` (importance ≥8), `reflection._rescore_importance` / `_merge_duplicates` / `_form_edges` (importance/salience/confidence/weight). Framework-set floats (`_EVENT_SALIENCE`, episode/error defaults) stay numeric.

### C.3 Where the translation lives (single source of truth)

- **Vocabulary** → one new tiny module **`memory/descriptors.py`**: a `StrEnum` per descriptor (`Importance`, `Salience`, `Confidence`, `EdgeWeight`) each carrying its `{label: float}` map, plus **one factored pair** `to_numeric(enum_cls, label) -> float` / `to_label(enum_cls, value) -> str` that does the lookup and **raises `ValueError` on an unknown label** (no dual-accept, no fallback — g2/g3). This is the *only* place that knows the verbal vocabulary.
- **Storage / scoring** → `schema.py` `Memory` keeps numeric fields; **internal calculations are untouched** (separation of concerns, g5). `Memory` gains pure accessors `importance_label()` etc. that call `to_label` (for rendering numeric→verbal in recall excerpts / `doc`).
- **Agent boundary** → `MemoryToolsMixin.remember`/`update_memory` (the agent tools) accept the **verbal label** and call `to_numeric` before forwarding the float to `MemoryManager.remember` (which stays numeric). The mixin is the single agent-facing boundary; the skill (Workstream A) inherits it and gets the verbal interface for free.

```python
# manager.py — agent-tool boundary (after change)
def remember(self, content: str, *, type: str = "info",
             importance: str = "MEDIUM", tags=None, title=None) -> str:
    mem = self._tool_enabled("remember")
    return mem.remember(content, type=self._as_type(type),
                        importance=to_numeric(Importance, importance), tags=tags, title=title)
```

### C.4 Guideline-2 cleanup folded in (net-LOC offset)

`MemoryToolsMixin._as_type` currently **silently** maps an unknown type to `INFO` (`except ValueError: return MemoryType.INFO`, `manager.py:511-513`) — a banned silent recovery. Change it to **raise** (consistent with `to_numeric`). Likewise `MemoryManager.associate` silently falls back to `RELATED` on an unknown relation — make the agent-tool boundary validate/raise. These deletions of fallback branches offset the additions in `descriptors.py`.

### C.5 Agent-facing text

Rewrite `remember`'s docstring and `MEMORY_SCHEMA_GUIDE` (`manager.py:57`, line 78 `importance: 1-10`) to the verbal ladder, e.g. *"importance: one of CRITICAL · HIGH · MEDIUM · LOW · TRIVIAL"*. This is a prose swap (numeric → verbal), not a new block.

---

## Implementation order

1. **B (rename)** first — mechanical, unblocks everything (the skill imports `reflect`/`ReflectionReport`). Verify zero `Dream*` grep hits + tests green.
2. **C (verbal descriptors)** — add `descriptors.py`, `Memory.*_label()`, switch the mixin boundary to verbal, raise on unknown labels/types, rewrite `MEMORY_SCHEMA_GUIDE`.
3. **A (skill)** — add `nooa-skills/memory/`; it reuses B's `reflect()` and C's verbal mixin automatically.

---

## Test plan (uses `examples/memory_bench` + `tests/memory`)

| workstream | verification | example / test used |
|---|---|---|
| **B rename** | renamed unit tests pass **and** `grep -rnE 'Dream(Engine\|Report\|Policy\|Completed)\|\.dream(\|dream_engine\|dreams='` returns 0. | `reflecting.py` drives `manager.reflect()` end-to-end (LLM reasoner consolidates episodes→reflections); `test_memory_reflection.py` (renamed, same 9 bodies); `test_memory_dreaming_reasoner.py` is **verify-only** (the reasoner keeps its name). |
| **B rename (offline e2e)** | `pytest tests/memory/test_memory_bench_smoke.py` passes with the `reflections=` assertion. | `bench.py --solver oracle` runs install→remember→**reflect**→recall→memory_stats with no LLM; the smoke asserts its printed summary. |
| **C descriptors (unit)** | `importance_label()`/`salience_label()`/`confidence_label()` are monotonic across a sweep, extremes hit first/last band, sourced from the single `descriptors.py` enum; `to_numeric` **raises** on an unknown label; accessors are pure (no mutation). | `tests/memory/test_memory_schema.py` (extended). |
| **C descriptors (e2e)** | the agent sets importance verbally and recall renders the label, while grading (content substrings) is unchanged. | `recall_qa.py` / `memory_effect.py` via the oracle `MemFacade` (prefix excerpts with `importance_label()`); `reflecting.py` shows the label in an LLM-graded answer. Smoke assertions (`0/8 (0%)`, `8/8 (100%)`, `memory HELPED`/`HURT`) still hold (label is a prefix, not a token swap). |
| **A skill (round-trip)** | NEW `tests/memory/test_memory_skill.py`: bare `Agent` + `FakeLLMClient` (offline, hashing embedder); `MemorySkill().attach(agent)` → `agent._memory` is the installed manager; `remember`→`recall` round-trips; `reflect()`/`stats()` work; **`detach()` uninstalls all hooks**; an un-attached/disabled skill **raises** `RuntimeError` on `remember` (no silent fallback). | new test (no existing bench wraps a skill); offline + deterministic. |
| **A skill (regression)** | the mixin path is unchanged by the skill addition. | `pytest tests/memory/test_memory_manager.py tests/memory/test_memory_authoring.py` (baseline green). |

Run order: `uv run pytest tests/memory -q` after each workstream; the bench smoke (`bench.py`, `recall_qa.py`, `reflecting.py`) exercises the renamed `reflect()` and the verbal descriptors with **no API keys** (oracle/offline), so the whole plan is verifiable without network.

---

## Guidelines & net-LOC ledger

| guideline | how it's met |
|---|---|
| 1 No `getattr` | skill resolves via `self._mgr`; verbal layer uses concrete enums/dicts. (Pre-existing mixin `getattr(agent,"_memory")` for the agent-host path is unchanged, not new.) |
| 2 No fallback | `_memory_or_raise`, `to_numeric`, `_as_type`, `associate` all **raise**; rename adds **no** deprecation aliases; `--dream` flag dropped outright. |
| 3 No duplication | six tool bodies inherited (not re-typed); one `to_numeric`/`to_label` pair; one `_band` bucket helper. |
| 4 Essentials only | no `config.toml` section, no extra skill methods beyond `reflect`/`stats`. |
| 5 Separation | scoring stays numeric in core; vocabulary isolated in `descriptors.py`; skill isolated in the submodule. |
| 6 Minimal | rename is text-substitution; skill is ~30 lines; verbal layer is one small module + accessors. |
| 7 Net-neutral LOC | additions (`descriptors.py`, `MemorySkill`, accessors) offset by: deleted silent-fallback branches (`_as_type`, `associate`, `MemFacade` try/except ~8 lines), numeric→verbal prose swaps in docstrings/guide, and the rename being line-for-line. |
