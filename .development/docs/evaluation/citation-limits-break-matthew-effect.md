# Finding: Citation Limits Break the Matthew Effect in Idea Propagation

**Date:** 2025-12-09
**Experiment:** Agent Society
**Run IDs:** 20251208_004447 (pre-limit), 20251208_181127 (post-limit), 20251208_223933 (extended)

## Summary

Introducing a citation limit (max 5 citations per idea) dramatically shifts citation dynamics from seed-dominated (95%) to balanced (50/50) between foundational "Prior Literature" seeds and agent-generated ideas.

## The Problem: Rich-Get-Richer Dynamics

Without citation limits, we observed classic Matthew Effect behavior:
- Foundational seed papers accumulated 95%+ of all citations
- Agent-generated ideas struggled to gain traction
- Even high-quality new ideas were buried under canonical citations

## The Intervention

Added to idea generation prompt:
```
IMPORTANT CONSTRAINTS:
- Content length: Maximum 500 words (be focused and concise)
- Citations: Maximum 5 cited ideas (cite only the most relevant, not everything)
  Choose citations carefully - which ideas are truly foundational to your contribution?
```

## Results

| Run | Agents | Cycles | Citation Limit | Seeds % | Agent Ideas % |
|-----|--------|--------|----------------|---------|---------------|
| Pre-limit | 10 | 30 | None | **94.8%** | 5.2% |
| Post-limit | 20 | 100 | Max 5 | **48.8%** | 51.2% |
| Extended | 50 | 230+ | Max 5 | **48.8%** | 51.2% |

## Key Observations

### 1. Ratio Stabilizes Early
The 50/50 ratio emerged quickly and remained constant regardless of additional cycles. This suggests citation limits are the causal factor, not time/volume.

### 2. No Single-Citation Ideas
Interestingly, ideas either get 0 citations or 2+. There's almost no middle ground - ideas either "catch on" or die completely.

### 3. Recency Bias Emerges
In later cycles (150+), agents heavily favor recent ideas:
- Ideas from cycles 1-100: only 5% of reads
- Ideas from cycles 150+: 89% of reads

### 4. Seeds Become "Implicit Knowledge"
Seeds drop to only 1.1% of reads in later cycles. Their concepts get incorporated into newer syntheses rather than being cited directly - mimicking how foundational papers work in real academia.

## Why It Works

Without limits, agents exhibit "cite everything foundational" behavior:
- Safe to cite canonical work
- More citations = appears more thorough
- Seeds accumulate via preferential attachment

With max-5 limit, agents must prioritize:
- Choose most directly relevant work
- Recent peer ideas compete fairly
- Breaks the "everyone cites the classics" pattern

## Analogy to Real Academia

Modern papers cite 20-50 references, not 200. This constraint:
- Forces prioritization of truly relevant work
- Gives recent work a fair chance
- Prevents runaway accumulation on canonical papers

## Implications

1. **For multi-agent simulations:** Citation/reference limits may be essential for realistic knowledge dynamics
2. **For LLM behavior:** Without constraints, LLMs default to "safe" canonical references
3. **For studying innovation:** Constraints that force selectivity may promote idea diversity

## Files Modified

- `agents/research-agent/research_agent.py`: Added citation limit to `generate_idea` prompt (lines 258-271)

## Raw Data

- Pre-limit run: `results/20251208_004447/`
- Post-limit run: `results/20251208_181127/`
- Extended run: `results/20251208_223933/`
