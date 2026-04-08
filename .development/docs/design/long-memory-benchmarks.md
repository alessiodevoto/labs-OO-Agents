# Long-Term Memory Benchmark Adapters Design

**Status**: Implementation Complete
**Date**: 2026-01-08
**Author**: Claude
**Related**: MR 186 (context-blocks), evaluation framework

## Overview

This document describes the design of benchmark adapters for evaluating long-term memory capabilities in LLM agents, specifically:

1. **LoCoMo** (Long-term Conversational Memory) - ACL 2024
2. **LongMemEval** (Long-Term Interactive Memory) - ICLR 2025

These benchmarks require explicit context management beyond what standard single-turn benchmarks need. The implementation leverages the `context-blocks` package for dynamic context management.

## Benchmark Characteristics

### LoCoMo

| Aspect | Details |
|--------|---------|
| **Source** | [snap-research/locomo](https://github.com/snap-research/locomo) |
| **Paper** | [arxiv.org/abs/2402.17753](https://arxiv.org/abs/2402.17753) |
| **Tasks** | QA (1,986 questions), Event Summarization |
| **Context Size** | ~600 turns, ~16K tokens per conversation |
| **Sessions** | Up to 32 sessions per conversation |
| **Human Ceiling** | F1 = 87.9 |
| **GPT-4** | F1 = 32.1 |

**Question Categories:**
- `single-hop`: Intra-session factual recall
- `multi-hop`: Cross-session reasoning
- `temporal`: Date/order/interval inference
- `open-domain`: General knowledge applied to context
- `adversarial`: Unanswerable questions

### LongMemEval

| Aspect | Details |
|--------|---------|
| **Source** | [xiaowu0162/LongMemEval](https://github.com/xiaowu0162/LongMemEval) |
| **Paper** | [arxiv.org/abs/2410.10813](https://arxiv.org/abs/2410.10813) |
| **Dataset** | HuggingFace: `xiaowu0162/longmemeval-cleaned` |
| **Tasks** | 500 curated QA questions |
| **Variants** | Oracle (~evidence only), Small (~115K tokens), Medium (~1.5M tokens) |
| **GPT-4o** | 30-70% accuracy |

**Memory Abilities Tested:**
- Information extraction (single-session)
- Multi-session reasoning
- Temporal reasoning
- Knowledge updates
- Abstention (knowing when to say "I don't know")

## Adapter Implementation

### File Structure

```
evaluation/adapters/
├── locomo.py         # LoCoMo adapter
├── longmemeval.py    # LongMemEval adapter
└── __init__.py       # Registry with new entries
```

### Registry Entries

```python
ADAPTER_REGISTRY = {
    # ... existing adapters ...
    # Long-term memory benchmarks
    "locomo": LoCoMoAdapter,
    "locomo_qa": lambda: LoCoMoAdapter(include_event_summarization=False),
    "locomo_events": lambda: LoCoMoAdapter(include_event_summarization=True),
    "longmemeval": LongMemEvalAdapter,
    "longmemeval_oracle": lambda: LongMemEvalAdapter(variant="oracle"),
    "longmemeval_small": lambda: LongMemEvalAdapter(variant="small"),
    "longmemeval_medium": lambda: LongMemEvalAdapter(variant="medium"),
}
```

### Key Design Decisions

#### 1. Context Management Hints

Both adapters provide `context_management` metadata in `format_for_agent()`:

```python
{
    "context_management": {
        "total_sessions": len(sessions),
        "requires_temporal_tracking": question_type == "temporal",
        "requires_cross_session": question_type == "multi-hop",
        "conversation_tokens": token_count,
    }
}
```

This allows specialized agents to make informed decisions about:
- Which sessions to prioritize
- Whether to use RAG vs full context
- Temporal indexing strategies

#### 2. Session Formatting

Both benchmarks use a consistent session format:

```
### Session N (YYYY-MM-DD)
Speaker/User: content
Speaker/Assistant: content
```

This provides:
- Clear session boundaries for temporal reasoning
- Consistent parsing for both benchmarks
- Compatibility with context-blocks section splitting

#### 3. Evaluation Metrics

**LoCoMo**: Token-level F1 score (threshold: 0.5)
- Matches official benchmark scoring
- Event summarization uses fuzzy matching with 0.6 similarity threshold

**LongMemEval**: Combined approach
- Exact match (normalized)
- Substring containment
- Token F1 (threshold: 0.6)
- Abstention detection for unanswerable questions

## Specialized Agent Design

### Why a Specialized Agent?

Standard agents (`ToolsAgent`, `ReactAgent`) process everything in a single context window. For long-memory benchmarks with 100K+ tokens:

1. **Context overflow**: Most models have 8K-128K context limits
2. **Lost in the middle**: Models struggle to recall info from middle of long contexts
3. **No temporal awareness**: Standard prompts don't index by time
4. **Expensive**: Full context = maximum token costs per query

### Proposed: `LongMemoryAgent`

Using the `context-blocks` package for dynamic context management:

```python
from nemo_oo_agents import Agent
from context_blocks import BlockManager, Block, scoped_blocks

class LongMemoryAgent(Agent):
    """Agent with explicit long-term memory management.

    Uses context-blocks for dynamic context window management:
    - Session indexing for efficient retrieval
    - Temporal metadata tracking
    - Sliding window for conversation history
    - Summarization for older sessions
    """

    def __init__(self, llm, max_context_tokens: int = 8000):
        super().__init__(llm=llm)
        self.max_context_tokens = max_context_tokens
        self.blocks = BlockManager()

        # Core context blocks
        self.blocks.set("context", "persona", expr="self.persona_prompt")
        self.blocks.set("context", "instructions", expr="self.memory_instructions")

        # Dynamic session blocks
        self.blocks.set("context", "recent_sessions",
            expr="self.get_recent_sessions(5)",
            update="True"  # Re-evaluate each turn
        )
        self.blocks.set("context", "relevant_sessions",
            expr="self.retrieve_relevant_sessions()",
            show="self.has_retrieval_query"
        )

    async def answer_from_memory(
        self,
        question: str,
        sessions: list[dict],
        context_hints: dict,
    ) -> str:
        """Answer a question using long-term conversation memory.

        Strategy:
        1. Index sessions by date and content
        2. Identify question type (temporal, multi-hop, etc.)
        3. Retrieve relevant sessions based on question
        4. Use scoped context to inject relevant sessions
        5. Generate answer with explicit reasoning
        """
        # Index sessions
        self.session_index = self._build_session_index(sessions)

        # Determine retrieval strategy
        if context_hints.get("requires_temporal_tracking"):
            relevant = self._temporal_retrieval(question)
        elif context_hints.get("requires_cross_session"):
            relevant = self._multi_hop_retrieval(question)
        else:
            relevant = self._semantic_retrieval(question)

        # Use scoped context for this specific query
        with scoped_blocks(self.blocks, context={
            "relevant_sessions": Block(
                key="relevant_sessions",
                expr=f"'''{self._format_sessions(relevant)}'''",
            )
        }):
            return await self._generate_answer(question)
```

### Context Block Strategy

For a conversation with 32 sessions (~16K tokens), the agent uses:

```
┌─────────────────────────────────────────────────────────────┐
│ SYSTEM PROMPT (~500 tokens)                                   │
│ - Persona                                                     │
│ - Memory instructions                                         │
│ - Question type guidance                                      │
├─────────────────────────────────────────────────────────────┤
│ CONTEXT BLOCKS (dynamic, ~4000 tokens)                        │
│                                                               │
│ <recent_sessions>                                             │
│ [Last 3 sessions - always included]                           │
│ </recent_sessions>                                            │
│                                                               │
│ <relevant_sessions>                                           │
│ [Retrieved sessions based on question - semantic/temporal]    │
│ </relevant_sessions>                                          │
│                                                               │
│ <session_summaries show="self.has_overflow">                  │
│ [Compressed summaries of non-retrieved sessions]              │
│ </session_summaries>                                          │
├─────────────────────────────────────────────────────────────┤
│ USER MESSAGE (~500 tokens)                                    │
│ - Question                                                    │
│ - Question type hint                                          │
│ - Current date (for temporal reasoning)                       │
└─────────────────────────────────────────────────────────────┘
```

### Retrieval Strategies

1. **Semantic retrieval**: Embed question, find similar session turns
2. **Temporal retrieval**: Parse date references, retrieve sessions by time
3. **Entity retrieval**: Extract entities from question, find mentions
4. **Hybrid**: Combine above with learned weights

### Implementation Plan

1. **Phase 1** (Current): Basic adapters with full context
   - Adapters work with any agent
   - Suitable for models with 128K+ context

2. **Phase 2**: `LongMemoryAgent` with context-blocks
   - Explicit session indexing
   - Retrieval-based context selection
   - Works with 8K context models

3. **Phase 3**: Advanced memory features
   - Session summarization
   - Episodic memory with temporal tags
   - Cross-session reasoning chains

## Usage

### Running Benchmarks

```bash
# Basic usage with any agent
python experiments/evaluation-ablations/run_ablation.py \
    --config nemo_oo_agents \
    --benchmark locomo \
    --limit 50

# LongMemEval variants
python experiments/evaluation-ablations/run_ablation.py \
    --config nemo_oo_agents \
    --benchmark longmemeval_oracle \
    --limit 100
```

### With Specialized Agent (Phase 2)

```bash
python experiments/evaluation-ablations/run_ablation.py \
    --agent-file agents/long_memory_agent.py \
    --benchmark locomo \
    --limit 50
```

## Evaluation Considerations

### Expected Performance Ranges

| Model | LoCoMo F1 | LongMemEval Acc |
|-------|-----------|-----------------|
| Human | 87.9 | ~95% |
| GPT-4 | 32.1 | 30-70% |
| GPT-3.5 | 25.9 | ~25% |
| Llama-2-70B | 21.0 | ~20% |

### Key Metrics to Track

1. **Overall accuracy/F1**
2. **Performance by question type** (temporal, multi-hop, etc.)
3. **Context efficiency** (accuracy vs. context tokens used)
4. **Retrieval precision** (for RAG-based approaches)

## References

- [LoCoMo Paper](https://arxiv.org/abs/2402.17753) - ACL 2024
- [LongMemEval Paper](https://arxiv.org/abs/2410.10813) - ICLR 2025
- [context-blocks package](../methodic006/phase-3-context-blocks.md)
- [Evaluation Framework README](../../evaluation/README.md)
