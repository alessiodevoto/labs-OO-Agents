# Agent006 Capability Tests: Consolidated Analysis & Work Packages

**Date**: January 6, 2026
**Status**: Ready for team distribution
**Purpose**: Define work packages for improving the capability test suite

---

## Executive Summary

The current capability test suite has **14 tests** that validate core nemo_oo_agents functionality but has significant gaps in measuring what makes nemo_oo_agents valuable. This document consolidates all findings and defines actionable work packages.

### Key Gaps Identified

| Gap | Priority | Current State |
|-----|----------|---------------|
| **Mode Selection** (internal vs code) | P0 | Only one direction tested, weak validation |
| **Token Cost Measurement** | P0 | Not measured at all |
| **Context Manipulation** | P1 | No tests for context block system |
| **Introspection Effectiveness** | P1 | doc(self) not validated |
| **Tool Discovery** | P2 | All tools explicitly documented |
| **Error Recovery** | P2 | REPL refinement never tested |
| **State Validation** | P2 | Order tests only check counts |

---

## Part 1: The Core Framework Capabilities

Agent006 enables capabilities that raw LLMs cannot achieve:

| Capability | Mechanism | Current Test Coverage |
|------------|-----------|----------------------|
| Mode Selection | LLM decides: internal reasoning vs code | ⚠️ Weak (sentiment_single only) |
| Code Execution | `execute_python()` tool | ✅ Good |
| Tool/Method Invocation | Call `self.*` methods | ✅ Good |
| State Management | Read/write `self.attribute` | ⚠️ Weak |
| Child Agent Orchestration | Create subagents, `asyncio.gather()` | ✅ Good |
| REPL Iteration | Multi-turn observe → refine | ⚠️ Weak (hints given) |
| Introspection | `doc(self)`, `brief()`, `methods()` | ❌ Not tested |
| Context Manipulation | `context.update_block()`, `scoped_blocks()` | ❌ Not tested |
| Token Efficiency | Choose efficient execution paths | ❌ Not measured |

---

## Part 2: Work Package Definitions

### WP-0: Infrastructure - Token Cost Measurement 🔴 P0

**Owner**: TBD
**Effort**: 3-5 days
**Dependencies**: None

**Problem**: We have no visibility into token costs. An agent that passes tests but costs 10x more tokens is not better.

**Deliverables**:

1. **Token tracking in test harness**
   ```python
   @dataclass
   class TestResult:
       passed: bool
       output: Any
       # NEW
       input_tokens: int
       output_tokens: int
       total_tokens: int
       execution_steps: int  # Number of execute_python calls
   ```

2. **Token metrics in reports**
   - Tokens per test (input, output, total)
   - Tokens per execution step
   - Comparison across models
   - Comparison across strategy variants

3. **Efficiency scoring**
   ```python
   # Efficiency = correctness / token_cost
   efficiency_score = passed_weight / (total_tokens / baseline_tokens)
   ```

4. **Baseline establishment**
   - Record current token usage per test
   - Track regression in token efficiency

**Acceptance Criteria**:
- [ ] Every test run logs token counts
- [ ] CI reports include token metrics
- [ ] Token regression alerts configured

---

### WP-1: Mode Selection Validation 🔴 P0

**Owner**: TBD
**Effort**: 5-7 days
**Dependencies**: WP-0 (for token tracking)

**Problem**: The agent must correctly decide when to use internal reasoning vs code execution. Currently:
- Only tests "answer directly when you can" (sentiment_single)
- Does NOT test "use code when you must"
- Validation is weak (LLM judge with vague rubric)

**Anti-pattern to prevent**:
```python
# BAD: LLM writes naive code for semantic task
if "happy" in text:
    return "positive"
```

**Deliverables**:

1. **Binary mode metric**
   ```python
   def score_mode_selection(trace, expected_mode: Literal["internal", "code"]) -> bool:
       executed_code = any(call.tool == "execute_python" for call in trace.tool_calls)
       if expected_mode == "internal":
           return not executed_code
       else:
           return executed_code
   ```

2. **Paired test suite** (same domain, different mode requirements)

   | Internal Mode Test | Code Mode Test |
   |-------------------|----------------|
   | `sentiment_single` - "What is the sentiment of 'I love this'?" | `sentiment_filter` - "Filter these 100 texts by positive sentiment" |
   | `compute_trivial` - "What is 2+2?" | `compute_complex` - "What is GCD(827636, 983688)?" |
   | `summarize_single` - "Summarize this paragraph" | `summarize_batch` - "Summarize each of these 10 documents" |
   | `json_valid` - "Is this valid JSON?" | `json_extract` - "Parse this JSON and extract the 'users' field" |

3. **Scorer implementation**
   - Mode selection score (did it use expected mode?)
   - Output correctness score (did it get right answer?)
   - Combined score with penalty for "wrong mode, right answer"

4. **Interface improvement recommendations**
   - Document decision criteria more explicitly in prompts
   - Add examples to strategy instructions

**Acceptance Criteria**:
- [ ] 8+ paired mode selection tests implemented
- [ ] Binary mode metric in all test results
- [ ] ≥90% mode selection accuracy on paired tests

---

### WP-2: Introspection Effectiveness (doc/agentdoc) 🟡 P1

**Owner**: TBD
**Effort**: 5-7 days
**Dependencies**: None

**Problem**: `doc(self)` provides tool documentation to the LLM, but we don't validate:
- Is the information sufficient?
- Is it too verbose (wasting tokens)?
- Does the LLM actually use it effectively?

**Current implementation**:
```python
# Default context block
"python_tools": Block(
    key="python_tools",
    expr="doc(self)",  # Uses agentdoc
    update="True",
    protected=True,
)
```

**Deliverables**:

1. **Introspection tests**
   ```python
   class ToolDiscoveryAgent(Agent):
       # Tools NOT documented in docstring
       async def tool_alpha(self, x: int) -> int:
           """Doubles the input."""
           return x * 2

       async def tool_beta(self, s: str) -> str:
           """Reverses the string."""
           return s[::-1]

       async def complete_task(self, task: str) -> Any:
           """Complete the task. Available tools are on self - use doc(self) to discover them."""
           ...
   ```

2. **doc() output analysis**
   - Measure token count of `doc(self)` output
   - Track which parts LLM actually uses
   - Identify redundant information

3. **Verbosity levels testing**
   - `doc(self)` - full documentation
   - `brief(self)` - signatures only
   - `methods(self)` - method list only
   - Compare accuracy vs token cost tradeoffs

4. **Recommendations for agentdoc configuration**
   - Optimal verbosity level per task type
   - When to use `update="True"` vs static

**Acceptance Criteria**:
- [ ] Tool discovery tests passing at ≥80%
- [ ] doc() output token costs documented
- [ ] Recommendations for verbosity configuration

---

### WP-3: Context Block Manipulation 🟡 P1

**Owner**: TBD
**Effort**: 5-7 days
**Dependencies**: None

**Problem**: The context block system (`context.update_block()`, `context.scoped_blocks()`) is a powerful feature for LLM-driven context management, but has zero test coverage.

**Context system capabilities**:
```python
# Update what information appears in context
context.update_block("python_tools", expr="self.doc.show()")

# Remove a block
context.remove_block("my_block")

# Temporarily override blocks
with context.scoped_blocks({"history": None}):
    result = await self.stateless_helper()
```

**Deliverables**:

1. **Context manipulation tests**
   ```python
   class ContextAwareAgent(Agent):
       async def task_with_context_management(self) -> dict:
           """
           You need to manage context for efficiency:
           1. Check current context blocks using context.get_block()
           2. Remove unnecessary blocks for cleaner context
           3. Add relevant blocks for your subtask
           4. Complete the task and return results
           """
           ...
   ```

2. **Scoped blocks tests**
   - Test `scoped_blocks()` for temporary overrides
   - Verify inheritance to nested calls
   - Verify reversion after context exit

3. **Context optimization tests**
   - Can agent reduce context to save tokens?
   - Can agent add context when needed?
   - Does agent correctly use history block?

4. **Error handling tests**
   - Protected block modification attempts
   - Invalid block expressions

**Acceptance Criteria**:
- [ ] Context manipulation tests at ≥80% pass rate
- [ ] Scoped blocks inheritance verified
- [ ] Token savings from context management documented

---

### WP-4: REPL Refinement & Error Recovery 🟡 P2

**Owner**: TBD
**Effort**: 5-7 days
**Dependencies**: None

**Problem**: REPL's value proposition is iterative refinement, but we never test refinement. Current needle test gives away the strategy.

**Deliverables**:

1. **Error recovery tests**
   ```python
   class ResilientAgent(Agent):
       def __init__(self, **kwargs):
           super().__init__(**kwargs)
           self._call_count = 0

       async def flaky_api(self, x: int) -> int:
           """Sometimes fails. Handle errors gracefully."""
           self._call_count += 1
           if self._call_count < 3:
               raise APIError("Service unavailable - retry")
           return x * 2

       async def resilient_compute(self, value: int) -> int:
           """Call flaky_api and handle failures. Return the result."""
           ...
   ```

2. **True REPL exploration tests** (no hints)
   ```python
   class DataExplorerAgent(Agent):
       def __init__(self, data: dict, **kwargs):
           super().__init__(**kwargs)
           self.data = data  # Complex nested structure

       async def find_anomaly(self) -> str:
           """Find the anomalous entry in self.data. Explore the structure."""
           ...  # NO strategy hints
   ```

3. **Refinement tests**
   - First attempt produces wrong output
   - Agent must observe error and correct
   - Track number of refinement iterations

4. **Update existing tests**
   - Remove strategy hints from needle test
   - Remove keyword mapping from router docstring

**Acceptance Criteria**:
- [ ] Error recovery tests at ≥70% pass rate
- [ ] Exploration tests without strategy hints
- [ ] Refinement iteration tracking in metrics

---

### WP-5: State Validation Enhancement 🟡 P2

**Owner**: TBD
**Effort**: 3-5 days
**Dependencies**: None

**Problem**: Order tests only validate counts, not contents. Agent could produce wrong state and still "pass."

**Current validation**:
```json
{"order_submitted": true, "order_canceled": false, "item_count": 2}
```

**Deliverables**:

1. **Enhanced state validation**
   ```json
   {
     "order_submitted": true,
     "order_items": [
       {"item": "chicken sandwich", "modifications": ["no lettuce"]},
       {"item": "fries", "modifications": []}
     ]
   }
   ```

2. **Complex state scenarios**
   - Multiple items with same name
   - Overlapping modifications
   - Order changes mid-conversation

3. **State mutation tests**
   - Explicit read/write verification
   - Cross-turn state consistency
   - Complex nested state structures

4. **Conversation complexity**
   - Longer conversations (10+ turns)
   - Distractor messages ("The weather is nice")
   - Ambiguous references ("Make that larger" - which item?)

**Acceptance Criteria**:
- [ ] State contents validated, not just counts
- [ ] 5+ data points per order test
- [ ] Distractor message handling tested

---

### WP-6: Test Suite Restructuring 🟢 P3

**Owner**: TBD
**Effort**: 3-5 days
**Dependencies**: WP-1, WP-2, WP-3

**Problem**: Tests are not categorized by capability type. Need tiered structure for regression vs progress tracking.

**Deliverables**:

1. **Category structure**

   | Category | Purpose | Pass Threshold |
   |----------|---------|----------------|
   | A: Mode Selection | Interface intuitiveness | ≥90% |
   | B: Code Execution | Basic code generation | ≥95% |
   | C: Tool/State | Tool usage, state management | ≥85% |
   | D: REPL/Introspection | Exploration, doc() usage | ≥70% |
   | E: Child Agents | Orchestration | ≥90% |
   | F: Context Management | Block manipulation | ≥70% |

2. **Tier structure**
   - **Passing Set**: Must achieve ≥95% (regression guard)
   - **Emerging Set**: Track improvement over time

3. **Config file updates**
   - Add category tags to each test
   - Separate pass thresholds by category
   - CI integration for tiered reporting

4. **Migration plan**
   - Reclassify existing tests
   - Document expected category for new tests

**Acceptance Criteria**:
- [ ] All tests categorized
- [ ] Tiered CI reporting implemented
- [ ] Category-specific pass thresholds enforced

---

### WP-7: Prompt/Interface Improvements 🟢 P3

**Owner**: TBD
**Effort**: 3-5 days
**Dependencies**: WP-1 (mode selection findings)

**Problem**: Current interface may not optimally guide mode selection and tool usage.

**Current prompt issues**:
```
3. If you are confident that you know the answer it is absolutely OK to not execute
   any Python code but just return the result. This is actually much more efficient.
```
- Buried as item #3
- "Confident" is vague
- No examples of decision criteria

**Deliverables**:

1. **Mode selection guidance improvements**
   - Elevate direct answer guidance
   - Provide explicit decision criteria
   - Add examples for boundary cases

2. **doc() usage guidance**
   - When to use doc() vs brief()
   - How to interpret output
   - Trust signals for interfaces

3. **Context block guidance**
   - When to manipulate context
   - How to optimize for token efficiency
   - Scoped blocks usage patterns

4. **A/B testing framework**
   - Compare prompt variants
   - Measure mode selection accuracy
   - Measure token efficiency

**Acceptance Criteria**:
- [ ] Updated strategy prompts
- [ ] A/B test results documented
- [ ] Mode selection accuracy improved by ≥10%

---

## Part 3: Priority Matrix

| Work Package | Priority | Effort | Dependencies | Owner |
|--------------|----------|--------|--------------|-------|
| WP-0: Token Cost Measurement | 🔴 P0 | 3-5d | None | |
| WP-1: Mode Selection Validation | 🔴 P0 | 5-7d | WP-0 | |
| WP-2: Introspection Effectiveness | 🟡 P1 | 5-7d | None | |
| WP-3: Context Block Manipulation | 🟡 P1 | 5-7d | None | |
| WP-4: REPL Refinement & Error Recovery | 🟡 P2 | 5-7d | None | |
| WP-5: State Validation Enhancement | 🟡 P2 | 3-5d | None | |
| WP-6: Test Suite Restructuring | 🟢 P3 | 3-5d | WP-1,2,3 | |
| WP-7: Prompt/Interface Improvements | 🟢 P3 | 3-5d | WP-1 | |

**Total estimated effort**: 33-48 days (parallelizable to ~15-20 days with 3 people)

---

## Part 4: Suggested Execution Order

### Phase 1: Foundation (Week 1-2)
- **WP-0**: Token measurement (critical for all other work)
- **WP-1**: Mode selection (foundational capability)

### Phase 2: Core Capabilities (Week 2-3)
- **WP-2**: Introspection (can run parallel)
- **WP-3**: Context manipulation (can run parallel)
- **WP-5**: State validation (can run parallel)

### Phase 3: Advanced & Integration (Week 3-4)
- **WP-4**: REPL refinement
- **WP-6**: Test restructuring (depends on new tests)
- **WP-7**: Prompt improvements (informed by WP-1 findings)

---

## Part 5: Success Metrics

After completing all work packages:

| Metric | Current | Target |
|--------|---------|--------|
| Mode selection accuracy | ~50% (guess) | ≥90% |
| Token cost visibility | 0% | 100% |
| Tool discovery tests | 0 | ≥5 |
| Context manipulation tests | 0 | ≥5 |
| Error recovery tests | 0 | ≥3 |
| State validation depth | Counts only | Full contents |
| Tests with single data point | 7/14 (50%) | 0/20 (0%) |
| Test categorization | None | 100% |

---

## Appendix A: Original Analysis Points

From initial review (capability_tests_analysis.md):

- ❌ No situational tool usage tests except for explicit prompting
- ❌ No test for navigating attributes and codebase (method signatures)
- ❌ No context manipulation tests
- ⚠️ Order tests need more complex state space validation
- ⚠️ Router tests too explicit (keyword-based routing)
- ⚠️ Calculate examples are artificial (not real-world NL)
- ⚠️ Needle test gives away strategy in prompt
- ❓ Args descriptions in docstrings - unclear if needed

## Appendix B: Test Capability Taxonomy

### Level 0: Mode Selection (Foundation)
- Correct mode choice (internal vs code)
- Efficiency awareness
- Capability self-model

### Level 1: Code Execution
- Correct Python generation
- Variable usage
- Return value production

### Level 2: State & Context
- State reading/writing
- Parameter access
- Cross-turn memory
- Context block manipulation

### Level 3: Tool Usage
- Tool discovery (via doc())
- Tool selection
- Tool invocation
- Tool chaining

### Level 4: REPL Behavior
- Iterative exploration
- Error observation/recovery
- Result refinement
- Convergence

### Level 5: Child Agents
- Agent instantiation
- Delegation
- Parallel dispatch
- Result aggregation

## Appendix C: Existing Test Inventory

| Test | Category | Data Points | Status |
|------|----------|-------------|--------|
| sentiment_single | A: Mode | 4 | Reframe as mode test |
| sentiment_batch | B: Code | 1 | Add data points |
| calculate_single | A/B: Mixed | 8 | Clarify purpose |
| calculate_batch | B: Code | 1 | Add data points |
| needle_in_haystack | D: REPL | 1 | Remove hints |
| router_analyze | E: Child | 2 | Remove keyword mapping |
| router_validate | E: Child | 2 | Remove keyword mapping |
| router_transform | E: Child | 2 | Keep |
| router_multi_* | E: Child | 2 | Keep |
| fast_food_order | C: State | 1 | Enhance validation |
| fast_food_cancel | C: State | 1 | Enhance validation |
| summarize_single | A: Mode | 1 | Pair with batch |
| summarize_batch | B: Code | 1 | Add data points |
