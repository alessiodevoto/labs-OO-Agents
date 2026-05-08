# Subagent Composition

Delegating work to a separate agent class with its own tools, strategy, and return type.

## When to use a subagent

- The sub-task is **conceptually distinct** (writing vs. reviewing, extracting vs. classifying)
- The sub-task has its **own tools** (a reviewer needs different tools than a writer)
- You want **separate trace spans** for the delegated work
- The sub-task is **reusable** across parent agents
- The sub-task uses a **different LLM** (e.g., a cheap model for extraction, a strong one for synthesis)

## When a subagent is overkill

- A concrete deterministic method would do -- subagents add latency
- The "sub-task" is really just a different prompt on the same inputs -- use another generation method on the same class instead
- You just want separate trace spans -- use `with hidden:` or phase methods instead

## Basic pattern

Subagent with its own typed return type, called from the parent:

```python
from pydantic import BaseModel

class Feedback(BaseModel):
    suggestions: list[str]
    score: float  # 0-1

class ReviewerAgent(Agent, llm=llm):
    """Reviews a draft and returns structured feedback."""

    async def review(self, draft: str) -> Feedback:
        """Review the draft. Return specific improvements and a quality score."""
        ...

class WriterAgent(Agent, llm=llm):
    """Writes essays and refines them using a reviewer."""

    def __init__(self):
        super().__init__()
        self.reviewer = ReviewerAgent()  # subagent stored on self

    async def _write(self, topic: str, feedback: Feedback | None = None) -> str:
        """Write an essay; incorporate feedback if provided."""
        ...

    async def run(self, topic: str) -> str:
        """Write and refine an essay using reviewer feedback."""
        draft = await self._write(topic)
        feedback = await self.reviewer.review(draft)
        return await self._write(topic, feedback)
```

### Key properties

- **Typed return** (`Feedback`, not `str`) -- the parent consumes structured data
- Subagent stored as an instance attribute (`self.reviewer`), not instantiated per call
- Parent's `run()` is concrete -- enforces the write → review → refine sequence
- Each agent has a single responsibility readable from its class name alone

## LLM inheritance

Define the subagent **without** `llm=...` and it inherits the parent's LLM at runtime:

```python
class ReviewerAgent(Agent):  # no llm=
    async def review(self, draft: str) -> Feedback: ...

class WriterAgent(Agent, llm=claude):
    def __init__(self):
        super().__init__()
        self.reviewer = ReviewerAgent()  # uses claude (parent's LLM)
```

Set an explicit `llm=` on the subagent class only when you want a different model:

```python
class FastExtractor(Agent, llm=nemotron_nano):  # cheap model
    @strategy(PredictStrategy())
    async def extract(self, text: str) -> list[str]: ...

class MainAgent(Agent, llm=claude_opus):  # strong model for reasoning
    def __init__(self):
        super().__init__()
        self.extractor = FastExtractor()
```

## Multiple subagents

Compose specialized subagents for complex workflows:

```python
class ResearcherAgent(Agent):
    async def gather(self, topic: str) -> list[Source]: ...

class WriterAgent(Agent):
    async def draft(self, sources: list[Source]) -> str: ...

class EditorAgent(Agent):
    async def polish(self, draft: str) -> str: ...

class PipelineAgent(Agent, llm=llm):
    """Orchestrates research, writing, and editing."""

    def __init__(self):
        super().__init__()
        self.researcher = ResearcherAgent()
        self.writer = WriterAgent()
        self.editor = EditorAgent()

    async def run(self, topic: str) -> str:
        sources = await self.researcher.gather(topic)
        draft = await self.writer.draft(sources)
        return await self.editor.polish(draft)
```

## Parallel subagent calls

Each agent has its own internal lock. To run subagents in parallel, use **separate instances**:

```python
# BAD: single reviewer instance -- internal lock makes these sequential
reviewer = ReviewerAgent()
feedbacks = await asyncio.gather(*[
    reviewer.review(draft) for draft in drafts
])

# GOOD: one reviewer per draft -- true parallelism
reviewers = [ReviewerAgent() for _ in drafts]
feedbacks = await asyncio.gather(*[
    r.review(d) for r, d in zip(reviewers, drafts, strict=True)
])
```

## Pitfalls

- **Returning strings between agents.** Parent can't branch on content reliably. Use Pydantic models.
- **Over-decomposition.** Three agents for a task a single `run()` with helpers could handle. Each subagent is a separate LLM call -- latency adds up.
- **Subagents that only wrap a single prompt.** If `ReviewerAgent` just has one method with no tools and no state, make it a method on the parent with `@strategy(PredictStrategy())` instead.
