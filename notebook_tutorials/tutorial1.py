# Your first Object Oriented Agent
# In this short tutorial we're going to explore what makes NOOA special. We'll start with a very simple `BaristaAgent` whose job is to recommend drinks to sleepy customers. Along the way we'll see that a NOOA agent is **just a Python object**: we give it new tools by adding methods, we spin up multiple agents by instantiating the same class N times, and we strongly type its outputs like any regular Python function.
# We won't try to sell you on a whole new paradigm by the end of this tutorial. What you'll actually walk away with is a way to build agents using... plain Python (with a tiny sprinkle of magic).

## Before we start
# You'll need to install NOOA and pick an inference backend. NOOA works with virtually any LLM provider — local or hosted API. In the rest of the tutorial we'll be using Claude through the `unifiedllm` client. See [the LLM setup guide]() for wiring in a different provider.

from unifiedllm import get_llm_client
llm = get_llm_client("aws/anthropic/claude-haiku-4-5-v1")


## A Barista Agent
# As promised, a NOOA agent is just a Python class. No configs to learn, no prompt directories to lay out, no YAML in sight. Let's define our first agent:

from nooa import Agent

class BaristaAgent(Agent, llm=llm):
    """You are a friendly barista at a small neighborhood cafe."""  # this becomes the agent's system prompt

    async def recommend_drink(self, customer_request: str) -> str:
        """Recommend a single drink to the customer based on what they just told you. Be warm and concise — one sentence is plenty."""
        ...

barista = BaristaAgent()
await barista.recommend_drink("Hi, I feel so sleepy.")

# That's it — a friendly recommendation, ready to serve. So what just happened?
# Behind the scenes, NOOA is doing a bit of work to keep the interface this Pythonic. A few things worth noticing:
# - the **class docstring** became the agent's system prompt
# - the **method docstring** became the task description
# - the **ellipsis (`...`)** is how you tell NOOA "this method is *agentic* — hand it off to the LLM instead of running it as normal Python"
# More concretely, NOOA is quietly assembling a prompt that looks roughly like:
# ```
# <system prompt>
# You are a friendly barista at a small neighborhood cafe.
#
# <available methods>
# recommend_drink(customer_request: str) -> str
# ```
# ...plus a handful of other bits we'll unpack later.

## Adding tools
# Our friendly barista just offered a strong caffeine kick — but it's 9pm as we write this, and a double espresso is definitely not the move. How do we teach the agent to respect a "no caffeine after 4pm" policy?
# In most agent frameworks you'd have to register a tool, describe it in some JSON schema, and wire it into the runtime. In NOOA? You just **add a method to the class**. Anything the agent can see on itself, it can call as a tool.

from datetime import datetime

class BaristaAgent(Agent, llm=llm):
    """You are a friendly barista at a small neighborhood cafe."""

    def is_only_decaf_hour(self) -> bool:
        """Return True if we should only be serving decaf right now. After 4pm we go decaf-only so our customers can still sleep tonight."""
        return datetime.now().hour >= 16

    async def recommend_drink(self, customer_request: str) -> str:
        """Recommend a single drink to the customer based on what they told you. Check whether we're in decaf-only hours before suggesting anything caffeinated."""
        ...

barista = BaristaAgent()
await barista.recommend_drink("Hi, I feel so sleepy.")

# **Takeaway:** in NOOA, ordinary Python methods and agentic methods live side by side on the same class. The agent freely calls the deterministic ones as tools — no registration, no schema, no glue code.

## Strong typing
# What if our cafe only offers a fixed menu and we want the agent to *only* recommend drinks we actually sell? This is where NOOA really starts to shine.
# Most agent frameworks only ever deal with a single data type: text. Text gets passed to tools, text gets exchanged between agents, and text gets returned as output — and then you painstakingly parse it back into JSON hoping the model got the shape right. NOOA takes a different approach: because our agent lives inside a Python program, we can strongly type everything.
# Let's put a real type on the return value of `recommend_drink`:

from enum import Enum

class Drink(Enum):
    ESPRESSO = "espresso"
    CAPPUCCINO = "cappuccino"
    FLAT_WHITE = "flat white"

class BaristaAgent(Agent, llm=llm):
    """You are a friendly barista at a small neighborhood cafe."""

    def is_only_decaf_hour(self) -> bool:
        """Return True if we should only be serving decaf right now. After 4pm we go decaf-only so our customers can still sleep tonight."""
        return datetime.now().hour >= 16

    async def recommend_drink(self, customer_request: str) -> Drink:
        """Pick the single best drink for the customer from the menu, based on what they told you. Check whether we're in decaf-only hours before recommending anything caffeinated."""
        ...

barista = BaristaAgent()
await barista.recommend_drink("Hi, I feel so sleepy.")

# This isn't just prompt engineering — NOOA actually enforces the return type at runtime. If the LLM tries to return something that isn't a valid `Drink`, the framework retries until it does. You get real Python objects back, not strings you have to guess-parse.

## Give your agent some state
# Our cafe has a finite stash of coffee beans, and every drink burns through one. Since our agent is *just a Python object*, giving it state is as easy as adding a field in `__init__`:

class BaristaAgent(Agent, llm=llm):
    """You are a friendly barista at a small neighborhood cafe."""

    def __init__(self, coffee_beans: int) -> None:
        super().__init__()
        self.coffee_beans = coffee_beans

    def is_only_decaf_hour(self) -> bool:
        """Return True if we should only be serving decaf right now. After 4pm we go decaf-only so our customers can still sleep tonight."""
        return datetime.now().hour >= 16

    async def recommend_drink(self, customer_request: str) -> Drink:
        """Pick the single best drink for the customer from the menu, based on what they told you. Check whether we're in decaf-only hours before recommending anything caffeinated."""
        self.coffee_beans -= 1
        ...

# Notice the new line *before* the `...`: `self.coffee_beans -= 1`. That's **prefill** — any code you write before the ellipsis runs as regular Python and is handed to the LLM as already-executed context. The LLM sees "here's what happened so far" and continues from there. Above, we deduct a serving of coffee for the order that's about to happen; the LLM writes the recommendation with that state already applied.
# Let's serve a customer and check the till:

barista = BaristaAgent(coffee_beans=2)
drink = await barista.recommend_drink("Something to keep me going, please.")
print(f"Served: {drink} — beans left: {barista.coffee_beans}")

## Dynamic prompts, live from `self`
# So far the *object* knows how many beans are left, but the *LLM* doesn't. If we kept serving customers, the agent would happily recommend espressos long after the beans ran out. We need a way to expose `self.coffee_beans` into the prompt at call time.
# First, let's give the barista a graceful fallback for when we've run dry — a nice cup of tea:

class Drink(Enum):
    ESPRESSO = "espresso"
    CAPPUCCINO = "cappuccino"
    FLAT_WHITE = "flat white"
    TEA = "tea"

# Now we drop `{self.coffee_beans}` straight into the docstring and tell the agent what to do when it hits zero:

class BaristaAgent(Agent, llm=llm):
    """You are a friendly barista at a small neighborhood cafe."""

    def __init__(self, coffee_beans: int) -> None:
        super().__init__()
        self.coffee_beans = coffee_beans

    def is_only_decaf_hour(self) -> bool:
        """Return True if we should only be serving decaf right now. After 4pm we go decaf-only so our customers can still sleep tonight."""
        return datetime.now().hour >= 16

    async def recommend_drink(self, customer_request: str) -> Drink:
        """Pick the single best drink for the customer from the menu, based on what they told you. Check whether we're in decaf-only hours before recommending anything caffeinated.

        Coffee beans are limited — after preparing this order we'll have {self.coffee_beans} servings left. If that's already negative, apologize politely and recommend a nice cup of tea instead."""
        self.coffee_beans -= 1
        ...

# That `{self.coffee_beans}` is **not** a Python f-string, and it isn't resolved when the class is defined. NOOA interpolates it *at call time*, so every call the LLM sees the current bean count. Change the field, and the next call sees the new value — no re-rendering, no cache to invalidate.
# You can drop the same `{...}` syntax around any Python expression, not just simple attributes — `{self.check_beans()}`, `{len(self.orders)}`, `{datetime.now()}` all work.
# Let's push the agent to the edge and watch it adapt as the beans run out:

barista = BaristaAgent(coffee_beans=2)
for _ in range(3):
    drink = await barista.recommend_drink("Something to keep me going, please.")
    print(f"Served: {drink} — beans left: {barista.coffee_beans}")

# The takeaway: your agent's prompt is always in sync with the object's state — no `format()` calls sprinkled through your code, no stale strings, no forgetting to update a prompt when the state changes. **If you want the LLM to know something, put it on `self` and reference it in a docstring.**

## OK, so what's actually happening?
# We've quietly built up a fair number of moving pieces — a system prompt, a deterministic tool, a typed return value, dynamic state. NOOA has been stitching all of them into a real LLM prompt behind the scenes. Time to pull back the curtain.
# The `print_prompt` helper renders exactly what the LLM would see for a specific method call:

from nooa import print_prompt
await print_prompt(barista.recommend_drink, customer_request="I could use a pick-me-up")

# The output is longer than the code, but every block is labeled. The ones worth naming today:
# - **`<system_prompt>`** — opens with your class docstring. This is the persona the model wears for every method on this class.
# - **`<strategy_prompt>`** — a compact rulebook explaining how to act each turn: what tools exist (`execute_python`, `return_result`), when to use which, and how to finish a run. This is what teaches the LLM to "inhabit" the framework, and it's the same for every agent.
# - **`<execution_context>`** — the imports, types, and helpers that will be in scope when the LLM writes code. Anything you import at module level shows up here (that's why we imported `datetime` and `Drink` at the top).
# - **`<self>`** — auto-generated documentation of the agent's public methods and fields. This is how the LLM discovers what the agent can do — no separate tool registry.
# - **Task prompt** — your method docstring, with `{self.coffee_beans}` and any other placeholders already interpolated to their current values.
# That's the whole prompt. No hidden system messages, no external template files, no per-tool JSON schemas glued on the side. If you ever wonder "why did the agent do that?", `print_prompt` is your first port of call — it shows you exactly what the model was reading when it made the decision.
# Don't sweat the details of the strategy block for now — a later tutorial unpacks **strategies** (how methods execute) and **context blocks** (how you inject extra state into the prompt at runtime, statically or dynamically). The whole thing stays this short because the framework is plain Python — and the model already knows Python.

## Watching agents in flight: the trace viewer
# `print_prompt` shows the *outgoing* prompt for one call. To watch a whole run unfold — every LLM response, every generated code cell, every tool call, every retry — NOOA ships a live **trace viewer**. Start it once in a separate terminal:
# ```bash
# nooa start-dev
# ```
# Now open [http://localhost:5001](http://localhost:5001) and re-run the barista. Every agent you create will stream into the viewer automatically, no code changes needed. It's the single most useful debugging tool in the box, and we'll dedicate a whole tutorial to reading traces once we're building more complex agents.
