# Design: `agent006.print_prompt` runtime API

## Goal

```python
agent = MyAgent(llm=client)
# ... lots of things happen
await agent006.print_prompt(agent.analyze, my_data)
```

Renders the three prompt sections that would be sent to the LLM for a given agent method — without triggering an actual LLM call. Uses real agent state and real argument values. Useful for prompt debugging, evaluation prep, and sanity-checking agent definitions.

Output sections:
1. **System prompt** — the formatted system message (framework blocks: `_system_prompt()` + `doc(self)` etc.)
2. **Task prompt** — the task user-message built by the strategy's `_build_task_message()` from the method docstring
3. **Prefill** — pre-execution code blocks injected before the first LLM turn (CodeAct only: `InspectInputsPrefill` + pre-ellipsis code)

---

## Runtime API (only)

```python
await agent006.print_prompt(agent.analyze, my_data)

data = await agent006.build_prompt_data(agent.analyze, my_data)
print(data.task_prompt)
```

### Why runtime-only (no static CLI)

A static CLI (`agent006 prompts agent.py MyAgent.analyze`) was considered and designed but dropped because:

- **Decorator-scoped blocks are invisible** at analysis time — blocks set via `@strategy(ScopedContext(context={...}))` only exist during a live method call
- **Dynamic blocks render cold** — `DynamicContext` expressions evaluated against a fresh agent have no accumulated state
- **Task prompt shows templates, not values** — the CLI would show `{data}` placeholders, not the actual argument values

The runtime API avoids all of these: the agent is already instantiated, context blocks have accumulated, and real argument values are available.

---

## API Reference

### `agent006.print_prompt`

```python
async def print_prompt(method: Any, /, *args: Any, **kwargs: Any) -> None:
```

Writes plain-text prompt sections to stdout.  `*args` and `**kwargs` are the arguments the method would be called with — they are forwarded to `build_prompt_data` and from there to the agent's context pipeline, so real argument values appear in the prefill and task prompt.

### `agent006.build_prompt_data`

```python
async def build_prompt_data(method: Any, /, *args: Any, **kwargs: Any) -> PromptData:
```

Returns a `PromptData` without printing.  Use this when you want to inspect sections programmatically rather than print them.

Note: calling this on a non-`...` method (not a generation method) still returns `PromptData`, but the output may not be meaningful.

Note: calls `agent.runtime._build_messages()` internally, which updates the agent's `DynamicContext` resolved-value cache as a side effect. Avoid using with speculative arguments mid-session if your agent relies on cached DynamicContext values between turns.

### `agent006.PromptData`

```python
@dataclass(frozen=True)
class PromptData:
    system_prompt: str | None   # Formatted system message (None if unavailable)
    task_prompt: str            # Task user-message from strategy template
    inspect_prefill: str | None # InspectInputsPrefill code (CodeAct/CodeActLite only)
    pre_ellipsis: str | None    # Setup code before ... in method body
    strategy_name: str          # e.g. 'CodeActStrategy'
    method_path: str            # e.g. 'MyAgent.analyze'
```

---

## Output format

Plain text with `===` section headers, written to stdout:

```
=== SYSTEM PROMPT  [MyAgent] ===

<system_prompt>
You are MyAgent, a Python agent working in an interactive session.
...
</system_prompt>

=== TASK PROMPT  [MyAgent.analyze] ===

## Task: analyze

Analyze hello world and return results.

=== PREFILL  [CodeActStrategy] ===

reasoning("""Inspecting inputs for analyze().""")
print("Task: analyze()")
print(f"\ndata ({type(data).__name__}):")
pprint(data, max_length=50, max_string=500, max_depth=4)
```

---

## Implementation

### Core builder (`src/agent006/prompts.py`)

```python
async def _build_prompt_data_from_agent(agent, wrapper, original_func, args, kwargs):
    strategy = getattr(wrapper, "_plan_strategy", None) or get_default_strategy()
    call = CurrentCall.from_method(original_func, args=args, kwargs=kwargs)

    # _build_messages falls back to _current_strategy_var for methods using
    # the default strategy (no explicit @strategy decorator).
    token = _current_strategy_var.set(strategy)
    try:
        messages = await agent.runtime._build_messages(wrapper, args, kwargs)
    finally:
        _current_strategy_var.reset(token)

    system_prompt = next((m["content"] for m in messages if m["role"] == "system"), None)
    task_prompt = await _get_task_prompt(agent.runtime, strategy, call)
    inspect_code, pre_ellipsis = _get_prefill(strategy, call)
    return PromptData(...)
```

### Task prompt

`_get_task_prompt` reads `_build_task_message.__doc__` from the original (unwrapped) function on the strategy class and expands `{original_call.X}` placeholders via `await runtime.expand_variables(template, {"original_call": call})` — the same expansion path used during a real call. Only bound methods are auto-called when referenced without `()` in the template — class objects and other callables are left as-is to avoid silently invoking type constructors.

Falls back to `call.docstring` when the strategy has no `_build_task_message` or when the rendered template is empty.

### Prefill

- **CodeActStrategy / CodeActLiteStrategy**: `InspectInputsPrefill().get_code(call)` + `call.pre_ellipsis_code`
- **PurePythonStrategy**: user-configured `strategy.prefill.get_code(call)` if set + `call.pre_ellipsis_code`
- **Other strategies**: `(None, call.pre_ellipsis_code)` — pre-ellipsis is never silently dropped

---

## File layout

```
src/agent006/prompts.py   # Core helpers + print_prompt() + build_prompt_data()
```

`print_prompt`, `build_prompt_data`, and `PromptData` are exported from `agent006/__init__.py`.

---

## Dependencies

All reused from existing framework — no new dependencies:

| Import | Used for |
|--------|----------|
| `agent006.runtime.actor._current_strategy_var` | Strategy contextvar for `_prepare_context` |
| `context_blocks.render_context` | System prompt message formatting |
| `agent006.strategies.current_call.CurrentCall` | Task + prefill construction |
| `agent006.strategies.prefill.InspectInputsPrefill` | Prefill code generation |
| `string.Formatter` | Template variable expansion (stdlib) |
