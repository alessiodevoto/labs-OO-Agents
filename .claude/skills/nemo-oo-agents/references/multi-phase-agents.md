# Multi-Phase Agents

An **optional** pattern for agents that benefit from a controlled outer loop with generation methods inside. Not every agent needs this -- many are fine as a single CodeAct entrypoint.

## When this pattern helps

Use multi-phase decomposition when:
- The workflow has **mandatory sequential steps** that must all run (you don't want the LLM to skip fact-gathering and go straight to conclusions)
- Each step produces an **intermediate artifact** another step consumes
- You want **typed checkpoints** between steps (easier to debug, easier to inspect traces)
- Different steps benefit from different strategies (e.g., `PredictStrategy` for extraction, `CodeActStrategy` for synthesis)

## When a single CodeAct entrypoint is better

Keep the agent flat (one `async def run(...) -> Result: ...`) when:
- The task is open-ended and the LLM should decide the approach
- You already have helper methods as tools and the LLM can orchestrate them itself
- You don't care how the LLM sequences internal work, only about the final output

## The pattern

Concrete outer `run()` method chains typed-output phase methods:

```python
from pydantic import BaseModel

class Facts(BaseModel):
    claims: list[str]
    sources: list[str]

class Answer(BaseModel):
    text: str
    confidence: float

class ResearchAgent(Agent, llm=llm):
    """Research agent with controlled phase sequence."""

    def __init__(self, document: str):
        super().__init__()
        self.document = document

    async def extract_facts(self) -> Facts:
        """Extract factual claims from the document."""
        ...  # LLM implements, returns typed Facts

    async def synthesize(self, facts: Facts, question: str) -> Answer:
        """Synthesize an answer from the facts."""
        ...  # LLM implements, returns typed Answer

    async def run(self, question: str) -> Answer:
        """Answer the question by extracting facts then synthesizing."""
        facts = await self.extract_facts()
        return await self.synthesize(facts, question)
```

### Key properties

- `run()` has a **concrete Python body**, not `...` -- it enforces the phase sequence
- Each phase method returns a **typed output** (Pydantic, dataclass, typed list) -- never a free-form string the next phase has to parse
- Phases flow linearly; control lives in your Python code, not the LLM

## Contrast: flat CodeAct version of the same agent

```python
class ResearchAgent(Agent, llm=llm):
    """Research agent -- LLM decides the approach."""

    def __init__(self, document: str):
        super().__init__()
        self.document = document

    async def run(self, question: str) -> Answer:
        """Answer the question using the document."""
        ...  # LLM generates code that inspects self.document, reasons, returns Answer
```

Both are valid. The flat version trusts the LLM to figure out extraction → synthesis on its own. The multi-phase version enforces it.

## Pitfalls

- **Splitting too finely.** Two phases where one phase just reformats the output of another add latency without benefit. Merge them.
- **Free-form strings between phases.** If `extract_facts` returns a string the next method has to re-parse, you've lost the typed-checkpoint benefit. Return structured data.
- **Calling phases conditionally in `run()` without justification.** If every call path needs all phases, make the sequence unconditional. Branching in the outer loop re-introduces the "LLM might skip steps" problem you were trying to avoid.

## Mixing with CodeAct

A multi-phase agent can still use CodeAct inside a single phase:

```python
async def synthesize(self, facts: Facts, question: str) -> Answer:
    """Synthesize using any tools on self: self.search(), self.fetch()..."""
    ...  # CodeAct strategy (default) -- iterates and calls tools
```

The outer `run()` enforces that extraction happens first; the inner `synthesize` phase is free-form CodeAct.
