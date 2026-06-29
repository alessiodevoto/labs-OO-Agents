# Continuous Integration Memory (CIM) — Draft Section

Extracted from `main.tex` on 2026-04-13 for future inclusion once experiments are complete.

---

## Motivation

Current memory architectures treat accumulated knowledge as a library: the agent retrieves relevant information when it thinks it needs it (RAG). This approach has two failure modes: (1) the agent doesn't know what it doesn't know, so it fails to retrieve critical facts, and (2) retrieved context bloats the working window, degrading performance on the current task.

We propose *Continuous Integration Memory* (CIM), which treats memory as a test suite rather than a library. The analogy to software CI/CD is direct:

| CI/CD concept | Memory analogue | Agent006 implementation |
|---|---|---|
| Commit | New thought or action | Return value of generation method |
| Test suite | Historical fact base | Collection of sub-agents, each owning a topic |
| Broken build | Contradiction | `ValidationError` from critic method |
| Cache hit | Irrelevant memory | Hidden context block (no topic overlap) |
| Dependency graph | Relevance filtering | Metadata-based shard selection |

## Architecture

CIM uses three components:

1. **Fact shards**: The accumulated fact base is partitioned into topic-specific sub-agents, each holding a manageable subset (e.g., 10 pages of a book, one chapter of policy). Each shard is a lightweight Agent006 agent with a `verify(claim) -> list[Contradiction]` method.

2. **The linter**: When the main agent produces an output (a "commit"), the claim is broadcast to relevant shards in parallel. Only shards that detect a contradiction respond; others return `None`. This is a MapReduce over memory.

3. **Integration**: Detected contradictions are injected into the main agent's context as a targeted correction, not a bulk retrieval. The main agent "fixes the build" by re-generating with the specific missing fact.

```python
class MainAgent(Agent, llm=strong_llm):
    async def reason(self, task: str) -> Answer:
        """Solve {task}. Check consistency before returning."""
        ...

    def verify_consistency(self, claim: str) -> list[Contradiction]:
        """Broadcast claim to memory shards. Returns contradictions only."""
        shards = self._get_relevant_shards(claim)
        results = asyncio.gather(*[s.verify(claim) for s in shards])
        return [c for r in results for c in r if c is not None]

class MemoryShard(Agent, llm=cheap_llm):
    facts: list[str]  # This shard's portion of the fact base

    @strategy(StructuredOutputStrategy())
    async def verify(self, claim: str) -> Contradiction | None:
        """Check if {claim} contradicts any of your facts."""
        ...
```

### Key properties

- **Context efficiency**: The main agent's window stays lean — it only receives the specific contradiction, not the entire fact base.
- **Scaling**: Adding facts means adding shards, not enlarging the main prompt. Shards run on cheap models in parallel.
- **Selective checking**: A dependency graph (metadata overlap between claim and shard topics) avoids checking irrelevant shards, analogous to how `bazel` only reruns affected tests.

## Planned Experiments

We plan to evaluate CIM on four dimensions:

### Experiment 1: Contradiction detection
A multi-turn agent accumulates facts, with contradictions injected at varying positions. We compare vanilla (all in context), RAG, and CIM on detection rate, false positive rate, and token consumption.

### Experiment 2: Scaling behavior
Vary fact base size (10 to 10,000 facts). We hypothesize CIM maintains flat accuracy while vanilla degrades (context overflow) and RAG degrades (retrieval relevance).

### Experiment 3: Context efficiency
Same task and accuracy target; measure peak context window usage in the main agent across approaches.

### Experiment 4: Bolt-on improvement
Add CIM to existing τ-bench and DABStep agents. We expect CIM to catch errors the base agent makes (wrong payment method, wrong fee rule) by verifying against policy shards.

## Related Work (Memory architectures)

MemGPT introduces OS-inspired memory management for LLMs with a tiered memory hierarchy. Reflexion uses verbal self-reflection as a form of episodic memory. RAG retrieves from external knowledge stores. Our CIM proposal differs by treating memory as an active verification system rather than a passive retrieval store.
