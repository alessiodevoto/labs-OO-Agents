# feat/tui-input-queues — morning notes

Per-turn `respond()` with unified queue/notification I/O. Tests green
(602 cli/runtime). Ready for you to drive the interactive TUI.

## Shape of the change

**Framework additions**
- `Notification(source, description)` — generic "something happened"
  event rendered into the LLM context. Not coupled to queues; any
  producer (timer, webhook, long-running job, queue push) can emit one.
- `InputQueue(name, agent)` — deque-backed async queue with
  `put`/`get`/`peek`/`snapshot`/`pop_last`/`has_waiters`/`qsize`. Emits
  a `Notification(source="queue:<name>", ...)` on every `put()`.
- `wait_for_any(queues)` — races multiple `.get()` coroutines,
  cancelling losers cleanly.

**Agent side (`BaseTUIAgent`)**
- `self.user_messages: InputQueue` lives on every TUI agent. Subclasses
  declare additional queues as instance attributes (e.g.
  `self.job_outputs = InputQueue("job_outputs", agent=self)`).
- `respond(notification, restored=None) -> RespondResult` — invoked
  *once per turn* by the outer dispatcher. `notification` is a
  `(queue_name, item)` pair; `restored` is whatever dict the previous
  turn returned via `persist`.
- `RespondResult` is now a Pydantic model with:
  - `kind: "GET_USER_INPUT" | "WAIT" | "STOP"` — tells the dispatcher
    what to do next.
  - `persist: dict[str, Any]` — variables (name → value) to carry to
    the next turn's `restored` kwarg. Empty = clean slate next turn.
- The LLM builds and returns via `return_result(RespondResult(kind=..., persist=...))`.
  No new strategy, no new tool — just the standard `return_result`
  carrying a structured value.

**Dispatcher (`TUIApplication`)**
- On session start: no task running.
- First user message → `submit_message` pushes to
  `agent.user_messages`, then lazy-starts the dispatcher task.
- Dispatcher loop:
  1. `await agent.user_messages.get()` (first item)
  2. `result = await agent.respond((queue_name, item), restored=...)`
  3. `result.kind == "STOP"` → exit
  4. `result.kind == "WAIT"` → `wait_for_any(all declared InputQueues)`
  5. `result.kind == "GET_USER_INPUT"` (default) → `await user_messages.get()`
  6. goto 2 with `restored=result.persist`
- Slash/bang commands still dispatch immediately via
  `on_command`/`on_bang` — they don't touch the agent's queues.
- `is_thinking()` = dispatcher task alive AND
  `user_messages.has_waiters()` is False. Spinner idles out while
  the dispatcher is between turns awaiting input.
- Esc/Ctrl-C cancels the dispatcher. If messages remain queued,
  dispatcher lazy-restarts (queued input is not stranded).

**Rendering**
- `AgentEventRenderer._render_message` is back to the original
  buffered-until-PythonOutput contract. Per-turn `respond()` gives us
  natural `PythonOutput` cadence again — no await-blocks-flush
  problem.

## Touched files

- `src/nemo_oo_agents/events.py` — `Notification(source, description)`.
- `src/nemo_oo_agents/runtime/input_queue.py` — emits new Notification shape.
- `src/nemo_oo_agents/strategies/` — `ScopedCodeActStrategy` deleted
  (not needed; per-turn teardown gives clean REPL state for free).
- `src/nemo_oo_agents_cli/tui/agent.py` — `RespondResult` Pydantic
  model, per-turn `respond(notification, restored)`, system-prompt
  docstring rewritten for the new pattern, `get_next_input` and
  `wait_for_input` removed.
- `src/nemo_oo_agents_cli/tui/tui_application.py` — dispatcher loop
  replaces the forever-loop lazy-start; `submit_message` unchanged
  from the user's POV.
- `src/nemo_oo_agents_cli/tui/agent_event_renderer.py` — back to
  buffered rendering.
- `src/nemo_oo_agents_cli/tui/skills-sw/*.md` — 23 sites updated
  (`return_result(RespondResult(kind="GET_USER_INPUT"))`).
- Tests: `tests/cli/test_tui_input_queue_wiring.py` reworked for the
  dispatcher contract; `tests/cli/tui_app_harness.py` `FakeAgent`
  matches per-turn shape; behaviour/coverage/integration tests updated
  signatures.

## How to drive it in the TUI

Launch as before. Type a message — the dispatcher starts, `respond()`
runs one turn, returns a `RespondResult`, and the dispatcher decides
what to wait on next.

Watch for:
1. **Notifications render correctly.** Each queue push should emit
   a `<notification source="queue:user_messages" ...>` event into the
   LLM's context.
2. **`persist` round-trips.** The LLM returns
   `RespondResult(kind="GET_USER_INPUT", persist={"plan": plan})`;
   the next turn's `restored` dict should contain the same value.
3. **`WAIT` races queues.** If you wire a second queue (e.g.
   `self.job_outputs`) and the LLM returns `kind="WAIT"`, the
   dispatcher should pick up the first arrival from either queue.
4. **Idle-vs-thinking.** The spinner should be gone while the
   dispatcher is awaiting `user_messages.get()` between turns.
5. **Esc soft-cancel.** If you hit Esc mid-generation, the dispatcher
   cancels; typing a new message restarts it.

## Known limitations / follow-ups

- **Model switching**: `actor.py` captures `agent._llm` at method-
  invocation time. In the per-turn model, each `respond()` call is a
  fresh invocation, so `/model` now DOES take effect on the next
  turn — the gap I flagged earlier is actually closed by this design.
- **True "stop without restart"**: Esc still lazy-restarts if queued
  messages exist. For "stop the loop even though I have stuff queued",
  we'd need a `/stop` command or a modifier keybinding. ~10 lines.
- **Skill markdowns** use a bare `kind="GET_USER_INPUT"` with no
  persist. If a skill needs to hand state forward, the prose in the
  skill needs to mention that — a real pass over the skill files is
  still a follow-up.
- **`orchestrator` config flag**: unused, still accepted. Delete in a
  follow-up cleanup.

## Running tests

- Fast feedback: `uv run pytest src/nemo_oo_agents/runtime/tests/test_input_queue.py tests/cli/test_tui_input_queue_wiring.py -v`
- Full TUI suite: `uv run pytest tests/cli/`
