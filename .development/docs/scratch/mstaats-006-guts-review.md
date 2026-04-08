# Agent006 Guts Review — Conversation Notes

## 1. The `...` (Ellipsis) Methods — Metaprogramming

There's metaprogramming to identify ellipsis methods and wrap them with hooks that trigger LLM generation.

- **Class creation**: `AgentMeta.__new__()` in `metaclass.py` runs when Python processes the class definition. It iterates every method in the class namespace.
- **Ellipsis detection**: For each async method, it calls `has_ellipsis_body()` from `ellipsis_detection.py`. This inspects the function's actual source code (via `inspect.getsource`) to check if the body is literally `...`. It's parsing the AST of the function body, not checking the return value.
- **Wrapping**: If the body is `...`, the metaclass calls `create_agent_method_wrapper()` from `runtime/method_wrapper.py`, which replaces the original method with a wrapper. That wrapper is what actually runs when you call `await agent.some_method()`.
- **Nuance**: The metaclass skips methods that already have `_agent_decorator` set — meaning if you used `@strategy` on a method, the decorator already did the wrapping itself. The metaclass only picks up "bare" ellipsis methods. Both paths end up going through the same `create_agent_method_wrapper()`.

The wrapper routes the call: is this a generation call? Am I already inside a generation session? Based on that, it either calls `runtime._call_plan()` (new session) or `runtime._execute_task()` (nested inside an existing session), or falls through to direct execution if no generation is needed.

---

## 2. Context Blocks — Building the LLM Prompt

Blocks are a composable template for building the system message and conversation history.

### Mapping to traditional LLM concepts

```
Traditional LLM call:
  system_message  = "You are a ..."
  messages        = [{role: user, ...}, {role: assistant, ...}, ...]

nemo_oo_agents equivalent:
  CONTEXT BLOCKS  → rendered together → become the system message
  EVENT BLOCKS    → rendered          → become the conversation messages
```

### The specific default blocks

| Block | Evaluates | What it is |
|-------|-----------|------------|
| `system_prompt` | `self._system_prompt()` | Traditional system prompt. "You are an AI Agent that exists as a Python object of type X..." |
| `self` | `doc(self)` | Auto-generated documentation of the agent: docstring, public methods with signatures, attributes. How the LLM knows "what am I, what can I do?" |
| `context_api` | `doc(self.context, concise=True)` | API docs for `self.context` so LLM-generated code knows it can call `self.context.set("key", value="...")` etc. Only shown for PURE_PYTHON/CODEACT strategies. |
| `events_api` | `doc(self.events, concise=True)` | API docs for the events system. Same visibility rule. |
| `events` | `self.events.query(limit=50)` | The conversation history. Last 50 events formatted as user/assistant messages. |

### What the final LLM call looks like

```
System message:
  <system_prompt> You are an AI Agent... </system_prompt>
  <self> class MyAgent: ... (auto-documented) </self>
  <context_api> self.context.set(...), ... </context_api>
  <events_api> self.events.query(...), ... </events_api>

User/Assistant messages (from events):
  [user]      Task: "analyze the given data..." topic="quantum"
  [assistant] LLMOutput: x = self.fetch(topic) ...
  [user]      PythonOutput: stdout="fetched 42 results"
  [assistant] LLMOutput: return_result(summary)
```

### Why blocks instead of hardcoding?

Each block can be overridden, hidden, or removed at any layer — class, instance, method, or at runtime by the LLM's own code. For example, `@strategy(blocks={"events": None})` makes a stateless method that sees no history. It's a composable prompt system rather than a hardcoded one.

Manipulation levels:
1. **Class level**: `class MyAgent(Agent, blocks={"system_prompt": Block(expr="'Custom prompt'")})`
2. **Instance level**: `MyAgent(blocks={"context_api": None})`
3. **Method level**: `@strategy(blocks={"extra": Block(expr="self.domain_knowledge")})`
4. **Runtime** (from LLM-generated code): `self.context.set("notes", value="Remember X for later")`

`None` removes a block, `show="False"` hides it conditionally, `protected=True` prevents the LLM from messing with it.

---

## 3. Events — Conversation History

The event types are a categorization layer on top of the standard user/assistant message pairing that LLMs expect.

| Event | Role | When created |
|-------|------|-------------|
| `Task` | USER | Strategy starts — contains method docstring + args |
| `LLMOutput` | ASSISTANT | LLM returns generated code |
| `PythonOutput` | USER | Code executes — stdout, return value, captured locals |
| `Error` | USER | Execution failed — traceback sent back to LLM |
| `Feedback` | USER | No return yet — hints for LLM to continue |
| `Message` | ASSISTANT | Agent calls `message()` to talk to user |
| `Reasoning` | ASSISTANT | Agent calls `reasoning()` for chain-of-thought |
| `Summary` | ASSISTANT | Collapsed older events |

### Routing

When the LLM calls `message("Here's what I found")` or `reasoning("Let me think...")` in generated code, those create events that get emitted through the `EventManager`. You can subscribe:

```python
agent.event_manager.on("message", my_ui_handler)
agent.event_manager.on("reasoning", my_logging_handler)
```

Events serve double duty — they're both the conversation history for the LLM *and* the pub/sub mechanism for external consumers.

---

## 4. The Generation Loop

### Flow

1. We see the `...`, the wrapper fires before hooks, starts generation.
2. **Resolve strategy** — determines *how* the LLM generates and returns code.
3. **Resolve LLM** — determines which model to use (method-level > instance-level > class-level).
4. **Apply block logic** — determine what we'll send to the LLM.
5. **Loop**: Get code from LLM, execute, check result. Continue until the LLM explicitly returns a value.

### Strategies

A strategy controls the entire interaction protocol with the LLM:

| Strategy | How it works |
|----------|-------------|
| **CodeActStrategy** (default) | LLM uses tool calling (`execute_python`) to submit code. Supports structured output. Most flexible. The LLM is an *agent* — it decides when it's done. It might explore in turn 1, process in turn 2, return in turn 3. |
| **PurePythonStrategy** | LLM response text *is* the code. Framework strips markdown fences, executes it. Simpler, more mechanical. |
| **StructuredOutputStrategy** | No code at all. LLM returns JSON matching the return type schema directly. One shot. |
| **ReflexionStrategy** | Wraps another strategy with self-reflection after each attempt. |

**CodeAct vs PurePython**: The key difference is the transport mechanism — raw text vs tool calls. CodeAct uses the LLM's tool calling API (`execute_python` tool), PurePython extracts code from the response text. CodeAct is more flexible because tool calling has better parsing guarantees and naturally supports structured output.

**CodeAct is the CodeAct paper pattern** ([arxiv.org/abs/2402.01030](https://arxiv.org/abs/2402.01030)): The LLM acts through code, observes results, acts again. The loop doesn't end because the code "worked" — it ends because the LLM explicitly chose to return via `return_result()`.

### The loop in detail

Each iteration is a full LLM turn:
- LLM sees all prior events (its previous code, outputs, errors)
- Generates new code informed by what happened
- Code executes
- Three outcomes:
  - **Error** → Error event, loop continues
  - **No return** → Feedback event ("you didn't return yet"), loop continues
  - **Return** → validate the type, done

The loop has a max iterations cap (per strategy config) so it doesn't run forever.

---

## 5. Code Execution

### How it works

It's `exec()` in the same process. The LLM returns a string of Python code, the framework wraps it in an async function, and runs `exec(compile(wrapper, ...), exec_globals)` right there in the same Python process. No subprocess, no sandbox.

From `actor.py:793`:
```python
exec(compile(wrapper, cell_filename, "exec"), exec_globals)
```

### The namespace (`exec_globals`) is built from:
- The agent's module `__dict__` (so all imports from the agent's .py file are available)
- `self` = the agent instance
- Strategy builtins: `reasoning()`, `message()`, method parameter values
- Session locals from previous turns

### Session lifecycle

**New session per method invocation.** `GenerationSession` is created fresh each time a `...` method is called. It has `session_locals: dict = {}` that starts empty. Call the same method twice, second call gets a fresh session.

### REPL persistence across turns (within one invocation)

The code is wrapped in:

```python
async def __repl_wrapper__():
    global x, y, z  # session vars from prior turns
    try:
        <LLM's code here>
    finally:
        __repl_captured_locals__.update({
            k: v for k, v in locals().items()
            if not k.startswith('_')
        })
```

After execution, `_extract_captured_locals()` pulls those out, and the strategy merges them into `session.session_locals`. Next turn, those locals get injected as builtins into `exec_globals` and declared as `global` so assignments work. It's literally a `dict[str, Any]` — variable name to Python object.

### Isolation

**Not a subprocess** — it's `contextvars.ContextVar`. Stdout/stderr are wrapped in `ContextVarStream` so each async task gets its own capture buffer. Stdin is blocked. There's a timeout via `asyncio.wait_for`. But the code runs in-process with full access to everything — filesystem, network, whatever. The "isolation" is just about parallel async tasks not stomping on each other's output, not about security sandboxing.

### What is ContextVar?

`contextvars.ContextVar` is a Python stdlib feature that gives thread-local-style storage but for async tasks instead of threads.

The problem: if you have 10 `asyncio.Task`s running concurrently in the same process and they all `print()` to `sys.stdout`, everything gets interleaved.

`ContextVar` lets each task have its own value for a variable. When task A sets `my_var.set(buffer_a)` and task B sets `my_var.set(buffer_b)`, each sees its own value when calling `my_var.get()` — even though they share the same event loop in the same thread.

In nemo_oo_agents, `sys.stdout` is replaced once with a `ContextVarStream` wrapper. When code does `print("hello")`, the wrapper checks the `ContextVar` to find the current task's buffer and writes there. No buffer set? Falls through to real stdout.

It's the async equivalent of thread-local storage.

### Raw text

The Big Picture
Agent006 is a code-generating agent framework where you write Python methods with ... (ellipsis) bodies, and an LLM fills in the implementation at runtime. The framework manages the prompt, executes generated code, captures results, and loops until the task is done.

1. What happens when you write ...

class MyAgent(Agent):
    async def analyze(self, data: str) -> str:
        """Analyze the given data and return insights."""
        ...
At class creation time, the AgentMeta metaclass (metaclass.py) iterates over all methods. For each one, it calls has_ellipsis_body() (ellipsis_detection.py) to check if the body is literally .... If so, it wraps the method with create_agent_method_wrapper() (runtime/method_wrapper.py), which replaces it with a wrapper that routes to the runtime for LLM-powered generation instead of executing the ellipsis.

2. Context Blocks — Building the LLM prompt
The LLM needs to know what to generate. That comes from blocks — named, dynamically-evaluated sections of the prompt.

Default blocks are defined in agent.py:38-75:

Block	What it provides	Evaluates to
system_prompt	Base instructions	self._system_prompt()
self	Agent's docstring + method signatures	doc(self) — auto-documented API
context_api	Docs for self.context	Shown only in PURE_PYTHON/CODEACT strategies
events_api	Docs for self.events	Same visibility rule
events	Conversation history	self.events.query(limit=50)
Each block has an expr (Python expression), update (re-evaluate each turn?), show (visibility condition), and protected flag.

Layers of override (later wins):

Library defaults → 2. Class-level blocks={} → 3. Constructor blocks={} → 4. @strategy decorator blocks → 5. Strategy's own get_block_overrides()
When it's time to call the LLM, ActorRuntime._build_messages() (runtime/actor.py) renders all blocks: context blocks become the system message, event blocks become the conversation history.

3. Events — The conversation memory
There is no traditional message array. Instead, an event log serves as conversation history.

The event types live in events.py:

Event	Role	When created
Task	USER	Strategy starts — contains method docstring + args
LLMOutput	ASSISTANT	LLM returns generated code
PythonOutput	USER	Code executes — stdout, return value, captured locals
Error	USER	Execution failed — traceback sent back to LLM
Feedback	USER	No return yet — hints for LLM to continue
Message	ASSISTANT	Agent calls message() to talk to user
Reasoning	ASSISTANT	Agent calls reasoning() for chain-of-thought
Summary	ASSISTANT	Collapsed older events
The EventManager (runtime/event_manager.py) stores events in an InMemoryBackend (runtime/event_backend.py). Each event gets a tag ("1", "2", etc.) and a role (USER/ASSISTANT). When building messages, USER-role events become user messages, ASSISTANT-role events become assistant messages — creating a proper multi-turn conversation for the LLM.

The events block evaluates self.events.query(limit=50), so the LLM sees the last 50 events. Older events can be collapse()d into a Summary node (tag becomes "1..40") to save context window.

4. The Generation Loop — What actually happens when you call a ... method
Here's the complete flow:


await agent.analyze("some data")
        │
        ▼
┌─ Method Wrapper ──────────────────────────────┐
│  Push call_id onto stack                       │
│  Fire before_agent_call hook                   │
│  Not in generation session? → _call_plan()     │
│  Already in session? → _execute_task()         │
└────────────────────┬──────────────────────────┘
                     ▼
┌─ _execute_with_generation() ──────────────────┐
│  Resolve strategy (decorator > default)        │
│  Resolve LLM (decorator > agent._llm)          │
│  Set _in_generation_session = True             │
│  Apply scoped block overrides                  │
│  → strategy.execute(runtime, call)             │
└────────────────────┬──────────────────────────┘
                     ▼
┌─ Strategy.execute() LOOP ─────────────────────┐
│                                                │
│  1. Add Task event (docstring + args)          │
│  2. ┌──── WHILE not exhausted ────────────┐    │
│     │                                     │    │
│     │  a. runtime.generate()              │    │
│     │     → render blocks → system msg    │    │
│     │     → render events → user/asst msgs│    │
│     │     → call LLM                      │    │
│     │     → store LLMOutput event         │    │
│     │                                     │    │
│     │  b. runtime.execute_code(code)      │    │
│     │     → validate (security, async)    │    │
│     │     → AST parse + transform         │    │
│     │     → wrap in async function        │    │
│     │     → execute with captured stdout  │    │
│     │     → collect ExecutionResult       │    │
│     │                                     │    │
│     │  c. Check result:                   │    │
│     │     ERROR → Error event → continue  │    │
│     │     RETURN → validate type → DONE   │    │
│     │     NO RETURN → Feedback → continue │    │
│     │                                     │    │
│     └─────────────────────────────────────┘    │
│                                                │
│  Return validated result                       │
└────────────────────────────────────────────────┘
5. Code Execution Details
When runtime.execute_code() runs LLM-generated Python (runtime/actor.py):

Namespace setup: The code gets access to self (the agent), asyncio, doc(), message(), reasoning(), method parameters by name, plus any strategy-provided builtins

REPL persistence: Captured locals from each turn are merged into session.session_locals and injected as globals in the next turn. Variables survive across LLM turns — it's like an interactive Python session

Implicit return: If the last statement is an expression with no explicit return, the framework adds one (like IPython/Jupyter)

return_result(): A special function that raises ExecutionSignal (a BaseException subclass). This can't be caught by except Exception: in LLM code, ensuring the result always propagates up

Isolation: ContextVar-based stdout/stderr capture, blocked stdin, timeout enforcement, async-safety guards

6. Strategies — Different generation modes
All strategies inherit from GenerationStrategy (strategies/base.py):

Strategy	How it works
CodeActStrategy (default)	LLM uses an execute_python tool call, supports structured output
PurePythonStrategy	LLM returns raw Python code, REPL-style
StructuredOutputStrategy	LLM returns JSON matching the return type schema
TemplateStrategy	String templating, no LLM loop
ReflexionStrategy	Wraps another strategy with self-reflection
The @strategy decorator (decorators.py) attaches strategy metadata to a method:


@strategy(PurePythonStrategy(max_iterations=10))
async def my_method(self) -> str:
    ...
Priority: call-level _strategy= kwarg > @strategy decorator > get_default_strategy() (CodeAct).

7. Putting it all together — A concrete example

class Researcher(Agent):
    """Research agent that finds information."""

    async def research(self, topic: str) -> str:
        """Research the given topic and return a summary."""
        ...
When you call await researcher.research("quantum computing"):

Wrapper pushes call ID, fires hooks
Runtime resolves CodeActStrategy (default) and the configured LLM
Strategy adds a Task event: "Research the given topic and return a summary. topic='quantum computing'"
Turn 1: LLM sees system prompt (agent docs, API docs) + the Task event → generates Python code via execute_python tool call → code runs, stdout captured → PythonOutput event recorded → no return yet → Feedback event added
Turn 2: LLM sees all prior events + feedback → generates more code → maybe calls return_result("Here's what I found...") → ExecutionSignal raised → result validated against str return type → done
Result propagates back, hooks fire, "Here's what I found..." returned to caller
The LLM essentially gets a persistent Python REPL with the agent instance, sees its own history through events, and keeps going until it calls return_result() or hits the iteration limit.

Let's talk through these piece by piece. First,#1: .... Basically there's some metaprogramming to identify this and wrap it with the "hook" to make that actually execute things. Correct, incorrect, am I missing details?
Thu Feb 12 15:36:00 CET 2026

Correct — and here are the details worth knowing:

Step 1: Class creation — AgentMeta.__new__() in metaclass.py runs when Python processes your class definition. It iterates every method in the class namespace.

Step 2: Ellipsis detection — For each async method, it calls has_ellipsis_body() from ellipsis_detection.py. This inspects the function's actual source code (via inspect.getsource) to check if the body is literally .... It's not checking the return value — it's parsing the AST of the function body.

Step 3: Wrapping — If the body is ..., the metaclass calls create_agent_method_wrapper() from method_wrapper.py, which replaces the original method with a wrapper. That wrapper is what actually runs when you call await agent.some_method().

One nuance: the metaclass skips methods that already have _agent_decorator set — meaning if you used @strategy on a method, the decorator already did the wrapping itself. The metaclass only picks up "bare" ellipsis methods that weren't explicitly decorated.

So the metaclass is the catch-all, and @strategy is the explicit opt-in. Both end up going through the same create_agent_method_wrapper() — they just arrive there by different paths.

The wrapper itself is what does the actual routing: is this a generation call? Am I already inside a generation session? Based on that, it either calls runtime._call_plan() (new session) or runtime._execute_task() (nested inside an existing session), or falls through to direct execution if no generation is needed.

That's the full picture for #1. Want to move to #2 (context blocks)?

#2: I don't get this at all, this concept mapping is new. I normally think of an llm prompt as (1) system prompt, the big magic prompt I give as part of setting an API (2) a user prompt, i.e. "you are a blah blah blah agent...". Then there's usually a conversation history that follows all that.

I see here you're listing (1) system_prompt, no idea where this would come from? (2) self = the docstring + method, that makes sense I guess, that's us telling the LLM what we want our method to do (3) context_api ? dunno where this comes from (4) events_api ? same dunno where this comes from? (5) events = maybe the conversation history?

Help me understand, look at design principles if you need to.
Thu Feb 12 15:38:00 CET 2026

Let me look at the actual code to give you a concrete answer rather than an abstract one.

Read agent.py
Grep "design.principles|design_principles|DESIGN" (in /Users/mstaats/git-repos/nemo_oo_agents/durability, glob: *.md)
2 lines of output
Grep "_system_prompt" (in /Users/mstaats/git-repos/nemo_oo_agents/durability/src/nemo_oo_agents/agent.py)
3 lines of output
Grep "design_principles|design.principles|DESIGN_PRINCIPLES" (in /Users/mstaats/git-repos/nemo_oo_agents/durability, glob: *.{py,md})
2 lines of output
Globpattern: "**/block-customization*.md"
Found 1 file
Read agent.py (lines 389-438)
Read block-customization-api.md
Grep "def _build_messages|def _render|ContextSpec|BlockRenderer" (in /Users/mstaats/git-repos/nemo_oo_agents/durability/src/nemo_oo_agents/runtime/actor.py)
6 lines of output
Read actor.py (lines 1954-2033)
OK. Here's the mapping to what you already know:

Your mental model is right, blocks just reorganize the same concepts.

Think of the blocks system as a composable template for building the two things you already understand: the system message and the conversation history.

How it maps

Traditional LLM call:
  system_message  = "You are a ..."
  messages        = [{role: user, ...}, {role: assistant, ...}, ...]

nemo_oo_agents equivalent:
  CONTEXT BLOCKS  → rendered together → become the system message
  EVENT BLOCKS    → rendered          → become the conversation messages
The specific blocks
system_prompt — This is literally self._system_prompt(), defined at agent.py:391-401:


def _system_prompt(self) -> str:
    return f"""You are an AI Agent that exists as a Python object of type `{self.__class__.__name__}`.

You process requests via **generation methods** — methods on your class that you implement
using your capabilities as an LLM. Your goal is to return the correct value matching the
method's return type.

Python syntax provides strongly-typed I/O. The method signature defines the contract;
you decide how to fulfill it."""
That's your traditional system prompt. Nothing mysterious.

self — Evaluates doc(self), which auto-generates documentation of the agent: its docstring, all its public methods with signatures, its attributes. This is how the LLM knows "what am I, what can I do?" Think of it as the agent describing itself to the LLM.

context_api — Evaluates doc(self.context, concise=True). This documents the self.context API so the LLM-generated code knows it can call self.context.set("key", value="...") etc. It's API docs for one of the tools the LLM can use in its generated code.

events_api — Same idea, doc(self.events, concise=True). Documents the events API so the LLM knows it can query its own history if needed.

events — Yes, this is the conversation history. self.events.query(limit=50) returns the last 50 events, which get formatted as user/assistant messages based on each event's role.

So the final LLM call looks like

System message:
  <system_prompt> You are an AI Agent... </system_prompt>
  <self> class MyAgent: ... (auto-documented) </self>
  <context_api> self.context.set(...), ... </context_api>
  <events_api> self.events.query(...), ... </events_api>

User/Assistant messages (from events):
  [user]      Task: "analyze the given data..." topic="quantum"
  [assistant] LLMOutput: x = self.fetch(topic) ...
  [user]      PythonOutput: stdout="fetched 42 results"
  [assistant] LLMOutput: return_result(summary)
Why blocks instead of just hardcoding this?
Because each block can be overridden, hidden, or removed at any layer — class, instance, method, or even at runtime by the LLM's own code. The design doc at block-customization-api.md spells this out. For example, you could do @strategy(blocks={"events": None}) to make a stateless method that sees no history, or add a custom block with domain-specific instructions, all without touching the base agent code.

It's a composable prompt system rather than a hardcoded one.

Ok, so the system prompt = you are an AI agent, here's you class info, and you can call a context + events tool. And then events is just traditional history.

And behind the scenes, I have the ability to manipulate this if needed?
Thu Feb 12 15:40:00 CET 2026

Exactly right on both counts.

The system prompt is: "here's who you are, here's your class, here's the tools you can use in generated code." The events are just conversation history. That's it.

And yes — you can manipulate every piece of it at multiple levels:

Class level: class MyAgent(Agent, blocks={"system_prompt": Block(expr="'Custom prompt'")})
Instance level: MyAgent(blocks={"context_api": None}) — e.g. remove the context API docs for a specific instance
Method level: @strategy(blocks={"extra": Block(expr="self.domain_knowledge")}) — add a block only for one method
Runtime (from LLM-generated code itself): self.context.set("notes", value="Remember X for later") — the agent can add blocks to its own prompt mid-execution
And None removes a block, show="False" hides it conditionally, protected=True prevents the LLM from messing with it.

So you'd rarely need to touch this for basic agents — the defaults work. But when you need to inject domain knowledge, strip out unnecessary context to save tokens, or let the agent modify its own prompt, the machinery is there.

Want to move on to #3 (events / conversation history)?

#3 events: this is the events we talked about previously, yes? The types there are us categorizing types, and this maps to the user/assistant pairing so we can interact with LLMs. In practice PythonOutput, Error, etc, are just us translating CodeAct into responses. Reasoning, Message, etc are specific response types from the LLM, which we probably route appropriately.
Thu Feb 12 15:42:00 CET 2026

Yes, that's exactly right. The event types are just a categorization layer on top of the standard user/assistant message pairing that LLMs expect.

The one detail worth adding: the routing you mentioned. When the LLM calls message("Here's what I found") or reasoning("Let me think about this...") in its generated code, those create Message and Reasoning events respectively. Those events get emitted through the EventManager, and you can subscribe to them:


agent.event_manager.on("message", my_ui_handler)
agent.event_manager.on("reasoning", my_logging_handler)
So message() is how the agent "talks to the user" during execution, and you hook into that to actually display it in whatever UI you have. The events serve double duty — they're both the conversation history for the LLM and the pub/sub mechanism for external consumers.

That's basically it. Shall we do #4, the generation loop / strategies?

Ok, to paraphrase #4 generation loop and tell me where I'm wrong:

1) We see the ..., this wraps the method, fires before hooks, and starts generation.
2) Resolve strategy..... this does what? What would different strategies do?
3) Resolve LLM, same question, what is this? What could a decorator do?
4) Apply the block logic i.e. determine what we'll send to the LLM.
5) Try to get the LLM to send us usable code, execute in a loop until it works. When we get a correct result, return the python

That sound right-ish? Still have some questions tho.
Thu Feb 12 15:45:00 CET 2026

Right-ish, yes. Let me fill in the gaps on your questions:

#2 — Resolve strategy: A strategy controls how the LLM generates and returns code. The main ones:

CodeActStrategy (default): LLM uses a tool call (execute_python) to submit code. Supports structured output. Most flexible.
PurePythonStrategy: LLM just returns raw Python in its response text. Simpler, no tool-calling needed.
StructuredOutputStrategy: No code at all — LLM returns JSON that matches your return type schema directly.
ReflexionStrategy: Wraps another strategy and adds a self-reflection step after each attempt.
So "resolve strategy" means: did the @strategy decorator specify one? If not, use the default (CodeAct).

#3 — Resolve LLM: Same cascading idea. You can set the LLM at the class level, instance level, or per-method via @strategy(llm=...). "Resolve" just means: which one wins? Method-level > instance-level > class-level. This lets you do things like use a cheap model for simple methods and an expensive one for hard ones, all on the same agent.

#5 — One correction: It's not just "loop until it works." The loop continues until the code calls return_result(value) (or does an implicit return). Each iteration is a full LLM turn:

LLM sees all prior events (its previous code, the output/errors, feedback)
Generates new code informed by what happened
Code executes
Three outcomes: error → Error event, loop continues; no return → Feedback event ("you didn't return yet"), loop continues; return → validate the type, done
So the LLM isn't just retrying broken code. It's having a multi-turn conversation with a Python REPL. It might intentionally take multiple turns — fetch data in turn 1, process it in turn 2, return results in turn 3. Variables persist between turns (REPL-style).

The loop has a max iterations cap (per strategy config) so it doesn't run forever.

Strategy: this is governed by the prompt then? We're telling the LLM: just directly answer (structuredoutputstrategy) or send me python (codeact)? Also, what's the difference between codeactstrategy and purepythonstrategy?

LLM resolution: we're just saying what model to use.

The loop is then the conversation continuing, in effect, until the python execution succeeds?
Thu Feb 12 15:47:00 CET 2026

LLM resolution — yes, just which model to use. Nothing more.

The loop — yes, the conversation continues until execution succeeds and returns a value. Those are two separate conditions. The code can execute successfully but not return anything (the LLM is doing intermediate work), and the loop continues.

Strategy and the prompt — partially. The strategy controls more than just the prompt. It controls the entire interaction protocol with the LLM API:

CodeAct vs PurePython — this is the key distinction:

PurePythonStrategy: LLM response text is the code. The framework strips markdown fences, then executes whatever the LLM wrote. The LLM is told "just write Python."

CodeActStrategy: Uses the LLM's tool calling mechanism. The LLM is given a tool definition called execute_python, and it makes a tool call with the code as an argument. This means the LLM can also return structured output alongside code, or choose not to call the tool and return a direct answer instead.

So the difference is the transport mechanism — raw text vs tool calls. CodeAct is more flexible because tool calling is a first-class LLM API feature with better parsing guarantees, and it naturally supports structured output (the LLM can return JSON matching your return type schema without executing any code at all).

StructuredOutputStrategy — skips code entirely. The LLM just returns JSON conforming to the method's return type. No Python execution, no loop. One shot.

So the strategy governs: what do we tell the LLM to do (prompt), how does the LLM give us back its answer (protocol), and how do we process that answer (execution). The prompt is just one piece of it.

ahhh with codeact there's likely a loop where the agent decides to code something, executes, and reacts -- plan -> code -> execute until it decides it's done.
Thu Feb 12 15:49:00 CET 2026

Exactly. That's the core insight. With CodeAct the LLM is an agent — it decides when it's done. It might:

Write some exploratory code, see the output
Reason about what it learned
Write more code based on that
Call return_result(answer) when it's satisfied
Each turn it sees its full history (prior code, outputs, errors) and makes a deliberate choice about what to do next. The loop doesn't end because the code "worked" — it ends because the LLM explicitly chose to return.

That's also why it's called CodeAct — it's the CodeAct paper pattern. The LLM acts through code, observes results, acts again.

PurePython is more mechanical by comparison — it's closer to "generate the implementation of this function." CodeAct is "here's a REPL, figure it out."

Explain to me how the code execution is launched. Is it main -> ... method -> LLM generated code -> run in Python method? Basically just taking a string (cleaned up) from the LLM, and interpreting it in python in the same process? And that intpretation is run as a one large session across the CodeAct invocation?

Questions:
- Do we start a new session for each new CodeAct? I.e. if I have a method using CodeAct, when it's invoked the second time does it get a new session?
- How exactly do I keep REPL persistance? Is it just a list of python str : obj?
- Is isolation a subprocess then?
Thu Feb 12 15:51:00 CET 2026

Let me check the actual implementation to give you precise answers.

Grep "class GenerationSession" (in /Users/mstaats/git-repos/nemo_oo_agents/durability/src/nemo_oo_agents)
2 lines of output
Grep "session_locals|captured_locals|exec\(|compile\(" (in /Users/mstaats/git-repos/nemo_oo_agents/durability/src/nemo_oo_agents/runtime/actor.py)
24 lines of output
Read actor.py (lines 701-820)
Grep "class GenerationSession" (in /Users/mstaats/git-repos/nemo_oo_agents/durability/src/nemo_oo_agents/strategies/pure_python.py)
31 lines of output
Read actor.py (lines 524-583)
OK here's the concrete picture:

Yes — it's exec() in the same process. The LLM returns a string of Python code, the framework wraps it in an async function, and runs exec(compile(wrapper, ...), exec_globals) right there in the same Python process. No subprocess, no sandbox.

You can see it at actor.py:793:


exec(compile(wrapper, cell_filename, "exec"), exec_globals)
The namespace (exec_globals) is built from:

The agent's module __dict__ (so all imports from the agent's .py file are available)
self = the agent instance
Strategy builtins: reasoning(), message(), method parameter values
Session locals from previous turns
New session per invocation — yes. GenerationSession (pure_python.py:72) is created fresh each time a ... method is called. It has session_locals: dict = {} that starts empty. If you call the same method twice, second call gets a fresh session.

REPL persistence across turns within one invocation — it's exactly what you guessed. At actor.py:770-784, the code is wrapped in:


async def __repl_wrapper__():
    global x, y, z  # session vars from prior turns
    try:
        <LLM's code here>
    finally:
        __repl_captured_locals__.update({
            k: v for k, v in locals().items()
            if not k.startswith('_')
        })
After execution, _extract_captured_locals() pulls those out, and the strategy merges them into session.session_locals. Next turn, those locals get injected as builtins into exec_globals, and declared as global so assignments work. It's literally a dict[str, Any] — variable name to Python object.

Isolation is not a subprocess — it's contextvars. Stdout/stderr are wrapped in ContextVarStream so each async task gets its own capture buffer. Stdin is blocked. There's a timeout via asyncio.wait_for. But the code runs in-process with full access to everything — it can touch the filesystem, network, whatever. The "isolation" is just about parallel async tasks not stomping on each other's output, not about security sandboxing.

Explain to me what ContextVar is?
Thu Feb 12 15:53:00 CET 2026

It's a Python stdlib feature (contextvars.ContextVar) that gives you thread-local-style storage but for async tasks instead of threads.

The problem it solves: if you have 10 asyncio.Tasks running concurrently in the same process, and they all print() to sys.stdout, everything gets interleaved. You can't tell which output came from which task.

ContextVar lets each task have its own value for a variable. When task A sets my_var.set(buffer_a) and task B sets my_var.set(buffer_b), each task sees its own value when it calls my_var.get() — even though they're sharing the same event loop in the same thread.


from contextvars import ContextVar

my_buffer = ContextVar("my_buffer", default=None)

# Task A sets its buffer
my_buffer.set(StringIO())  # only Task A sees this

# Task B sets its buffer
my_buffer.set(StringIO())  # only Task B sees this
In nemo_oo_agents, this is how sys.stdout capture works: the real sys.stdout is replaced once with a ContextVarStream wrapper. When code does print("hello"), the wrapper checks the ContextVar to find the current task's buffer and writes there. No buffer set? Falls through to the real stdout.

So it's the async equivalent of thread-local storage — same concept, different concurrency model.
