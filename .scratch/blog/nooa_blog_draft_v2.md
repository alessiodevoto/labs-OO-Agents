# Build State-of-the-Art AI Agents as Ordinary Python Software with the NVIDIA NeMo Object-Oriented Agents (NOOA) Framework

*Draft v2 — replaces the security-first "Vanessa blog" draft. ~1,400 words. Figures suggested inline. All numbers sourced from the NeMo OO Agents tech report.*

---

The past two years of progress in AI agents have been told mostly as a story about models: bigger, better-trained, more capable of reasoning. But our research points to a second lever that is just as real and far cheaper to pull: the **harness** — the software that surrounds the model, renders its context, executes its actions, and decides when a task is actually done.

Run the same frontier model through different harnesses and the results diverge sharply. In our evaluations, one identical backend model spanned a gap of up to 11 points on SWE-bench Verified and consumed twice the tokens depending only on the software around it. The model is not the whole system. The harness is not an afterthought. **The two need to be co-developed** — and the harness is the part every organization can inspect, improve, and own today.

The NVIDIA NeMo Object-Oriented Agents (NOOA) framework is our open-source **research preview** for that co-development: the framework itself, a tech report, capability tests, and benchmark agents — with code, data, and evaluations released so the entire community can reproduce and build on the results.

## An agent is a Python object

Traditional agent development scatters an agent across prompt templates, tool schemas, callback code, and workflow graphs. The NOOA framework takes a radically simpler approach: **where Python has existing abstractions, we adopt them directly**. Agents are classes, capabilities are methods, type annotations are contracts, asynchronous work is `asyncio`, and tools and orchestration are normal Python code. Docstrings are the prompts. A method whose body is an ellipsis (`...`) is completed at runtime by an LLM-driven loop; a method with a normal body is ordinary, deterministic Python.

```python
class SupportAgent(Agent):
    """You are a support agent for a customer service system."""

    order_db: OrderDB  # object state: model-visible, passed by reference

    def is_refund_eligible(self, order: Order) -> bool:
        """Return whether an order is eligible for a refund."""
        return order.delivered and order.days_since_delivery <= 30

    @strategy(PredictStrategy())
    async def classify(self, message: str) -> TicketKind:
        """Classify the customer message into the best ticket kind."""
        ...

    async def triage(self, message: str, photo: Image | None,
                     order: Order | None) -> Ticket:
        """Triage a customer message and create a support ticket."""
        ...
```

Where agent-specific capabilities have no standard Python form — context construction, event history, state rendering, long-term memory, and validated LLM loops — NOOA exposes them as simple Pythonic APIs, so **both developers and agents share one familiar programming model**. The developer writes the class; the agent calls its methods, inspects its state, and can even extend it — through the exact same interface.

The implication cuts both ways. **Software developers already know how to build with NOOA**, because it is simple Python — no workflow DSL, no new orchestration language, no framework-specific mental model to acquire. And **models are already trained on it**: Python is among the best-represented languages in LLM training data, so agents act by writing ordinary code against ordinary objects, drawing on knowledge they already have instead of learning a bespoke tool protocol. This "agent readiness" is measurable: ten current models — open and closed, small and frontier — pass 97.9% of NOOA's capability suite zero-shot, despite never having been trained on the framework.

This design is deliberately inspired by PyTorch, which showed that a powerful runtime can still present users with a simple, native programming model. The NOOA framework applies the same principle to agents — and we believe it is the right foundation for agentic development to mature the way deep learning did.

The payoff is that **agentic development becomes traditional software development**. An agent can be diffed, code-reviewed, unit-tested, traced, versioned, and refactored — by humans *and* by AI coding agents, using the same tools they already use for every other codebase. In our benchmark evaluations, the difference between two of our agents was an ordinary code-review-sized change to a 150-line Python file — the practical meaning of "agents as ordinary software."

## Six ideas, one surface

The tech report identifies six model-facing interface capabilities that drive agent performance:

1. **Typed input/output** — agentic calls have typed arguments and validated return values, not free text.
2. **Pass by reference** — the model operates on live Python objects, seeing bounded previews instead of serialized dumps.
3. **Code as action** — the model acts by writing Python, with control flow and inline method calls.
4. **Programmable loop engineering** — orchestration loops are ordinary Python, writable by developers and by the model itself.
5. **Explicit object state** — durable, typed state lives on the agent object, not just in conversation history.
6. **Model-callable harness APIs** — context blocks and event history are APIs the model can inspect and manage.

We surveyed fourteen agent frameworks and harnesses — LangGraph, the OpenAI Agents SDK, Google ADK, PydanticAI, smolagents, Claude Agent SDK, OpenAI Codex, OpenHands, and others — against these six capabilities, with every score pinned to a specific commit and verifiable in the report's appendix. The comparison table below shows the full matrix: green where a capability is a first-class part of what the model sees, yellow where it exists but mainly for the developer or behind a flag or tool, red where we found no evidence of it.

**[IMAGE PLACEHOLDER 1: Six-capability comparison table across fourteen frameworks — Table 6 of the report]**

The finding: **the field is converging on these ideas, but no system combines all six**. Yellow dominates the table — most systems implement partial versions, often as experimental or flag-gated features that shipped during our evaluation window. The NOOA framework is, to our knowledge, the first to expose all six on a single surface.

We want to be clear about why we publish this comparison. It is not a scoreboard — it is a **map for the whole ecosystem**. The convergence in the table means every one of these projects has independently discovered some of the same ideas; the table shows each framework exactly which capabilities it already has and which are worth adopting, with pinned-commit evidence to check our reading. If in a year this table is all green, across every framework in it, this release will have done its job. Advancing the open state of the art for everyone — not ranking anyone — is the point.

## One general framework, state-of-the-art results across domains

The six capabilities are not aesthetics — they are measurable. And crucially, everything below comes from **the same general approach**: one framework, general-purpose agents, no benchmark-specific prompts or per-domain tuning. That generality is the claim being tested — a good harness interface should transfer.

- **Software engineering.** On SWE-bench Verified, NOOA reaches **82.2%** with GPT-5.5 — above the published leaderboard SOTA at submission (79.2%) — and 79.8% with Opus 4.6, using a general-purpose, 253-line agent. Strikingly, an unscaffolded ablation — the bare CodeAct loop with a generic prompt and a plain shell tool, a **150-line Python file** — scores **84.0%**: at frontier capability, the typed loop itself carries the result.
- **Terminal-based operations.** On Terminal-Bench 2.0, NOOA reaches **73.0%**, within a few points of the best purpose-built systems.
- **Cybersecurity.** On CyberGym L1 — where an agent must inspect a codebase, identify a security-relevant bug, and validate it with a proof-of-concept that reliably triggers it — NOOA solves **86.8%** with GPT-5.5: the **top-scoring open-source agent**, ahead of the majority of leading closed-source systems. It ran with network access *blocked* and a rule-based "cheat check" over every trajectory, so the result comes from reasoning over the code itself, not from looking up disclosed vulnerabilities. No cybersecurity-specific steering was used — the performance is predicated on the agent architecture.
- **General interactive reasoning.** On ARC-AGI-3, a single NOOA agent with **one 45-line skill** compresses a state-of-the-art six-agent, ~150k-line multi-agent system into one agent in one REPL — and beats the baseline fleet: **RHAE 50.2%** vs. 41.7% on GPT-5.5 (+8.5 points; +11.8 over the same skill without the memory subsystem), rising to **74.5% on GPT-5.6-sol** — at **under $20 per game**.
- **Contained by design.** The ARC-AGI-3 fleet ran under layered sandboxing — in-process cell guard, OS-level privilege drop, end-to-end anonymization. An 18-pass red-team audit of the live 25-game run found **no leakage on any rule** across 13,335 agent logs. For production deployment, NOOA pairs with [NVIDIA OpenShell](https://developer.nvidia.com/blog/run-autonomous-self-evolving-agents-more-safely-with-nvidia-openshell/).

## Same performance, half the tokens — and no context compaction

Harness engineering doesn't only buy accuracy; it buys efficiency. With GPT-5.5, NOOA reaches 82.2% on SWE-bench Verified using ~28 LLM calls and ~1.1M tokens per task. The comparison harnesses need 66 calls and 2.2M tokens for 78.2%, and 29 calls at 1.3M for 78.6%. **Parity or better, at roughly half the cost.**

The Pareto frontier plot below makes the pattern visible: score against per-task token cost, across backends and reasoning efforts. NOOA sits on the frontier — every other configuration in the comparison pays more tokens for the same score, or scores less at the same cost.

**[IMAGE PLACEHOLDER 2: Pareto frontier — SWE-bench Verified score vs. per-task token cost (prefill + output), by harness, backend, and reasoning effort — Figure 6 of the report]**

Two mechanisms drive this. First, *pass by reference*: tool results become live Python variables composed directly in code, instead of round-tripping through the context window as text. The model sees a typed, bounded preview; the full value stays live in the execution environment. Second, that same property means **NOOA never needs context compaction**: median sessions peak at 22–72k prompt tokens against 200–400k windows. Where other harnesses summarize their own transcripts — irreversibly discarding details the agent may need later, and invalidating their prompt cache to do it — NOOA's transcript stays append-only and cache-valid for the whole session, contributing a ~2× per-task prefill economy at equal scores.

There is also a reliability dividend: NOOA's only exit is a **validated, typed result** whose evidence field must cite observed output. An agent cannot simply declare success — the "false success" failure mode where a model claims completion without verifying its work. In our traces, no sampled NOOA session returned without running tests, at any reasoning effort.

## A research preview for the open agent ecosystem

The best-performing agent harnesses today are closed source and purpose-built. This work shows the gap narrowing to single digits: an open, general-purpose object-oriented agent comes within a few points of the strongest proprietary systems on both software-engineering benchmarks — and everything we used to get there is public: the framework, the capability tests (88 tests, 4,400 records across ten open and closed models), the benchmark agent, and the evaluation methodology.

To be clear about what that means: **the NOOA framework is a research preview, not a product — and not a bid to compete with anyone's harness or framework.** The agent ecosystem has advanced through many complementary contributions to orchestration, tools, memory, model interfaces, and evaluation, and this work builds directly on that collective progress. The results are evidence that the open ecosystem, as a whole, can reach the capability of the best purpose-built systems — and every technique behind them is documented in the report precisely so that any framework, including the fourteen we surveyed, can adopt, challenge, and improve on it. Nothing here is proprietary to NOOA.

We think fostering this open development matters. Transparent, typed, inspectable agents — with human-readable memory in a single SQLite file, queryable event histories, and standard Python interfaces that existing security review, access control, and observability practices apply to directly — should not be capabilities available only to companies that can build proprietary infrastructure. Open implementations let researchers, startups, enterprises, and governments *inspect* the approach rather than trust it: reproduce the results, find the weaknesses, and fold the useful patterns into their own platforms. NOOA is offered in that spirit — not as a replacement for existing frameworks, but as an open experimental surface for strengthening all of them, and an invitation to collaborate on shared tests, interoperable patterns, and joint evaluations.

## Get started

- **Read the tech report:** [link]
- **Get the code:** [github.com/nvidia-nemo/labs-OO-Agents](https://github.com/nvidia-nemo/labs-OO-Agents) — framework, capability tests, and benchmark agents
- **Deploy safely:** [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell)

We look forward to what the community builds — and breaks, and improves.

---

*Image placeholders in text: (1) Table 7 six-capability comparison table, (2) Figure 6 Pareto frontier scatter. Optional third: Figure 7 ARC-AGI-3 RHAE curves (now four fleets incl. GPT-5.6-sol 74.5%), if the results section wants a visual. Open items: tech report link; final GitHub URL. Numbers verified against ground truth: "nemo_oo_agents (3).pdf" (2026-07-17).*
