# Benchmark Selection Plan for Agent006

**Created:** 2026-01-08
**Status:** Draft for discussion

---

## Executive Summary

This document recommends an orthogonal set of benchmarks for nemo_oo_agents that exercise its unique capabilities:

1. **Code-as-action** - REPL-style execution with embedded tool calls
2. **Subagent orchestration** - Hierarchical routing, parallel execution
3. **Context management** - Memory, condensation, long-horizon tasks
4. **E2E optimization target** - Suitable for GEPA-style prompt evolution

**Recommended benchmark portfolio (two tracks):**

| Track | Benchmark | Primary Dimension | Difficulty | Progression |
|-------|-----------|------------------|------------|-------------|
| Foundation | **τ-bench** | Multi-turn tool use | Hard | Already started |
| **Data** | **DABStep** | Data analytics | Hard | → DSBench |
| **Data** | **DSBench** | Data science E2E | Hard | (from DABStep) |
| **Code** | **SWE-bench Lite** | Code generation | Medium | → Pro |
| **Code** | **SWE-bench Pro** | Long-horizon (hours) | Very Hard | (from Lite) |
| Memory | **GAIA** | General reasoning | Hard | - |
| Memory | **LOCOMO** | Long-term memory | Medium | - |

**Why DABStep first:** Clean benchmark (models aren't polluted), factoid answers (automatic eval), multi-step code reasoning is nemo_oo_agents's strength. Best agent at 45% - room to differentiate.

---

## Agent006's Unique Capabilities

### 1. Code with Embedded Tool Calls (CodeAct-style)

Agent006's `PurePythonStrategy` allows the model to write executable Python with tool calls embedded:

```python
# Model can write this directly
results = await asyncio.gather(
    analyze_agent.run(text=chunk1),
    analyze_agent.run(text=chunk2),
)
if results[0].score > results[1].score:
    return summarize(results[0])
```

**Key differentiator:** Conditionals, loops, variables, parallel execution - all in one code block.

**Best exercised by:** τ-bench (complex multi-step), SWE-bench (real code generation)

### 2. Subagent Orchestration

Agent006 supports spawning subagents with:
- Different models (cheaper/smaller for subtasks)
- No history (fresh context)
- Configurable strategies

```python
validator = Agent(
    model="qwen-7b",  # Cheap model for validation
    strategy=ValidationStrategy(),
    max_history=0,  # No context accumulation
)
```

**Best exercised by:** GAIA (requires decomposition), custom capability tests

### 3. Context Management

API-level support for:
- History condensation (`condense_history()`)
- Selective context injection
- Memory across sessions

**Best exercised by:** MemoryAgentBench, τ-bench (multi-turn)

### 4. E2E Optimization Loop

Unique to this project - evolving prompts/strategies based on trace analysis.

**Requirements for optimization:**
- Fast iteration (< 5 min per eval batch)
- Clear signal (pass/fail + partial scores)
- Traceable execution (spans for each decision)

**Best exercised by:** Starting with simpler benchmarks (τ-bench, capability tests)

---

## Benchmark Deep Dives

### 1. τ-bench (Already Started) ✅

**What it tests:** Multi-turn customer service with tool calling + policy compliance

**Why it fits nemo_oo_agents:**
- Multi-turn dialogue via `respond_to_user()` - exercises conversation flow
- 16 domain tools - exercises tool selection and sequencing
- Policy constraints - exercises instruction following
- Partial scores (0.6-0.8) - good signal for optimization

**Current status:** Infrastructure complete, 0% pass rate baseline (expected)

**Unique insight:** Agent gathers info but doesn't execute final action - suggests strategy-level issues that optimization could fix.

**Resources:**
- [τ-bench GitHub](https://github.com/sierra-research/tau-bench)
- [τ²-bench (updated)](https://github.com/sierra-research/tau2-bench)
- [Leaderboard](https://taubench.com/)

**SOTA (Jan 2025):** Claude 3.7 Sonnet ~49% pass^1 on τ²-bench

---

### 2. DABStep (Data Agent Benchmark) ⭐ NEW

**What it tests:** Multi-step data analytics with code + contextual reasoning

**Why it fits nemo_oo_agents:**
- **Clean benchmark** - Built on Adyen financial data, models aren't polluted
- **Code-as-action native** - Requires iterative code-based data processing
- **Multi-step reasoning** - Cross-referencing docs + data manipulation
- **Factoid answers** - Automatic correctness checks, clean signal for optimization

**Key stats:**
- 450+ real-world data analysis tasks
- Best agent: 45.2% (Google DS-STAR, Sep 2025)
- Hardest tasks: only 14.55% accuracy
- Created by Adyen + Hugging Face

**Why adopt early:**
- Low contamination risk (proprietary financial data context)
- Clean eval (factoid answers, no LLM judge needed)
- Multi-step code iteration is nemo_oo_agents's sweet spot
- Good signal for GEPA-style optimization

**Resources:**
- [DABStep Paper](https://arxiv.org/abs/2506.23719)
- [HuggingFace Leaderboard](https://huggingface.co/spaces/adyen/DABstep)
- [Adyen Blog Post](https://www.adyen.com/knowledge-hub/data-agent-benchmark-for-multi-step-reasoning-dabstep)
- [Reference Agent Code](https://github.com/cyyeh/data-agent)

**SOTA (Jan 2026):** DS-STAR 45.2%, Microsoft Analyst Agent competitive

---

### 3. DSBench (Data Science Benchmark) ⭐ NEW

**What it tests:** End-to-end data science: analysis + modeling

**Why it fits nemo_oo_agents:**
- **Realistic setting** - Long contexts, multimodal backgrounds, large data files
- **Multi-table reasoning** - Tests context management with complex schemas
- **End-to-end modeling** - Not just analysis, includes ML pipeline tasks
- **Kaggle-sourced** - Real competition problems, unambiguous ground truth

**Key stats:**
- 466 data analysis tasks + 74 data modeling tasks
- Sourced from Eloquence and Kaggle competitions
- Best agent: 34.12% on analysis tasks
- ICLR 2025 paper

**Why it complements DABStep:**
- DABStep = financial analytics, factoid answers
- DSBench = broader data science, includes modeling
- Together they cover the data agent space thoroughly

**Resources:**
- [DSBench Paper](https://arxiv.org/abs/2409.07703)
- [DSBench Website](https://liqiangjing.github.io/dsbench.github.io/)
- [GitHub](https://github.com/LiqiangJing/DSBench)

**Related:** DataSciBench (arXiv:2502.13897) is a similar benchmark with TFC framework

**SOTA (Jan 2026):** ~34% on analysis, significant room for improvement

---

### 4. GAIA (General AI Assistants)

**What it tests:** Real-world questions requiring reasoning + tools + web browsing

**Why it fits nemo_oo_agents:**
- Questions are "conceptually simple for humans" but require multi-step reasoning
- Requires tool orchestration (web search, file ops, calculation)
- Multi-modal handling (PDFs, images, spreadsheets)
- Clear pass/fail grading

**Why it's valuable:**
- Tests general capability, not narrow skill
- Forces compositional tool use
- Human baseline 92% vs AI ~75% - room for improvement

**Resources:**
- [GAIA Paper](https://arxiv.org/abs/2311.12983)
- [Hugging Face Leaderboard](https://huggingface.co/spaces/gaia-benchmark/leaderboard)
- [Princeton HAL Leaderboard](https://hal.cs.princeton.edu/gaia)

**SOTA (Jan 2026):** H2O.ai at 75% (first "C grade"), DeepSeek R1 ~45%

**Adoption notes:**
- Test set has 165 tasks (validation) + hidden test set
- Costs ~$5 with GPT-4o-mini (Knowledge Graph of Thoughts)
- Web browsing capability required

---

### 5. LOCOMO (Long-term Conversational Memory) ⭐ PREFERRED

**What it tests:** Very long-term conversational memory with temporal reasoning

**Why it fits nemo_oo_agents:**
- **Multi-session conversations** - 300 turns, 9K tokens avg, up to 35 sessions
- **Temporal reasoning** - Date/order/interval inference across sessions
- **Cross-session QA** - Multi-hop reasoning over conversation history
- **Event summarization** - Aligns with `condense_history()` capability

**Key stats:**
- 10 high-quality annotated conversations
- Human performance: ~88% F1 (LLMs substantially lag)
- Tasks: QA (5 types), event summarization, multi-modal dialog
- ACL 2024 paper from Snap Research

**Why LOCOMO over MemoryAgentBench:**
- More realistic: temporal sessions with timestamps
- Tests exactly what nemo_oo_agents's context management does
- Clear human baseline (88%) to measure against
- Multi-modal support (image sharing/reactions)

**Resources:**
- [LOCOMO Paper](https://arxiv.org/abs/2402.17753)
- [GitHub](https://github.com/snap-research/locomo)
- [Project Page](https://snap-research.github.io/locomo/)

**SOTA (Jan 2026):** MemMachine leads, but still substantially below human 88%

**Related benchmarks:**
- **MemoryAgentBench** - 4-dimension competency evaluation (alternative)
- **Context-Bench** (Letta) - file operations, entity relationships
- **MEMTRACK** - contradictory memory, cross-KB retrieval

---

### 6. SWE-bench Pro (Long-Horizon) ⭐ OPTIONAL

**What it tests:** Enterprise-scale software engineering tasks requiring hours to days

**Why it fits nemo_oo_agents:**
- **True long-horizon** - Tasks take hours/days for professional SWEs
- **Multi-file complexity** - Avg 107 lines across 4.1 files, min 10 lines change
- **Context engineering stress test** - Can't fit everything in context, must manage
- **Chaining problem** - Tests whether stateful reasoning beats accuracy decay

**Key stats:**
- 1,865 problems from 41 actively maintained repos
- Public (11 repos) + held-out (12) + commercial (18 proprietary)
- Best model: 23.3% (GPT-5) vs 72% on Lite/Verified
- Avg solution: 107.4 lines across 4.1 files

**Why it's different from Lite/Verified:**
- Lite/Verified = short bug fixes, ~72% SOTA (saturated)
- Pro = "hours to days" tasks, ~23% SOTA (wide open)
- Pro tests exactly what nemo_oo_agents's context management enables

**The chaining insight:**
> "If base task success rate is 90%, chaining reduces probability dramatically"
> 90%^5 = 59%. Stateful reasoning with pinned variables beats this.

**Resources:**
- [SWE-bench Pro Paper](https://arxiv.org/abs/2509.16941)
- [Scale AI Research](https://scale.com/research/swe_bench_pro)
- [Leaderboard](https://scale.com/leaderboard/swe_bench_pro_public)
- [GitHub](https://github.com/scaleapi/SWE-bench_Pro-os)

**SOTA (Jan 2026):** 23.3% (GPT-5), significant room for improvement

**Adoption notes:**
- Start with public set (11 repos)
- Longer iteration time - not ideal for rapid optimization
- Use after DABStep/DSBench baselines established

---

## Benchmarks Considered But Not Recommended

### BFCL (Berkeley Function Calling Leaderboard)

**What it tests:** Function/tool calling accuracy

**Why not primary:**
- Already have adapter in `evaluation/adapters/bfcl.py`
- V4 is more agentic, but still narrower than τ-bench
- Agent006's code-as-action goes beyond JSON function calling
- Keep as secondary validation

### AgentBench

**What it tests:** 8 diverse environments (OS, DB, web, games, etc.)

**Why not primary:**
- Very broad but shallow on each dimension
- Overlaps with other benchmarks
- Complex setup (8 different environments)
- Consider cherry-picking specific environments later

### MultiAgentBench / MARBLE

**What it tests:** Multi-agent collaboration and competition

**Why not yet:**
- Agent006 subagent model is orchestration, not peer collaboration
- Graph/star/chain topologies don't match our hierarchy model
- Interesting for future when we have peer-to-peer agents

### SWE-bench Lite/Verified (On-ramp to Pro)

**What it tests:** Short bug fixes, single-file patches

**Why it's useful as on-ramp:**
- ~72% SOTA but good for establishing baseline infrastructure
- Faster iteration than Pro (minutes vs hours)
- Same tooling/harness as Pro
- Progression: Lite → Pro validates context engineering hypothesis

### LiveCodeBench

**What it tests:** Code generation from competitive programming

**Why not primary:**
- Already have adapter
- Overlaps with DABStep/DSBench (code generation)
- Less realistic than real data analysis tasks
- Keep as secondary validation

### InterCode (SQL/Bash)

**What it tests:** Interactive coding in SQL and Bash

**Why not primary:**
- Already have adapter
- Narrow domains
- τ-bench covers tool use better for our purposes

---

## Recommended Adoption Order

### Phase 1: Foundation (Now - 2 weeks)

1. **τ-bench** - Continue current work
   - Goal: Get pass^1 > 20%
   - Owner: [assign]
   - Effort: Already started

2. **Capability Tests** - Internal validation
   - Already in `experiments/capability_eval/`
   - Exercises scale awareness, routing, multi-turn
   - Use for rapid iteration

## Two Parallel Tracks

### Track A: Data Analytics (Primary)

| Phase | Benchmark | Goal | Effort |
|-------|-----------|------|--------|
| 2a | **DABStep** | Baseline on 450+ tasks | Low-Medium |
| 3a | **DSBench** | Extend to ML pipelines | Medium |

**Progression:** DABStep (factoid answers, clean) → DSBench (modeling, long context)

### Track B: Code Generation (Secondary)

| Phase | Benchmark | Goal | Effort |
|-------|-----------|------|--------|
| 2b | **SWE-bench Lite** | Establish infrastructure, baseline | Medium |
| 3b | **SWE-bench Pro** | Validate context engineering on long-horizon | High |

**Progression:** Lite (saturated but fast) → Pro (tests chaining problem)

**Key hypothesis:** If we beat Lite SOTA proportionally more than Pro SOTA, context engineering isn't helping. If we beat Pro proportionally more, it validates stateful reasoning.

---

### Phase 2: Data + Code Foundations (2-4 weeks)

3. **DABStep** - Clean, unpolluted data analytics
   - Goal: Establish baseline on 450+ tasks
   - Owner: [assign]
   - Effort: Low-Medium (HuggingFace toolkit available)
   - **Why now:** Clean benchmark, factoid answers = perfect for optimization

4. **SWE-bench Lite** - Code generation on-ramp
   - Goal: Establish infrastructure, get baseline
   - Owner: [assign]
   - Effort: Medium (Docker, same harness as Pro)
   - **Why now:** Sets up tooling for Pro progression

### Phase 3: Extended Capability (4-6 weeks)

5. **DSBench** - Broader data science + modeling
   - Goal: Extend data agent capabilities to ML pipelines
   - Owner: [assign]
   - Effort: Medium (Kaggle data, longer tasks)
   - **Progression from DABStep:** Adds modeling, longer context

### Phase 4: General + Memory (6-8 weeks)

6. **GAIA** - General reasoning + tool orchestration
   - Goal: Validate general reasoning + tool orchestration
   - Owner: [assign]
   - Effort: Medium (web browsing, multi-modal)

7. **LOCOMO** - Long-term conversational memory
   - Goal: Validate context management + condensation
   - Owner: [assign]
   - Effort: Low-Medium (10 conversations, well-documented)
   - **Why LOCOMO:** Temporal reasoning, 35 sessions, human baseline 88%

### Phase 5: Long-Horizon Validation (8-10 weeks)

8. **SWE-bench Pro** - Enterprise-scale, hours-to-days tasks
   - Goal: Stress-test context engineering on truly long tasks
   - Owner: [assign]
   - Effort: High (longer iteration, complex setup)
   - **Progression from Lite:** Same infra, 10x harder tasks
   - **Hypothesis:** Stateful reasoning beats 90%^5 = 59% chaining decay

---

## Benchmark Orthogonality Matrix

| Dimension | τ-bench | DABStep | DSBench | SWE-Lite | SWE-Pro | GAIA | LOCOMO |
|-----------|---------|---------|---------|----------|---------|------|--------|
| **Multi-turn dialogue** | ★★★ | ★★ | ★ | ★ | ★ | ★★ | ★★★ |
| **Tool orchestration** | ★★★ | ★★ | ★★ | ★★ | ★★ | ★★★ | ★ |
| **Code iteration** | ★★ | ★★★ | ★★★ | ★★★ | ★★★ | ★★ | ★ |
| **Data manipulation** | ★ | ★★★ | ★★★ | ★ | ★ | ★★ | ★ |
| **Long context** | ★★ | ★★ | ★★★ | ★★ | ★★★ | ★★ | ★★★ |
| **Memory/state** | ★★ | ★ | ★ | ★ | ★★ | ★ | ★★★ |
| **Policy compliance** | ★★★ | ★ | ★ | ★ | ★ | ★ | ★ |
| **Web/external** | ★ | ★ | ★ | ★ | ★ | ★★★ | ★ |
| **ML modeling** | ★ | ★ | ★★★ | ★ | ★ | ★ | ★ |
| **Long-horizon (hours)** | ★ | ★ | ★ | ★ | ★★★ | ★ | ★ |
| **Multi-file changes** | ★ | ★ | ★★ | ★★ | ★★★ | ★ | ★ |

★★★ = Primary, ★★ = Secondary, ★ = Minimal

**Track progressions:**
- **Data:** DABStep (factoid, clean) → DSBench (modeling, long context)
- **Code:** SWE-Lite (baseline, fast) → SWE-Pro (long-horizon, chaining test)

**Key insight:** Comparing Lite vs Pro performance validates context engineering hypothesis.

---

## E2E Optimization Suitability

| Benchmark | Iteration Time | Signal Quality | Traceability | Optimization Fit |
|-----------|---------------|----------------|--------------|------------------|
| **τ-bench** | ~5 min/10 samples | High (partial scores) | Good | ★★★ Best |
| **DABStep** | ~3-5 min/10 samples | High (factoid answers) | Good | ★★★ Best |
| **DSBench** | ~10-15 min/10 samples | High (clear ground truth) | Good | ★★ Good |
| **SWE-bench Lite** | ~5-10 min/10 samples | High (tests pass/fail) | Good | ★★ Good |
| **GAIA** | ~5 min/10 samples | High (clear answers) | Medium | ★★ Good |
| **LOCOMO** | ~10 min/conversation | High (human baseline 88%) | Good | ★★ Good |
| **SWE-bench Pro** | ~30-60 min/task | High (tests pass/fail) | Good | ★ Validation only |

**Recommendation:** Start optimization loop with **DABStep** or **τ-bench**:
- DABStep: Clean benchmark, factoid answers, no LLM judge needed, multi-step code iteration
- τ-bench: Already instrumented, tests multi-turn flow

DABStep is particularly good for optimization because:
1. **No contamination** - Models haven't seen this data
2. **Automatic eval** - Factoid answers, no expensive LLM judge
3. **Multi-step** - Exercises iterative code refinement
4. **45% SOTA** - Room to show differentiation

---

## Open Questions

1. **Should we adopt τ²-bench instead of τ-bench?**
   - Pro: Newer, telecom domain, dual-control
   - Con: Harder, less established baselines

2. **DABStep vs DSBench for first data benchmark?**
   - DABStep: Cleaner (Adyen data), factoid answers, lower effort
   - DSBench: Broader (includes modeling), ICLR paper, more established
   - **Recommendation:** DABStep first (cleaner), DSBench as follow-on

3. **Memory benchmark choice?** → LOCOMO selected
   - ✅ LOCOMO: Temporal reasoning, 35 sessions, human baseline 88%
   - MemoryAgentBench: Alternative if we need 4-dimension competency focus
   - Context-Bench: Alternative if we need file operations focus

4. **When to revisit SWE-bench?**
   - If we need GitHub PR workflow specifically
   - If DABStep/DSBench don't exercise code gen sufficiently
   - Consider SWE-bench Verified (500 samples) as optional addition

---

## References

### Benchmark Papers & Leaderboards
- [τ-bench Paper](https://arxiv.org/pdf/2406.12045) | [Leaderboard](https://taubench.com/)
- [DABStep Paper](https://arxiv.org/abs/2506.23719) | [Leaderboard](https://huggingface.co/spaces/adyen/DABstep)
- [DSBench Paper](https://arxiv.org/abs/2409.07703) | [GitHub](https://github.com/LiqiangJing/DSBench)
- [SWE-bench Lite/Verified](https://www.swebench.com/) | [Leaderboard](https://www.swebench.com/)
- [SWE-bench Pro Paper](https://arxiv.org/abs/2509.16941) | [Leaderboard](https://scale.com/leaderboard/swe_bench_pro_public)
- [GAIA Paper](https://arxiv.org/abs/2311.12983) | [Leaderboard](https://huggingface.co/spaces/gaia-benchmark/leaderboard)
- [LOCOMO Paper](https://arxiv.org/abs/2402.17753) | [GitHub](https://github.com/snap-research/locomo)

### Related Work
- [CodeAct Paper](https://arxiv.org/abs/2402.01030) - Executable code actions (validates nemo_oo_agents's approach)
- [OpenHands CodeAct 2.1](https://openhands.dev/blog/openhands-codeact-21-an-open-state-of-the-art-software-development-agent)
- [Google DS-STAR](https://research.google/blog/ds-star-a-state-of-the-art-versatile-data-science-agent/) - SOTA on DABStep
- [AgentArch](https://arxiv.org/html/2509.10769v1) - Architecture evaluation
- [MultiAgentBench](https://arxiv.org/abs/2503.01935) - Multi-agent collaboration

### Surveys
- [KDD 2025 Tutorial: LLM Agent Evaluation](https://sap-samples.github.io/llm-agents-eval-tutorial/)
- [Evidently AI: 10 Agent Benchmarks](https://www.evidentlyai.com/blog/ai-agent-benchmarks)
- [O-mega: 2025 Agent Eval Guide](https://o-mega.ai/articles/the-best-ai-agent-evals-and-benchmarks-full-2025-guide)
