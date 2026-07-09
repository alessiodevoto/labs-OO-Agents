# Memory System — Research Notes (Appendix)

This document is the appendix and supporting-evidence collection for the memory-system design. It gathers (A) codebase analysis of nooa extension points, (B) cognitive-science and prior-art research, (C) the schema and algorithms synthesis, and (D) the integration architecture and open-questions synthesis.

## Part A — Codebase Analysis (nooa extension points)

### A1 — Agent Class & Task Lifecycle (C1-agent-lifecycle)

# Long-Term Memory Add-On: Agent Class & Task Lifecycle Analysis

All paths absolute; line numbers from the read at analysis time.

#### 1. How an Agent subclass is defined and how `llm=` binding works

`Agent` uses the `AgentMeta` metaclass (subclass of `ABCMeta`), so subclass creation flows through `AgentMeta.__new__` → then Python calls `Agent.__init_subclass__`.

- `/root/projects/nooa/src/nooa/agent.py:74` — `class Agent(metaclass=AgentMeta)`.
- `/root/projects/nooa/src/nooa/agent.py:127` — `__init_subclass__(cls, llm=INHERIT, truncation=None, execution=None, context=None, event_query=None, **kwargs)`.

The `llm=` in `class MyAgent(Agent, llm=llm):` is a **class-keyword argument** consumed by `__init_subclass__`. It is stored on the class as `cls._agent_llm` (agent.py:153-154). `INHERIT` (a sentinel, agent.py:49-61) distinguishes "omitted" (cascade) from explicit; `llm=None` is rejected (`_validate_llm_param`, agent.py:64-71).

Resolution happens per-instance in `_resolve_llm` (agent.py:286-333), cascading:
1. instance `__init__(llm=...)`,
2. class hierarchy `getattr(self.__class__, "_agent_llm")` (walks MRO),
3. runtime parent via `_parent_agent_var.get()._llm` (set when a parent agent's generated code instantiates a sub-agent),
4. else `ValueError`.

`__init__` (agent.py:166-266) builds all framework state, notably:
- `self.event_manager = EventManager(...)` (agent.py:216) — **the event bus**,
- `self.context_manager = ContextManager()` (agent.py:234) — context-block state,
- protected framework blocks registered at agent.py:241-246: `system_prompt`, `self`, `state`,
- `self.context = ContextApi(self)` / `self.events = EventsApi(self)` (agent.py:262-263),
- `self.runtime = ActorRuntime(self)` (agent.py:266).

#### 2. How ellipsis (`...`) generation methods are dispatched & executed

**Class-creation wrapping.** `AgentMeta.__new__` (`/root/projects/nooa/src/nooa/metaclass.py:49`) iterates the namespace; for each async function it calls `_should_generate` = `has_ellipsis_body(...)` (metaclass.py:119-130). Qualifying methods are replaced via `_create_wrapper` → `create_agent_method_wrapper` (metaclass.py:211-256). So an ellipsis method on the class becomes a **wrapper** (`_needs_generation=True`).

**Call-time dispatch** lives in `create_agent_method_wrapper` (`/root/projects/nooa/src/nooa/runtime/method_wrapper.py:33-321`). When the wrapped method is awaited:
- validates args (method_wrapper.py:99-104), resolves strategy (defaults via `get_default_strategy()`, method_wrapper.py:107-111),
- Agent path (has `self.runtime`): pushes call-id, sets `_parent_agent_var`, fires `before_agent_call` hook (method_wrapper.py:199-232), then `_dispatch` (method_wrapper.py:173-180):
  - not in a generation session → `runtime._call_plan(...)`,
  - already in one (nested) → `runtime._execute_task(...)`,
- finally fires `after_agent_call` (method_wrapper.py:250-258).

**Runtime execution chain** (`/root/projects/nooa/src/nooa/runtime/actor.py`):
- `_call_plan` (actor.py:2262) → wraps `_execute_task` in an `asyncio.Task`.
- `_execute_task` (actor.py:2290) → if generation: acquires `_generation_lock` (when `strategy.requires_lock`) and calls `_execute_with_generation`.
- `_execute_with_generation` (actor.py:2355) — the **core of a task**: pops framework kwargs, resolves strategy/llm/truncation, sets `_in_generation_session`, generates a `generation_id`, **fires `before_generation` hook (actor.py:2430)**, expands the docstring placeholders (actor.py:2467), builds a `CurrentCall` (actor.py:2507), sets context-vars (`_current_call_var`, `_current_method_var`, `_current_llm_var`, `_current_strategy_var`), then `await strategy.execute(self, call)` (actor.py:2557), and in `finally` **fires `after_generation` hook (actor.py:2597)**.
- Strategy (e.g. `CodeActStrategy.execute`, `/root/projects/nooa/src/nooa/strategies/codeact.py:551`) builds the task prompt from `call.docstring` via `_build_task_message` (codeact.py:522-533), adds it as a `Task` event (codeact.py:602), then loops calling `runtime.generate(...)` (actor.py:753) which calls `_build_messages` → `_prepare_context` → `llm_client.acall(...)`.

**Docstring = prompt:** `_build_task_message` renders `## Task: {method_name}\n\n{docstring}` and `event_manager.add(Task(prompt=...))` (codeact.py:601-602) is the **first conversation event before the first LLM turn**.

#### 3. Full task lifecycle + concrete hook points

Lifecycle of one `await agent.some_ellipsis_method(args)`:

1. wrapper (`method_wrapper.py:70`) → `before_agent_call` hook → `_call_plan` (actor.py:2262)
2. `_execute_task` (actor.py:2290) → lock → `_execute_with_generation` (actor.py:2355)
3. **`before_generation` hook** (actor.py:2430) — *before strategy runs, before any LLM turn*
4. docstring expanded → `CurrentCall` built → `strategy.execute(self, call)` (actor.py:2557)
5. CodeAct: builds `Task` event (codeact.py:602) → loop: `runtime.generate()` (actor.py:753) → `_build_messages` (actor.py:2751) → `_prepare_context` (actor.py:2653) → `before_code_execution` hook → `execute_code` (actor.py:1130) → repeat until `return_result`
6. result returned; `finally`: **`after_generation` hook** (actor.py:2597); wrapper `finally`: `after_agent_call` hook (method_wrapper.py:251)

##### Hook mechanisms available (two distinct systems)

**System A — `InstrumentationHooks`** (`/root/projects/nooa/src/nooa/runtime/hooks.py`). A single, global-per-async-context hooks object installed via `set_hooks(obj)` (hooks.py:321). **Caveat: single-slot** — `set_hooks` overwrites; the OTLP tracer uses it, so an add-on that calls `set_hooks` would clobber/be clobbered by tracing. The hooks fire via `call_before_hook`/`call_after_hook`. Available hook methods: `before_agent_call`/`after_agent_call`, `before_generation`/`after_generation`, `before_code_execution`/`after_code_execution`, `before/after_method_invocation`, `before/after_tool_execution`, `on_messages_built`.

**System B — per-agent EventManager** (`/root/projects/nooa/src/nooa/runtime/event_manager.py`), **scoped to each agent instance, multi-subscriber** (preferred for an add-on):
- `agent.event_manager.on(event_type, handler)` (event_manager.py:184) — observe events; returns an unsubscribe callable. e.g. `on("Task", ...)`, `on("LLMOutput", ...)`.
- `agent.event_manager.intercept(kind, fn)` (event_manager.py:262) — async middleware that **wraps and can mutate** `agent_call`, `llm_call`, or `execute_python`. Multiple allowed (registration order = execution order, first = outermost).

##### Candidate hook points

**(a) BEFORE the first LLM turn — inject retrieved memories**

1. **`intercept("agent_call", fn)`** — RECOMMENDED for pre-turn injection. Registered via `agent.event_manager.intercept("agent_call", mw)`. The middleware runs in `create_agent_method_wrapper` (method_wrapper.py:182-219) *around the entire method execution, before `before_agent_call` fires and before any LLM turn*. `ctx` is an `AgentCallContext` with `agent`, `method_name`, `args`, `kwargs`. Inside `fn`, retrieve memories and inject before calling `nxt(ctx)`:
   - `ctx.agent.context["retrieved_memories"] = retrieved_text` (static block via `ContextApi.__setitem__`, context.py:63 → renders into the system prompt next `_build_messages`), or
   - `ctx.agent.event_manager.add(SomeEvent(...))` to prepend a conversation event.
   This wraps the whole task, so injected blocks/events are present for the *first* `generate()`.

2. **`intercept("llm_call", fn)`** — finest-grained: fires per LLM call inside `runtime.generate` (actor.py:834-924). `ctx` is `LLMCallContext` with mutable `ctx.messages` (already-rendered message list) and `ctx.params`. You can splice retrieved memories directly into `ctx.messages` before `nxt(ctx)`. Fires on *every* turn (not just the first) — gate on first turn via `_gen_hm.turn_count` or your own flag if you only want pre-first-turn.

3. **`InstrumentationHooks.before_generation`** (hooks.py:97, fired actor.py:2430) — fires once per task before the strategy runs, before the `Task` event. Gives `agent`, `method_name`, `generation_id`. Good signal, but mutating context here works (set `agent.context[...]`); the single-slot limitation applies.

4. **`on_messages_built`** (hooks.py:291, fired actor.py:852/927) — observation-only point with the final `messages` list right before `acall`; not intended for mutation (no return value consumed).

5. **DynamicContext block (no hook needed)** — register `agent.context.set_dynamic("memories", "self._retrieve_memories()")` (context.py:70) or class-level `context={...}` (agent.py:157). Re-evaluated each turn in `_prepare_context` (actor.py:2700-2728). Cleanest if memory retrieval can be expressed as a method on the agent; injects into the system prompt automatically every turn.

**(b) AFTER a task completes — trigger offline "dreaming" consolidation**

1. **`InstrumentationHooks.after_generation`** (hooks.py:121, fired actor.py:2597 in `finally`) — fires once per generation task with `agent`, `method_name`, `result`, `exception`, `generation_id`. The single most precise "task finished" signal. Single-slot caveat.

2. **`after_agent_call` via `intercept("agent_call", fn)`** — RECOMMENDED, multi-subscriber. In your middleware, do work *after* `result = await nxt(ctx)` returns (method_wrapper.py:210). At that point `ctx.result` holds the task result. Spawn consolidation with `asyncio.create_task(self._dream(...))` so it runs offline without blocking the caller. This is the natural symmetric counterpart to the pre-turn `agent_call` injection and avoids `set_hooks` collision with tracing.

3. **`on("Task", handler)` / `on("LLMOutput", handler)`** (event_manager.py:184) — observe-only; useful to accumulate episodic traces during the task that the dreaming step later consolidates. Note: `on` handlers are synchronous (event_manager.py:248-250) and exceptions are swallowed; schedule async work with `asyncio.create_task`.

**Recommended add-on shape (no core edits):** a mixin/subclass that, in `__init__` after `super().__init__()`, calls `self.event_manager.intercept("agent_call", self._memory_middleware)` where `_memory_middleware` (a) injects retrieved memories into `self.context[...]` before `await nxt(ctx)`, and (b) after it returns, schedules `asyncio.create_task(self._dream(ctx))`. This is per-instance, composable with tracing, and hooks both required points.

#### 4. How tools/methods are exposed to the LLM (`doc(self)`, agentdoc)

- The agent's public methods/fields are surfaced through the **agentdoc protocol**: `Agent.__type_info__` (agent.py:450-526) and `Agent.__instance_values__` (agent.py:528-590) decide what is visible — hiding `@hidden`/`Annotated[T, hidden]` and dunder/`_private` (unless `@spec(hidden=False)`), via `is_hidden_field`/`is_hidden_method`.
- `doc(...)` is the rendering entrypoint, injected into exec globals in `execute_code` (actor.py:1251, 1278) alongside `methods`, `variables`, `pprint`, `show`, strategies, etc.
- The **`self` context block** is registered protected as `doc(type(self))` (agent.py:242) and the **`state` block** as `pformat(self, ...)` (agent.py:243-246) — these render the agent's API + current field values into every system prompt. So any new public method on a memory mixin (e.g. `def recall(self, q): ...`) automatically becomes LLM-visible; mark internals `@hidden`/`_private` to keep them out.
- CodeAct also exposes `execute_python(code)` and `return_result(...)` as the two LLM tools (codeact.py:594-596).

#### 5. Existing wrapper / middleware / subclass extension mechanisms for cross-cutting behavior

1. **EventManager middleware** `intercept(kind, fn)` (event_manager.py:262) for `agent_call` / `llm_call` / `execute_python` — async wrappers that can transform inputs/outputs or short-circuit. Multi-registration, per-agent. **This is the primary supported extension point for cross-cutting behavior** (the docstring at event_manager.py:269-285 explicitly frames it as guardrails/auth/rate-limiting). Context types: `AgentCallContext`, `LLMCallContext`, `ExecutePythonContext` in `/root/projects/nooa/src/nooa/runtime/middleware.py`.
2. **EventManager observers** `on(event_type, handler)` (event_manager.py:184) — fire-and-forget observation.
3. **InstrumentationHooks** `set_hooks(obj)` (hooks.py:321) — global single-slot, used by tracing; avoid for add-ons that must coexist with tracing.
4. **Context blocks** — `context=` class kwarg (agent.py:132/157), instance `context=` (agent.py:172/254), `self.context[...]`/`set_dynamic` (ContextApi, context.py), `Strategy.get_block_overrides()` (base.py:215). These are the declarative way to inject content into the system prompt every turn.
5. **Custom event types** — subclass `EventBase` and `event_manager.register_event_type(cls)` (event_manager.py:165); add via `event_manager.add(event, record=True|False)` (event_manager.py:120) to persist memory/episodic events into the conversation or just notify handlers.
6. **Subclassing/mixins** — override `_system_prompt` (agent.py:411), add public helper methods (auto-visible via agentdoc), and wire middleware/observers in `__init__` after `super().__init__()`. Custom event subclasses + storage persistence are supported (`StorageManager`, agent.py:215).

##### Bottom line for the add-on
Implement long-term memory as a **subclass/mixin that registers an `agent_call` middleware in `__init__`** (after `super().__init__()`): pre-`nxt` it injects retrieved memories via `self.context[...]` (or splices `LLMCallContext.messages` for turn-level control), and post-`nxt` it schedules offline "dreaming" via `asyncio.create_task`. This needs zero core changes, is per-instance, composes with the tracing hook system (unlike `set_hooks`), and lands the injection strictly before the first LLM turn (which begins when CodeAct adds the `Task` event at codeact.py:602 and first calls `runtime.generate` at actor.py:753).

### A2 — Context System: Injection Surface for Spontaneous Recall (C2-context-injection)

# Context System Analysis — Injection Surface for Spontaneous Recall

#### 1. Storage, evaluation, caching, rendering of context blocks

##### Storage (single source of truth: `ContextManager`)
`ContextManager` (`src/nooa/runtime/context_manager.py:28`) holds three dicts:
- `_blocks: dict[str, Any | DynamicContext]` — the raw value (or a `DynamicContext` marker for dynamic blocks).
- `_dynamic_cache: dict[str, Any]` — last-resolved value of each dynamic block (populated post-turn).
- `_static: dict[str, bool]` — partition flag (True = cacheable prefix, False = volatile suffix).

`self.context` is `ContextApi` (`src/nooa/runtime/context.py:25`), a thin `Skill` wrapper that delegates everything to `agent.context_manager`. It is always present, hidden from the LLM by default (opt-in via `spec(self, "context", hidden=False)`).

Key routing distinction:
- **Static** = cacheable prefix. Only `set_static(key, value)` (`context_manager.py:73`) sets `_static[key]=True`. There is no `ContextApi` method for this — `ContextApi` does **not** expose `set_static`.
- **Dynamic / volatile** = everything via `ContextApi`. Both `self.context["k"]=v` (`context.py:63` → `context_manager.py:51`) and `self.context.set_dynamic("k","expr")` (`context.py:70` → `context_manager.py:102`) set `_static[key]=False`. A bare value goes to `_blocks` as-is; an `expr` string is wrapped in `DynamicContext(expr)`.

`DynamicContext` (`context_blocks/models.py:25`) is a frozen pydantic model that **compiles `expr` as an eval expression at construction time** (`models.py:51-54`) — syntax errors raise `BlockSyntaxError` immediately, not at render time.

##### Evaluation + caching (per turn)
- Static block read: `__getitem__` returns the value directly from `_blocks` (`context_manager.py:153`).
- Dynamic block read via `self.context["k"]`: returns the cached resolved value from `_dynamic_cache`, or raises `DynamicNotResolvedError` if not yet resolved (`context_manager.py:156-159`). So the LLM-visible read is the *previous* turn's resolved value; the live re-eval happens during prompt build.
- Cache invalidation: `_invalidate(key)` drops the cache entry on every `set/set_dynamic/del/pop` (`context_manager.py:232`).
- Cache fill: `_update_resolved(resolved)` (`context_manager.py:275`) is called exactly once per turn by the actor (`actor.py:2747`) after the build pipeline resolves all dynamic exprs.

##### Rendering
`_phase_persistent_blocks` (`context_builder.py:249`) iterates `context_manager._raw_items()` and produces a `ResolvedBlock` per key:
- DynamicContext → `await resolve_fn(key, value)`, `source_dynamic=True`, `expr=value.expr` (`context_builder.py:272-284`).
- Static value → rendered through `pformat` with `context_block_format` structural bounds (`max_string/max_length/max_depth`), `expr=f'self.context["{key}"]'` (`context_builder.py:285-296`).

`BlockMetadata.user_block` is `True` for non-protected (user) keys — **this is the truncation-eviction priority flag** (see §3). `static` flag controls cache placement and protects from eviction.

Final wire format: `XMLBlockFormatter` (default) wraps each system block as `<key expr="...">content</key>` (`formatter.py:128-138`); `expr=` is emitted **only** when `source_dynamic` is True (`formatter.py:136`) — so `set_dynamic` blocks advertise their accessor to the LLM, plain `self.context[k]=v` dynamic blocks do not show expr (they get `expr=self.context["k"]` metadata but `source_dynamic=False`).

#### 2. EXACT mechanism + timing of dynamic re-evaluation per turn

This is the candidate channel for spontaneous recall. The chain on **every** LLM turn:

1. `Runtime.generate()` is invoked once per LLM turn and calls `_build_messages(...)` unconditionally (`actor.py:785`). Recovery paths (context-window overflow) also rebuild (`actor.py:903`, `:990`).
2. `_build_messages` → `_prepare_context(method, call_args, call_kwargs)` (`actor.py:2772`).
3. `_prepare_context` (`actor.py:2653`) builds `extra_context` = `{method, call_args, call_kwargs, strategy, datetime, runtime}` (`actor.py:2687`) and defines the closure `_resolve_value(key, value)` (`actor.py:2700`):
   - For a `DynamicContext`, it calls `await self.evaluate_expression(value.expr, extra_context=extra_context, error_mode="raise")` (`actor.py:2710`).
   - On exception → returns `f"{type(e).__name__}: {e}"` inline (block does not crash the build, `actor.py:2713-2721`).
   - Non-str result → `pformat(result, unquote_strings=True, **ctx_block_kwargs)` (`actor.py:2728`).
4. `evaluate_expression` (`actor.py:1924`) compiles the expr as an **async** function `async def __eval_expr(): return <expr>` and execs it in a namespace seeded with `self` (the agent), `doc`, `pformat`, `methods`, `variables`, plus `extra_context`, plus live REPL locals (`repl._data`) and `result` (`actor.py:1963-1985`). **It auto-awaits coroutines** (`actor.py:1993-1997`) — so a dynamic expr can call an `async` method. This is critical: a memory-retrieval coroutine works as a dynamic expr.
5. `build_context` runs the resolve over all persistent blocks (`context_builder.py:275`), returns `BuildResult(blocks, resolved_cache)`.
6. Back in `_prepare_context`, the single side effect: `context_manager._update_resolved(build_result.resolved_cache)` (`actor.py:2747`) — refreshes `_dynamic_cache` so subsequent `self.context["k"]` reads see this turn's value.

**Timing summary:** dynamic exprs are re-evaluated synchronously inside the prompt build, once per `generate()` call, i.e. once per LLM turn (plus on each context-overflow retry). The expr namespace has full agent access and awaits coroutines.

#### 3. Ordering, placement, size/truncation

##### Ordering (the pipeline, `context_builder.build_context`, `context_builder.py:171`)
Blocks are assembled in strict phase order (later overrides earlier by key):
1. **Persistent blocks** from `context_manager` — protected framework blocks (`system_prompt`, `self`, `state`) + all user blocks, **in `_blocks` dict insertion order** (`_phase_persistent_blocks`, `context_builder.py:249`).
2. Strategy overrides (`get_block_overrides()`, e.g. `strategy_prompt`, `execution_context`) (`context_builder.py:224`).
3. `@strategy(context={...})` decorator context (`:227`).
4. `with ScopedContext(...)` scoped blocks (`:230`).
5. **Reorder**: `_reorder_blocks` applies `strategy.get_block_order()` — listed keys first in given order, unlisted system blocks keep relative order (`context_builder.py:127`, `:233`).
6. Events appended last as USER/ASSISTANT/TOOL-role blocks (`_phase_events`, `:236`).

`render_context` (`renderer.py:137`) then partitions by role: all SYSTEM blocks concatenate into one system message (joined by `\n\n`, `formatter.py:293`), in list order; event blocks become per-event messages after.

**Net placement for a new user/dynamic block:** it lands among the persistent SYSTEM blocks. New keys are *appended* (`_apply_overrides` appends, `context_builder.py:120`; `_phase_persistent_blocks` appends in insertion order). Unless a strategy's `get_block_order()` names it, a freshly-created block renders **after** framework + strategy blocks but **before** the event history. To pin position, register the key in `get_block_order()`.

##### Size / truncation (`renderer.py`)
- Default budget: if no `max_context_tokens` configured, `effective_context_limit = ctx_window // 2` (`actor.py:2785`).
- **No per-block head/tail truncation** — content passes verbatim (`renderer.py:148-157`). Bounding happens earlier via `pformat` structural knobs (`context_block_format`: `max_string/max_length/max_depth`) for non-string static/dynamic results.
- Total-budget eviction `_apply_context_total_limit` (`renderer.py:80`): two-pass, **from the end**:
  1. Evict `user_block and not static` blocks first.
  2. Then any `not static` block.
  Evicted blocks are replaced in place with `"EVICTED: over context budget (block_tokens=N)"` and `metadata.truncated=True` (`renderer.py:118-132`).
- **Implication for a memory block:** a user/dynamic block (`user_block=True, static=False`) is in the **first eviction tier** — it gets dropped before framework blocks under pressure. Good (memory shouldn't push out the task), but means it must self-bound its own size since eviction is all-or-nothing per block (replaced with a stub, not trimmed).

#### Recommendation: inject retrieved memories as a bounded dynamic block

The dynamic-block channel (§2) is the correct injection surface. Concrete approach:

**Register one dynamic block whose expr calls an async retrieval method on the agent.** Because `evaluate_expression` awaits coroutines and seeds the namespace with `self` (`actor.py:1963`, `:1993-1997`), the expr can be an async method call. Add a `@hidden` method on the agent (or a memory `Skill`) and register the block once at `__init__`:

```python
# expr is re-evaluated every turn; awaited automatically; self is the agent
self.context.set_dynamic("recalled_memories", "self._recall_memories()")
```

```python
@hidden            # keep it out of doc(self) / exec_globals
@no_trace          # optional: avoid trace noise every turn
async def _recall_memories(self) -> str:
    # Derive the query from current state — e.g. last user/event text,
    # or self.events / self._last_execution_result available in namespace.
    hits = await self._memory_store.search(self._current_query(), k=5)
    return self._format_memories(hits)   # return a STRING, pre-bounded
```

Why this design:

- **Spontaneity / per-turn freshness:** the expr re-runs inside every `generate()` build (`actor.py:785` → `_prepare_context` → `_resolve_value` → `evaluate_expression`), so retrieval reflects the latest turn state with zero orchestrator wiring. No need to call it from each generation method.
- **Failure isolation:** if retrieval throws, `_resolve_value` catches it and renders `"<Error>: msg"` inline (`actor.py:2713-2721`) instead of crashing the turn.
- **Bounded size (do this in the method, not the framework):** truncation is all-or-nothing per block, so cap the content yourself before returning:
  - Fixed `k` (e.g. top-5), and hard-cap the formatted string (e.g. `text[:MAX_CHARS]` or N tokens) inside `_format_memories`.
  - If returning a non-string structure, rely on `pformat`'s `context_block_format` bounds (`max_string/max_length/max_depth`), but a pre-truncated string is more predictable.
  - Keep it a **user/dynamic block** (which it is — set via `ContextApi`), so under context pressure it is evicted *first* (`renderer.py:102`), protecting the task and framework blocks. The block already carries `user_block=True, static=False`.
- **Visibility hygiene:** the block renders as `<recalled_memories expr="self._recall_memories()">...</recalled_memories>` (`source_dynamic=True`, `formatter.py:136`). If you don't want the LLM to see the accessor/expr, set a plain value each turn instead — but that requires an orchestrator hook to recompute; the dynamic-expr route is strictly better for "every turn, automatically." Mark the helper `@hidden` so the method itself isn't advertised in `doc(self)`.
- **Placement:** to pin the memory block to a stable position (e.g. right after `system_prompt`, before events), have the strategy include `"recalled_memories"` in `get_block_order()` (`context_builder.py:139`); otherwise it appends after framework/strategy blocks and before the event log, which is usually acceptable.

Caveat on caching: dynamic blocks live in the **volatile/non-cacheable** partition (`_static=False`), so a per-turn-changing memory block will not break prompt-prefix caching of the static framework prefix — but the memory block itself is never cached (correct, since it changes). The LLM-visible `self.context["recalled_memories"]` read returns the previous turn's cached value (`context_manager.py:156`); the live value is what's rendered into the prompt.

##### Key file:line references
- `ContextApi`: `src/nooa/runtime/context.py:25` (`set_dynamic` `:70`, `__setitem__` `:63`)
- `ContextManager`: `src/nooa/runtime/context_manager.py:28` (`set_dynamic` `:102`, `__getitem__`/cache `:136-159`, `_update_resolved` `:275`, `_invalidate` `:232`, `_raw_items` `:190`)
- `DynamicContext` (compile-on-create): `src/nooa/context_blocks/models.py:25`
- Per-turn build entry: `src/nooa/runtime/actor.py:785` (`generate`→`_build_messages`)
- `_prepare_context` + `_resolve_value` closure: `src/nooa/runtime/actor.py:2653`, resolve `:2700`, cache side-effect `:2747`
- `evaluate_expression` (async eval, auto-await, namespace): `src/nooa/runtime/actor.py:1924`
- Build pipeline + phases: `src/nooa/runtime/context_builder.py:171`; persistent/dynamic resolve `:249-303`; ordering `_reorder_blocks` `:127`; append behavior `:120`
- Rendering + truncation/eviction: `src/nooa/context_blocks/renderer.py:137`, eviction `_apply_context_total_limit` `:80`; default budget `actor.py:2785`
- XML wrapping / `expr=` emission gate: `src/nooa/context_blocks/formatter.py:128-138`, system concat `:293`

### A3 — Events + Runtime Lifecycle Hooking (C3-events-runtime)

# nooa Events + Runtime — Lifecycle Hooking Analysis (for an add-on memory system)

#### 1. EventsApi / EventManager surface

**`EventsApi`** (`src/nooa/runtime/events.py:21`) — the agent/LLM-facing, *read-only* wrapper exposed as `self.events`. It holds `self._manager = agent.event_manager` (`runtime/events.py:72`). Methods: `query(...)` (`:74`), `get(key|[keys])` (`:115`), `__getitem__` (`:149`), `__contains__` (`:180`), `collapse(start,end,summary_text=None)` (`:195`), `keys()` (`:216`). It cannot subscribe or write events — only query and collapse. Hidden from the LLM by default (`agent.py:110`, `Annotated["EventsApi", hidden, nosnapshot]`).

**`EventManager`** (`src/nooa/runtime/event_manager.py:73`) — the real bus, exposed as `self.event_manager` (also hidden, `agent.py:102`). This is what a memory system should attach to. Relevant API:

- `add(event, *, record=True) -> tag` (`event_manager.py:120`) — the single ingress point. It (1) auto-tags with `call_id` from the agent call stack (`:134`), (2) `self._emit(event)` to handlers (`:139`), (3) records unless `RUNTIME_EVENT` role (`:142`).
- `on(event_type: str, handler) -> unsubscribe` (`event_manager.py:184`) — **observe** events fire-and-forget. `event_type="*"` subscribes to all (`:254`). Handlers are sync `Callable[[EventBase], None]`; exceptions are caught and logged, never propagated (`:251`). Returns an idempotent unsubscribe closure.
- `intercept(kind, fn) -> unsubscribe` (`event_manager.py:262`) — **wrap** a live operation (middleware; see §2).
- `register_event_type(cls)` (`event_manager.py:165`) — register a custom `EventBase` subclass so persistent backends (SQLite) can deserialize it. No-op for `InMemoryBackend`. **A memory system defining its own event type should call this.**
- Query: `filter(type=, call_id=, query=, regex=, limit=)` (`:348`), `get`, `__getitem__`, `__contains__`, `items/keys/values`.
- Mutation/archive: `update`, `remove`, `clear`, `collapse(...)` (`:504`, emits the `Summary` to handlers at `:584`).
- `set_backend` (`:205`) — swap persistence while preserving handlers/middleware.

Storage is pluggable via the `EventBackend` Protocol (`runtime/event_backend.py:36`); default `InMemoryBackend` (`:215`), SQLite backend referenced at `storage/sqlite.py`. A memory system can persist independently of the agent's backend.

#### 2. The full event set and WHEN each fires

Events are defined in `src/nooa/events.py`. The `_role` ClassVar determines recording: `Role.RUNTIME_EVENT` events are **emitted to handlers but never recorded** in LLM context (`event_manager.py:142`) — they are pure observability hooks. All events fire through `event_manager.add(...)`, so `on(...)` sees every one.

| Event | `_role` | Fired by → when | Key fields |
|---|---|---|---|
| **`Task`** | USER | `codeact.py:602` (also predict/pure_python) — **at task start**, first thing after tool setup; its tag becomes `call.id` | `prompt`, `images` |
| **`BeforeTurn`** | RUNTIME_EVENT | `codeact.py:213` (inside `session.turn()` CM), `predict.py:163`, `pure_python.py:283` — **before each LLM generation turn** | `method_name`, `strategy`, `generation_id`, `parent_generation_id`, `turn_number` |
| **`SystemPrompt`** | RUNTIME_EVENT | `actor.py:187` (`_snapshot_llm_request`) — right after messages list built, per LLM call | `content` (static system prompt), `generation_id` |
| **`LLMCallStart`** | RUNTIME_EVENT | `actor.py:826` (`_emit_llm_start`) — immediately before the `acall()` round-trip | `method_name`, `strategy`, `generation_id`, `turn_number` |
| **`LLMCallEnd`** | RUNTIME_EVENT | `actor.py:828` (`_emit_llm_end`) — immediately after `acall()` returns or raises; fires even on failure | `+ success`, `exception_type` |
| **`LLMComplete`** | RUNTIME_EVENT | `actor.py:1100` — after each LLM round-trip, **before `LLMOutput`** | tokens, `cost_usd`, `tool_calls`, `reasoning_content`, `model_name`, `generation_id`, `dynamic_context` |
| **`LLMOutput`** | ASSISTANT | `actor.py:1126` — the raw model output (code / JSON / tool calls) recorded after `LLMComplete` | `content` |
| **`Reasoning`** | ASSISTANT | when generated code calls `reasoning()` | `content` |
| **`Message`** | ASSISTANT | when generated code calls `message()` (multi-turn comms back to caller) | `content` |
| **`PythonOutput`** | USER | after `execute_python` runs — captures stdout/stderr/value/status | `tool_call_id`, `execution_status`, `stdout`, `stderr`, `value`, `error`, `images` |
| **`Error`** | USER | on retryable errors, prefill errors, validation failures, tool-use reminders (many sites in `codeact.py`) | `content` |
| **`Feedback`** | USER | execution feedback when target method not yet defined | `content` |
| **`Notification`** | USER | **generic external "something happened" signal** — input queues emit on `put()`; equally for webhooks/jobs/timers | `source` (e.g. `"queue:user_messages"`), `description` |
| **`Summary`** | ASSISTANT | `event_manager.collapse(...)` (`:564`) — when a range is archived/summarized | `summary_tag`, `replaced_range`, `children_tags`, `summary_text` |
| **`AfterTurn`** | RUNTIME_EVENT | `codeact.py:231` (CM `finally`), `:909` (loop exhausted), `predict.py:183`, `pure_python.py:444/459` — **after each turn**; `is_final=True` on the last turn | `+ is_final`, `success`, `exception_type`, `parent_generation_id` |
| **`DebugTrace`** | METADATA | manual; stored in traces, never shown to LLM | `content` |

The `Event` union is at `events.py:493`. There is **no dedicated "tool call" or "agent_call start/end" event** — tool calls are surfaced structurally via `LLMComplete.tool_calls` and via the `execute_python` middleware; agent method boundaries are surfaced via the `agent_call` middleware (§2 below) and the `before_agent_call`/`after_agent_call` *tracing* hooks (`method_wrapper.py:200,251`), not as bus events.

**Critical timing facts for a memory system:**
- **Task start** = `Task` event (recorded, USER role).
- **Turn boundaries** = `BeforeTurn` / `AfterTurn` (runtime-only, not recorded).
- **Task end** = the **final `AfterTurn` where `is_final=True`**. A *top-level* task end is `is_final=True and parent_generation_id is None` (this is exactly how the ATIF exporter detects run completion — `atif/exporter.py:225`). This is uniform across CodeAct, Predict, and PurePython strategies, making it the single reliable post-task signal regardless of strategy.
- Per-LLM-call order within a turn: `BeforeTurn` → `SystemPrompt` → `LLMCallStart` → `LLMCallEnd` → `LLMComplete` → `LLMOutput` → (`PythonOutput`/`Error`/...) → `AfterTurn`.

#### 3. Middleware mechanism

Three wrap points, registered on the `EventManager` (`runtime/middleware.py`, engine in `event_manager.py:262-327`):

| kind constant | wraps | context object | invoked at |
|---|---|---|---|
| `MIDDLEWARE_AGENT_CALL` = `"agent_call"` | the **entire agent method** (all turns, all code, the return) | `AgentCallContext` (agent, method_name, args, kwargs, result) | `method_wrapper.py:213` |
| `MIDDLEWARE_LLM_CALL` = `"llm_call"` | `runtime.generate()` LLM round-trip | `LLMCallContext` (messages, params, agent, runtime, response) | `actor.py:879 / :914` |
| `MIDDLEWARE_EXECUTE_PYTHON` = `"execute_python"` | `runtime.execute_code()` sandbox exec | `ExecutePythonContext` (code, params, agent, runtime, result) | `actor.py:1194` |

**Signature:** `async def mw(ctx, nxt) -> ctx`. Middleware must `return await nxt(ctx)` (or set `ctx.result`/`ctx.response` and return `ctx` to short-circuit). Returning `None` raises (`event_manager.py:63`). Chain is built in `run_middleware` (`:309`): registration order = execution order, **first registered = outermost**; zero overhead when none registered (`:319-321`).

**Registration:**
```python
unsubscribe = agent.event_manager.intercept("agent_call", my_mw)   # event_manager.py:262
```
Reference implementation: `nemo_flow_middleware.py` — `install_nemo_flow(event_manager)` (`:297`) registers all three and returns an uninstall closure; `nemo_flow_scope(agent, name)` (`:320`) is an `@asynccontextmanager` that installs on entry and uninstalls on exit. **This is the exact pattern a memory system should copy.**

**Can it intercept before/after turns or tasks?**
- **Tasks: yes**, directly — `agent_call` middleware wraps the whole method. Code before `await nxt(ctx)` runs at task start; code after runs at task end (and `ctx.result` holds the return value). It can also transform inputs/outputs or block.
- **Turns: no** via middleware — there is no `turn` middleware kind. Turn boundaries are observable only via `on("BeforeTurn"/"AfterTurn", fn)` (fire-and-forget) — which is the right tool anyway.

#### 4. Detecting "important external events" → memory WRITE

Two complementary detection points:

- **`Notification` event** (`events.py:301`) is purpose-built for this: "Generic 'something happened' signal… long-running job completions, timer ticks, external webhook deliveries, or any other asynchronous signal." Its `source` field is a namespaced string (`"queue:user_messages"`, `"job:12345"`, `"timer:daily-cron"`) the outer dispatcher keys off. **Subscribe `on("Notification", write_handler)`** and write to memory when `source`/`description` match your salience policy. This is the cleanest "external event" channel and is already recorded into LLM context (USER role).

- **`Message` event** (`events.py:74`) for important *agent-emitted* statements (the LLM called `message()`), and **`Error`** for failures worth remembering. Subscribe selectively.

Detection mechanics: `on(type, fn)` handlers are sync and best-effort (exceptions swallowed, `event_manager.py:251`). For an *actual write* (likely async / I/O), do not block in the handler — capture the event and schedule the write (e.g. `asyncio.create_task`, or a queue your consolidation loop drains), mirroring how the summarization agent schedules work off `AfterTurn` rather than doing it inline (`agents/summarization.py:189-219`). If you need to *gate/transform* the agent's behavior on the external event (not just record), use `agent_call` or `llm_call` middleware instead, since those can mutate `ctx`.

#### 5. Recommended hook points

##### (a) Write-on-event
**Primary:** `agent.event_manager.on("<Type>", handler)` for observe-and-write, with salience filtering inside the handler.
- Salient external signals → `on("Notification", h)` (filter on `event.source`).
- Errors/important messages → `on("Error", h)`, `on("Message", h)`.
- To capture *everything* and filter centrally → `on("*", h)` (`event_manager.py:254`), but be selective to avoid write amplification (every `LLMComplete`/`PythonOutput` fires here too).
- Keep handlers non-blocking: enqueue, don't `await` heavy I/O in-handler. Register/unregister with the returned unsubscribe; wrap install/uninstall in an `@asynccontextmanager` like `nemo_flow_scope` (`nemo_flow_middleware.py:320`).
- If you mint a custom memory event type, call `event_manager.register_event_type(MyEvent)` (`event_manager.py:165`) at startup so SQLite persistence round-trips it; emit via `event_manager.add(MyEvent(...), record=False)` if it should stay out of LLM context (or give it `Role.RUNTIME_EVENT`).

**If you need to gate/transform rather than just observe:** register `intercept("agent_call", mw)` and act in the pre-`nxt` phase.

##### (b) Post-task "dreaming" consolidation
Two viable hooks; recommend the **`agent_call` middleware** as primary, with `on("AfterTurn")` as the lightweight alternative.

1. **`intercept("agent_call", consolidate_mw)` (cleanest, recommended).** Wraps the whole top-level method synchronously. Run consolidation in the post-phase, with the final result in hand:
   ```python
   async def consolidate_mw(ctx, nxt):
       ctx = await nxt(ctx)            # task runs to completion
       # ctx.result holds the return value; ctx.agent.events to query the trajectory
       schedule_dream(ctx.agent, ctx.method_name, ctx.result)  # don't block the return
       return ctx
   ```
   Advantages: fires exactly once per method call, has the return value, runs inside the agent's async context, can be scoped to only the top-level entry method, and you can read the full episode via `ctx.agent.events.query(call_id=...)`. Invoked at `method_wrapper.py:213`. Caveat: it fires for **every** decorated method including nested subagent calls — gate on `ctx.method_name` (your orchestrator entrypoint) or check the call stack depth (`_get_agent_call_stack`, used at `event_manager.py:134`) to only consolidate at the outermost boundary.

2. **`on("AfterTurn", handler)` filtered to top-level final turn (lightweight alternative).** This is precisely the signal the framework itself uses for run-completion (ATIF: `atif/exporter.py:663-669`, `:225`) and what the summarization agent subscribes to (`agents/summarization.py:161`):
   ```python
   def on_after_turn(ev):
       if ev.is_final and ev.parent_generation_id is None:
           schedule_dream(...)        # top-level task just completed
   ```
   Strategy-agnostic (CodeAct/Predict/PurePython all emit it). Downsides vs. middleware: no direct access to the method return value, and handler is sync + best-effort, so you must schedule the actual async consolidation (queue / `create_task`) exactly as `agents/summarization.py` does (`_handle_after_turn` → `_schedule_summarization`, `:189-219`; and note its two-phase pattern: schedule on `AfterTurn`, apply on the next `BeforeTurn`, to avoid mutating context mid-turn).

**Net recommendation:** attach a memory `EventManager` subscriber set in an `@asynccontextmanager` scope (model on `nemo_flow_scope`). Use `on("Notification"/"Error"/"Message")` for write-on-event, and a single `intercept("agent_call", ...)` gated to the orchestrator entrypoint for post-task dreaming — falling back to `on("AfterTurn")` filtered on `is_final and parent_generation_id is None` if you prefer pure observation and don't need the return value.

##### Key file:line references
- Event definitions + roles: `src/nooa/events.py:59-507`
- Bus: `add` `event_manager.py:120`; `on` `:184`; `intercept` `:262`; `run_middleware` `:309`; `register_event_type` `:165`; `collapse` `:504`
- Middleware contexts/constants: `runtime/middleware.py:59-148`
- agent_call invocation: `runtime/method_wrapper.py:168-219`; llm_call: `actor.py:807-922`; execute_python: `actor.py:1158-1197`
- Turn emission: `strategies/codeact.py:202-243` (CM), `:602` (Task), `:909` (final AfterTurn on exhaustion); `strategies/predict.py:156-194`; `strategies/pure_python.py:283,444,459`
- LLM-call observability events: `actor.py:187` (SystemPrompt), `:826/:828` (LLMCallStart/End), `:1100` (LLMComplete), `:1126` (LLMOutput)
- Reference install/scope pattern: `nemo_flow_middleware.py:297-356`
- Agent exposure of `event_manager`/`events` (both hidden): `agent.py:102,110,216,263`
- Existing AfterTurn-driven consolidation precedent: `agents/summarization.py:160-219`; top-level-completion detection precedent: `atif/exporter.py:663-669`

### A4 — Strategies & Tools: Conscious Tool Calls (C4-strategies-tools)

# Memory System Analysis: How Agents Consciously Call Tools (CodeAct strategy)

Read-only analysis of `/root/projects/nooa/src/nooa/strategies/`. All file:line refs below.

#### 1. How CodeAct exposes `execute_python()` / `return_result()` and how agent methods become callable

CodeAct is a **two-tool** strategy. Both tools are built fresh per call in `execute()`:

- `codeact.py:594-596` — `tools = [execute_python_tool, return_result_tool]`
- `_build_execute_python_tool()` at `codeact.py:1798-1827` — returns a `Tool(name="execute_python", callable=..., parameters_model=None)`. The schema is auto-generated from the callable signature `execute_python(code: str)`. The callable body is a stub (`return ""`); actual execution is handled in the loop.
- `_build_return_result_tool(return_type, method_name)` at `codeact.py:1900+` — builds a `Tool` whose parameter schema is a Pydantic model `{result: <return_type>}` derived from the method's return annotation (`_create_return_model`, `codeact.py:1845`).

**Critically, agent methods are NOT registered as individual LLM tools.** The only two tools the model ever sees are `execute_python` and `return_result`. The agent's own methods (`self.recall(...)`, `self.search(...)`) become callable because the agent instance is injected as `self` into the Python execution namespace, and the LLM writes `await self.method(...)` *inside* an `execute_python` cell.

- The namespace is built by `ExecutionNamespaceBuilder.build(agent, extra=...)` at `generated_code.py:52-92`. Line `generated_code.py:74` sets `"self": agent`. So every public (non-`@hidden`) method on the agent is reachable through `self`.
- Async agent methods MUST be awaited; `GeneratedCodeValidator._missing_await_errors` (`generated_code.py:113-141`) statically rejects un-awaited `self.async_method(...)` calls before execution.
- Unknown tool calls (if the model tries to call a method as a top-level tool) are auto-translated into `execute_python` code by `_translate_tool_call_to_code` (`codeact.py:925-978`, dispatched at `codeact.py:1106-1119`) — it rewrites `foo(args)` into `await self.foo(args)` / `return_result(...)` cells. This reinforces that the canonical path is "call methods inside execute_python," not "expose each method as its own tool."

The per-turn dispatch lives in `_process_tool_calls` (`codeact.py:980+`): `execute_python` → `_handle_execute_python` (`codeact.py:1202`); `return_result` → `_handle_return_result` (`codeact.py:1471`); inline `return_result()` raised from within code is caught via `_ReturnResultSignal` (`codeact.py:75-90`, raised by the builtin at `codeact.py:2344/2347`).

#### 2. `exec_globals` — how module-level + agent-level names become visible

Two layers combine into the execution namespace:

**Layer A — `_build_builtins(runtime, call)` (`codeact.py:2313-2368`):**
1. Module context via `_extract_module_context(agent_module, agent)` (`codeact.py:2223-2280`):
   - `filter_module_globals(agent_module)` (`codeact.py:2242`) drops names hidden by `@hidden` / `Annotated[..., hidden]` / `with hidden:`.
   - Step 1 (`:2245-2257`) keeps imported modules and imported classes/functions.
   - Step 2 (`:2266-2271`) keeps classes AND functions *defined in the agent's own module* (this is how a module-level `recall`/`remember` function would surface).
   - Step 3 (`:2273-2275`) `_import_dynamic_classes` auto-imports classes for skill/tool instances attached to the agent (`codeact.py:2282-2311`) — relevant if a memory store is attached as an agent field of a custom class.
2. Strategy builtins override module names (`:2356-2361`): `reasoning` and `return_result`.
3. Method parameters merged in as locals via `call.kwargs` (`:2366`).

**Layer B — `ExecutionNamespaceBuilder.build` (`generated_code.py:52-92`):** starts again from `filter_module_globals(agent_module)`, then adds the always-available core symbols: `self` (the agent), `asyncio`, `typing`, `doc`, `methods`, `variables`, `help`→`doc`, `strategy`, `pprint`. The `extra` dict passed at `codeact.py:2164-2166` is `{**builtins, **session.session_locals, **strategy_extras}`, so module context + params + REPL-persistent locals + `CodeActStrategy`/`PredictStrategy` all land in `exec_globals`.

The `execution_context` system-prompt block (`codeact.py:370-457`) documents exactly these names to the LLM (Imported modules / Available types / Available functions / Imported items / Always available: `self, print(), pprint(), doc(), return_result(), reasoning()`), so the model knows what it can reference.

Net: a memory API is made visible either as **agent methods** (visible by default via `self` + `doc(self)`) or as **module-level functions** (visible by default via `_extract_module_context` step 2).

#### 3. How a memory toolset (`recall`, `search`, `remember`, `associate`) is surfaced idiomatically as CONSCIOUS tools

The idiomatic surface in this framework is **agent methods called through `self` inside `execute_python`** — NOT new top-level LLM tools. This matches CLAUDE.md ("an agent's methods are its capabilities") and the actual mechanics above. Concretely:

- Define them as deterministic (no-ellipsis) public methods on the agent — or on a mixin/base the agent subclasses:
  ```python
  def remember(self, text: str, *, tags: list[str] | None = None) -> str: ...   # store, return id
  def recall(self, query: str, k: int = 5) -> list[Memory]: ...                  # semantic/keyword fetch
  def search(self, query: str, k: int = 5) -> list[Memory]: ...
  def associate(self, a: str, b: str) -> None: ...                                # link two memory ids
  ```
  Because they are public and have no `...` body, they are real Python (deterministic helpers — "Helpers beat prompts"), automatically visible to the LLM via `doc(self)` and callable as `await self.recall(...)` (or sync `self.recall(...)` if not async). No tool registration, no `Tool` objects needed.
- The return type `Memory` must be a Pydantic model defined/imported at **module level** so it lands in `exec_globals` (step 1/2 of `_extract_module_context`) and the LLM can `doc(Memory)` it.
- If the memory store is a separate object held as an agent field (e.g. `self.memory: MemoryStore`), keep it visible (don't `Annotated[..., hidden]` it); its class is auto-imported by `_import_dynamic_classes` (`codeact.py:2282`) so `doc()`/isinstance work, and the LLM calls `await self.memory.recall(...)`.
- To advertise them in the prompt, embed `{doc(self)}` in the orchestrating method's docstring (per CLAUDE.md "Understanding `doc(self)`"). The methods then appear with signatures + docstrings under the agent's API.

This makes the four operations **conscious, deliberate tool calls** — the model decides to write `mems = await self.recall("prior incidents", k=3)` in a cell, sees the result echoed back as a tool result event, and reasons over it on the next turn. The two-tool envelope (`execute_python` / `return_result`) is preserved; memory ops ride inside `execute_python`.

(For a memory op that itself needs LLM judgment — e.g. "summarize then store" — make it a generation method with `...` and `@strategy(PredictStrategy())`, still called via `await self.remember_summary(...)`.)

#### 4. Per-turn loop structure and where retrieval-injection fits

The loop is in `execute()` (`codeact.py:551-…`):

1. Build session + builtins + the two tools (`:577-596`).
2. **Prefill (pre-loop, once):** `_run_prefill` (`codeact.py:1984-2034`) runs synthetic `execute_python` cells *before* the loop — input inspection (`InspectInputsPrefill`) and any user pre-ellipsis code (`call.pre_ellipsis_code`). Each is added to the event log as a `ToolCallEvent` with `metadata={"prefill": True}` (`codeact.py:2055-2063`). **This is the natural place for a one-shot "retrieve relevant memories at task start" injection.**
3. **`while not session.is_exhausted()`** (`codeact.py:629`): each iteration opens `session.turn(...)` (`:632`) then calls `runtime.generate(tools=tools, tool_choice="auto", ...)` at `codeact.py:654`. The comment at `:652-653` is the load-bearing fact: *"generate() rebuilds the conversation from event_manager each call, so events added in prior iterations change what the LLM sees."* Then tool calls are processed (`:723 _process_tool_calls`); inline/explicit `return_result` ends the loop (`:732-736`).

**Two injection points for memory retrieval:**

- **Static/once-at-start:** add a prefill step (extend the loop's pre-phase like `_run_prefill`) or, cleaner, set a static context block `self.context["relevant_memory"] = ...` computed in the orchestrator before the generation method runs. Good for "load memories for this task."
- **Per-turn dynamic (recommended for "live" recall):** use a **DynamicContext block**, which is re-evaluated at the start of every LLM turn — exactly aligned with the `generate()`-rebuilds-each-turn behavior at `codeact.py:654`. In the agent's `__init__`:
  ```python
  self.context.set_dynamic("working_memory", "self.format_recent_memories()")
  ```
  `DynamicContext` (`context_blocks/models.py:25-46`) is documented as "re-evaluated each turn"; formatter notes confirm evaluation "at the start of each LLM generation call" (`context_blocks/exceptions.py:66`, `formatter.py:341-376`). The strategy already injects its own blocks this way: `get_block_overrides` (`codeact.py:351-355`) sets `strategy_prompt` to `DynamicContext("strategy.strategy_instructions(runtime)")`.

So an **automatic** (non-conscious) retrieval layer that surfaces "relevant memories given the current conversation state" belongs in a DynamicContext block re-evaluated before each `generate()`, while the **conscious** `recall/search/remember/associate` tools are agent methods invoked through `self` inside `execute_python`. The two are complementary: dynamic context = passive priming each turn; agent methods = deliberate, model-driven memory operations.

#### Concrete recommendation

1. **Conscious tools = public agent methods**, not new `Tool` objects. Put `recall/search/remember/associate` on a `MemoryMixin`/base class the agent subclasses (or as deterministic methods directly). They surface automatically through `self` in `exec_globals` (`generated_code.py:74`) and through `doc(self)`. The LLM calls `await self.recall(...)` inside `execute_python`. This requires zero changes to `CodeActStrategy` — it leverages the existing `self`-in-namespace mechanism and respects the framework's "methods are capabilities" model.
2. **Define the `Memory` return type at module level** (Pydantic model) so it enters `exec_globals` via `_extract_module_context` (`codeact.py:2245-2271`) and is inspectable via `doc(Memory)`; it then also drives `return_result` schema generation if a method returns it.
3. **Advertise the tools** by including `{doc(self)}` in the orchestrating generation method's docstring; the `execution_context` block (`codeact.py:370-457`) will additionally list any module-level memory helpers/types automatically.
4. **Automatic retrieval-injection** (if desired) goes in a `self.context.set_dynamic("working_memory", "self.format_relevant_memories()")` block (re-evaluated each turn, aligned with `runtime.generate()` at `codeact.py:654`), or as a one-shot prefill/static `self.context[...]` at task start. Keep the underlying store hidden only if needed; the recall/search/remember/associate surface stays visible.
5. **No core-strategy edits required** for the conscious path — this is purely additive at the agent layer, consistent with an "add-on" memory system.

Relevant files: `/root/projects/nooa/src/nooa/strategies/codeact.py`, `/root/projects/nooa/src/nooa/strategies/generated_code.py`, `/root/projects/nooa/src/nooa/strategies/base.py`, `/root/projects/nooa/src/nooa/strategies/predict.py`, `/root/projects/nooa/src/nooa/strategies/pure_python.py`, `/root/projects/nooa/src/nooa/context_blocks/models.py`.

### A5 — Storage Layer Suitability (C5-storage)

# Storage Layer Analysis: Suitability as Memory Metadata/Graph Store

#### 1. The storage abstraction (two protocols, layered)

There are **two** distinct protocols, not one. The naming is a little misleading: `StorageManager` is a *session-snapshot* manager, and it *owns* an `EventBackend` which is the actual append-only record store.

##### `StorageManager` protocol — `src/nooa/storage/manager.py:24-102`
```python
@runtime_checkable
class StorageManager(Protocol):
    @property
    def event_backend(self) -> "EventBackend": ...          # manager.py:52
    def save_snapshot(self, agent: "Agent") -> str: ...      # manager.py:63
    def restore_snapshot(self, snapshot_id: str, agent: "Agent") -> None: ...  # manager.py:84
```
This is a thin facade: hand-it-an-agent / get-a-snapshot-id-back. It is **agent-centric** — every method takes an `Agent`. It has no concept of arbitrary records, keys, queries, or collections beyond "one blob per snapshot_id."

##### `EventBackend` protocol — `src/nooa/runtime/event_backend.py:35-212`
This is the real record store and is much richer:
```python
@runtime_checkable
class EventBackend(Protocol):
    def store(self, tag: str, event: EventBase) -> None: ...        # :53
    def get(self, tag: str) -> EventBase | None: ...                # :65
    def get_by_id(self, event_id: str) -> EventBase | None: ...     # :78
    def update(self, tag: str, **fields: Any) -> bool: ...          # :91
    def remove(self, tag: str) -> bool: ...                         # :103
    def set_status(self, tag: str, status: EventStatus) -> bool: ...# :114
    def active_tags(self) -> list[str]: ...                         # :128
    def insert_active_tag(self, tag: str, index: int) -> None: ...  # :138
    def remove_active_tag(self, tag: str) -> bool: ...              # :149
    def all_events(self) -> Iterator[EventBase]: ...                # :162
    def find_tag(self, event: EventBase) -> str | None: ...         # :172
    def register_event_type(self, cls: type[EventBase]) -> None: ...# :183
    def clear(self) -> None: ...                                    # :196
    def allocate_next_tag(self) -> str: ...                         # :200
    def __len__(self) -> int: ...                                   # :210
```
Key constraint: it stores **`EventBase` instances only** — every record must be an `EventBase` (Pydantic) subclass with `id`, `event_type`, `status`, `metadata` fields. Records are keyed by a monotonic string `tag` and an `event_id`. There is **no content-query API** — `all_events()` is a full scan; there is no `query(filter)`, no secondary indexing beyond `event_id`, and crucially **no relationship/edge concept**.

#### 2. Backends and their API surface

| Backend | File:line | Snapshots? | Events? | Persistent? |
|---|---|---|---|---|
| `InMemoryStorageManager` | `in_memory.py:13` | No (`save_snapshot`/`restore_snapshot` raise `StorageNotConfiguredError`, `in_memory.py:31-45`) | Yes, via `InMemoryBackend` | No |
| `SQLiteStorageManager` | `sqlite.py:640` | Yes (`snapshots` table) | Yes, via `SQLiteEventBackend` | Yes |
| `InMemoryBackend` (EventBackend) | `event_backend.py:215` | n/a | dict + list, live objects | No |
| `SQLiteEventBackend` (EventBackend) | `sqlite.py:209` | n/a | SQLite `events` + `active_tags` tables | Yes |

`SQLiteStorageManager` adds non-protocol convenience methods beyond the interface: `get_latest_snapshot_id()`, `restore_latest_snapshot()`, plus `close()`/context-manager support and a process-level **session lock** (`_acquire_session_lock`) — only one process may open a given DB file.

##### SQLite schema — `sqlite.py:100-128`
```sql
events(tag PK, event_id, event_type, status, data TEXT, insertion_order)   -- data = JSON blob, source of truth
active_tags(position, tag UNIQUE)
snapshots(snapshot_id PK, created_at, data TEXT)                            -- data = JSON
schema_version(version)                                                     -- _SCHEMA_VERSION = 1
```
`event_type` and `status` are denormalized columns for indexed filtering; the JSON `data` blob is authoritative. The only secondary index is `idx_events_event_id`. There is **no edges/relations table, no JSON1/FTS indexing, no arbitrary metadata-key index.**

#### 3. Serialization patterns

Three layers, all JSON-text-into-a-`TEXT`-column:

- **Events:** `EventBase.model_dump_json()` → stored in `events.data`; restored by a per-instance type registry keyed on `event_type` (`sqlite.py:276-298`, `_CORE_TYPES` at `sqlite.py:62`, `register_event_type` at `:231`). Unknown types fall back to `Metadata` (`sqlite.py:286`).
- **Snapshots:** `AgentSnapshot` (Pydantic, `snapshot.py:37`) → `model_dump()` → `json.dumps` into `snapshots.data` (`sqlite.py:732-743`).
- **Arbitrary values inside snapshots:** the general-purpose `serialize()`/`deserialize()` in `serialization.py:69-100`. This is the reusable gem — an 8-step isinstance dispatch (`serialization.py:108-187`) handling primitives, enums, dict/list/tuple, Pydantic, dataclasses, and `@snapshotable` classes via a typed **envelope** `{"__type__","__class__","data"}`. Security: deserialize requires the FQN be in an **allowlist** collected during serialize (`serialization.py:223`), preventing arbitrary class instantiation on restore.

Markers (`markers.py`): `nosnapshot` (`Annotated[T, nosnapshot]` or `__nosnapshot__=True`) excludes a field/value from snapshots; `@snapshotable` opts a plain class into `vars()`-based serialization. These are **snapshot-only** controls — `EventBackend` does not consult them.

#### 4. Recommendation: **New dedicated store, but reuse the serialization layer and mirror the protocol style**

Do **not** reuse `EventBackend` or `StorageManager` as the metadata/graph layer for memories. Reasons:

1. **Wrong record shape.** `EventBackend` stores only `EventBase` subclasses keyed by a monotonic conversation `tag`. Memories are not conversation events; forcing them through `events`/`active_tags` would pollute the agent's live context window (everything in `active_tags` is rendered into the prompt) and couple memory lifetime to session tags.
2. **No query surface.** A memory metadata store needs filtering by arbitrary fields (user_id, namespace, tags, recency, importance) and **graph edges** (memory→memory relations, entity links). `EventBackend` offers only `get(tag)`, `get_by_id`, and full-scan `all_events()`. There is no edges table and no metadata index. `StorageManager` is even thinner (one blob per snapshot_id).
3. **Single-DB session lock.** `SQLiteStorageManager` takes an exclusive process flock on its file (`sqlite.py:600-637`). A long-lived shared memory store that outlives/spans sessions must not share that lock semantics — you would deadlock against the agent's own session DB or be limited to one process.
4. **Schema is frozen at v1 with a hard mismatch error.** `_ensure_schema` raises on any version delta (`sqlite.py:202-206`) and there is no migration path. Adding `memories`/`edges` tables to that same DB would bump the version and break existing session DBs.

##### What to reuse / extend instead

- **Reuse `serialization.py` verbatim** for memory payloads: `serialize()`/`deserialize()` with the allowlist (`serialization.py:69`, `:85`) plus the `@snapshotable`/`nosnapshot` markers give you safe, typed JSON round-tripping for whatever Pydantic/dataclass memory records you define. This is the highest-value reuse and is fully decoupled from the agent.
- **Mirror the protocol pattern, define a new `MemoryStore` Protocol** (same `@runtime_checkable Protocol` style as `manager.py`/`event_backend.py`) with the operations Chroma cannot serve: `put(memory)`, `get(id)`, `query(filter, limit)`, `link(src_id, dst_id, relation)`, `neighbors(id, relation=...)`, `delete(id)`. Back it with its **own** SQLite DB (or the same file but **separate tables `memories` + `memory_edges`** with their own `schema_version` row) so Chroma owns vectors and this layer owns metadata + graph. Keep the Chroma `id` == memory `id` as the join key.
- **Copy the SQLite hardening, not the manager.** `SQLiteEventBackend` already solves virtiofs/WAL detection (`sqlite.py:131-174`, `:685-704`), corruption detection (`_is_corruption_error`, `:177`), and disk-I/O reconnect/retry (`:339-349`). Lift those helpers into the new store rather than inheriting the event/snapshot semantics.

**Net:** new dedicated `MemoryStore` (own protocol + own SQLite tables for metadata and an explicit edges table), reusing `serialization.py` for payloads and copying the SQLite robustness helpers. Chroma handles vectors; the new store handles metadata + graph. Do not overload `EventBackend`/`StorageManager`.

##### Key file references
- Protocols: `src/nooa/storage/manager.py:24`, `src/nooa/runtime/event_backend.py:35`
- Backends: `src/nooa/storage/in_memory.py:13`, `src/nooa/storage/sqlite.py:209` (event backend), `:640` (manager); `event_backend.py:215` (in-mem backend)
- Schema: `src/nooa/storage/sqlite.py:98-128`
- Reusable serialization: `src/nooa/storage/serialization.py:69-100` (API), `:108-260` (dispatch), `:223` (allowlist security)
- Markers: `src/nooa/storage/markers.py:36` (`nosnapshot`), `:119` (`@snapshotable`)
- Snapshot IR: `src/nooa/storage/snapshot.py:37`
- SQLite robustness to lift: `sqlite.py:131` (`_is_virtiofs`), `:177` (`_is_corruption_error`), `:339` (`_retry_on_io_error`)

### A6 — Config Patterns + NVIDIA Inference/Embeddings (C6-config-nvidia-embeddings)

# Memory System Integration Analysis: Config Patterns + NVIDIA Inference/Embeddings

#### 1. How the framework configures LLMs / endpoints

The framework has a clean, layered config story that you should mirror exactly for an embeddings client + Chroma + memory hyperparameters.

##### 1a. Endpoint/model configuration = YAML registry over litellm

- `src/nooa/unifiedllm/registry.py:237` — `get_llm_client(name, *, client_type=None, **overrides)` is the single entry point. It is a thin layer over litellm: if `name` is a registry alias it applies that config; otherwise it passes the string straight to litellm. Per-alias fields are merged in at `registry.py:298-321` (`model_name`, `api_base`, `api_key`, `temperature`, `top_p`, `max_tokens`, `reasoning`, `drop_params`).
- The registry YAML schema is documented at `registry.py:29-42`. Key fields per model: `model_name` (litellm routing string), `api_base`, `api_key_env`, `context_window`, plus generation params.
- **API keys are resolved from env vars, never inlined**: `resolve_api_key_from_config()` at `registry.py:70-109` reads the `api_key_env`-named variable and warns if it is unset. This is the pattern to reuse for an embeddings API key.
- `MODELS` is a merged, last-wins dict populated lazily via `ensure_loaded()` / `reload_registry()` (`registry.py:152-234`).

##### 1b. Config-file discovery chain (layered, last-wins)

`src/nooa/llm_config.py:121` — `llm_config_chain()` returns YAML paths, lowest priority first:
1. Bundled defaults from the `nooa.bundled_configs` entry-point group (`llm_config.py:48-88`)
2. `get_user_dir("llm_config.yaml")` → `~/.config/nat/oo/llm_config.yaml`
3. `get_project_dir("llm_config.yaml")` → `<root>/.nooa/llm_config.yaml`
4. `NEMO_OO_LLM_CONFIG` env var (comma-separated, highest priority)

Filesystem roots come from `src/nooa/paths.py`: `get_user_dir()` (`paths.py:44`, honors `NAT_CONFIG_DIR`/`NEMO_OO_USER_DIR`) and `get_project_dir()` (`paths.py:73`, honors `NEMO_OO_PROJECT_DIR`). **A persistent Chroma DB directory should live under `get_user_dir("memory")` or `get_project_dir("memory")`** to match convention.

##### 1c. NVIDIA gateway aliases (the bundled package)

`packages/nemo-oo-agents-nvidia/` is a standalone wheel that contributes only a YAML of aliases — no code dependency on the core (`pyproject.toml:11-15`). It registers itself via an entry point (`pyproject.toml:17-22`):
```
[project.entry-points."nooa.bundled_configs"]
nvidia = "nooa_nvidia:get_default_config_path"
```
`__init__.py:26-54` just materializes the bundled YAML path. The YAML (`data/llm_config_default.yaml`) shows the gateway pattern: **every model routes through one OpenAI-compatible endpoint** `https://inference-api.nvidia.com/v1` with `api_key_env: NVIDIA_INTERNAL_API_KEY` (public NIM uses `https://integrate.api.nvidia.com/v1` + `NVIDIA_API_KEY`, see `data/llm_config_default.yaml:91-95`). The litellm routing prefix is `openai/...` (OpenAI-compatible), e.g. `openai/nvidia/meta/llama-3.3-70b-instruct`.

##### 1d. Hyperparameter config objects = frozen Pydantic + `merge_with`

Every config in `src/nooa/config/` follows an identical idiom:
- `pydantic.BaseModel` with `model_config = ConfigDict(frozen=True)`
- Typed fields with defaults
- A `merge_with(other)` method that overlays only `other.model_fields_set` (so partial overrides compose). It raises if `model_fields_set` is empty.

Examples: `ExecutionConfig` (`execution_config.py:6`), `HttpConfig` (`http_config.py:6`), `TokenBudgetConfig`/`MethodSummarizerConfig` (`summarizer_config.py:8,30`), `CodeActConfig`/`PredictConfig` (`strategy_config.py:26,96`). **Your `MemoryConfig` (with `enabled`, embedding model alias, Chroma path/collection, `top_k`, similarity threshold, etc.) should copy this exact shape** and re-export from `config/__init__.py` (`config/__init__.py:1-28`).

#### 2. Existing embedding client / NIM integration — NONE to reuse directly, but litellm is the bridge

- **There is no embeddings client, no Chroma, no vector store, and no `litellm.embedding` call anywhere** in `src/` or `packages/`. Grep for `embed|chroma|vector|aembedding` returns only unrelated hits (the EMBEDDING/RETRIEVER trace span-kind enum at `src/nooa/tracing/_hooks_impl.py:92-93` from OpenInference, and code comments about "embedding heredocs").
- The closest "memory" precedent is the **locomo benchmark agent** at `packages/nemo-oo-agents-benchmarks/src/nooa_benchmarks/agents/locomo.py` — but it uses **keyword-overlap retrieval** (`_retrieve_relevant_session_indices`, `locomo.py:304`), not embeddings. Not a vector-search base to build on.
- **What you CAN reuse**: `litellm` is a first-class dependency (`pyproject.toml:21`, `litellm>=1.83.0`, locked at 1.83.10) and is the entire transport layer of `unifiedllm.py`. litellm exposes `litellm.embedding(...)` / `litellm.aembedding(...)` which speak the same OpenAI-compatible protocol as the NVIDIA gateway already configured. So an embeddings client can:
  - reuse the **same `api_base` + `api_key_env` resolution** as the LLM registry, and
  - reuse the global httpx hardening already applied process-wide (`_apply_httpx_no_pool_patch`, `unifiedllm.py:95-169`) and the `HttpConfig` defaults.

NVIDIA NIM embedding models (e.g. `nvidia/nv-embedqa-e5-v5`, `nvidia/llama-3.2-nv-embedqa-1b-v2`) are served on the same `/v1` OpenAI-compatible endpoint, so a litellm `model="openai/nvidia/<embed-model>"` + `api_base=https://inference-api.nvidia.com/v1` call works with the existing key. **Recommendation: build a small `EmbeddingClient` wrapper that resolves its config from the registry/YAML (do not invent a parallel key system).**

#### 3. Idiomatic way to add a configurable, toggleable subsystem

The framework's established pattern for an optional, attachable subsystem is a **manager/agent class with a `classmethod install(cls, agent, *, config=...)`** that wires itself onto an existing agent and stores itself to keep it alive. Two canonical exemplars:

- **`SummarizationAgent.install()`** (`src/nooa/agents/summarization.py:84-112`): `install()` constructs the subsystem, subscribes to the parent's event manager, and appends to `agent._summarizers` so its lifetime is tied to the agent. Subclasses (`TokenBudgetSummarizer.install`, `summarization.py:504`; `MethodSummarizer.install`, `summarization.py:592`) take a typed `config=` object and validate kwargs. It inherits the LLM from the parent (`summarization.py:126`, `kwargs.setdefault("llm", agent._llm)`).
- **`LibraryManager.install()`** (`src/nooa/library_manager.py:45-50`): simpler — `install(cls, agent, *, libs_dir)` returns a manager that attaches things as `agent.<name>`.

**Toggling**: the framework's convention for opt-in/opt-out is *don't install it* (the NVIDIA package README, `packages/nemo-oo-agents-nvidia/README.md:18`, even states "External users who don't want the aliases simply don't install this package — there's no env-var toggle"). For a memory subsystem, follow both layers:
- An `enabled: bool` field on `MemoryConfig` for in-process toggling, AND
- Gate the whole thing behind `MemoryManager.install(agent, config=MemoryConfig(...))` so an agent that never calls install has zero memory overhead.

Visibility: the subsystem's internal fields should be `Annotated[T, hidden]` (as `SummarizationAgent` does with `target_event_manager`, `_target_agent`, `config` at `summarization.py:66-75`) so they don't leak into the LLM's `doc(self)`.

#### Concrete recommendations for the memory add-on

1. **Config**: add `MemoryConfig(BaseModel, frozen=True)` with `merge_with()` in `src/nooa/config/` (mirror `summarizer_config.py`), fields like: `enabled: bool = False`, `embedding_model: str = "nv-embedqa-e5-v5"` (a registry alias), `db_path: Path = get_user_dir("memory")`, `collection: str`, `top_k: int`, `min_similarity: float`, `max_memories: int`. Re-export from `config/__init__.py`.
2. **Embeddings client**: write an `EmbeddingClient` that resolves `api_base`/`api_key` exactly like `get_llm_client` (reuse `resolve_api_key_from_config`, `registry.py:70`, and `MODELS` lookup) and calls `litellm.aembedding(model=..., input=..., api_base=..., api_key=...)`. Do not add a new key env var — reuse `NVIDIA_INTERNAL_API_KEY` / `NVIDIA_API_KEY`.
3. **Bundled embed aliases**: optionally add NVIDIA embedding-model aliases to the existing bundled YAML pattern (a new package under the `nooa.bundled_configs` entry-point group, purely additive — see `llm_config.py:48-88`).
4. **Storage path**: put the Chroma persistent dir under `get_project_dir("memory")` or `get_user_dir("memory")` (`paths.py:44,73`) so it honors `NEMO_OO_PROJECT_DIR`/`NEMO_OO_USER_DIR`.
5. **Wiring**: expose the subsystem via `MemoryManager.install(agent, *, config=MemoryConfig(...))` (mirror `SummarizationAgent.install`, `summarization.py:84`), store it on the agent, inherit the agent's LLM if needed, and mark internal fields `Annotated[T, hidden]`.
6. **Dependency**: `chromadb` is NOT in `pyproject.toml`/`uv.lock` — add it with `uv add chromadb`. `litellm` is already present so embeddings need no new transport dep.

Key files: `src/nooa/unifiedllm/registry.py`, `src/nooa/llm_config.py`, `src/nooa/paths.py`, `src/nooa/config/summarizer_config.py` (config template), `src/nooa/agents/summarization.py` (install/toggle template), `packages/nemo-oo-agents-nvidia/src/nooa_nvidia/data/llm_config_default.yaml` (gateway YAML template), `src/nooa/library_manager.py` (simpler install template).

### A7 — Prior Art & Dependency Availability (C7-prior-art-deps)

# Memory-System Add-On: Existing Related Work & Dependency Availability

#### TL;DR
- **No vector/embedding deps are present** in the main project (`pyproject.toml`, `uv.lock`, or `.venv`). chromadb, sentence-transformers, faiss, langchain — all absent. Only `numpy 2.4.4` exists.
- The only embedding/vector-store code in the tree (`beam` similarity/embedding) lives inside the **`progressive-learning` git submodule**, which uses a **separate, uninstalled environment** — it is reference material, not a usable dependency.
- The framework already ships **two natively-reusable building blocks**: an event-summarization agent and a persistent skill-library tool. The right "memory" foundation is **events + context blocks + summarizer + SQLite storage**, not a bolt-on vector DB.

#### 1. Dependency availability (the hard answer)

| Dependency | In `pyproject.toml` | In `uv.lock` | Importable in `.venv` |
|---|---|---|---|
| chromadb | No | No (0 hits) | No (`ModuleNotFoundError`) |
| sentence-transformers | No | No (0 hits) | No |
| faiss | No | No | No |
| langchain / langchain_core | No | No | No |
| pinecone/qdrant/weaviate/llama-index | No | No | — |
| numpy | (transitive) | yes | **Yes, 2.4.4** |
| scikit-learn | No | — | No |

If the memory add-on needs embeddings/ANN, it requires **net-new dependencies** (`uv add ...`). Nothing is pre-wired.

#### 2. The `beam` similarity/embedding stack — present but NOT usable as-is

These live under the **`progressive-learning` submodule** (`.gitmodules` → `gitlab-master.nvidia.com/esarafian/progressive-learning.git`). `import beam` fails in the main venv; beam has its own `progressive-learning/pyproject.toml` and no installed `.venv`. Treat as **read-only reference / algorithm source**, not an importable library.

- `/root/projects/nooa/progressive-learning/beam/similarity/chroma.py` — `ChromaSimilarity` + `ChromaEmbeddingFunction`: thin wrapper over `chromadb.HttpClient` with `add()` / `search(k)` returning a `Similarities(index, distance, ...)`. **Requires a running Chroma server** (host/port), not embedded mode.
- `/root/projects/nooa/progressive-learning/beam/similarity/{core,dense,sparse,tfidf,sparnn}.py` — `BeamSimilarity` base, `Similarities` dataclass, plus `DenseSimilarity`, `SparseSimilarity`, `TFIDF`, `SparnnSimilarity` backends.
- `/root/projects/nooa/progressive-learning/beam/embedding/text.py` — `BeamEmbedding`, `OpenAIEmbedding`, `SentenceTransformerEmbedding` (lazy-imports `sentence_transformers`).
- `/root/projects/nooa/progressive-learning/beam/embedding/robust_encoder.py` — `RobustDenseEncoder`.

These are all coupled to the `beam` `Processor`/`Resource`/`Types` framework, so reusing one class drags in the whole beam runtime. Useful as an **API design reference** (the `Similarity.add/search` + `Similarities` shape is clean), not for direct import.

#### 3. "Memory" code already in the repo

##### Legacy ARC memory (submodule, LangChain-coupled — DROP, do not reuse)
- `/root/projects/nooa/progressive-learning/arc_agi/memory.py` — `BestLastTupleMemory` / `MemoryBit`. A **heuristic conversation-window memory** (keep best-k by score + last-k message chunks) built on LangChain `BaseMessage` + LangGraph `add_messages`. **Not embedding-based; not retrieval.** Wired into the legacy agent at `progressive-learning/arc_agi/agent.py:45,460,1288` and `containers.py:234`; configured by `MemoryConfig(k_best, k_last)` in `progressive-learning/arc_agi/config.py:82`.
- `/root/projects/nooa/progressive-learning/arc_agi/elasticsearch.py` — `ARCResultDocument` + `ARCElasticIndex`: indexes full ARC run trajectories to Elasticsearch (via beam's `BeamElastic`) with query/aggregation helpers. This is **experiment-result logging/analytics**, not agent-runtime retrieval, and is beam+ES-coupled.

**The in-repo port already classifies these as "Drop."** `/root/projects/nooa/examples/arc_agi/DESIGN.md:675-679` explicitly says:
> `elasticsearch.py`, `memory.py` (→ **nemo events/summarizer**), `checkpointer/` (→ `SQLiteStorageManager`)

i.e., the existing design guidance is that this legacy memory should be **replaced by the framework's events + summarizer**, and persistence by `SQLiteStorageManager`.

##### Trivial demo (not a system)
- `/root/projects/nooa/examples/advanced/memory.py` — 30-line demo showing event-history persistence across two method calls (`greet` then `recall`). Illustrates the native "memory = event history" model; no storage/retrieval.

##### Framework-native, directly reusable (main `src/` — IMPORTABLE, the real foundation)
- `/root/projects/nooa/src/nooa/agents/summarization.py` — `SummarizationAgent` base + `TokenBudgetSummarizer` / method summarizers. A proper sub-agent that subscribes to a parent's `EventManager` and LLM-summarizes event ranges at safe turn boundaries (`.install(agent, config=...)`). This is the canonical **memory-consolidation / reflection** primitive in the framework.
- `/root/projects/nooa/src/nooa/tools/library_writing_lib.py` — `SkillWriting` skill: scaffold / lint / hot-reload / test **persistent skill libraries** (creates `pyproject.toml` + modules, runs pytest + security validation). This is the existing **"skill library"** mechanism — the closest thing to procedural/long-term memory already in the repo.
- Supporting infra: `src/nooa/events.py`, `runtime/event_manager.py`, `runtime/event_query.py` (event storage + querying), `storage/sqlite.py` + `storage/in_memory.py` (`SQLiteStorageManager` for cross-session persistence), `context_blocks/` (static + dynamic context injection per CLAUDE.md). `tests/` only references these in passing — there is **no existing RAG/vector/embedding test**, confirming no such subsystem exists.

#### 4. Reuse vs. avoid-duplication recommendation

**Reuse (build the memory add-on on these):**
- **Events + `EventManager`/`event_query` as the episodic memory store**, and **`SummarizationAgent`/`TokenBudgetSummarizer`** (`src/nooa/agents/summarization.py`) as the consolidation/reflection layer — this is what DESIGN.md already prescribes. Do not reimplement window/best-k memory.
- **`SQLiteStorageManager`** (`src/nooa/storage/sqlite.py`) for cross-session persistence instead of any new DB.
- **`SkillWriting`** (`src/nooa/tools/library_writing_lib.py`) as the procedural "skill library" memory — extend it rather than inventing a parallel one.
- **Context blocks** (`self.context[...]` / `set_dynamic`) to surface retrieved memories into prompts.

**Avoid / do not duplicate:**
- Do **not** import `beam` (`progressive-learning/beam/...`) — it is an uninstalled submodule with a heavy framework runtime and a server-mode Chroma client. Use it only as an API-shape reference for the `add()`/`search(k)→Similarities` interface if you build a vector layer.
- Do **not** port `BestLastTupleMemory` or the Elasticsearch indexer — both are LangChain/beam-coupled and already marked "Drop."

**Net-new only if semantic retrieval is required:** there is no embedding/vector capability in the installed environment, so a true RAG/vector-memory tier means adding a dependency. Lightest path consistent with the existing stack: `numpy` is already present, so a small in-process cosine/ANN store over numpy is feasible with zero new heavy deps; otherwise `uv add chromadb` (embedded `PersistentClient`) for a managed store. Decide embeddings provider via the framework's existing LLM client rather than pulling `sentence-transformers`.

##### Key file paths
- `/root/projects/nooa/src/nooa/agents/summarization.py`
- `/root/projects/nooa/src/nooa/tools/library_writing_lib.py`
- `/root/projects/nooa/src/nooa/storage/sqlite.py`
- `/root/projects/nooa/src/nooa/runtime/event_manager.py`, `.../runtime/event_query.py`, `.../events.py`
- `/root/projects/nooa/examples/arc_agi/DESIGN.md` (lines 675-686 — the "drop/reuse/provided-by-nemo" mapping)
- `/root/projects/nooa/examples/advanced/memory.py`
- `/root/projects/nooa/progressive-learning/arc_agi/memory.py` (legacy, drop)
- `/root/projects/nooa/progressive-learning/arc_agi/elasticsearch.py` (legacy, drop)
- `/root/projects/nooa/progressive-learning/beam/similarity/chroma.py` + `.../similarity/{core,dense,sparse,tfidf}.py`, `.../embedding/text.py` (submodule reference only; not installed)

## Part B — Cognitive Science & Prior Art

### B1 — Cognitive Science of Human Long-Term Memory → Agent Memory Taxonomy (R1-memory-taxonomy)

# Cognitive Science of Human Long-Term Memory → A Schema of Agent Memory Types

#### Part 1: The Cognitive Science Foundations

##### 1.1 Working Memory vs. Long-Term Memory (Baddeley & Hitch, 1974)

**Working memory** is "a brain system that provides temporary storage and manipulation of the information necessary for such complex cognitive tasks as language comprehension, learning, and reasoning" (Baddeley). It is not a passive store but a multi-component system — a *central executive* coordinating a *phonological loop* (verbal) and a *visuo-spatial sketchpad* (visual), later joined by the *episodic buffer*. It is small, fast, and transient.

**Long-term memory (LTM)** is "a large repository of knowledge and of information on prior events, which can be stored in the mind for long periods of time." The central executive is what makes working memory and long-term memory work together — pulling relevant LTM content into the active workspace.

*This is the first axis: transient active workspace (WM) vs. durable persistent store (LTM).*

##### 1.2 Declarative vs. Non-Declarative Memory (Squire's Taxonomy)

Larry Squire's taxonomy is the standard modern map of LTM, splitting it into two families served by distinct, parallel brain systems:

- **Declarative (explicit) memory** — "Knowledge available as conscious recollection" that "can be brought to mind as remembered verbal or nonverbal material, such as an idea, sound, image, sensation, odor, or word." It is *flexible, accessible to awareness, supports rapid single-trial learning*, and is mediated by the **hippocampus and medial temporal lobe**. It subdivides into **episodic** and **semantic** memory.

- **Non-declarative (implicit) memory** — An umbrella for "multiple forms of memory that are not declarative," expressed through *performance rather than conscious recollection*. It is *dispositional, unconscious, and gradually extracts common elements across experiences*. Its subtypes are:
  1. **Procedural memory (skills & habits)** — automatized behaviors; striatum/basal ganglia and cerebellum.
  2. **Priming** — improved detection/classification of recently encountered items, felt as perceptual fluency; neocortex.
  3. **Classical conditioning** — simple associative learning; amygdala/cerebellum.
  4. **Non-associative learning** — habituation and sensitization.

##### 1.3 Episodic vs. Semantic Memory (Tulving, 1972)

Endel Tulving fractionated declarative memory into two contrastive systems:

- **Episodic memory** — "the capacity to mentally re-experience time-and-place-specific events in their original context." It stores *personal experiences* anchored to a *when and where* (autobiographical, context-bound). Retrieval is accompanied by "autonoetic" awareness of re-living the event.

- **Semantic memory** — "facts about the world": general knowledge "independent of when or where they were learned." Context-free, generalized, propositional.

Tulving's later (post-1972) refinement: the two are **interdependent** — episodic encoding and retrieval rely on semantic scaffolding, and repeated episodes consolidate into semantic knowledge.

##### 1.4 Procedural Memory ("Knowing How")

**Procedural memory** is "a type of long-term implicit memory that involves the performance of certain cognitive and motor tasks without the conscious retrieval of past information" — the "knowing how" of skills (tying a shoelace, riding a bike). It is built by *procedural learning*: "repeating a complex activity over and over again until all of the relevant neural systems work together to automatically produce the activity." Contrast with declarative "knowing that."

##### 1.5 Prospective Memory ("Remembering to Remember")

**Prospective memory** is "a form of memory that involves remembering to perform a planned action or recall a planned intention at some future point in time." Its defining feature is that it focuses on *when to act, rather than on informational content* — it is fundamentally future-oriented ("remembering to remember"). Two canonical triggers:

- **Event-based** — "remembering to perform certain actions when specific circumstances occur" (a cue in the environment).
- **Time-based** — remembering "to perform an action at a particular point in time" (the clock as cue).

(A third, *activity-based*, fires after completing some other activity.)

##### Summary map of the standard taxonomies

```
MEMORY
├── Working memory (Baddeley) — transient active workspace
└── Long-term memory
    ├── Declarative / explicit (Squire) — "knowing that", conscious
    │   ├── Episodic (Tulving) — events, when & where
    │   └── Semantic (Tulving) — facts, context-free
    ├── Non-declarative / implicit (Squire) — "knowing how", unconscious
    │   ├── Procedural — skills & habits
    │   ├── Priming
    │   ├── Classical conditioning
    │   └── Non-associative learning
    └── Prospective memory — intentions for the future ("remembering to remember")
```

#### Part 2: Proposed Enumerated Agent Memory-Type Taxonomy

The user proposed two types: **`skill`** (≈ procedural) and **`info`** (≈ semantic). Both are well-grounded. Below I keep them and add more, each justified by a human-memory basis. The taxonomy converged on by the 2025–2026 agent ecosystem (episodic / semantic / procedural, plus working memory) maps directly onto Squire + Tulving + Baddeley, which validates this layout.

| # | Agent memory type | Human-memory basis | What the agent stores | Persistence |
|---|-------------------|--------------------|------------------------|-------------|
| 1 | **`info`** (semantic) | Semantic memory (Tulving) / declarative (Squire) | Context-free facts and generalized knowledge: user preferences, domain facts, definitions, business rules, learned conventions ("the deploy command is `make ship`"). The user's proposed `info` type. | Long-term |
| 2 | **`skill`** (procedural) | Procedural memory (Squire non-declarative) | "Knowing-how": reusable, validated procedures, workflows, multi-step routines, code patterns, and behavioral rules the agent has learned to execute automatically. The user's proposed `skill` type. | Long-term |
| 3 | **`episode`** (episodic) | Episodic memory (Tulving) | Time-and-place-stamped records of specific past task runs / events: what the agent did, when, in which session, with inputs/outcomes. The autobiographical log ("on 2026-06-20 I ran the migration and it failed with X"). | Long-term |
| 4 | **`intent`** (prospective) | Prospective memory | Future intentions: reminders, TODOs, deferred actions, scheduled follow-ups. Each carries a **trigger** — *event-based* ("when the build finishes, notify Slack") or *time-based* ("at 09:00 tomorrow, re-run the report"). | Long-term, until fired |
| 5 | **`scratch`** (working) | Working memory (Baddeley & Hitch) | The transient active workspace for the current task: conversation turn, intermediate reasoning, tool outputs, retrieved chunks. Cleared/compacted between tasks; the "central executive" pulls from types 1–4 into here. | Transient |

##### Justification for going beyond `skill` + `info`

- **`episode` is the highest-value addition.** `skill` (procedural) and `info` (semantic) are both *generalized, context-free* stores — neither can answer "what happened last time?" That requires episodic memory, the *when-and-where* system Tulving isolated. It is also the **raw material for consolidation**: agents convert episodic memory into semantic memory by "identifying patterns across past interactions and distilling them into reusable knowledge" — i.e. `episode` → `info`/`skill`. Without `episode`, the agent has no source to learn from.
- **`intent` covers a category none of the others can.** Semantic, procedural, and episodic memory are all *retrospective* (about the past or about timeless facts). Prospective memory is the distinct system for *future action* — "remembering to remember." Reminders/TODOs/scheduled triggers have no home in `skill` or `info`; they are categorically prospective.
- **`scratch` makes the working-vs-long-term axis explicit.** Conflating the transient workspace with durable stores is the most common agent-memory bug (context-window bloat). Naming it as a first-class, *non-persistent* type mirrors Baddeley's separation and clarifies what should be consolidated out versus discarded.

##### Optional finer-grained types (defensible, lower priority)

- **`affordance`/`priming`** (Squire: priming) — recently-seen items that bias retrieval/ranking (e.g., recency-weighted relevance). Usually folded into retrieval ranking rather than a stored type.
- **`policy`/`habit`** — splitting `skill` into deliberate *procedures* vs. always-on *behavioral rules* (Squire distinguishes skills from habits). Worth it only if the agent has standing always-applied guardrails distinct from invocable workflows.

##### Recommended minimal enum

A clean, fully-grounded enum that covers the cognitive map without over-fragmenting:

```python
class MemoryType(Enum):
    INFO     = "info"      # semantic   — context-free facts & knowledge
    SKILL    = "skill"     # procedural — reusable how-to procedures
    EPISODE  = "episode"   # episodic   — timestamped records of past events
    INTENT   = "intent"    # prospective— future reminders/TODOs (+trigger)
    SCRATCH  = "scratch"   # working    — transient per-task workspace (non-persistent)
```

#### Citations

- Tulving, episodic–semantic distinction (historical perspective) — [An historical perspective on Endel Tulving's episodic-semantic distinction (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0028393220300373)
- Tulving distinction, contemporary review — [Rethinking the distinction between episodic and semantic memory (Memory & Cognition, Springer)](https://link.springer.com/article/10.3758/s13421-022-01299-x)
- Tulving vs. Squire memory divisions — [The memory divisions of Tulving versus Squire (INPACT)](https://inpact-psychologyconference.org/wp-content/uploads/2024/07/202401OP003.pdf)
- Squire taxonomy, definitions & neural substrates — [Conscious and Unconscious Memory Systems (Squire & Dede), PMC4355270](https://pmc.ncbi.nlm.nih.gov/articles/PMC4355270/)
- Squire, structure & function of declarative/nondeclarative memory — [Structure and function of declarative and nondeclarative memory (ResearchGate)](https://www.researchgate.net/publication/24461358_Structure_and_function_of_declarative_and_nondeclarative_memory)
- Working memory / long-term memory — [Baddeley's model of working memory (Wikipedia)](https://en.wikipedia.org/wiki/Baddeley's_model_of_working_memory)
- Working memory review — [Working Memory From the Psychological and Neurosciences Perspectives (Frontiers in Psychology)](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2018.00401/full)
- Procedural memory — [Procedural Memory In Psychology: Definition & Examples (Simply Psychology)](https://www.simplypsychology.org/procedural-memory.html)
- Procedural memory — [Procedural memory (Wikipedia)](https://en.wikipedia.org/wiki/Procedural_memory)
- Prospective memory definition & event/time-based types — [Prospective memory (Wikipedia)](https://en.wikipedia.org/wiki/Prospective_memory)
- Prospective memory overview — [Prospective Memory - an overview (ScienceDirect Topics)](https://www.sciencedirect.com/topics/neuroscience/prospective-memory)
- Agent-memory mapping to cognitive science (episodic/semantic/procedural/working) — [Types of AI Agent Memory: Episodic, Semantic, Procedural and More (Atlan)](https://atlan.com/know/types-of-ai-agent-memory/)
- Agent-memory consolidation (episodic → semantic), framework mappings — [AI Agent Memory Architectures: From Context Windows to Persistent Knowledge (Zylos Research)](https://zylos.ai/research/2026-04-05-ai-agent-memory-architectures-persistent-knowledge/)
- Agent memory survey — [Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers (arXiv)](https://arxiv.org/html/2603.07670v1)

### B2 — Neuroscience of Memory Consolidation & Sleep → Offline "Dreaming" Phase (R2-consolidation-dreaming)

# The Neuroscience of Memory Consolidation & Sleep — Grounding an Offline "Dreaming" Refinement Phase for Agents

This brief covers ten neural mechanisms of memory consolidation, then translates each into a concrete **offline operation** an agent can run on its memory store after a task finishes. It closes with an **ordered "dream" algorithm**, each step annotated with the mechanism that motivates it.

#### Part 1 — Mechanisms, Definitions, Citations, and Agent Translations

##### 1. Systems Consolidation (hippocampal → neocortical transfer)
**Definition.** The two-stage / "standard model": new memories are first encoded in a fast, temporary store (hippocampus), then gradually redistributed to a slow, distributed long-term store (neocortex) over time — or forgotten. The hippocampus is a fast learner; the neocortex is a slow learner that integrates over many episodes to avoid catastrophic interference.

**Agent translation.** Maintain a **two-tier memory store**: a hot/episodic buffer (recent raw task traces, cheap to write) and a cold/semantic store (consolidated, indexed, deduplicated knowledge). The offline phase is the transfer process: read recent episodes from the buffer, integrate them into the long-term store in small batches, and clear the buffer. Replay each consolidated item against the long-term store multiple times rather than one-shot writing, to interleave new and old knowledge and avoid overwriting prior skills.

- [System consolidation of memory during sleep — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC3278619/)
- [Memory consolidation — Wikipedia](https://en.wikipedia.org/wiki/Memory_consolidation)

##### 2. Synaptic / Cellular Consolidation (LTP, protein synthesis, synaptic tagging & capture)
**Definition.** At the cellular level, fragile short-term traces are stabilized into durable form within minutes–hours. Strong stimulation triggers late-phase LTP (L-LTP) requiring new protein synthesis. Under **synaptic tagging and capture**, synapses active during encoding are "tagged," and later "capture" plasticity-related proteins produced by sufficiently strong/important events — stabilizing only tagged traces.

**Agent translation.** When committing a memory, **stabilize it into a structured, durable record** (normalize schema, embed, write canonical fields) rather than leaving raw text. Implement a **tag-and-capture write gate**: only episodes that carry an "importance tag" (high salience/novelty/reward — see #10) get the expensive, durable write ("protein synthesis"); weakly tagged traces remain ephemeral and are allowed to decay. This makes durable storage a function of importance, not recency alone.

- [Mechanisms of Translation Control Underlying Long-lasting Synaptic Plasticity… — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6019682/)
- [The roles of protein expression in synaptic plasticity and memory consolidation — Frontiers](https://www.frontiersin.org/journals/molecular-neuroscience/articles/10.3389/fnmol.2014.00086/full)

##### 3. Hippocampal–Neocortical Dialogue (active systems consolidation)
**Definition.** During slow-wave sleep, neocortical slow oscillations exert top-down control: their depolarizing "up-states" drive repeated reactivation of hippocampal traces, time-locked to hippocampal sharp-wave ripples and thalamo-cortical spindles. This coordinated dialogue redistributes memories from hippocampus to cortex (Buzsáki's two-learner model).

**Agent translation.** Run a **scheduled, top-down orchestrated replay loop**: the orchestrator (cortical "slow oscillation") iterates over episodic items, and for each, pulls the related cluster of records and "replays" them together — re-deriving and writing the integrated lesson into the long-term store. The orchestration is deterministic Python; the per-item integration is an LLM generation step.

- [System consolidation of memory during sleep — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC3278619/)
- [The hippocampal sharp wave–ripple in memory retrieval… — Nature Reviews Neuroscience](https://www.nature.com/articles/s41583-018-0077-1)

##### 4. Sharp-Wave Ripples & Memory Replay
**Definition.** SWRs are brief (50–100 ms) bursts of synchronized hippocampal population activity (CA3→CA1) during quiet rest and NREM sleep. They carry **replay**: time-compressed sequential reactivation of neuron ensembles from prior experience. SWR density increases after learning; boosting/lengthening them improves memory, and they support both consolidation and planning (replaying past and possible future trajectories).

**Agent translation.** The core "dream" primitive: **selective, compressed re-execution of stored episode sequences**. Don't replay everything — replay selected high-value/weak/recent traces. "Time-compressed" = summarize the full trajectory into a compact action→outcome sequence before re-processing. Replaying **possible-future** trajectories maps to **counterfactual/what-if rollouts**: generate alternative action sequences for past tasks and store the better ones as candidate strategies.

- [The hippocampal sharp wave–ripple… — Nature Reviews Neuroscience](https://www.nature.com/articles/s41583-018-0077-1)
- [Long-duration hippocampal sharp wave ripples improve memory — Science](https://www.science.org/doi/10.1126/science.aax0758)
- [Large sharp-wave ripples promote hippocampo-cortical memory reactivation… — Neuron/Cell](https://www.cell.com/neuron/abstract/S0896-6273(25)00756-1)

##### 5. REM vs. NREM / Slow-Wave-Sleep Roles
**Definition.** NREM/SWS is primarily associated with **declarative/systems consolidation** (hippocampus-dependent facts and events) and is where slow-oscillation–ripple–spindle coupling happens. REM (with theta activity, mesolimbic/amygdala activation) is linked to **procedural/skill** integration, **emotional** processing, and creative recombination. The "sequential hypothesis" holds that ordered NREM→REM cycles together yield optimal consolidation.

**Agent translation.** Run the dream in **two distinct passes**:
- **NREM pass (faithful):** deduplicate, stabilize, transfer facts/outcomes verbatim, strengthen weak items. Low temperature, high fidelity.
- **REM pass (generative):** abstract skills, recombine across episodes, form new causal edges, run counterfactuals, integrate emotionally/high-salience tagged items. Higher temperature, broader context window across many memories.
Order matters: NREM first (clean + stabilize), then REM (abstract + recombine) — mirroring the sequential hypothesis.

- [Both slow wave and rapid eye movement sleep contribute to emotional memory consolidation — Communications Biology](https://www.nature.com/articles/s42003-025-07868-5)
- [The Role of Slow Wave Sleep in Memory Processing — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC2824214/)
- [Differential Effects of Non-REM and REM Sleep on Memory Consolidation? — Springer](https://link.springer.com/article/10.1007/s11910-013-0430-8)

##### 6. Schema Integration / Assimilation
**Definition.** Pre-existing knowledge structures ("schemas") accelerate consolidation. The **SLIMM** model: when new information *resonates* with an existing schema, the mPFC activates the schema and inhibits the MTL, expediting direct neocortical integration; schema-*inconsistent* novel information instead recruits the hippocampus for separate encoding. Schemas are abstractions of commonalities across many experiences (Tse et al. showed prior knowledge speeds systems consolidation).

**Agent translation.** Maintain explicit **schemas** (typed knowledge structures / domain templates / a graph of concepts). During the dream: for each new episode, test **schema-congruence**. If congruent, **assimilate fast** — update the existing schema/skill in place. If incongruent/novel, **create a new node** flagged for slower, more careful integration and extra replay. This is a routing decision (update vs. create) that controls write cost and prevents schema corruption.

- [The Assimilation of Novel Information into Schemata and Its Efficient Consolidation — J. Neuroscience](https://www.jneurosci.org/content/42/30/5916)
- [To update or to create? The influence of novelty and prior knowledge on memory networks — Phil. Trans. R. Soc. B](https://royalsocietypublishing.org/doi/10.1098/rstb.2023.0238)
- [Neurobiology of Schemas and Schema-Mediated Memory — Trends in Cognitive Sciences](https://www.cell.com/trends/cognitive-sciences/supplemental/S1364-6613(17)30086-4)

##### 7. Memory Reconsolidation (reactivation → labile → restabilize)
**Definition.** Retrieving/reactivating a consolidated memory returns it to a **labile** state; it must be **reconsolidated** to persist. This window allows the memory to be **updated** with new information before restabilizing (Nader et al. 2000; Nader & Hardt 2009). It is the brain's mechanism for keeping memories relevant as circumstances change — and for correcting them.

**Agent translation.** When the dream phase touches an existing memory whose content is contradicted or extended by new evidence, **open it for editing rather than appending a duplicate**: load the record (reactivate → labile), merge the correction / add the new fact / fix the stale belief, then re-write with an updated timestamp and provenance (reconsolidate). This is how the store self-corrects instead of accumulating contradictory copies. Guard it: only update when new evidence is sufficiently strong, to avoid corrupting a good memory.

- [Memory Reconsolidation Mediates the Updating of Hippocampal Memory Content — Frontiers](https://www.frontiersin.org/journals/behavioral-neuroscience/articles/10.3389/fnbeh.2010.00168/full)
- [An update on memory reconsolidation updating — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC5605913/)

##### 8. Forgetting & Pruning — Synaptic Homeostasis Hypothesis (SHY)
**Definition.** Tononi & Cirelli: wakeful learning produces a net **increase** in synaptic strength (potentiation), which is unsustainable (energy, saturation, noise). Sleep (esp. SWS) performs global **synaptic downscaling/renormalization**: synapses are proportionally weakened, **weak connections are eliminated**, while the strongest survive — preserving relative strength and improving signal-to-noise. "Sleep is the price the brain pays for plasticity."

**Agent translation.** **Decay + prune.** Apply a global down-scaling to memory importance/recency scores each dream cycle, then **delete memories that fall below threshold** (and trivially-weak edges). The strongest/most-reinforced memories survive renormalization; rarely-retrieved, low-value, redundant traces are garbage-collected. This bounds store size, raises retrieval signal-to-noise, and prevents proactive interference from stale clutter.

- [Sleep and synaptic down-selection — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6612535/)
- [Sleep function and synaptic homeostasis (Tononi & Cirelli 2006) — PDF](https://www.psychiatry.wisc.edu/courses/Nitschke/seminar/tononi%20&%20cirelli%202006%20sleep%20medicine%20reviews.pdf)
- [Learning to Forget: Sleep-Inspired Memory Consolidation for Resolving Proactive Interference in LLMs — arXiv](https://arxiv.org/pdf/2603.14517)

##### 9. Abstraction / Generalization During Sleep (gist, rules, insight)
**Definition.** Sleep (esp. NREM) facilitates extraction of **gist** and hidden regularities: inferential gist (transitive inference across non-adjacent events), statistical learning, summary gist (themes), and category gist (higher-level abstraction). Replay of overlapping memories supports rule discovery, generalization to new circumstances, and insight into hidden structure.

**Agent translation.** **Episodes → skills.** Cluster related episodes and induce a **generalized, parameterized procedure** ("skill"/"playbook") plus the conditions under which it applies — discarding episode-specific detail. Mine **cross-episode regularities** ("API X always rate-limits → back off"; "task type T → use approach A") and store them as reusable heuristics. This is where raw experience becomes transferable competence.

- [Sleep and the Extraction of Hidden Regularities: A Systematic Review… — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6779511/)
- [Sleep-dependent memory triage: Evolving generalization through selective processing — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC5826623/)

##### 10. Emotional / Salience Tagging of Memories (prioritized & weak-memory replay)
**Definition.** The amygdala tags emotionally/motivationally salient events, biasing the hippocampus to preferentially encode and later replay them. The sleeping brain **preferentially consolidates salient memories** (emotional, novel, rewarding, future-relevant); rewarded experiences are preferentially reactivated during sleep. Notably, replay also **prioritizes weakly-learned items** (those most at risk of forgetting), while items of intermediate strength benefit most — a triage that maximizes net retained value.

**Agent translation.** Attach a **salience/importance score** to each memory at write time and **re-score** it during the dream, driving replay priority. Signals: outcome (success/failure/reward), surprise/novelty (prediction error), error/"pain" (failures are high-salience and should be over-replayed to extract lessons), future-relevance (likely to recur), and uncertainty/weak-encoding (replay shaky-but-valuable items to shore them up). Replay budget = function of salience; skip already-strong, low-value items.

- [The amygdala mediates the facilitating influence of emotions on memory… — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC10034520/)
- [Reward biases spontaneous neural reactivation during sleep — PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8260738/)
- [Human hippocampal replay during rest prioritizes weakly learned information… — Nature Communications](https://www.nature.com/articles/s41467-018-06213-1)
- [Sleep and dreaming are for important matters — PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3722492/)

#### Part 2 — The Proposed "Dream" Algorithm (ordered offline operations)

Run after each task (or batched nightly). Mirrors the biological sequence: **gate write → ripple-replay → NREM clean/stabilize → REM abstract/recombine → renormalize/prune**. Steps grouped by phase; each annotated with its motivating mechanism.

##### Phase 0 — Encode the just-finished task (the "tag")
1. **Write episodic trace + salience tag.** Persist the raw task trajectory (goal, actions, observations, outcome) to the episodic buffer, and attach an initial salience score from outcome, surprise/prediction-error, failure/"pain", and novelty. — *Emotional/salience tagging (#10); synaptic tagging & capture (#2).*
2. **Write gate (cellular consolidation).** Only traces above a tag threshold are queued for durable processing this cycle; weakly-tagged traces stay ephemeral and are eligible to decay. — *Synaptic/cellular consolidation, tag-and-capture (#2).*

##### Phase 1 — Selection & Replay (sharp-wave ripples)
3. **Build the replay set by priority.** Select items for this dream cycle: high-salience + **weakly-encoded-but-valuable** + recent buffer items. Skip already-strong, low-value memories. — *Prioritized & weak-memory replay (#10); SWR replay (#4).*
4. **Compress each selected trajectory.** Summarize each episode into a compact action→outcome sequence (time-compressed replay) for cheap downstream processing. — *Sharp-wave ripples & replay, time compression (#4).*

##### Phase 2 — NREM pass (faithful: dedup, stabilize, transfer, strengthen)
5. **Dedup & merge.** Cluster near-identical / overlapping memories; merge into one canonical record, accumulating reinforcement count. — *Synaptic homeostasis: eliminate redundancy (#8); replay of overlapping traces (#4).*
6. **Reconsolidation update.** For any existing long-term memory contradicted or extended by new evidence, reactivate → merge/correct → rewrite with updated provenance (only if new evidence is strong enough). — *Memory reconsolidation (#7).*
7. **Schema routing (assimilate vs. create).** For each consolidated item, test schema-congruence: if congruent, update the existing schema/skill in place (fast); if novel/incongruent, create a new flagged node for slower integration + extra replay. — *Schema integration / SLIMM, "update vs. create" (#6).*
8. **Hippocampal→neocortical transfer.** Move stabilized, structured records from the episodic buffer into the long-term semantic store (normalized, embedded, indexed); interleave with existing knowledge across several replay rounds to avoid catastrophic overwrite. — *Systems consolidation (#1); hippocampal–neocortical dialogue (#3); cellular consolidation/stabilization (#2).*

##### Phase 3 — REM pass (generative: abstract, recombine, reason)
9. **Abstraction: episodes → skills.** Cluster related episodes and induce a generalized, parameterized skill/playbook + its applicability conditions, dropping episode-specific detail. — *Abstraction/generalization in sleep, gist & rule extraction (#9).*
10. **Regularity & causal-edge mining.** Across episodes, extract recurring cause→effect patterns; add new edges to a causal/knowledge graph ("action A under condition C → outcome O"). — *Inferential gist / generalization (#9); replay of past *and possible-future* sequences (#4).*
11. **Counterfactual rollouts.** For salient failures, generate alternative action sequences ("what should I have done?"); store improved strategies as candidate skills. — *Future-trajectory replay in SWRs (#4); REM recombination/creativity (#5).*
12. **Emotional/salience integration.** Give high-salience items (especially failures) extra abstraction passes and link them to the relevant skills/schemas so the lesson is retrievable in context. — *Emotional tagging & salience (#10); REM emotional processing (#5).*

##### Phase 4 — Renormalization (synaptic homeostasis)
13. **Re-score importance globally.** Recompute each memory's importance from access frequency, recency, reinforcement count, outcome value, and downstream usefulness. — *Re-scoring / salience triage (#10).*
14. **Global down-scaling.** Apply proportional decay to all importance scores (preserving relative ordering) — the renormalization step. — *Synaptic homeostasis hypothesis / downscaling (#8).*
15. **Prune.** Delete memories and graph edges below threshold (low-value, stale, redundant, rarely-retrieved); the strongest survive. — *Synaptic homeostasis: weak-connection elimination, signal-to-noise, anti–proactive-interference (#8).*
16. **Clear episodic buffer & log the dream.** Flush processed episodic traces, record what was merged/abstracted/pruned (provenance + metrics) for auditability and the next cycle. — *Two-stage systems consolidation: empty the fast store for new learning (#1).*

##### Sequencing note
NREM-type operations (dedup, stabilize, transfer — Phase 2) run **before** REM-type operations (abstraction, recombination — Phase 3), and renormalization/pruning (Phase 4) runs **last**, matching the sequential NREM→REM hypothesis and ensuring you abstract from a clean, deduplicated store and only prune after value has been extracted. — *REM vs. NREM / sequential hypothesis (#5).*

##### Implementation note for this codebase
This maps cleanly onto the NeMo OO Agents pattern: the dream phase is a **pure-Python orchestrator method** (Phases 0–4 as a deterministic sequence, mirroring "orchestrators are pure Python") that calls **single-task generation methods** (`...`) for the LLM-judgment steps — e.g. `merge_duplicates()`, `induce_skill()`, `extract_causal_edges()`, `should_update_or_create()`, `rescore_importance()` — each one LLM task, split per the "one method = one LLM task" rule. Deterministic steps (decay, prune, buffer flush, schema-congruence thresholds) are plain helpers, visible to the LLM via `doc(self)`.

### B3 — Spreading Activation, Priming & Associative Retrieval → Scoring + k-Hop Traversal (R3-association-retrieval)

# Spreading Activation, Priming & Associative Retrieval → An Agent Retrieval-Scoring + k-Hop Traversal Design

#### 1. Cognitive-science foundations (definitions)

##### 1.1 Spreading activation (Collins & Loftus, 1975)
Concepts in human memory are stored as a **semantic network**: nodes (concepts) connected by weighted, labeled links whose length encodes semantic/associative distance. When a node is activated (by perception, thought, or retrieval), **activation spreads in parallel along its links to neighboring nodes, decreasing in strength with distance from the source and decaying over time**. The result is that recently/strongly associated concepts become temporarily more accessible. This unifies semantic priming, free-association structure, and the "train of thought" — the core mechanism we want to imitate for "spontaneous association" retrieval. Quillian originated the network model; Collins & Loftus corrected misconceptions and added graded link strengths and decay.

##### 1.2 Priming (Meyer & Schvaneveldt, 1971)
**Priming** = exposure to one stimulus changes the response to a later related stimulus, with no intent. In lexical decision, "NURSE" is classified faster after "DOCTOR" than after an unrelated word ("BUTTER"), because activation has already spread to the target's region of the network. Two dissociable flavors:
- **Associative priming** — co-occurrence/association (spider→WEB) even without semantic similarity.
- **Semantic priming** — shared features/category (dolphin→WHALE) even without normative association.

Design consequence: an agent's memory graph should carry **both** an association edge type (co-occurrence/temporal) and a similarity signal (embeddings) — they are independent retrieval routes.

##### 1.3 Cue-dependent retrieval & encoding specificity (Tulving & Thomson, 1973; Tulving, 1974)
**Encoding specificity principle:** a retrieval cue is effective to the degree that it overlaps with the information encoded at storage time. **Cue-dependent forgetting:** memories are often intact but inaccessible because the right cue is absent. Context-dependent and state-dependent memory are special cases (match the room/state → better recall).

Design consequence: store **the encoding context with each memory** (time, location/task, co-active entities, emotional/importance tag) and let the *current* query context act as the cue. Retrieval score should reward cue↔encoding-context overlap, not just topical similarity to the memory text.

##### 1.4 ACT-R declarative memory (Anderson; Anderson & Schooler, 1991; Schneider & Anderson, 2011)
ACT-R is the most rigorous quantitative model and gives us drop-in formulas. Each fact ("chunk") has a scalar **activation** that drives retrieval probability and latency.

**Total activation:**
$$A_i = B_i + \underbrace{\sum_j W_j\,S_{ji}}_{\text{spreading}} + \varepsilon$$

**Base-level activation** (recency + frequency, the power law of practice/forgetting):
$$B_i = \ln\!\left(\sum_{k=1}^{n} t_k^{-d}\right)$$
- $n$ = number of prior uses of chunk $i$; $t_k$ = time elapsed since the $k$-th use; **decay $d = 0.5$** (default).
- Each past access contributes $t_k^{-d}$; recent uses dominate (recency), more uses raise the sum (frequency). The log makes it a graded scalar.

**Spreading activation** from currently-active sources (the cues / "context buffer"):
$$\sum_j W_j\,S_{ji}, \qquad W_j = \frac{W}{n_{\text{sources}}}$$
- $W_j$ = source weight; total source activation $W$ (default $W=1$) is split evenly across the $n$ active cue sources $j$.

**Associative strength** with the **fan effect** (a cue linked to many things spreads less to each):
$$S_{ji} = S - \ln(\text{fan}_j), \qquad S \approx 1.5$$
- $\text{fan}_j$ = number of chunks associated with source $j$. More links ⇒ weaker per-link activation (interference). This is the formal basis for **down-weighting high-degree "hub" nodes** during traversal.

**Retrieval probability** (logistic over threshold $\tau$, logistic noise $s$):
$$P(\text{retrieve } i) = \frac{1}{1 + e^{-(A_i - \tau)/s}}$$
- threshold $\tau$ (default $\approx 0$), noise $s$ (default $\approx 0.25$–$0.4$); noise SD $\sigma = s\pi/\sqrt 3$.

**Retrieval latency:**
$$t_i = F\,e^{-A_i}$$
- higher activation ⇒ faster retrieval ($F$ = latency scaling factor).

##### 1.5 Generative Agents (Park et al., 2023) — the existing LLM-agent translation
Stanford's generative agents score each memory by a weighted sum, each component min-max normalized to $[0,1]$:
$$\text{score} = \alpha_{\text{rec}}\cdot\text{recency} + \alpha_{\text{imp}}\cdot\text{importance} + \alpha_{\text{rel}}\cdot\text{relevance}$$
- **recency** = exponential decay over hours since last access, **decay factor 0.995**;
- **importance** = LLM "poignancy" rating 1–10;
- **relevance** = cosine similarity of embeddings;
- all $\alpha=1$ by default.

This is essentially ACT-R **without** the spreading-activation term. Our design adds spreading activation + k-hop traversal back in.

#### 2. Proposed retrieval-scoring function for the agent

##### 2.1 Memory graph model
A **directed, typed, weighted memory graph** $G=(V,E)$.
- **Node** $m$: `{text, embedding, t_created, accesses:[timestamps], importance∈[1,10], context:{time, place/task, entities[], tags[]}}`.
- **Edge** $m\!\to\!n$ with type and weight $w_{mn}\in(0,1]$:
  - `temporal/co-occurrence` (associative priming),
  - `semantic` (kNN over embeddings; semantic priming),
  - `causal / reflection / entity` (derived links, e.g. "X caused Y", "both about person P").

##### 2.2 Per-node base score (ACT-R base-level + Generative-Agents signals)
For query $q$ at time $t_{\text{now}}$:

- **Relevance** (encoding-specificity cue match): blend text similarity with context-cue overlap
$$\text{rel}(m,q) = \lambda\,\cos(e_m, e_q) + (1-\lambda)\,\text{ctxOverlap}(m.\text{context}, q.\text{context}),\quad \lambda=0.7$$
  where `ctxOverlap` = Jaccard over {entities, tags, place/task} (cue ↔ encoding context).
- **Base-level / recency+frequency** (ACT-R, exact):
$$B(m) = \ln\!\Big(\sum_{t_k\in m.\text{accesses}} (\Delta_k)^{-d}\Big),\quad \Delta_k = \max(t_{\text{now}}-t_k,\ \epsilon),\ d=0.5$$
  then squash $\text{rec}(m)=\sigma(B(m))$ (logistic) into $[0,1]$.
- **Importance**: $\text{imp}(m)=m.\text{importance}/10$.

**Base node score** (min-max normalize each term across the candidate set first):
$$\boxed{\,S_{\text{base}}(m) = \alpha_{\text{rel}}\,\widehat{\text{rel}} + \alpha_{\text{rec}}\,\widehat{\text{rec}} + \alpha_{\text{imp}}\,\widehat{\text{imp}}\,}$$
Defaults: $\alpha_{\text{rel}}=1.0,\ \alpha_{\text{rec}}=0.5,\ \alpha_{\text{imp}}=0.5$ (relevance-led; tune per task).

##### 2.3 Adding associative spread (the new term)
Seed activation on the top-$N$ nodes by $S_{\text{base}}$ (these are the "active cues"), then add an **incoming spreading-activation** term so that a node also gets credit for being associated to strongly-activated cues — ACT-R's $\sum_j W_j S_{ji}$ with the fan penalty:

$$\text{Act}(n) = S_{\text{base}}(n) + \gamma \sum_{m \in \text{cues}} A(m)\cdot w_{mn}\cdot \underbrace{\big(S_{\max} - \ln(\text{fan}_m)\big)_{+}}_{\text{fan-penalized strength}}$$

- $A(m)$ = activation of source cue $m$ (use $S_{\text{base}}(m)$ at hop 0);
- $w_{mn}$ = edge weight; $\text{fan}_m$ = out-degree of $m$ (down-weights hubs, per fan effect);
- spread gain $\gamma=0.5$; $S_{\max}=1.5$ (ACT-R default); $(\cdot)_+$ clamps at 0.

This single term is what produces **"spontaneous association"**: a memory not directly similar to the query surfaces because it's strongly linked to something that is.

#### 3. k-hop traversal semantics (activation decay per hop)

Generalize §2.3 to multiple hops. Run **bounded BFS** from the seed cue set, decaying activation each hop — the discrete analog of Collins–Loftus spread + ACT-R fan/strength.

**Per-hop propagation** (node receives from its activated predecessors):
$$A^{(h)}(n) = A^{(h-1)}(n) + \delta^{h}\sum_{m\in \text{pred}(n)} A^{(h-1)}(m)\cdot w_{mn}\cdot \big(S_{\max}-\ln(\text{fan}_m)\big)_{+}$$

- **hop-decay $\delta = 0.6$** (activation falls geometrically with graph distance — Collins–Loftus distance effect);
- **max hops $K = 3$** (free association rarely useful beyond ~3 links);
- **per-node beam / branching cap $b = 5$** (expand only top-$b$ out-edges by $w$ — bounds the fan-out blowup);
- **activation floor $\theta = 0.05$**: stop propagating from a node once $A<\theta$ (ACT-R retrieval threshold $\tau$ analog) — prunes the frontier;
- **visited accumulation**: a node reached by multiple paths sums contributions (parallel spreading), but each *edge* is traversed once per source to avoid cycles.

**Final activation** = $A^{(K)}$. **Final retrieval set** = top-$M$ nodes by $A^{(K)}$ above $\theta$ (default $M=12$), optionally converted to a probability/ranking via the ACT-R logistic:
$$P(\text{retrieve } n) = \frac{1}{1+e^{-(A^{(K)}(n)-\tau)/s}},\quad \tau=0,\ s=0.3.$$

On retrieval, **bump accessed nodes** (append $t_{\text{now}}$ to `accesses`) so $B$ rises — implements ACT-R/Generative-Agents reinforcement and makes hot paths self-strengthening.

##### 3.1 Default parameter table

| Symbol | Meaning | Default | Source/rationale |
|---|---|---|---|
| $d$ | base-level decay | 0.5 | ACT-R canonical |
| $\alpha_{\text{rel}},\alpha_{\text{rec}},\alpha_{\text{imp}}$ | base-score weights | 1.0 / 0.5 / 0.5 | Generative Agents (=1) adapted, relevance-led |
| $\lambda$ | embedding vs context-cue mix | 0.7 | encoding specificity |
| $S_{\max}$ | max associative strength | 1.5 | ACT-R default |
| $\gamma$ | spread gain (1-hop) | 0.5 | tuning |
| $\delta$ | per-hop activation decay | 0.6 | distance effect |
| $K$ | max hops | 3 | free-association depth |
| $b$ | branching beam per node | 5 | fan-out bound |
| $\theta$ | activation floor (≈ $\tau$) | 0.05 | ACT-R retrieval threshold |
| $M$ | final memories returned | 12 | context-budget |
| $s,\tau$ | logistic noise / threshold | 0.3 / 0 | ACT-R retrieval prob |
| recency squash | $\sigma(B)$ or 0.995-decay | logistic | ACT-R / Gen-Agents |

##### 3.2 Algorithm sketch
1. Embed query + extract query context (entities, tags, time/place).
2. Candidate set = kNN(embedding) ∪ context-cue matches; compute $\widehat{\text{rel}},\widehat{\text{rec}},\widehat{\text{imp}}$ (min-max), then $S_{\text{base}}$.
3. Seed cues = top-$N$ by $S_{\text{base}}$; set $A^{(0)}=S_{\text{base}}$.
4. For $h=1..K$: propagate over top-$b$ out-edges, apply $\delta^h$ and fan penalty, accumulate, prune below $\theta$.
5. Rank by $A^{(K)}$ (or its logistic $P$); return top-$M$; bump access timestamps of returned nodes.

This gives: relevance (embeddings) + recency/frequency (ACT-R base-level) + importance (poignancy) + **associative spread with per-hop decay and hub down-weighting** (Collins–Loftus + ACT-R fan), cued by encoding-context overlap (Tulving) — i.e. principled "spontaneous association" and multi-hop recall.

#### 4. Citations (title + URL)

- Collins, A. M., & Loftus, E. F. (1975). *A Spreading-Activation Theory of Semantic Processing.* Psychological Review, 82, 407–428. — https://www.semanticscholar.org/paper/A-spreading-activation-theory-of-semantic-Collins-Loftus/61374d14a581b03af7e4fe0342a722ea94911490 (PDF: https://scispace.com/pdf/a-spreading-activation-theory-of-semantic-processing-4e28t5lvm7.pdf)
- Meyer, D. E., & Schvaneveldt, R. W. (1971). Facilitation in recognizing pairs of words (semantic/associative priming, lexical decision). Overview: https://www.researchgate.net/publication/11196569_The_effects_of_associative_and_semantic_priming_in_the_lexical_decision_task ; Semantic priming without association (meta-analysis): https://link.springer.com/article/10.3758/BF03212999
- Tulving, E., & Thomson, D. M. (1973). *Encoding specificity and retrieval processes in episodic memory.* Psychological Review, 80(5), 352–373. — https://en.wikipedia.org/wiki/Encoding_specificity_principle
- ACT-R (Anderson) declarative memory / activation equations overview — https://grokipedia.com/page/ACT-R ; Spreading activation — https://grokipedia.com/page/Spreading_activation
- Anderson & Schooler base-level learning equation $B_i=\ln(\sum t_j^{-d})$, $d=0.5$ (power law of forgetting); base-level approximations — https://www.researchgate.net/publication/229005123_Computationally_efficient_approximation_of_the_base-level_learning_equation_in_ACT-R
- Schneider, D. W., & Anderson, J. R. (2011). *Modeling Fan Effects on the Time Course of Associative Recognition* — activation $A_i=B_i+\sum_j W_j S_{ji}$, $S_{ji}=S-\ln(\text{fan}_j)$ ($S\approx1.5$), latency $t=Fe^{-A}$ — https://pmc.ncbi.nlm.nih.gov/articles/PMC3259266/
- Fan effect / associative interference — https://onlinelibrary.wiley.com/doi/full/10.1111/tops.12244
- Park, J. S., et al. (2023). *Generative Agents: Interactive Simulacra of Human Behavior* — retrieval score = recency(decay 0.995) + importance(1–10) + relevance(cosine), $\alpha=1$, min-max normalized — https://arxiv.org/pdf/2304.03442 (HTML: https://ar5iv.labs.arxiv.org/html/2304.03442)

Note: the exact numeric defaults for $S$, $d$, latency factor $F$, threshold $\tau$, and noise $s$ are ACT-R conventions confirmed across the Schneider & Anderson paper and ACT-R overview pages; for production tuning, validate against the official ACT-R tutorials at act-r.psy.cmu.edu (the unit pages return 404 on direct fetch but the equations above match the published model).

### B4 — LLM-Agent Long-Term Memory: Comparative Survey & Design Patterns (R4-llm-agent-memory-survey)

# LLM-Agent Long-Term Memory: Comparative Survey & Design Patterns

A survey of 10 long-term memory systems for LLM agents, extracting architecture, record schema, write/retrieve/consolidate mechanics, and the single best idea worth stealing from each. Ends with a prioritized "patterns to adopt" shortlist.

#### 1. MemGPT / Letta — Memory as a Virtual OS

**Architecture.** Treats the LLM context window like RAM in an OS, with explicit paging between an in-context tier and out-of-context tiers. Two-tier design:
- **Main context (in-context):** system instructions (read-only) + **core memory** (fixed-size, editable `persona` and `human` blocks) + **FIFO message queue**.
- **External context (out-of-context):** **recall storage** (full, uncompressed message-history DB) + **archival storage** (vector DB of facts/documents).

**Record shape.** Core memory = small text "blocks" (persona, human) with a fixed character budget. Archival = embedded text chunks. Recall = raw message log rows with timestamps.

**Write.** The agent self-edits memory via tool calls: `core_memory_append`, `core_memory_replace`, `archival_memory_insert`. Nothing is written implicitly — memory operations are deliberate LLM actions.

**Retrieve.** `archival_memory_search` (vector), `conversation_search` / `recall_memory_search` (keyword over history). Results are paged back into the main context.

**Consolidate.** When the context nears its limit (a "memory-pressure" warning), the FIFO queue is flushed and evicted messages are **recursively summarized**; the summary occupies the first queue slot. Older content remains fully recoverable from recall storage (lossless).

**Mechanism worth noting:** the **heartbeat** — the agent sets `request_heartbeat: true` to chain multiple memory operations before producing a user-facing reply (read → reflect → edit → respond in one turn).

**Best idea to steal:** **Self-editing memory as first-class tools.** Memory isn't a background pipeline; it's a set of functions the agent deliberately calls, making writes auditable and controllable.

- [MemGPT: Towards LLMs as Operating Systems (arXiv:2310.08560)](https://arxiv.org/abs/2310.08560)
- [Virtual context management with MemGPT and Letta — Leonie Monigatti](https://www.leoniemonigatti.com/blog/memgpt.html)

#### 2. Stanford Generative Agents — The Memory Stream

**Architecture.** A flat, append-only **memory stream** of natural-language observations, plus a retrieval function and a **reflection** synthesizer that builds higher-level abstractions.

**Record shape.** Each memory object: natural-language description, **creation timestamp**, **last-access timestamp**, and an **importance (poignancy) score**.

**Write.** Every observation is appended verbatim. At creation, an LLM rates **importance on a 1–10 scale** (1 = mundane "brushing teeth"; 10 = "a breakup").

**Retrieve.** Score every memory and take the top-k:
```
score = α_recency·recency + α_importance·importance + α_relevance·relevance     (all α = 1)
recency   = 0.995 ^ (game-hours since last access)      # exponential decay
importance = LLM poignancy rating (1–10)
relevance = cosine(embedding(memory), embedding(query))
```
All three are **min-max normalized to [0,1]** before combining.

**Consolidate (Reflection).** When the **sum of importance of recent memories exceeds ~150** (≈2–3×/day), the agent: (1) asks an LLM for the "3 most salient high-level questions" over its 100 most recent memories; (2) retrieves evidence for each; (3) generates insights **with citations to the supporting memories**. Reflections are themselves added to the stream, forming a **reflection tree** (observations at leaves, increasingly abstract thoughts as parents).

**Best idea to steal:** the **recency × importance × relevance retrieval score** — a tiny, tunable formula that captures most of what good memory ranking needs. Plus **importance scored at write time** so ranking is cheap at read time.

- [Generative Agents: Interactive Simulacra of Human Behavior (arXiv:2304.03442)](https://arxiv.org/abs/2304.03442)

#### 3. A-MEM — Agentic, Zettelkasten-Style Memory

**Architecture.** A self-organizing network of richly-annotated "memory notes" that **dynamically link** to and **rewrite** each other (Zettelkasten method), with no fixed schema of operations.

**Record shape.** Each note `mᵢ = {cᵢ, tᵢ, Kᵢ, Gᵢ, Xᵢ, eᵢ, Lᵢ}`:
- `cᵢ` content, `tᵢ` timestamp, `Kᵢ` LLM keywords, `Gᵢ` LLM tags, `Xᵢ` LLM contextual description, `eᵢ` embedding, `Lᵢ` linked notes.

**Write.** LLM generates `Kᵢ, Gᵢ, Xᵢ` from content; embedding is computed over the **concatenation of all text fields**: `eᵢ = enc(concat(cᵢ, Kᵢ, Gᵢ, Xᵢ))` (encoder: `all-MiniLM-L6-v2`).

**Link generation.** For a new note, take **top-k nearest neighbors** by cosine similarity (k≈10, up to 40–50 for stronger models), then an **LLM decides which links are actually meaningful** and writes them into `Lᵢ`.

**Consolidate (Memory Evolution).** Each linked neighbor `mⱼ` is fed back to the LLM, which may **rewrite its context/keywords/tags** in light of the new note — existing memories are continuously refined, not frozen.

**Retrieve.** Embed query, cosine-rank all notes, return top-k.

**Best idea to steal:** **Memory evolution** — new writes don't just link to old memories, they can *update* them, so the store self-improves its annotations over time. Also: embedding over **content + LLM-generated keywords/tags/context** boosts retrieval over raw-content embeddings.

- [A-MEM: Agentic Memory for LLM Agents (arXiv:2502.12110)](https://arxiv.org/abs/2502.12110) · [code](https://github.com/WujiangXu/A-mem)

#### 4. MemoryBank — Human-Like Forgetting

**Architecture.** Three-tier memory with a biologically-inspired **forgetting curve** governing retention.

**Record shape / schema.**
- **Detailed dialogues** (timestamped turns).
- **Hierarchical event summaries** (daily → global summaries).
- **User personality portraits** (daily insights → global persona).

**Write.** Store raw dialogue; periodically summarize into daily/global event summaries and personality assessments via LLM prompts.

**Retrieve.** Dense retrieval — every memory encoded by `E(·)` (MiniLM/Text2vec), indexed in **FAISS**; current context is the query.

**Consolidate (Ebbinghaus forgetting).**
```
R = e^(−t / S)
R = retention probability, t = time since last recall, S = memory strength (integer)
S initializes to 1; on recall, S += 1 and t resets to 0.
```
Memories decay probabilistically by `R`; frequently-recalled memories are reinforced (higher `S`, slower decay), unused ones fade.

**Best idea to steal:** **Spaced-repetition reinforcement** — recall *strengthens* a memory (and resets its decay clock), giving a principled, cheap way to prioritize what to keep vs. prune as the store grows.

- [MemoryBank: Enhancing LLMs with Long-Term Memory (arXiv:2305.10250)](https://arxiv.org/abs/2305.10250) · [code](https://github.com/zhongwanjun/MemoryBank-SiliconFriend)

#### 5. Reflexion — Verbal Self-Reflection (Episodic Memory of Mistakes)

**Architecture.** A trial-and-retry loop with three modules + an episodic reflection buffer. No weight updates — "reinforcement" is purely linguistic.

**Components.** **Actor** (LLM policy producing actions), **Evaluator** (computes reward from the trajectory — exact-match, heuristic, or LLM), **Self-Reflection model** (LLM turning trajectory + reward into a verbal lesson).

**Record shape.** Free-text reflections (e.g., "I failed because I searched X before checking Y; next time check Y first").

**Write.** After a failed/scored trial, the self-reflection LLM converts sparse feedback (binary/scalar/NL) into a specific verbal lesson and appends it to the buffer.

**Retrieve.** The buffer (a **sliding window of Ω≈1–3 reflections**) is prepended to the next trial's prompt.

**Consolidate.** Bounded buffer keeps only the most recent reflections (no unbounded growth).

**Best idea to steal:** **Convert outcome signals into verbal lessons stored as memory.** Reward → natural-language "what to do differently" is a lightweight, interpretable form of learning-from-failure that any agent loop can bolt on.

- [Reflexion: Language Agents with Verbal Reinforcement Learning (arXiv:2303.11366)](https://arxiv.org/abs/2303.11366)

#### 6. Voyager — Skill Library as Procedural Memory

**Architecture.** Lifelong-learning agent with an **automatic curriculum**, an iterative code-generation loop, and an **ever-growing skill library** of verified, executable code.

**Record shape.** Each skill = an **executable JavaScript function** (Mineflayer API), **keyed by an embedding of a GPT-generated natural-language description** of what the code does.

**Write.** A skill is added **only after self-verification** — a separate GPT-4 "critic" confirms (from the agent's state + task) that the program achieved the task. Verified-only writes prevent polluting the library.

**Retrieve.** Embed the current task context (+ environment feedback / errors), take **top-5** skills by cosine similarity over description embeddings, inject as in-context examples.

**Consolidate.** New skills are **added without modifying existing ones** (no catastrophic forgetting); complex skills are **composed** from simpler ones.

**Best idea to steal:** **Verified, composable procedural memory.** Store reusable *code/procedures* (not just facts), gate writes behind automatic verification, and index by NL description for retrieval. This is the cleanest "skills cache" pattern for a tool-using/coding agent.

- [Voyager: An Open-Ended Embodied Agent with LLMs (arXiv:2305.16291)](https://arxiv.org/abs/2305.16291) · [project](https://voyager.minedojo.org/)

#### 7. Mem0 — Production-Grade Extract-and-Reconcile

**Architecture.** A two-phase, latency-optimized pipeline that extracts salient facts from conversation and reconciles them against existing memory. Optional graph variant (`Mem0g`).

**Record shape.** Plain variant: text "memories" with embeddings. `Mem0g`: directed labeled graph `G=(V,E,L)` — nodes are typed entities (with embeddings + creation timestamps); edges are `(v_source, relation, v_dest)` triplets.

**Write — two phases.**
1. **Extraction:** LLM `φ` reads (conversation summary `S`) + (rolling window of last `m=10` messages) + (current message pair) and emits candidate facts `Ω`.
2. **Update:** for each candidate, retrieve **top s=10** similar existing memories; an LLM via **function-calling** picks one of **`ADD` / `UPDATE` / `DELETE` / `NOOP`**.

An **asynchronous summary module** periodically refreshes `S` so extraction stays current without blocking.

**Retrieve.** Dense similarity (plain) or **entity-centric graph traversal + semantic triplet matching** (`Mem0g`).

**Consolidate.** The ADD/UPDATE/DELETE/NOOP decision *is* consolidation — conflicts are detected and resolved at write time. Reported ~91% lower p95 latency and >90% token savings vs. full-context baselines.

**Best idea to steal:** the **ADD / UPDATE / DELETE / NOOP "memory decision engine"** — every new fact is reconciled against the top-k most-similar existing memories via a single LLM tool call, preventing duplication and contradiction. This is the most directly reusable consolidation primitive in the survey.

- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory (arXiv:2504.19413)](https://arxiv.org/abs/2504.19413)

#### 8. Zep / Graphiti — Temporal Knowledge Graph

**Architecture.** A dynamic, **bi-temporal knowledge graph** (`Graphiti` engine) with a three-tier hierarchy and hybrid retrieval.

**Record shape — three subgraphs.**
- **Episode subgraph:** raw messages/text/JSON (lossless source-of-truth).
- **Semantic entity subgraph:** extracted entities + fact edges (predicates) between them; hyper-edges for multi-entity facts.
- **Community subgraph:** clusters of strongly-connected entities with summaries.

Each fact **edge carries four timestamps**: `t_created`, `t_expired` (transactional/ingestion time) and `t_valid`, `t_invalid` (real-world event time) — a **bi-temporal model**.

**Write.** Process current message + prior `n=4` for context; extract speaker + entities (embedded into 1024-d vectors), resolve duplicates via embedding + full-text + LLM, then extract fact edges between entity pairs.

**Consolidate (temporal conflict resolution).** New edges are LLM-compared against semantically related existing edges; on a temporally-overlapping contradiction, the old edge is **invalidated** by setting its `t_invalid` to the new edge's `t_valid` — **invalidated, not deleted** (history preserved, new info prioritized).

**Retrieve.** Hybrid: **cosine similarity + BM25 full-text + breadth-first graph search** in parallel → **rerank** (RRF, MMR, node-distance, cross-encoder) → format edges/nodes with validity ranges into context. Beats MemGPT on DMR (94.8% vs 93.4%); up to +18.5% on LongMemEval.

**Best idea to steal:** the **bi-temporal model with edge invalidation** — never delete contradicted facts; mark them invalid with validity intervals so the agent can answer "what was true *as of* date X" and reconstruct how knowledge evolved. Crucial for facts that change over time (preferences, status, employer).

- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arXiv:2501.13956)](https://arxiv.org/abs/2501.13956) · [Graphiti — Neo4j blog](https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/)

#### 9. HippoRAG — Hippocampal Indexing via Personalized PageRank

**Architecture.** Neurobiologically-inspired retrieval: LLM = neocortex (parses language), a **schemaless KG = hippocampal index**, and **Personalized PageRank (PPR)** = pattern-completion across memories. Enables **single-step multi-hop** retrieval.

**Record shape.** KG of noun-phrase nodes + relation edges from **OpenIE triples**; **synonymy edges** between phrases with cosine similarity > τ (default **0.8**); a `|N|×|P|` **phrase-to-passage** incidence matrix `P`.

**Write (offline indexing).** Instruction-tuned LLM does OpenIE on each passage → triples → nodes/edges; add synonymy edges; record which phrases appear in which passages.

**Retrieve (online).**
1. LLM extracts query named entities; map each to its nearest KG node (cosine).
2. **Node specificity** `s_i = |P_i|^{-1}` (an IDF analog, no global aggregation needed) seeds/weights nodes.
3. Run **PPR** with probability mass on query (seed) nodes → distribution `n'` biased toward their neighborhoods.
4. **Passage scores** `p = n' × P`; rank passages. Up to +20% on multi-hop QA, far cheaper than iterative retrieve-read.

**Best idea to steal:** **Graph-walk retrieval (PPR) for multi-hop**, with **node-specificity (IDF-like) weighting** so a single retrieval pass integrates evidence scattered across many memories — no expensive multi-round retrieval loop.

- [HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs (arXiv:2405.14831)](https://arxiv.org/abs/2405.14831) · [code](https://github.com/OSU-NLP-Group/HippoRAG)

#### At-a-Glance Comparison

| System | Memory model | Write trigger | Retrieve | Consolidate | Signature idea |
|---|---|---|---|---|---|
| **MemGPT/Letta** | Tiered (core/recall/archival) | Agent tool call | Vector + keyword paging | Recursive summary on overflow | Self-editing memory as tools |
| **Generative Agents** | Flat NL stream | Append every obs. | recency×importance×relevance | Reflection trees (cited) | The 3-factor retrieval score |
| **A-MEM** | Linked notes (Zettelkasten) | LLM-annotated note | Cosine top-k | New notes rewrite old notes | Memory evolution |
| **MemoryBank** | Dialogues + summaries + persona | Summarize periodically | FAISS dense | Ebbinghaus `R=e^(−t/S)` | Spaced-repetition reinforcement |
| **Reflexion** | Episodic reflection buffer | After scored trial | Prepend last Ω≈1–3 | Bounded sliding window | Reward → verbal lesson |
| **Voyager** | Skill library (code) | After self-verification | Top-5 by description embed | Add-only, composable | Verified procedural memory |
| **Mem0** | Facts (+optional graph) | Extract from window | Dense / graph traversal | ADD/UPDATE/DELETE/NOOP | Memory decision engine |
| **Zep/Graphiti** | Bi-temporal KG (3 tiers) | Entity+fact extraction | Cosine+BM25+BFS → rerank | Edge invalidation (validity intervals) | Bi-temporal, lossless updates |
| **HippoRAG** | Schemaless OpenIE KG | Offline OpenIE indexing | PPR over KG → passages | Synonymy edges (τ=0.8) | PPR multi-hop + node specificity |

#### Prioritized Patterns to Adopt for Our Design

Ranked by leverage-to-effort for a tool-using/coding agent framework.

1. **Memory decision engine: ADD / UPDATE / DELETE / NOOP (from Mem0).** *Highest priority.* On every candidate fact, retrieve top-k similar existing memories and have one LLM tool call decide the operation. Single most reusable consolidation primitive — kills duplication and contradiction at write time, cheaply.

2. **Self-editing memory exposed as explicit tools (from MemGPT/Letta).** Fits this framework's "methods are capabilities" model perfectly: implement `core_memory_append/replace`, `archival_insert`, `archival_search` as deterministic agent methods the LLM calls deliberately. Auditable, controllable writes — no opaque background pipeline.

3. **recency × importance × relevance retrieval score (from Generative Agents).** Tiny, tunable ranking function; score importance once at write time (1–10), keep `recency = decay^Δt` and `relevance = cosine`, normalize, sum. Cheap to implement, strong baseline. Start with all weights = 1.

4. **Verified, composable procedural memory / "skill cache" (from Voyager).** For a coding agent, cache *verified* solution snippets/procedures, keyed by an LLM-generated description embedding, retrieve top-5, gate writes behind a verification step. Directly leverages this framework's "evidence before assertions" rule — only store skills that passed tests.

5. **Reward → verbal self-reflection in a bounded episodic buffer (from Reflexion).** After a failed task/test, write a 1–3 line "what to do differently" lesson and prepend it on retry. Trivial to add to any orchestrator loop; high payoff on iterative tasks. Keep a sliding window (Ω≈1–3) to bound growth.

6. **Memory evolution + rich note annotation (from A-MEM).** Embed over content **plus** LLM-generated keywords/tags/context (not raw content alone) for better recall; optionally let new writes refine neighbors' annotations.

7. **Bi-temporal facts with invalidation, not deletion (from Zep/Graphiti).** For facts that change (preferences, project status, config), store `(t_valid, t_invalid)` and invalidate-don't-delete. Adopt **if** the use case needs "what was true as of X" / change history; otherwise the Mem0 decision engine (#1) suffices.

8. **Spaced-repetition decay for pruning (from MemoryBank).** When the store grows large, use `R = e^(−t/S)` with `S++` on recall to decide what to forget — a principled, low-cost eviction policy.

9. **Graph-walk (PPR) retrieval for multi-hop (from HippoRAG).** *Adopt later, only if* single-vector retrieval proves insufficient for questions spanning many memories. Highest implementation cost (KG build + OpenIE + PPR); defer until evidence shows multi-hop is a real bottleneck.

**Suggested first build:** combine **#1 (Mem0 decision engine)** + **#2 (MemGPT-style tool-based editing)** + **#3 (Generative-Agents scoring)** as the core read/write/consolidate loop, then layer **#4/#5** for procedural + failure memory. Defer the graph approaches (#7, #9) until a concrete multi-hop or temporal-reasoning need appears.

##### Sources
- [MemGPT: Towards LLMs as Operating Systems (arXiv:2310.08560)](https://arxiv.org/abs/2310.08560) · [MemGPT walkthrough — Leonie Monigatti](https://www.leoniemonigatti.com/blog/memgpt.html)
- [Generative Agents: Interactive Simulacra of Human Behavior (arXiv:2304.03442)](https://arxiv.org/abs/2304.03442)
- [A-MEM: Agentic Memory for LLM Agents (arXiv:2502.12110)](https://arxiv.org/abs/2502.12110)
- [MemoryBank: Enhancing LLMs with Long-Term Memory (arXiv:2305.10250)](https://arxiv.org/abs/2305.10250)
- [Reflexion: Language Agents with Verbal Reinforcement Learning (arXiv:2303.11366)](https://arxiv.org/abs/2303.11366)
- [Voyager: An Open-Ended Embodied Agent with LLMs (arXiv:2305.16291)](https://arxiv.org/abs/2305.16291) · [project site](https://voyager.minedojo.org/)
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory (arXiv:2504.19413)](https://arxiv.org/abs/2504.19413)
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arXiv:2501.13956)](https://arxiv.org/abs/2501.13956) · [Graphiti — Neo4j](https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/)
- [HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs (arXiv:2405.14831)](https://arxiv.org/abs/2405.14831)

### B5 — Storage/Retrieval Layer Engineering Best Practices (R5-storage-engineering)

# Storage/Retrieval Layer for an Agent Memory System — Engineering Best Practices

Oriented to your target stack: **ChromaDB (local server mode) + NVIDIA-served `text-embedding-3-large`-class embeddings + a directed causal memory graph.**

#### 1. ChromaDB — Storage & Retrieval Substrate

##### Collections, distance metric, and HNSW config
Chroma stores vectors in **collections**. The distance metric and HNSW index parameters are **immutable after creation** — you must set them up front (to change them you have to clone the collection), so get the defaults right the first time.

- **Distance metric (`hnsw:space`):** options are `l2` (default, squared Euclidean), `cosine`, and `ip` (inner product). The default is L2, which is *wrong* for normalized text embeddings — explicitly set `cosine`.
- **Key HNSW params:** `ef_construction` (build quality/speed), `max_neighbors`/`M` (graph connectivity), `search_ef` (query-time recall/latency tradeoff), `batch_size` (in-memory brute-force buffer before transfer to HNSW, default 100), `sync_threshold` (when the index syncs to disk, default 1000).

**DEFAULTS (recommended):**
```python
collection = client.get_or_create_collection(
    name="causal_memory",
    configuration={
        "hnsw": {
            "space": "cosine",        # NOT the l2 default — embeddings are normalized
            "ef_construction": 200,   # 100 default is low; 200 improves recall for modest build cost
            "max_neighbors": 32,      # M; 16 default. 32 better recall for memory-graph sizes
            "search_ef": 100,         # raise toward 200 if recall@k is the bottleneck
        }
    },
)
```

##### Metadata filtering: `where` vs `where_document`
Two independent filter channels, combinable in a single query:

- **`where`** — filters on structured metadata. Operators: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, logical `$and`/`$or`, and array operators `$contains`/`$not_contains`. Metadata value types: **str, int, float, bool** (and same-typed arrays).
- **`where_document`** — full-text filter on the stored document body. Operators: `$contains` (substring), `$not_contains`, and `$regex`.

```python
collection.query(
    query_texts=[query],
    n_results=20,
    where={"$and": [
        {"node_type": {"$eq": "event"}},
        {"created_at": {"$gte": cutoff_ts}},   # store timestamps as int epoch
    ]},
    where_document={"$contains": "deployment"},  # cheap exact-keyword gate
    include=["documents", "metadatas", "distances"],
)
```

**For a causal memory graph,** push graph structure into metadata so vector recall and graph filtering happen in one call. Recommended metadata schema per memory node:
- `node_id` (str, UUID), `node_type` (str: `event`/`fact`/`entity`/`summary`)
- `created_at` (int, epoch seconds — int filtering is range-queryable)
- `parents`/`children`: Chroma metadata is flat scalars/arrays only, so store edges as a JSON string field (e.g. `causes='["id1","id2"]'`) plus a denormalized `parent_ids` array of strings for `$contains` membership checks. Keep the authoritative edge list in your graph store (see §4).

##### Local persistent client vs client/server mode
- **`PersistentClient(path=...)`** — embedded, in-process, single-writer. Fine for a single agent process; no network hop.
- **Client/server (`chromadb run` → `HttpClient`/`AsyncHttpClient`)** — separate Chroma server process. Use this when multiple agent processes/workers share memory, you want the DB lifecycle decoupled from the agent, or you need concurrent access. **This is the right choice for your stated "Chroma (local server)" target** — run the server locally and connect via `HttpClient`. Use `AsyncHttpClient` if your agent is asyncio-based (the NeMo OO framework is).

##### Known scaling caveats
- **No native hybrid search.** Chroma is dense-only; BM25/sparse must be layered on top by you (see §2). `where_document` is exact substring/regex, **not** ranked lexical search.
- HNSW lives in memory; **memory grows with vector count × dimensions** — a strong argument for dimension reduction (§5).
- Iterate large collections with `limit`/`offset` pagination (10–100 records/page), never load whole collections into memory.
- Single-node; not a horizontally-sharded store. For an agent's working/long-term memory (10k–low-millions of nodes) this is fine; beyond that, reconsider.

Sources: [Chroma Usage Guide](https://docs.trychroma.com/guides) · [Chroma Cookbook — Collections](https://cookbook.chromadb.dev/core/collections/) · [Chroma — Metadata Filtering](https://docs.trychroma.com/docs/querying-collections/metadata-filtering) · [ChromaDB: Semantic Search with Metadata Filters](https://medium.com/@sangal.sachin/chromadb-semantic-search-with-metadata-filters-using-python-456887e5e0cd)

#### 2. Hybrid Sparse + Dense Retrieval (BM25 + dense) and RRF/Reranking

**Why hybrid:** BM25 wins on exact-match — rare entities, error codes, names, IDs — that an agent's memory is full of; dense embeddings win on paraphrase/conceptual recall. They are **complementary first-stage retrievers**, not a toggle. The dominant pipeline is: *hybrid first-stage retrieval → fusion → optional neural rerank*.

**Fusion — Reciprocal Rank Fusion (RRF):** the standard because it's score-agnostic (combines ranks, not incompatible raw scores). Each doc scores `1/(k + rank)` per retriever, summed across retrievers; **k = 60** is the canonical constant.

```python
def rrf(rank_lists, k=60):
    scores = {}
    for ranked in rank_lists:           # each: [doc_id, ...] best-first
        for rank, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)
```

**Reranking:** a cross-encoder reranker over the fused top-N "dominates all single-stage methods" when top-5 precision is the bottleneck. NVIDIA serves a NeMo Retriever reranking NIM you can call the same way as the embedder.

**DEFAULTS:**
- BM25: implement with `rank_bm25` (in-process) or `bm25s`, over the same memory text corpus. Chroma has no BM25, so maintain the lexical index alongside.
- Retrieve **top-50 from dense (Chroma) and top-50 from BM25** → RRF (k=60) → take **top-20** → rerank → keep **top-5–8** for the agent context.
- **Phasing (don't over-build):** start dense-only; add BM25 hybrid once you observe failures on entity/keyword queries; add the reranker only when top-k precision is the limiter.

Sources: [Hybrid Search: BM25, Vector & Reranking 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026) · [Hybrid Search in RAG: Dense + Sparse, RRF](https://blog.gopenai.com/hybrid-search-in-rag-dense-sparse-bm25-splade-reciprocal-rank-fusion-and-when-to-use-which-fafe4fd6156e) · [Building Hybrid Search That Works: BM25 + Dense + Cross-Encoders](https://ranjankumar.in/building-a-full-stack-hybrid-search-system-bm25-vectors-cross-encoders-with-docker) · [Sparse vs Dense Retrieval for RAG](https://mljourney.com/sparse-vs-dense-retrieval-for-rag-bm25-embeddings-and-hybrid-search/)

#### 3. Chunking Strategy for Memories

For an **agent memory** system, the unit is usually a discrete memory (an event, observation, or fact), which is naturally short — so chunking matters less than for long-document RAG, but still applies to ingested documents/transcripts.

- **Size:** sweet spot is **256–512 tokens** for most prose; 300–800 tokens acceptable. Smaller = higher precision, less context; larger = more context, less retrieval focus.
- **Overlap:** **10–20%** of chunk size. NVIDIA's own research found **15%** optimal on financial docs. Overlap prevents thoughts being split across boundaries.
- **Semantic chunking** (split on embedding-similarity drops between sentences): improves recall ~2–3 points over fixed/recursive, but produces wildly variable chunk sizes and costs embedding compute at preprocessing.
- **Proposition chunking** (LLM decomposes text into atomic, self-contained statements): highest retrieval precision and the **best fit for a causal memory graph** — each proposition becomes a clean node you can attach causal edges to. Cost: an LLM call per source passage.

**DEFAULTS:**
- **Atomic memories (events/facts/observations):** do NOT chunk — store as one node each; that *is* a proposition. This is the recommended default for the causal graph's nodes.
- **Ingested long documents/transcripts:** recursive splitting at **~400 tokens, 15% overlap** as the cheap baseline; upgrade to **proposition chunking** for material that will become graph nodes, since clean propositions = clean causal edges.
- Always store the **source/parent reference** and timestamp in chunk metadata so retrieved chunks can be re-linked into the graph.

Sources: [Chunking Strategies for RAG (Weaviate)](https://weaviate.io/blog/chunking-strategies-for-rag) · [Best Chunking Strategies for RAG in 2026 (Firecrawl)](https://www.firecrawl.dev/blog/best-chunking-strategies-rag) · [Chunking Strategies (DataCamp)](https://www.datacamp.com/blog/chunking-strategies) · [The Ultimate Guide to RAG Chunking Strategies (Agenta)](https://agenta.ai/blog/the-ultimate-guide-for-chunking-strategies)

#### 4. Graph-Augmented Retrieval (GraphRAG / HippoRAG-style)

This is the layer that turns flat vector recall into **multi-hop causal reasoning** over memory.

##### HippoRAG (and HippoRAG 2) — directly applicable pattern
- **Build:** OpenIE extracts `(subject, relation, object)` triples from passages; entities/noun-phrases become **nodes**, relations become **edges** → a schemaless KG (the "hippocampal index"). **Synonymy edges** connect nodes above a similarity threshold so equivalent terms merge.
- **Retrieve:** encode query entities → cosine-match to graph nodes (query nodes) → run **Personalized PageRank seeded only from those query nodes**, letting activation flow through the neighborhood (this is the multi-hop step) → multiply node scores by a node-passage matrix to rank passages.
- **Node specificity (IDF-like):** `Specificity(i) = 1 / (#passages containing node i)` — rare/distinctive nodes get more weight.
- **HippoRAG 2** adds a **composite graph of both phrase nodes and passage nodes**; probability mass flows phrase→passage→phrase, and queries link directly to full triples ("deep contextualization") with a "recognition memory" triple filter. Reported gains over pure-embedding RAG (NV-Embed-v2 baseline): MuSiQue multi-hop F1 44.8→51.9, 2Wiki Recall@5 76.5%→90.4%, mean +7 F1 on associative tasks.

##### Recommended architecture for your directed causal memory graph
- **Vector search finds entry points; the graph does the hops.** Use Chroma to retrieve the top-k semantically relevant nodes, then **traverse your directed causal edges** (`causes`/`caused_by`, `precedes`/`follows`) outward to assemble the causal subgraph. This is exactly the GraphRAG "find starting points via vector/full-text search, then follow relationships" pattern.
- **Where the graph lives:** keep the **authoritative causal edges in a real graph structure**, not in Chroma metadata. Lightweight default: an in-process `networkx.DiGraph` persisted to disk (run Personalized PageRank / BFS over it directly). Scale-up: Neo4j/FalkorDB when concurrent multi-process access or large graphs demand it. Chroma holds node embeddings + denormalized metadata; the graph store holds edges.
- **Causal/temporal edges are the payoff:** time-stamped, directed edges give clean "what led to what / who did what when" queries that flat vector RAG cannot answer. Bound traversal (e.g., 2–3 hops, top-N by edge weight/PageRank) to control latency.

##### Cost caveats (don't over-engineer)
GraphRAG's extraction + community-summary passes are **token- and compute-expensive at index time**, traversal adds **2–3× inference latency**, and the index can grow **super-linearly**, complicating incremental updates. Mitigations: batch extractions, cache repeated entities, use a smaller model for first-pass extraction. **Use the graph only for multi-hop/relational/causal queries; route simple lookups to plain vector search** to avoid paying graph cost for no accuracy gain.

**DEFAULTS:**
- Entry: hybrid retrieval (§2) → top-10 seed nodes.
- Traversal: directed causal edges, **max 2–3 hops**, Personalized PageRank seeded from seed nodes, node-specificity weighting `1/(#passages containing node)`.
- Edge extraction: LLM/OpenIE on ingest; add synonymy edges at cosine ≥ ~0.85; dedupe entities.
- Storage split: Chroma = embeddings + metadata; `networkx.DiGraph` (or Neo4j at scale) = causal edges.

Sources: [HippoRAG: Neurobiologically Inspired Long-Term Memory (arXiv)](https://arxiv.org/html/2405.14831v1) · [From RAG to Memory / HippoRAG 2 (arXiv)](https://arxiv.org/html/2502.14802) · [HippoRAG 2 (MarkTechPost)](https://www.marktechpost.com/2025/03/03/hipporag-2-advancing-long-term-memory-and-contextual-retrieval-in-large-language-models/) · [What is GraphRAG? (Neo4j)](https://neo4j.com/blog/genai/what-is-graphrag/) · [Graph RAG vs Vector RAG for Agent Memory 2026](https://agentmarketcap.ai/blog/2026/04/07/graph-rag-vs-vector-rag-agent-memory-neo4j-pgvector) · [Vector DBs vs Graph RAG for Agent Memory (MLM)](https://machinelearningmastery.com/vector-databases-vs-graph-rag-for-agent-memory-when-to-use-which/)

#### 5. Embedding Model — `text-embedding-3-large`-class served via OpenAI-compatible NVIDIA endpoint

NVIDIA NeMo Retriever Text Embedding NIMs are Triton-accelerated and expose an **OpenAI-compatible `/v1/embeddings`** endpoint — so an OpenAI-style client works, with a few NVIDIA-specific behaviors that materially affect accuracy.

##### Query vs passage asymmetry — the #1 correctness pitfall
NVIDIA retrieval models (NV-Embed/E5-class) require an **`input_type`**: `"passage"` when indexing, `"query"` when searching. *"Failure to do so will result in large drops in retrieval accuracy."* Two ways to set it:
1. `"input_type": "query"|"passage"` in the request body, **or**
2. append `-query`/`-passage` to the model name (for strict OpenAI clients that won't pass `input_type`).

Note: `text-embedding-3-large` itself (the actual OpenAI model) does **not** use input_type; GTE/GTR-class models also ignore it. So check which model your NVIDIA endpoint actually serves — if it's an NV-Embed/E5-class model behind the OpenAI shim, you **must** honor input_type. Build a thin embedder wrapper that takes a mode flag and selects the suffix/field accordingly, so indexing and query paths can't drift.

##### Dimensions (Matryoshka)
- `text-embedding-3-large` produces up to **3072 dims**; NV-Embed and 3-large support **`dimensions`** to truncate (Matryoshka). **Request the reduced size up front** via the `dimensions` param rather than truncating client-side — the model optimizes for the target dimensionality.
- **Use the same `dimensions` value across concurrent requests** or NIM dynamic batching breaks.
- `dimensions` **cannot** be combined with `embedding_type` (int8/binary quantization).

##### Normalization
OpenAI 3-large embeddings are **L2-normalized to length 1**, so cosine == dot product and cosine/Euclidean give identical rankings — match this with Chroma `hnsw:space="cosine"`. **If you reduce dimensions, re-normalize** before storing (the NVIDIA OpenAI page doesn't guarantee post-truncation normalization), and verify with a quick `||v||≈1` check on a sample.

##### Batching
- OpenAI-style `/v1/embeddings` accepts up to **2048 inputs per call**; batch aggressively for corpus ingest.
- NIM also does **server-side dynamic batching** (default 100µs queue delay, tunable via `NIM_TRITON_DYNAMIC_BATCHING_MAX_QUEUE_DELAY_MICROSECONDS`) — keep `dimensions` constant across concurrent requests so it engages.
- Respect per-model **max token length** (model-dependent; check the support matrix) — set truncation explicitly rather than letting long memories silently overflow.

**DEFAULTS:**
- **dimensions = 1024** (strong recall/storage tradeoff for an agent memory store; cuts Chroma HNSW RAM ~3× vs 3072). Use 1536 if recall@k regresses; 3072 only if measured to matter.
- `encoding_format = "float"`; `embedding_type = float` (skip int8/binary unless RAM-bound — they block `dimensions` and binary needs Hamming, not cosine).
- **input_type: `passage` on ingest, `query` on retrieval** (centralize in the embedder wrapper).
- Chroma: `hnsw:space="cosine"`; **re-normalize vectors after any dimension reduction**.
- Ingest batch size: up to 2048 inputs/call; constant `dimensions` across concurrent calls.

Sources: [Use the API (OpenAI) for NeMo Retriever Embedding NIM](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/latest/use-the-api-openai.html) · [NeMo Retriever Text Embedding NIM — Overview](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/latest/overview.html) · [OpenAI — Vector embeddings guide](https://developers.openai.com/api/docs/guides/embeddings) · [Exploring text-embedding-3-large (DataCamp)](https://www.datacamp.com/tutorial/exploring-text-embedding-3-large-new-openai-embeddings) · [New embedding models and API updates (OpenAI)](https://openai.com/index/new-embedding-models-and-api-updates/)

#### Consolidated DEFAULTS (copy-paste reference)

| Layer | Parameter | Default |
|---|---|---|
| **Chroma** | client mode | local server (`HttpClient`/`AsyncHttpClient`) |
| | `hnsw:space` | `cosine` (immutable — set at creation) |
| | `ef_construction` / `max_neighbors` / `search_ef` | 200 / 32 / 100 |
| | metadata schema | `node_id`, `node_type`, `created_at`(int epoch), edge ids as str array + JSON |
| **Hybrid** | dense top-k / BM25 top-k | 50 / 50 |
| | RRF constant k | 60 |
| | after RRF → rerank → context | top-20 → top-5–8 |
| | phasing | dense → +BM25 on keyword failures → +reranker for precision |
| **Chunking** | atomic memories | no chunking (1 node = 1 proposition) |
| | ingested docs | recursive ~400 tok, 15% overlap; proposition chunking for graph nodes |
| **Graph** | entry / hops | hybrid top-10 seeds; 2–3 hops |
| | ranking | Personalized PageRank + node-specificity `1/(#passages with node)` |
| | synonymy edge threshold | cosine ≥ ~0.85 |
| | storage split | Chroma = embeddings+metadata; networkx DiGraph (→Neo4j at scale) = causal edges |
| **Embeddings** | dimensions | 1024 (→1536 if recall drops) |
| | input_type | `passage` ingest / `query` retrieval (mandatory for NV-Embed/E5) |
| | format | `float`; re-normalize after dim reduction |
| | ingest batch | ≤2048 inputs/call, constant `dimensions` |

**Architectural through-line:** Chroma (cosine, dim-reduced NVIDIA passage embeddings) + an in-process BM25 index supply hybrid first-stage recall → RRF + optional NIM reranker pick entry nodes → your directed causal graph does Personalized-PageRank multi-hop traversal over those seeds → reserve graph traversal for genuinely multi-hop/causal queries and route simple lookups to plain hybrid retrieval to avoid paying graph latency/token cost for no gain.

## Part C — Synthesis: Schema & Algorithms

# Opt-In Agent Memory System — Schema & Core Algorithms

Storage substrate: ChromaDB (cosine, dim-reduced NVIDIA passage embeddings) + a `networkx.DiGraph` causal/typed graph store; embeddings via an NVIDIA OpenAI-compatible `/v1/embeddings` endpoint [R5]. Design philosophy is intentionally **non-strict**: every descriptor and edge is optional with sane defaults, so a memory can be a one-line fact or a fully-annotated, graph-linked episode.

### (a) Memory Record Schema

A record is the unit stored in Chroma (vector + metadata) with its edges held authoritatively in the graph store [R5 §4: "Chroma holds node embeddings + denormalized metadata; the graph store holds edges"]. All fields except `id`, `type`, `content`, and `created_at` are **optional** (non-strict).

```python
class MemoryType(str, Enum):     # see (b)
    INFO = "info"; SKILL = "skill"; EPISODE = "episode"
    INTENT = "intent"; SCRATCH = "scratch"; REFLECTION = "reflection"

class EdgeType(str, Enum):
    DERIVED_FROM = "derived_from"   # CAUSAL provenance (required type) [R2 #1,#3]
    CREATED_BY   = "created_by"     # CAUSAL: task/event that encoded this [R2 #2]
    SUPPORTS     = "supports"       # corroborating evidence [R4 Gen-Agents cited reflections]
    CONTRADICTS  = "contradicts"    # conflicting belief [R4 Mem0/Zep]
    REFINES      = "refines"        # this updates/sharpens a prior memory [R4 A-MEM evolution]
    RELATED      = "related"        # generic associative/semantic link [R3 §1.2]
    CAUSES       = "causes"         # domain causal: A → outcome B [R5 §4; R2 #9]
    PRECEDES     = "precedes"       # temporal ordering of episodes [R4 Zep bi-temporal]
    PART_OF      = "part_of"        # composition (skill composed of sub-skills) [R4 Voyager]
    TRIGGERS     = "triggers"       # intent → action link (prospective) [R1 prospective]
```

#### Record fields

| Group | Field | Type | Notes / citation |
|---|---|---|---|
| **Identity** | `id` | `str` (UUID) | primary key in both stores [R5 §1] |
| | `type` | `MemoryType` | taxonomy tag (b) [R1] |
| | `title` | `str \| None` | short LLM-generated label; aids skim/rerank |
| | `content` | `str` | the memory text (one proposition for atomic memories) [R5 §3] |
| **Structural** | `size_chars` | `int` | raw length; cheap dedup/segmentation signal |
| | `token_len` | `int` | for context-budget accounting [R4 MemGPT paging] |
| | `sentence_count` | `int` | atomicity heuristic (>3 ⇒ candidate for proposition split) [R5 §3] |
| **Descriptors** | `importance` | `float` 1–10 | LLM "poignancy" at write time [R3 §1.5; R4 Gen-Agents]; drives retrieval + replay budget |
| | `salience` | `float` 0–1 | emotional/outcome/novelty tag: success/failure/surprise/reward [R2 #10]; over-replay failures |
| | `confidence` | `float` 0–1 | belief certainty; gates reconsolidation overwrite ("update only if new evidence strong") [R2 #7] |
| | `mood` | `str \| None` | affective valence of the source event [R2 #5 REM emotional processing] |
| | `strength` | `int` (init 1) | spaced-repetition counter; `+1` on recall, slows decay [R4 MemoryBank `R=e^(−t/S)`] |
| | `reinforcement_count` | `int` | merge/replay reinforcement tally [R2 #5,#8] |
| **Metadata** | `created_at` | `int` epoch | range-queryable in Chroma `where` [R5 §1] |
| | `last_accessed_at` | `int` epoch | recency input [R3 §1.4; R4 Gen-Agents] |
| | `access_log` | `list[int]` | full access timestamps → ACT-R base-level `B=ln(Σ Δ_k^−d)` [R3 §1.4] |
| | `access_count` | `int` | frequency (derivable from log; denormalized for fast filter) |
| | `source_task_ref` | `str \| None` | task/run id that produced it (provenance) [R2 #2; R4 Reflexion] |
| | `related_files` | `list[str]` | file paths touched (coding-agent cue) [R3 encoding-context] |
| | `chat_turn_ref` | `str \| None` | `session_id:turn` pointer into lossless message log [R4 MemGPT recall / Zep episode subgraph] |
| | `valid_from` / `valid_to` | `int \| None` | bi-temporal validity interval; invalidate-don't-delete [R4 Zep/Graphiti] |
| | `trigger` | `dict \| None` | INTENT only: `{kind: event\|time\|activity, cue, fire_at}` [R1 prospective] |
| **Context cue** | `context` | `dict` | encoding context for cue-overlap match: `{entities[], tags[], place_or_task, mood}` [R3 §1.3 encoding specificity] |
| **Embedding** | `embedding_ref` | `str` | Chroma `node_id` (collection + id); vector itself lives in Chroma, not duplicated here [R5 §1] |
| | `embedding_dims` | `int` (1024) | guards against dim drift across stores [R5 §5] |
| **Graph** | `edges` | `list[Edge]` | denormalized for read; authoritative copy in `DiGraph` [R5 §4]. `Edge = {target_id, type: EdgeType, weight: float∈(0,1], created_at}` |

**Embedding text** = `concat(title, content, context.tags, context.entities)` rather than raw content — boosts recall [R4 A-MEM]. Embed with `input_type="passage"` on write, `"query"` on read [R5 §5, the #1 correctness pitfall].

### (b) Memory Type Taxonomy

Extends the user's `skill` + `info` and is grounded in the Squire/Tulving/Baddeley map [R1].

| Type | Definition | Human-memory basis [R1] | Persistence |
|---|---|---|---|
| **`info`** | Context-free facts, user preferences, domain rules, conventions ("deploy = `make ship`"). *(user's type)* | Semantic memory (Tulving) / declarative (Squire) | long-term |
| **`skill`** | Reusable, **verified** procedures/workflows/code patterns + applicability conditions. *(user's type)* | Procedural memory (Squire non-declarative); verified-write [R4 Voyager] | long-term |
| **`episode`** | Time-and-place-stamped record of a specific task run: goal, actions, observations, outcome. Raw material for consolidation. | Episodic memory (Tulving); autobiographical when-and-where | long-term |
| **`intent`** | Future intention / reminder / TODO with a `trigger` (event-, time-, or activity-based). | Prospective memory ("remembering to remember") | until fired |
| **`reflection`** | Higher-level insight distilled from episodes, with `supports` edges citing evidence. Forms a reflection tree. | Schema/gist abstraction [R2 #6,#9]; cited reflections [R4 Gen-Agents] | long-term |
| **`scratch`** | Transient per-task workspace: intermediate reasoning, tool outputs, retrieved chunks. **Non-persistent** — consolidated out or discarded each cycle. | Working memory (Baddeley & Hitch) | transient |

`episode` is the highest-value addition: it's the only retrospective when-and-where store and the source the dream phase consolidates into `info`/`skill` [R1; R2 #9]. `intent` is the only prospective (future-action) category. `scratch` makes the working-vs-long-term axis explicit to prevent context bloat [R1].

### (c) Retrieval Scoring Function

Per-node base score combines **relevance + recency + importance**, then adds an **associative-spread** term [R3 §2, R4 Gen-Agents]. Each base term is min-max normalized across the candidate set first [R4 Gen-Agents].

**Relevance** (encoding-specificity: blend embedding similarity with context-cue overlap) [R3 §1.3, §2.2]:
```
rel(m,q) = λ·cos(e_m, e_q) + (1−λ)·ctxOverlap(m.context, q.context),   λ = 0.7
ctxOverlap = Jaccard over {entities, tags, place/task}
```

**Recency/frequency** (ACT-R base-level, exact) [R3 §1.4]:
```
B(m) = ln( Σ_k (Δ_k)^−d ),  Δ_k = max(t_now − access_k, ε),  d = 0.5
rec(m) = σ(B(m))                    # logistic squash to [0,1]
```

**Importance**: `imp(m) = m.importance / 10` [R4 Gen-Agents].

**Base node score** (normalize each term, then weight):
```
S_base(m) = α_rel·rel̂ + α_rec·reĉ + α_imp·imp̂
defaults: α_rel = 1.0,  α_rec = 0.5,  α_imp = 0.5     # relevance-led [R3 §3.1]
```

**Associative spread** added to the base (ACT-R `Σ W_j S_ji` with fan penalty) [R3 §2.3]:
```
Act(n) = S_base(n) + γ · Σ_{m∈cues} A(m)·w_mn·(S_max − ln(fan_m))_+
γ = 0.5,  S_max = 1.5,  fan_m = out-degree of m,  (·)_+ clamps ≥ 0
```
The fan penalty down-weights high-degree hub nodes [R3 §1.4 fan effect]. This spread term is what surfaces a memory not directly similar to the query because it's strongly linked to one that is — "spontaneous association" [R3 §2.3].

| Symbol | Meaning | Default |
|---|---|---|
| `λ` | embedding vs context-cue mix | 0.7 |
| `d` | base-level decay | 0.5 |
| `α_rel / α_rec / α_imp` | base weights | 1.0 / 0.5 / 0.5 |
| `γ` | 1-hop spread gain | 0.5 |
| `S_max` | max associative strength | 1.5 |

First-stage candidate recall is hybrid (dense Chroma top-50 ∪ BM25 top-50 → RRF k=60 → top-20) before scoring; add the reranker only when top-k precision is the limiter [R5 §2]. On retrieval, append `t_now` to `access_log` and `strength += 1` so hot paths self-strengthen [R3 §3; R4 MemoryBank].

### (d) Multi-Hop Traversal

Bounded BFS / Personalized-PageRank-style spread from the seed cue set over the directed typed graph, decaying activation per hop — the discrete analog of Collins–Loftus spreading activation [R3 §3] combined with HippoRAG graph-walk multi-hop [R4 HippoRAG; R5 §4].

**Per-hop propagation** (node receives from activated predecessors):
```
A^(h)(n) = A^(h−1)(n) + δ^h · Σ_{m∈pred(n)} A^(h−1)(m)·w_mn·(S_max − ln(fan_m))_+
```

| Symbol | Meaning | Default | Source |
|---|---|---|---|
| `δ` | per-hop activation decay | 0.6 | distance effect [R3 §3] |
| `K` | max hops | 3 | free-association depth; "reserve graph for multi-hop, 2–3 hops" [R3 §3; R5 §4] |
| `b` | branching beam per node | 5 | expand only top-`b` out-edges by weight; bounds fan-out [R3 §3] |
| `θ` | activation floor | 0.05 | stop propagating below this (ACT-R threshold τ analog) [R3 §3] |
| `M` | final memories returned | 12 | context budget [R3 §3] |

**Semantics:** seed `A^(0) = S_base` on top-`N` cues; for `h = 1..K` propagate over top-`b` out-edges, multiply by `δ^h` and fan-penalized edge strength, accumulate (a node reached by multiple paths sums contributions — parallel spread), prune frontier below `θ`, traverse each edge once per source to avoid cycles. Final activation `A^(K)`; return top-`M` above `θ`, optionally ranked via ACT-R logistic `P = 1/(1+e^−(A^(K)−τ)/s)`, `τ=0, s=0.3` [R3 §3]. **Weight edges by type**: causal `derived_from`/`created_by`/`causes` and `refines` get higher base `w`; `related` lower — node-specificity `1/(#linked)` further down-weights hubs [R5 §4]. Route simple lookups to plain hybrid retrieval (c); use traversal only for genuinely multi-hop/causal queries to avoid paying graph latency [R5 §4].

### (e) Dream / Consolidation Algorithm

Pure-Python orchestrator method running ordered offline ops after a task (or batched), mirroring the biological sequence: **gate write → ripple-replay → NREM clean/stabilize → REM abstract/recombine → renormalize/prune** [R2 Part 2]. Each LLM-judgment step is a single generation method (`...`); deterministic steps are plain helpers [R2 implementation note].

**Phase 0 — Encode + gate**
1. **Write episodic trace + salience tag** — persist the trajectory; set initial `salience` from outcome/surprise/failure/novelty [R2 #10, #2].
2. **Write gate** — only traces above a salience/importance threshold queue for durable processing; weakly-tagged stay ephemeral and may decay (tag-and-capture) [R2 #2].

**Phase 1 — Selection & replay (sharp-wave ripples)**
3. **Build replay set by priority** — high-salience + weakly-encoded-but-valuable + recent buffer items; skip strong/low-value [R2 #4, #10].
4. **Compress each trajectory** — summarize into compact action→outcome sequence (time-compressed replay) [R2 #4].

**Phase 2 — NREM pass (faithful: dedup/merge, stabilize, transfer)**
5. **Dedup / merge** — cluster near-identical memories; merge into one canonical record, accumulating `reinforcement_count`. Decision engine emits **ADD / UPDATE / DELETE / NOOP** on the top-k similar existing memories [R4 Mem0; R2 #8].
6. **Reconsolidation update** — for an existing memory contradicted/extended by new evidence, reactivate → merge/correct → rewrite with new timestamp + provenance; **only if `confidence` of new evidence is high** (guard against corrupting good memories). For changeable facts, invalidate-don't-delete via `valid_to` [R2 #7; R4 Zep].
7. **Schema routing (assimilate vs. create)** — test schema-congruence: congruent ⇒ update existing `skill`/`info` in place (fast); novel/incongruent ⇒ new node flagged for extra replay [R2 #6 SLIMM].
8. **Hippocampal→neocortical transfer** — move stabilized records from episodic buffer into the long-term store (normalize, embed, index); interleave across several rounds to avoid catastrophic overwrite [R2 #1, #3].

**Phase 3 — REM pass (generative: abstract, recombine)**
9. **Abstraction: episodes → skills** — cluster related episodes, induce a generalized parameterized `skill`/`reflection` + applicability conditions, drop episode-specific detail [R2 #9; R4 Gen-Agents reflection].
10. **Regularity & causal-edge mining** — extract recurring cause→effect patterns across episodes; add `causes` / `derived_from` edges to the graph [R2 #9; R5 §4].
11. **Counterfactual rollouts** — for salient failures, generate "what should I have done?" alternatives; store improved strategies as candidate `skill`s [R2 #4 future-trajectory replay, #5 REM recombination].
12. **Salience integration** — give high-salience items (esp. failures) extra abstraction passes and link the lesson to the relevant skill/schema [R2 #10, #5].

**Phase 4 — Renormalization (synaptic homeostasis) + edge formation + schema update**
13. **Re-score importance globally** — recompute from access frequency, recency, `reinforcement_count`, outcome value, downstream usefulness [R2 #10].
14. **Global down-scaling** — proportional decay of all importance/strength scores, preserving relative ordering (SHY renormalization); spaced-repetition `R = e^(−t/S)` governs retention [R2 #8; R4 MemoryBank].
15. **Edge formation** — for new/updated nodes, take top-k nearest neighbors and let an LLM decide which links are meaningful (`supports`/`contradicts`/`related`/`refines`) + add synonymy edges at cosine ≥ 0.85 [R4 A-MEM, HippoRAG; R5 §4].
16. **Prune / forget** — delete nodes and trivially-weak edges below threshold (low-value, stale, redundant, rarely-retrieved); strongest survive (weak-connection elimination → higher signal-to-noise, anti-proactive-interference) [R2 #8].
17. **Schema update + clear buffer** — update typed schemas with newly-stabilized abstractions; flush processed `scratch`/episodic buffer; log the dream (what merged/abstracted/pruned) for auditability [R2 #1, #6].

Ordering is load-bearing: NREM (clean) before REM (abstract) before prune (forget only after value is extracted) — the sequential NREM→REM hypothesis [R2 #5].

### (f) Write-Trigger Policy

Two complementary channels, with dedup-on-write on both [R4 Mem0, MemGPT].

**1. Conscious tool calls (deliberate, auditable)** — self-editing memory as first-class agent methods, the highest-leverage pattern for a "methods are capabilities" framework [R4 MemGPT/Letta]:
- `remember(type, content, **descriptors)` — explicit encode.
- `update_memory(id, ...)` / `forget(id)` — explicit edit/delete.
- `note_intent(content, trigger)` — encode a prospective `intent`.
Nothing implicit; writes are LLM-initiated, logged, and reversible [R4 MemGPT: "no opaque background pipeline"].

**2. Automatic event-driven (passive)** — fired by orchestrator hooks, gated by the tag-and-capture write gate so only salient events persist [R2 #2, #10]:
- **task completion** → write an `episode` (always) + salience tag from outcome.
- **failure / error** → high-salience write (over-replayed in dream for lessons) [R2 #10; R4 Reflexion verbal lesson].
- **verified solution** (tests pass) → candidate `skill`, gated behind verification [R4 Voyager; CLAUDE.md "evidence before assertions"].
- **explicit user preference / decision** stated in chat → `info` extraction [R4 Mem0 extraction phase].

**Dedup-on-write (both channels)** — the Mem0 decision engine is the core consolidation primitive [R4 Mem0, top-priority pattern]:
```
candidate → retrieve top-k=10 most-similar existing memories
          → one LLM tool call decides: ADD | UPDATE | DELETE | NOOP
ADD    → new node + edge formation (e)
UPDATE → reconsolidation merge (guarded by confidence) [R2 #7]
NOOP   → drop duplicate, bump strength/access of the matched memory [R4 MemoryBank]
```
Every write attaches a CAUSAL `created_by` / `derived_from` edge to its source task or parent memory, so provenance is never lost [R2 #2; schema (a)]. `scratch` is never durably written — it lives only in the working buffer and is flushed each dream cycle [R1].

Note: this is a design deliverable only — no files were created or modified. The NeMo OO mapping (orchestrator = pure-Python dream method calling per-task generation methods like `merge_duplicates()`, `induce_skill()`, `extract_causal_edges()`, `decide_write_op()`) follows the CLAUDE.md "one method = one LLM task" and "orchestrators are pure Python" rules [R2 implementation note].

## Part D — Synthesis: Integration Architecture & Open Questions

# Long-Term Memory Add-On: Integration Architecture

A toggleable, opt-in memory subsystem for nooa with two surfaces: (1) **conscious tools** (`recall/search/remember/associate`) and (2) a **wrapper/hook** that injects retrieved memories pre-turn and runs "dreaming" consolidation post-task. **Zero core edits required** — every hook below maps to an existing extension point.

### 1. Hook → extension-point map (every hook cited to file:line)

| # | Concern | Channel chosen | Actual extension point (file:line) | Notes |
|---|---------|---------------|-------------------------------------|-------|
| H1 | **Pre-turn injection** ("spontaneous association") | **Dynamic context block** | `ContextApi.set_dynamic(key, expr)` → `context.py:70` → `ContextManager.set_dynamic` `context_manager.py:102`; re-evaluated every turn at `actor.py:2700` (`_resolve_value`) inside `_prepare_context` (`actor.py:2653`), fed by `generate()`→`_build_messages` (`actor.py:785`) | `evaluate_expression` (`actor.py:1924`) **auto-awaits coroutines** (`actor.py:1993-1997`) and seeds `self`, so the expr can be `"self._recall_for_context()"` calling an async method. Failure-isolated: exceptions render inline (`actor.py:2713-2721`). Block is `user_block=True, static=False` → evicted **first** under context pressure (`renderer.py:102`), protecting the task. |
| H2 | **Conscious tools** surfaced via `doc(self)` | **Public agent methods on a mixin** | `self`-in-namespace: `ExecutionNamespaceBuilder.build` sets `"self": agent` (`generated_code.py:74`); methods rendered in the protected `self` block `doc(type(self))` (`agent.py:242`) | NOT new `Tool` objects. CodeAct's two-tool envelope (`execute_python`/`return_result`, `codeact.py:594-596`) is preserved; the LLM calls `await self.recall(...)` inside `execute_python`. `Memory` return type must be **module-level** to land in exec_globals (`codeact.py:2245-2271`). |
| H3 | **Write-on-event** (salient events → memory) | **`EventManager.on(type, handler)`** | `event_manager.py:184`; multi-subscriber, per-agent | Subscribe `on("Notification", h)` (events.py:301, purpose-built external signal), `on("Error", h)`, `on("Message", h)`. Handlers are **sync + exception-swallowing** (`event_manager.py:251-258`) → never block; enqueue/`asyncio.create_task` the actual write. |
| H4 | **Post-task "dreaming"** consolidation | **`EventManager.intercept("agent_call", mw)`** (primary) | `intercept` `event_manager.py:262`; chain run by `run_middleware` `event_manager.py:309`; invoked around the whole method at `method_wrapper.py:213`. `AgentCallContext` (agent, method_name, args, kwargs, **result**) at `middleware.py:68-89` | Run consolidation **after** `ctx = await nxt(ctx)`; `ctx.result` holds the return value, `ctx.agent.events.query(call_id=...)` reads the full episode. **Multi-subscriber** → composes with tracing, unlike `set_hooks`. |
| H4-alt | Post-task trigger (observation-only fallback) | **`on("AfterTurn", h)`** filtered to top-level final turn | `event_manager.py:184`; `AfterTurn.is_final` + `parent_generation_id` at `events.py:218-249` | Gate on `ev.is_final and ev.parent_generation_id is None` — the framework's own run-completion signal (strategy-agnostic across CodeAct/Predict/PurePython). No return value; schedule async work. |
| H5 | Episodic capture during a task (optional) | `on("Task"/"LLMOutput"/"PythonOutput", h)` | `event_manager.py:184`; events fired at `codeact.py:602`, `actor.py:1126`, etc. | Accumulate trajectory traces that H4 later consolidates. Use `on("*")` (`event_manager.py:254`) only with strict filtering to avoid write amplification. |
| H6 | Subsystem lifetime / wiring | **`MemoryManager.install(agent, config=...)`** classmethod | Mirrors `SummarizationAgent.install` (`summarization.py:84-112`): stores on `agent._memory` to tie lifetime; registers H1/H3/H4 subscriptions in `__init__` after `super().__init__()` | Returns an uninstall closure (model on `_uninstall`, `summarization.py:165-175`) that drops all `on`/`intercept` registrations. |
| H7 | Custom memory event persistence | `EventManager.register_event_type(cls)` | `event_manager.py:165` | Call at install so a `MemoryWritten`/`ReflectionCompleted` `EventBase` round-trips through SQLite. Emit with `add(ev, record=False)` or `Role.RUNTIME_EVENT` so it stays **out** of LLM context (`event_manager.py:142`). |

#### Critical correction to the upstream analysis (C1)
C1 claimed `ctx.agent.context["retrieved_memories"] = txt` sets "a static block via `ContextApi.__setitem__`". **This is wrong.** `ContextApi.__setitem__` routes to `set_dynamic` → the **volatile** partition (`context.py:63-68`, confirmed; `_static[key]=False`). Only `ContextManager.set_static` (no `ContextApi` accessor) creates a cacheable static block. This matters: per-turn-changing memory must be volatile anyway (correct here), but the design must not rely on `self.context[k]=v` for a cached prefix block.

#### Pinning placement
A fresh dynamic block appends after framework/strategy blocks, before the event log (`context_builder.py:120,249`). To pin it (e.g. right after `system_prompt`), the memory mixin's strategy would need `get_block_order()` to name the key (`context_builder.py:127`). Default placement is acceptable; pinning is a later refinement, not a hook requirement.

#### Needed hook that does NOT exist
There is **no turn-level middleware** and **no `before_first_turn` event**. Pre-turn injection therefore cannot be a "fire once before turn 1" middleware. **Two options, no core change needed:**
- **(Recommended)** H1 dynamic block — re-evaluates every turn automatically. To make it *spontaneous-on-state-change* rather than every-turn, the `_recall_for_context()` method self-gates (caches last query hash; returns the cached string if the working-set query is unchanged) so it only re-embeds/re-queries when the conversation moved.
- **(If true turn-level splicing is ever required)** `intercept("llm_call", mw)` (`actor.py:879/914`, `LLMCallContext.messages` mutable, `middleware.py:92-111`) — fires every turn; gate on first turn via your own counter. This is heavier and bypasses the eviction/budget machinery, so it is the fallback, not the default.

**Minimal framework change if a once-before-first-turn signal is wanted as a first-class hook:** add an `on("Task", ...)` consumer (the `Task` event already fires exactly once at task start, `codeact.py:602`) — this already exists, so **no change is actually required**; H3-style `on("Task")` is the clean "task just started, prime memory once" signal.

### 2. Public API shape (additive, opt-in)

Two layers, mirroring the framework's own opt-in convention ("don't install it" + an `enabled` flag, per C6).

**Layer A — `install()` (primary, fully additive).** No base-class change for existing agents:

```python
from nooa.memory import MemoryManager, MemoryConfig

agent = MyAgent(llm=llm)                       # unchanged, zero memory overhead
MemoryManager.install(agent, config=MemoryConfig(enabled=True, top_k=5))
```

`install` (classmethod, mirrors `summarization.py:84`):
1. constructs the manager, stores it on `agent._memory` (lifetime tie),
2. registers H1 dynamic block `agent.context.set_dynamic("recalled_memories", "agent._memory.recall_for_context()")` — note the expr references the manager, which is reachable because `self` is the agent and the manager is an attribute,
3. registers H3 `on(...)` write subscriptions, H4 `intercept("agent_call", ...)` dreaming, H7 `register_event_type`,
4. returns the manager (exposing `uninstall()`).

The **conscious tools** (H2) are delegating methods. Because `install` attaches the manager as `agent._memory` (hidden — `_`-prefixed), the four tools must be visible on the agent itself. Provide a thin **mixin** so they render in `doc(self)`:

```python
class MemoryToolsMixin:                          # opt-in by inheritance
    def remember(self, text: str, *, tags: list[str] | None = None) -> str: ...  # → self._memory.remember
    def recall(self, query: str, k: int = 5) -> list[Memory]: ...
    def search(self, query: str, k: int = 5) -> list[Memory]: ...
    def associate(self, a_id: str, b_id: str, relation: str = "related") -> None: ...

class MyAgent(MemoryToolsMixin, Agent, llm=llm): ...   # tools now in doc(self)
```

If `_memory` is absent (not installed), these raise a clear "memory not installed" error — so the mixin is harmless when memory is off. Each tool can be individually disabled via config (§3, `tools=`); a disabled tool is decorated `@hidden` at install time so it never appears in `doc(self)`.

**Layer B — `enabled` flag** on `MemoryConfig` for in-process toggling without un-installing (cheap on/off for A/B and ablations). When `enabled=False`: H1 returns `""`, H3/H4 short-circuit, tools raise/no-op.

**Why this shape, not a wrapper class or decorator:**
- A **wrapper class** would break the `class MyAgent(Agent, llm=llm)` ergonomics and the metaclass generation path (`metaclass.py:49`). Rejected.
- A **decorator** on the class can't register per-*instance* `on`/`intercept` (those need the live `event_manager`). Rejected.
- **`install()` + mixin** matches the two precedents (`SummarizationAgent.install`, `LibraryManager.install`) and the per-instance `event_manager` reality. The mixin is only for tool visibility; all wiring is in `install`. Internal manager fields are `Annotated[T, hidden]` (per `summarization.py:66-75`) so nothing leaks into `doc(self)`.

### 3. Configuration / hyperparameter surface

A frozen Pydantic `MemoryConfig` with `merge_with()`, mirroring `summarizer_config.py:8` (per C6), re-exported from `config/__init__.py`. Nested sub-configs keep the surface legible.

```python
class WritePolicy(BaseModel, frozen=True):
    on_events: tuple[str, ...] = ("Notification", "Error", "Message")  # which event types → write
    salience_min: float = 0.0          # gate: only write if salience score ≥ this
    dedup_window: int = 50             # skip near-duplicate of last N writes
    write_episodic: bool = True        # also persist task trajectories (H5)

class ReflectionPolicy(BaseModel, frozen=True):
    enabled: bool = True
    trigger: Literal["post_task", "after_turn_final", "manual"] = "post_task"  # H4 vs H4-alt
    only_top_level: bool = True        # gate on parent_generation_id is None / entrypoint method
    entrypoint_methods: tuple[str, ...] = ()  # () = any top-level; else restrict
    max_reflections_per_run: int = 1   # budget
    max_episodes_per_reflection: int = 50   # input cap
    max_new_memories_per_reflection: int = 10
    token_budget: int = 8000           # LLM budget per consolidation
    background: bool = True            # asyncio.create_task, don't block the return

class ScoringWeights(BaseModel, frozen=True):
    similarity: float = 1.0            # cosine
    recency: float = 0.3               # time-decay term
    importance: float = 0.2            # stored salience
    recency_half_life_hours: float = 168.0

class RetrievalConfig(BaseModel, frozen=True):
    top_k: int = 5                     # final returned per recall/context
    hops: int = 1                      # 0 = vector-only; 1 = +1 graph hop; 2+ = multi-hop
    per_hop_decay: float = 0.5         # multiply edge contribution per hop
    per_hop_fanout: int = 5            # neighbors expanded per hop
    min_similarity: float = 0.2        # cosine floor
    weights: ScoringWeights = ScoringWeights()
    context_char_budget: int = 2000    # hard cap on the H1 block string (self-bound; eviction is all-or-nothing, renderer.py:118)

class EmbeddingConfig(BaseModel, frozen=True):
    model: str = "nv-embedqa-e5-v5"    # registry alias; resolves api_base/api_key like get_llm_client (registry.py:237,70)
    endpoint: str | None = None        # override; default = NVIDIA gateway from the alias
    batch_size: int = 64
    dim: int | None = None             # None = infer from first response

class ChromaConfig(BaseModel, frozen=True):
    mode: Literal["persistent", "http", "ephemeral"] = "persistent"
    path: Path = get_project_dir("memory")   # honors NEMO_OO_PROJECT_DIR (paths.py:73)
    collection: str = "agent_memory"
    host: str | None = None            # http mode
    port: int | None = None

class MemoryConfig(BaseModel, frozen=True):
    enabled: bool = False              # master on/off (Layer B)
    chunk_size: int = 512              # tokens per memory chunk on write
    chunk_overlap: int = 64
    tools: tuple[str, ...] = ("recall", "search", "remember", "associate")  # which conscious tools are enabled (H2); others @hidden
    inject_context: bool = True        # register the H1 dynamic block at all
    context_block_key: str = "recalled_memories"
    retrieval: RetrievalConfig = RetrievalConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    chroma: ChromaConfig = ChromaConfig()
    write: WritePolicy = WritePolicy()
    reflection: ReflectionPolicy = ReflectionPolicy()
    metadata_db_path: Path | None = None  # graph/metadata store; default sibling of chroma.path
```

Full surface checklist mapped to the request: **enabled** (`MemoryConfig.enabled`), **chunk size** (`chunk_size`/`chunk_overlap`), **retrieval top-k** (`retrieval.top_k`), **hops 0/1/2+** (`retrieval.hops`), **per-hop decay** (`retrieval.per_hop_decay`), **scoring weights** (`retrieval.weights`), **which conscious tools** (`tools`), **embedding endpoint+model** (`embedding.model`/`endpoint`), **Chroma location/mode** (`chroma.*`), **write policy** (`write.*`), **reflection policy+budget** (`reflection.*`).

#### Storage split (per C5)
Do **not** overload `EventBackend`/`StorageManager`. Chroma owns vectors; a **new `MemoryStore` protocol** (own SQLite tables `memories`+`memory_edges`, own `schema_version`) owns metadata + the association graph (needed for `hops≥1` and `associate`). Reuse `serialization.py:69-100` (allowlist-secured) for payloads and lift the SQLite robustness helpers (`sqlite.py:131,177,339`). Join key: Chroma `id` == memory `id`. Embedding transport reuses `litellm.aembedding` over the existing NVIDIA gateway (no new key env var; `chromadb` is the only new dep — `uv add chromadb`).

### 4. Open design questions (prioritized — answer before in-depth design)

**P0 — block the design until resolved:**

1. **Spontaneous-association query derivation.** What is the retrieval query for the H1 block? `_prepare_context` exposes `self`, REPL locals, and `result` (`actor.py:1963-1985`) but there is no clean "current user intent" handle. Options: last `Task`/`Message` text via `agent.events`, last N events concatenated, or an embedding of recent REPL state. This choice dominates recall quality and is the single biggest unknown. **Risk:** a naive "embed the whole event log every turn" is O(turns) embedding calls per task → latency + cost blowup.

2. **Per-turn re-embedding cost vs. freshness.** H1 re-evaluates every turn (`actor.py:785`). Embedding + Chroma query on every turn adds latency to the critical path of *every* LLM call. Must we self-gate (cache by query hash, only re-query on state change), cap to first-turn-only, or move to an `on("Task")` once-per-task prime? Decide the gating policy and the SLA for added per-turn latency.

3. **Dreaming trigger granularity & re-entrancy.** `intercept("agent_call")` fires for **every** decorated method including nested subagent calls (`method_wrapper.py:213`). Confirm the gate (`only_top_level` + `entrypoint_methods`) and define behavior when memory-mixin agents are nested (does the child dream, the parent, both?). **Risk:** unbounded recursive dreaming; a dream that itself runs an agent that triggers a dream.

4. **Write amplification & salience.** Which events truly warrant a write? `on("*")` sees every `LLMComplete`/`PythonOutput`. Need a concrete salience function and dedup policy (`write.salience_min`, `dedup_window`) or the store grows unbounded and retrieval degrades.

**P1 — needed for correctness/robustness:**

5. **Concurrency & the SQLite session lock.** `SQLiteStorageManager` takes an exclusive process flock (`sqlite.py:600-637`). A shared cross-session memory DB must use a **separate** file/connection or it deadlocks against the agent's snapshot DB. Also: background dreams write while the next task reads — define isolation (WAL? per-write connection?).

6. **Background dreaming lifetime & failure.** `asyncio.create_task` dreams (like `summarization.py` does) can outlive the agent or be GC'd. Need the same pending-task tracking + cancel-on-uninstall the summarizer uses (`summarization.py:78-82,173-175`). What happens to a half-written dream on crash? Idempotency/transaction boundary?

7. **Snapshot interaction.** Should memory state participate in `SQLiteStorageManager` snapshots, or is it deliberately external/cross-session? The manager and its Chroma handle must be `Annotated[T, nosnapshot]` (`markers.py:36`) regardless, but the *memories themselves* are external — confirm restore semantics on `restore_snapshot`.

8. **Graph hops semantics.** For `hops≥1`, define the scoring composition: is a 1-hop neighbor's score `sim(seed) * per_hop_decay` or re-scored against the query? How are cycles handled? `hops=2` fanout can explode (`fanout^hops`) — confirm `per_hop_fanout` caps and whether edges are typed/filtered by `relation`.

**P2 — quality, ergonomics, observability:**

9. **Conscious vs. spontaneous overlap / confusion.** If H1 injects memories every turn AND the LLM can `recall()`, does the model double-count or get confused by stale injected context vs. fresh `recall`? Should the H1 block and `recall()` share a cache/dedup so the LLM isn't shown the same memory twice?

10. **Embedding model availability & dims.** `nv-embedqa-e5-v5` must be a registered alias on the gateway; if not, install must fail loudly. Dimension/model changes invalidate the Chroma collection — need a migration/version stamp on the collection.

11. **Tracing of memory ops.** Should `recall/remember`/dreams appear in OTLP traces (they're public methods → traced by default) or be `@no_trace` to cut noise (like `summarization.py:150`)? Decide per-op; dreams probably traced, per-turn H1 recall probably `@no_trace`.

12. **Block placement & token budget interaction.** The H1 block is first-evicted under pressure (`renderer.py:102`) — good — but it also means under a long task the memory silently vanishes. Confirm that's acceptable, or pin via `get_block_order()` (`context_builder.py:127`) and accept it competing with the task for budget.

#### Key file references (verified this session)
- Pre-turn dynamic block: `runtime/context.py:63-84`, `runtime/context_manager.py:102`, `runtime/actor.py:785,2653,2700,1924,1993-1997`; eviction `context_blocks/renderer.py:102,118`
- Conscious tools: `strategies/generated_code.py:74`, `agent.py:242`, `strategies/codeact.py:594-596,2245-2271`
- Events / middleware: `runtime/event_manager.py:120,165,184,251-258,262,309`; `runtime/middleware.py:59-148`; `runtime/method_wrapper.py:213`; `events.py:218-249,301`
- Install/toggle template: `agents/summarization.py:84-112,160-175,66-82`
- Config template: `config/summarizer_config.py:8`; paths `paths.py:73`; embeddings `unifiedllm/registry.py:237,70`
- Storage split: `storage/serialization.py:69-100`, `storage/sqlite.py:131,177,339,600-637`, `storage/markers.py:36`
