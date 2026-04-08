# Benchmark Optimization Comparison: Lessons for a Specialized Tau-Bench Agent

**Created:** 2026-02-18
**Context:** Synthesizing optimization learnings from DABStep (opt1-opt63), SWE-bench, and BigCodeBench to inform the design of a specialized tau-bench agent.

---

## Executive Summary

Across three benchmarks we've optimized, a consistent set of patterns emerges: **architectural fixes beat prompt engineering**, **model choice dominates**, and **simplicity outperforms complexity** once the right model is in play. This document compares the optimization journeys to extract transferable principles for building a tau-bench agent that can exceed the current ~49% SOTA.

---

## 1. Benchmark Profiles

| Dimension | DABStep | SWE-bench | BigCodeBench | Tau-bench |
|-----------|---------|-----------|-------------|-----------|
| **Task type** | Data analysis | Code patching | Code generation | Multi-turn dialogue + tools |
| **Turns** | Single | Single | Single | Multi (up to 50) |
| **Evaluation** | Exact match (factoid) | Test suite pass/fail | Test suite pass/fail | DB state + policy compliance |
| **Tool count** | 0 (code only) | 8-10 (file/git/search) | 0 (code only) | 14-16 per domain |
| **Simulated user** | No | No | No | Yes (LLM-powered) |
| **Our best** | 80% (opt49) | 0% (baseline only) | ~50% | ~40% (baseline) |
| **External SOTA** | 45% (DS-STAR) | 49% (Devin) | ~70% | ~49% (Claude 3.7) |
| **Key challenge** | Rule-based reasoning | Codebase navigation | Import sandboxing | Policy compliance + state mgmt |

---

## 2. DABStep: The 63-Iteration Prompt Engineering Journey

### What Happened

DABStep gave us the most data: 63 named optimization variants (opt1-opt63) plus automated e2e optimization attempts.

**Score Progression:**
```
opt1-opt3:    40% -> 50%   Architectural fix (data_dir parameter)
opt4-opt21:   50% -> 60%   More iterations, stability
opt22-opt30:  60% -> 40%   Failed approaches, regressions
opt31-opt34:  -> 80%       Model switch (Qwen -> Claude) + single-phase
opt35-opt44:  80% -> 70%   Competing-constraint regressions
opt45-opt49:  70% -> 80%   EUR rounding fix (one-line change)
opt50-opt56:  80% -> 60%   12 failed attempts to reach 90%
```

### Key Lessons

**Lesson 1: Architectural fixes >> prompt engineering.** opt3 jumped from 40% to 50% by adding a `data_dir` parameter to all phases. This was a code change, not a prompt tweak. Twelve subsequent prompt iterations failed to replicate that impact.

**Lesson 2: Model choice is the dominant variable.** Same code ran at 20% with Qwen and 80% with Claude -- a 60-point gap that dwarfs all prompt engineering gains combined.

**Lesson 3: Simplicity beats complexity once the model is good enough.** The 8-phase decomposition (Understand, Discover, Map, Explore, Extract, Apply Rules, Compute, Format) was theoretically elegant but Claude performed better with a single-phase approach. The model's native reasoning made the scaffolding counterproductive.

**Lesson 4: There is a prompting ceiling.** opt49 hit 80% and 12 consecutive iterations (opt45-opt56) failed to improve. The two remaining failures required either (a) fee-switching logic the model couldn't learn from hints, or (b) a likely benchmark error. Reaching 90% requires architectural changes (multi-phase pipeline, post-processing layer, or task-specific handlers).

**Lesson 5: Competing constraints cause cascading regressions.** Adding guidance for task 1871 broke task 1753. Verbose examples broke unrelated tasks. Keyword detection was fragile (checking `"delta" in guidelines` failed when the keyword appeared in `question` instead). Every gain on one task risked losses on others.

**Lesson 6: Automated optimization hit infrastructure limits.** The e2e_optimization loop (GEPA-style tournament selection) crashed every 4-6 iterations, only evaluated 5-6 of 10 tasks per iteration, and achieved 20-50% -- worse than manual optimization.

### Transferable Pattern: The Minimal Targeted Fix

The most successful optimizations were surgical:
- opt3: Add one parameter (`data_dir`)
- opt49: Fix one rounding rule (EUR two-step rounding)
- opt31: Switch one variable (model)

Each was a single change with clear causal reasoning. Broad prompt rewrites consistently failed.

---

## 3. SWE-bench: Failure-Driven Architecture

### What Happened

SWE-bench optimization took a different approach: analyze failures first, then design the architecture to address failure modes. 70 failed traces were analyzed using ARCA (Agentic Root Cause Analysis).

**Failure Distribution (70 traces):**

| Failure Mode | % | Root Cause |
|---|---|---|
| Implementation errors | 25.7% | Incomplete fixes, missed edge cases |
| Wrong solution strategy | 15.7% | Fixed symptoms not root causes |
| Failed to execute/submit | 12.9% | Context loss, format errors |
| Missing context | 11.4% | Didn't explore enough code |
| Pattern recognition failures | 8.6% | Used generic patterns vs. local conventions |

### The 3-Phase Architecture

This analysis produced a 3-phase agent design:

**Phase 1: Clarify** (addresses 15.7% + 11.4% = 27.1% of failures)
- `get_repo_overview()`: Understand project structure
- `clarify_issue()`: Generalize from symptoms to capabilities (critical rule: never echo symptom language)
- `analyze_root_cause()`: Trace symptom -> root cause across files, check subclasses/related modules

**Phase 2: Implement** (addresses 25.7% of failures)
- Create test cases first (cover all edge cases)
- Implement complete fix across ALL affected files
- No "not in scope" reasoning allowed

**Phase 3: Review** (addresses 12.9% + 8.6% = 21.5% of failures)
- Harsh FeedbackAgent review loop (up to 3 iterations)
- Anti-patterns: "minimal fixes", "all tests pass" (these indicate incomplete thinking)
- Complete fixes > minimal fixes

### Key Lessons

**Lesson 7: Failure analysis should drive architecture, not intuition.** The 3-phase design directly addresses the top 5 failure categories. Each phase has a measurable target (% of failures it should eliminate).

**Lesson 8: Generalization > symptom-fixing.** The `clarify_issue()` step explicitly bans implementation language ("writer", "class", "method") and forces capability-level thinking. This prevents the agent from locking onto a narrow fix.

**Lesson 9: Review loops with harsh critics work.** The FeedbackAgent explicitly rejects "minimal fix" reasoning and forces completeness checking. However, the review must be adversarial, not rubber-stamping.

**Lesson 10: Tools matter as much as prompts.** Jedi-based symbol analysis (`find_definition()`, `find_references()`, `get_class_structure()`) gave the agent code navigation abilities that no amount of prompting could replicate.

### Transferable Pattern: Failure-Driven Phase Design

Design each agent phase to address a quantified failure mode. If you can't tie a phase to a failure percentage, you probably don't need it.

---

## 4. BigCodeBench: The Tooling Tax

### What Happened

BigCodeBench revealed that agent framework overhead can suppress model capability. Our agent006 config scored 47.1% while direct_llm scored 49.6% -- the framework was a net negative.

**Root Causes:**

| Issue | Fix | Impact |
|---|---|---|
| Triple-quoted string indentation | `textwrap.dedent()` before `ast.parse()` | +20 tasks |
| Sandbox blocks common imports | Pre-load modules AND aliases | +21 tasks |
| Django lazy settings | Pre-configure with test SECRET_KEY | Unblocked Django tasks |
| Module pollution in parallel runs | Snapshot pattern with async lock | Correctness fix |

### Key Lessons

**Lesson 11: Framework overhead is a real cost.** Our REPL sandbox required both module names AND aliases to be pre-loaded. Forgetting `import numpy as np` (the alias) while having `import numpy` caused silent failures. This is invisible in unit tests but shows up at scale.

**Lesson 12: The simplest fix often has the largest impact.** `textwrap.dedent()` -- a single function call -- fixed 20 tasks. Compare this to the dozens of prompt iterations in DABStep that failed to move the needle.

**Lesson 13: Test with the actual eval framework, not in isolation.** Import sandboxing issues only appeared when running through the full evaluation pipeline. Developers testing locally with `python -c` would never see them.

### Transferable Pattern: Pre-flight Environment Validation

Before optimizing prompts, verify the execution environment doesn't have systematic issues. Run 10-20 tasks and categorize failures into "model failures" vs. "infrastructure failures". Fix infrastructure first.

---

## 5. Cross-Benchmark Optimization Patterns

### Pattern Matrix

| Pattern | DABStep | SWE-bench | BigCodeBench | Applicability to Tau-bench |
|---------|---------|-----------|-------------|---------------------------|
| **Model choice dominance** | 60-point gap (Qwen vs Claude) | N/A (only Claude tested) | 3-point gap (agent configs) | HIGH -- model selection for both agent and user-simulator |
| **Architectural fix > prompts** | opt3 data_dir (+10%) | 3-phase design | textwrap.dedent (+20 tasks) | HIGH -- conversation flow architecture |
| **Simplicity > complexity** | Single-phase > 8-phase with Claude | Simpler agents submitted first | Direct LLM > agent framework | MEDIUM -- tau-bench needs SOME structure for multi-turn |
| **Competing constraints** | opt50-56 regressions | Not yet observed | Not observed | HIGH -- retail has 9+ policy rules |
| **Failure-driven design** | Post-hoc only | ARCA on 70 traces | Post-hoc only | CRITICAL -- should drive phase design |
| **Tool quality** | N/A (no tools) | Jedi symbol analysis | Import pre-loading | HIGH -- 16 tools need good documentation |
| **Environment issues** | N/A | Docker image pulling | Sandbox import blocking | HIGH -- Docker + LLM user simulator |
| **Prompting ceiling** | 80% (12 failed attempts) | Unknown | Unknown | Expected at some threshold |

### The Optimization Priority Stack

Based on all three benchmarks, optimizations should be attempted in this order:

1. **Fix infrastructure** (BigCodeBench lesson): Verify Docker env, tool execution, user-simulator LLM, response parsing all work correctly
2. **Choose the right model** (DABStep lesson): Test 2-3 models before any prompt work
3. **Analyze failures** (SWE-bench lesson): Run 20-30 tasks, categorize failures, design phases to address top failure modes
4. **Make targeted architectural fixes** (DABStep opt3, BigCodeBench dedent): One change at a time, measure impact
5. **THEN optimize prompts** (DABStep opt31-49): Only after 1-4 are exhausted

---

## 6. What a Specialized Tau-Bench Agent Should Look Like

### 6.1 Why Tau-Bench is Different

Tau-bench has four properties no other benchmark shares:

1. **Multi-turn with a simulated adversary.** The LLM user-simulator responds dynamically. The agent must handle ambiguity, follow-up questions, and mid-conversation corrections.

2. **Policy compliance as a first-class metric.** It's not enough to call the right tools -- the agent must follow rules about authentication order, confirmation requirements, and state constraints.

3. **State verification, not output matching.** The evaluator checks the database state after the conversation. Correct reasoning with wrong tool arguments fails.

4. **Pass^k robustness.** Consistency across repeated trials matters. A strategy that works 70% of the time but fails randomly is worse than one that works 50% reliably.

### 6.2 Proposed Architecture: 3-Phase Conversation Agent

Drawing from the optimization patterns above:

```
Phase 1: Authenticate & Understand (addresses "missing context" failures)
  - Identify user (email or name+zip)
  - Retrieve user details, order details
  - Parse customer intent into structured form
  - DO NOT take any action yet

Phase 2: Plan & Confirm (addresses "wrong strategy" failures)
  - Map intent to required tool sequence
  - Check policy constraints BEFORE executing
  - Present plan to customer for confirmation
  - Handle objections or corrections

Phase 3: Execute & Verify (addresses "implementation error" failures)
  - Call tools in correct order
  - Verify each tool return value
  - Confirm completion with customer
  - Handle errors gracefully (escalate if needed)
```

**Why 3 phases, not 1:** Unlike DABStep where single-phase worked with Claude, tau-bench's multi-turn nature and policy compliance requirements benefit from explicit phase separation. The agent needs to resist the temptation to act before understanding (the primary failure mode we observed: "gathers info but doesn't execute final action").

**Why 3 phases, not 8:** DABStep's 8-phase decomposition was too rigid. The phases above are conversation phases, not processing steps. Each phase can span multiple turns.

### 6.3 Key Design Decisions

**Decision 1: Policy as structured checklist, not embedded prose.**

DABStep taught us that competing constraints cause regressions. Instead of embedding all policy rules in the system prompt (where they compete for attention), structure them as a checkable list:

```python
RETAIL_POLICY_CHECKS = [
    "user_authenticated",      # Must verify identity before ANY action
    "explicit_confirmation",   # Customer said yes/no before DB modification
    "single_tool_per_turn",    # Don't call tool AND respond in same turn
    "same_product_type",       # Modifications stay within product category
    "order_state_valid",       # pending=cancel/modify, delivered=return/exchange
    "one_modification_only",   # exchange/modify can only be called once per order
]
```

The agent checks this list before each tool call. This is an architectural constraint, not a prompt hint.

**Decision 2: Targeted tool documentation, not comprehensive dump.**

BigCodeBench showed that framework overhead suppresses model capability. Don't dump all 16 tool docstrings into the system prompt. Instead:

- Phase 1 tools: `find_user_id_by_email`, `find_user_id_by_name_zip`, `get_user_details`, `get_order_details`
- Phase 2 tools: `get_product_details`, `list_all_product_types`, `calculate`
- Phase 3 tools: Only the action tools relevant to the parsed intent

This reduces context window pressure and prevents the "tool selection confusion" failure mode.

**Decision 3: CodeActStrategy with explicit UserResponse type.**

The existing `tau_bench_agent.py` already uses this pattern:

```python
@strategy(CodeActStrategy(...))
async def handle_user_message(self, user_message: str) -> UserResponse:
    """..."""
```

This forces structured output (message + session_complete flag) and enables the GEPA optimizer to evolve the docstring.

**Decision 4: Conversation state tracking.**

Unlike single-turn benchmarks, tau-bench requires remembering what happened earlier. Add explicit state:

```python
self.conversation_state = {
    "phase": "authenticate",
    "user_id": None,
    "authenticated": False,
    "intent": None,
    "pending_action": None,
    "confirmation_received": False,
}
```

This gives the model explicit signals about where it is in the workflow, reducing the "gathers info but doesn't act" failure mode.

**Decision 5: Fail fast to escalation.**

SWE-bench's harsh review loop teaches that agents should not persist with failing strategies. After 2 failed tool calls or unclear customer responses, escalate via `transfer_to_human_agents()`. A clean escalation scores better than a botched action.

### 6.4 GEPA Optimization Strategy

Based on DABStep's e2e optimization experience (which hit infrastructure limits), the tau-bench GEPA loop should:

1. **Start with 10-task evaluation** (not 450). DABStep's 10-task subset was the sweet spot for iteration speed.
2. **Evolve docstrings only** in the first pass. DABStep showed that model + minimal docstring changes produce the most stable gains.
3. **Track per-task scores** across iterations. DABStep's competing constraints problem means we need to detect regressions immediately.
4. **Use retail domain first** (16 tools, more familiar pattern). Graduate to airline (14+ tools, more complex booking logic) after retail is optimized.
5. **Run 3 trials per evaluation** for Pass^k stability. Single-run scores are noisy.

### 6.5 Expected Score Trajectory

Based on the pattern from other benchmarks:

| Phase | Expected Score | What Gets Us There |
|---|---|---|
| **Infrastructure validation** | ~40% (baseline) | Fix Docker env, user-simulator, tool execution |
| **Model selection** | ~45-50% | Test Claude Sonnet vs others on same agent |
| **Failure analysis + 3-phase arch** | ~55-60% | Address top 3 failure modes with explicit phases |
| **Targeted fixes** | ~60-65% | Policy checklist, state tracking, phase-gated tools |
| **GEPA optimization** | ~65-70% | Docstring evolution, few-shot examples in prompts |
| **Prompting ceiling** | ~70% | Expected wall based on DABStep experience |
| **Architectural innovation** | ~75%+ | Needs new ideas beyond what we've tried |

The ~49% SOTA was achieved with Claude 3.7 Sonnet in Dec 2024. With Claude Sonnet 4.5 and the optimization patterns above, exceeding this is realistic.

---

## 7. Implementation Roadmap

### Sprint 1: Infrastructure Validation (1-2 days)
- [ ] Run 20 retail tasks with current baseline agent
- [ ] Categorize failures: infrastructure vs. model vs. policy
- [ ] Fix any Docker/tool/simulator issues found
- [ ] Establish reproducible baseline score with 3 trials

### Sprint 2: Failure Analysis (1 day)
- [ ] Run ARCA on 20-30 failed traces
- [ ] Quantify failure modes (use SWE-bench's category scheme as template)
- [ ] Map failure modes to architectural fixes
- [ ] Decide if 3-phase or simpler architecture fits the data

### Sprint 3: Agent Architecture (2-3 days)
- [ ] Implement chosen architecture (likely 3-phase)
- [ ] Add policy checklist constraint
- [ ] Add conversation state tracking
- [ ] Phase-gate tool documentation
- [ ] Run 20-task eval, compare to baseline

### Sprint 4: GEPA Optimization (3-5 days)
- [ ] Configure e2e_optimization for tau-bench
- [ ] 10-task eval set, 3 trials per iteration
- [ ] Evolve docstrings + helper methods
- [ ] Track per-task scores for regression detection
- [ ] Target: exceed 49% SOTA

### Sprint 5: Scale & Robustness (2-3 days)
- [ ] Expand to full task set
- [ ] Add airline domain
- [ ] Run Pass^k evaluation (k=3-5)
- [ ] Document final agent design and scores

---

## 8. Risk Factors

**Risk 1: User-simulator variance.** The LLM-powered customer generates different responses each run. This adds noise that DABStep (deterministic) and BigCodeBench (deterministic) didn't have. Mitigation: Run 3+ trials, report median.

**Risk 2: Competing policy constraints.** Retail has 9+ rules. DABStep showed that >5 competing constraints cause cascading regressions. Mitigation: Policy checklist (architectural), not prompt hints.

**Risk 3: GEPA infrastructure stability.** e2e_optimization crashed every 4-6 iterations on DABStep. Mitigation: Smaller eval batches (10 tasks), more frequent checkpointing, 180s timeout per step.

**Risk 4: Prompt ceiling may be lower.** Multi-turn + policy compliance may hit the ceiling earlier than DABStep's 80%. Mitigation: Plan architectural innovations early (Sprint 3), don't wait for GEPA to plateau.

---

## References

### DABStep
- Agent: `experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt49.py` (best)
- Analysis: `docs/dabstep-all-iterations-summary.md`, `docs/dabstep-opt49-final-recommendation.md`
- Framework: `docs/dabstep-generic-decomposition.md` (8-phase decomposition)

### SWE-bench
- Agent: `origin/feat/swebench-optimization:experiments/evaluation-ablations/agents/swebench_opt1.py`
- Environment: `evaluation/environments/swebench.py`
- Adapter: `evaluation/adapters/swebench.py`

### BigCodeBench
- Agent: `experiments/evaluation-ablations/agents/agent006_tools.py`
- Session summary: `docs/bigcodebench-agent006-session-summary.md`
- Adapter: `evaluation/adapters/bigcodebench.py`

### Tau-bench
- Agent (baseline): `experiments/evaluation-ablations/agents/tau_bench_agent.py`
- Adapter: `evaluation/adapters/tau_bench.py` (1725 lines)
- Environment: `evaluation/environments/tau_bench.py`
- Setup notes: `docs/scratch/tau-bench-setup-plan.md`
- Benchmark plan: `docs/benchmark-selection-plan.md`
