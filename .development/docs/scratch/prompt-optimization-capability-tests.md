# Prompt Optimization Capability Tests Plan

## Goal

Get the capability tests working end-to-end, then use them to optimize strategy prompts across models.

## Current State

The framework exists but hasn't been run end-to-end:
- **13 capability tests** defined in `util/prompt-optimization/config/capabilities.yaml`
- **Test agents** implemented in `test_agents/capability_tests.py`
- **Test functions** implemented in `test_functions/capability_tests.py`
- **Evaluators** implemented in `evaluators/capability_tests.py`
- **Viewer** has good infrastructure (list, summary, detail, filtering)
- **Models** configured including `qwen3-next-80b`

### Gaps

1. ~~**Strategy prompts** are hardcoded constants, not parameterized for optimization~~ **DONE**
2. **Evaluators** check output correctness but lack LLM-as-judge for method correctness
3. **Runner** hasn't been validated end-to-end with capability tests
4. **Viewer** lacks capability-specific renderers and statistics views

---

## Phase 0: Refactor Strategies for Prompt Templates (COMPLETE)

All prompts are now parameterized via `PurePythonConfig` dataclass.

### 0.1 Prompt Inventory

| Strategy | Prompt Type | Current Location | Description |
|----------|-------------|------------------|-------------|
| PurePythonStrategy | Instructions | `PurePythonConfig.instructions` | ✅ Main instructions (~60 lines) |
| PurePythonStrategy | Error: empty | `PurePythonConfig.error_empty` | ✅ "Empty response..." |
| PurePythonStrategy | Error: return outside fn | `PurePythonConfig.error_return_outside` | ✅ "This error means..." |
| PurePythonStrategy | Error: syntax | `PurePythonConfig.error_syntax` | ✅ "Syntax error..." |
| PurePythonStrategy | Error: method raised | `PurePythonConfig.error_method_raised` | ✅ "Method raised error..." |
| PurePythonStrategy | Feedback: not done | `PurePythonConfig.feedback_not_done` | ✅ "Define X to complete" |
| PurePythonStrategy | Task templates | `PurePythonConfig.initial_task/condensed_task` | ✅ For task_message_mode |
| ~~PythonTaskStrategy~~ | ~~All~~ | ~~Deleted~~ | ✅ Now `task_message_mode=True` |
| StructuredOutputStrategy | Task | `STRUCTURED_OUTPUT_PROMPT` constant | TODO: Add `StructuredOutputConfig` |
| StructuredOutputStrategy | Error: validation | inline | TODO: "Your output failed validation..." |
| ReflexionStrategy | Reflection | `REFLECTION_PROMPT` constant | TODO: Add `ReflexionConfig` |
| ReflexionStrategy | Feedback: improvement | `_format_reflection_feedback()` | TODO: Issues + suggestions |
| ReflexionStrategy | Feedback: retry | inline | TODO: "Previous attempt failed..." |

### 0.2 Unified Config Dataclass (IMPLEMENTED)

All configuration in a single `PurePythonConfig` dataclass. PythonTaskStrategy was deleted - use `task_message_mode=True` instead.

```python
# pure_python.py - all config in one dataclass

from dataclasses import dataclass

@dataclass
class PurePythonConfig:
    """Configuration for PurePythonStrategy. Override any field to customize."""

    # Execution limits
    max_iterations: int = 10
    max_retries: int = 3

    # Mode: False = prompt in system, True = prompt in task message
    task_message_mode: bool = False

    # Task templates (for task_message_mode)
    # Templates support any Python expression via runtime.expand_variables()
    # Built-in placeholders: {instructions}, {agent_doc}, {task}, {method_info}, {current_call}
    # Direct expressions: {self.doc()}, {call.docstring}, {config.max_iterations}
    initial_task: str = "{instructions}\n\n{agent_doc}\n\n{task}\n\n{method_info}\n\n{current_call}"
    condensed_task: str = "{task}\n\n{method_info}\n\n{current_call}"

    # Prompts
    instructions: str = """## PURE_PYTHON Mode (Code Execution Loop)
...(existing prompt)...
"""
    error_empty: str = "Empty response. Output Python code directly. Define `{method}` to complete."
    error_syntax: str = "**Syntax error - ensure you output a complete method definition.**..."
    error_return_outside: str = "**This error means you output a return statement...**"
    error_method_raised: str = "Method `{method}` raised error:\n```\n{error}\n```\nFix and redefine `{method}`."
    feedback_not_done: str = "Define `{method}` to complete the task."


class PurePythonStrategy(GenerationStrategy):
    def __init__(self, config: PurePythonConfig | None = None):
        self.config = config or PurePythonConfig()

    @property
    def strategy_prompt(self) -> str:
        if self.config.task_message_mode:
            return ""
        return self.config.instructions
```

### 0.3 Template Expansion via runtime.expand_variables()

Task templates now use `runtime.expand_variables()` instead of `str.format()`, enabling any Python expression:

```python
# _build_initial_task uses runtime.expand_variables()
return runtime.expand_variables(
    self.config.initial_task,
    extra_context={
        "call": call,
        "config": self.config,
        # Backwards-compatible placeholders
        "instructions": self.config.instructions.strip(),
        "agent_doc": agent_doc,
        "task": task_section,
        "method_info": method_section,
        "current_call": call_section,
    },
    error_mode="raise",
).strip()
```

This means templates can use:
- Built-in placeholders: `{instructions}`, `{agent_doc}`, `{task}`, `{method_info}`, `{current_call}`
- Direct expressions: `{self.doc()}`, `{call.docstring}`, `{config.max_iterations}`

### 0.4 To Customize

```python
# Override specific fields
minimal = PurePythonConfig(
    instructions="Output Python code. Define the target method.",
    error_empty="Empty. Define `{method}`.",
)
strategy = PurePythonStrategy(config=minimal)

# Task message mode (replaces PythonTaskStrategy)
strategy = PurePythonStrategy(config=PurePythonConfig(task_message_mode=True))

# Custom template with direct expressions
custom = PurePythonConfig(
    task_message_mode=True,
    initial_task="Instructions: {config.instructions}\n\nTask: {call.docstring}",
)
```

### 0.5 Files Modified

| File | Changes |
|------|---------|
| `strategies/pure_python.py` | ✅ `PurePythonConfig` dataclass with all config |
| `strategies/pure_python.py` | ✅ `_build_initial_task()` uses `runtime.expand_variables()` |
| `strategies/pure_python.py` | ✅ `_build_condensed_task()` uses `runtime.expand_variables()` |
| `strategies/__init__.py` | ✅ Exports `PurePythonConfig` |
| `strategies/python_task.py` | ✅ DELETED - fully consolidated into pure_python.py |
| `strategies/structured_output.py` | TODO: Add `StructuredOutputConfig` dataclass |
| `strategies/reflexion.py` | TODO: Add `ReflexionConfig` dataclass |

---

## Phase 1: Get Capability Tests Working

**Success Criteria:**
- All capability tests have verifiers + LLM-as-judge
- All capabilities pass with qwen3-next-80b and single sample
- Viewer displays results clearly

### 1.1 Validate Test Infrastructure

| Task | File | Description |
|------|------|-------------|
| Run basic test | `runner.py` | `python runner.py config/capabilities.yaml --test sentiment_single --models qwen3-next-80b` |
| Debug failures | various | Fix any import/path issues |
| Verify trace extraction | `runner.py` | Ensure `extract_llm_trace()` captures code correctly |

### 1.2 Enhance Evaluators with LLM-as-Judge

Currently evaluators check output correctness. Add method correctness checks:

| Test Category | What to Judge | Implementation |
|---------------|---------------|----------------|
| Scale Awareness (single) | Did NOT write unnecessary code | Check if output is direct answer vs code loop |
| Scale Awareness (batch) | DID write code loop | Check for iteration patterns |
| Generation Methods | Used `...` for LLM-per-item | Check for generation method definition |
| Needle-in-Haystack | Used REPL exploration | Check for print statements / data inspection |
| Router | Correct parallelism decisions | Did it parallelize independent agents? Did it encode dependencies correctly for sequential work? |
| Fast Food | Correct tool sequence | Verify tool_history matches expected pattern |

**Implementation approach:**

```python
# In evaluators/capability_tests.py

class CapabilityEvaluator:
    def __init__(self, llm_client=None):
        """Initialize with optional LLM client for method judgment."""
        self.llm_client = llm_client

    def evaluate_sentiment_single(self, agent, result, text, expected) -> EvalResult:
        # Existing correctness check
        correct = result.lower().strip() == expected.lower().strip()

        # NEW: Method correctness via code inspection
        code = self._extract_generated_code(agent)
        method_correct = self._is_direct_answer(code)  # No unnecessary loop

        # Optional: LLM-as-judge for subtle cases
        if self.llm_client and not method_correct:
            method_correct = await self._llm_judge_method(
                code,
                "Should answer directly without writing a loop"
            )

        return EvalResult(
            passed=correct and method_correct,
            score=1.0 if (correct and method_correct) else (0.5 if correct else 0.0),
            metrics={
                "output_correct": correct,
                "method_correct": method_correct,
                "wrote_code": self._has_loop_pattern(code),
            },
            reasoning=f"Output: {correct}, Method: {method_correct}"
        )
```

### 1.3 Update Viewer for Capability Tests

| Component | Change |
|-----------|--------|
| `renderers/capability.js` | New renderer showing output_correct + method_correct separately |
| `views.js` | Add capability-specific summary metrics |
| CSS | Visual distinction for method correctness (optimal badge) |

### 1.4 Run All Tests Once

```bash
cd util/prompt-optimization
python runner.py config/capabilities.yaml --models qwen3-next-80b
```

Fix any test-specific failures until all 13 pass.

### 1.5 Phase 1 Status (COMPLETE - 2025-12-07)

**Results: 11/14 tests passing with qwen3-next-80b**

| Category | Tests | Passed | Notes |
|----------|-------|--------|-------|
| Scale Awareness | sentiment_single, sentiment_batch | 1/2 | sentiment_single fails method check (uses keyword matching) |
| | calculate_single, calculate_batch | 2/2 | Both pass |
| | summarize_single, summarize_batch | 1/2 | summarize_batch fails method check (no `...` generation) |
| REPL + Exploration | needle_in_haystack | 0/1 | Output incorrect, uses keyword matching |
| Subagent Routing | router_* (5 tests) | 5/5 | All pass with `llm=self._llm` pattern |
| Stateful Multi-turn | fast_food_* (2 tests) | 2/2 | Both pass after docstring fix |

**Method Correctness Evaluation:**
- Implemented `quick_judge()` in `evaluators/method_judge.py`
- Tracks output_correct vs method_correct separately
- Identifies wrong approach even when output is correct

**Key Fixes Applied:**
1. Router tests: Added `llm=self._llm` instruction to docstring
2. Fast food tests: Documented `order_items` structure (list of dicts)
3. Method judge: Pattern-based evaluation for scale awareness tests

**Files Created/Modified:**
- `evaluators/method_judge.py` - New: MethodJudgeAgent + quick_judge()
- `evaluators/capability_tests.py` - Updated: method_judgment in EvalResult
- `test_agents/capability_tests.py` - Updated: docstrings for router + fast food

**Next Steps (Phase 2):**
- Scale to 10 samples per test
- Run across multiple models
- Add viewer statistics

### 1.6 Note: Runner as Agent006 Agent

**Consider replacing the runner with an agent006-based implementation:**

The current `runner.py` is imperative Python code. Several components could be replaced with agent006 patterns:

| Current Component | Agent006 Replacement |
|-------------------|---------------------|
| Test case iteration | Runner agent that spawns subagents per test |
| Parallel execution | Agent parallelism via `asyncio.gather` on subagent calls |
| Result aggregation | Agent method that collects subagent results |
| Error handling/retry | Built-in retry logic in strategies |
| Trace collection | Automatic via `enable_tracing()` |

**Example architecture:**

```python
@agent
class TestRunner:
    """Orchestrates capability test execution."""

    config: TestConfig
    models: list[str]

    @plan
    def run_test_suite(self) -> TestSuiteResults:
        """Run all capability tests across configured models.

        Spawns subagents for each (test, model) combination.
        Aggregates results and generates summary.
        """
        ...

    @plan
    def run_single_test(self, test_name: str, model: str) -> TestResult:
        """Run a single capability test.

        Instantiates the test agent, executes, evaluates result.
        """
        ...
```

**Benefits of agent006 runner:**
- Unified tracing across runner + test agents
- Consistent retry/error handling
- LLM-assisted result analysis
- Easier debugging via trace viewer

**Trade-off:** More complexity for simple test orchestration. Consider this for Phase 2+ when scaling up.

---

## Phase 2: Scale Up Testing

**Success Criteria:**
- 10 samples per capability test
- Results across multiple models
- Viewer shows aggregated statistics

### 2.1 Update Config for Multiple Samples

```yaml
# capabilities.yaml
test_cases:
  sentiment_single:
    name: "Sentiment x1 - Answer Directly"
    samples: 10  # Run 10 times
    ...
```

### 2.2 Run Across Models

**Target Models (8 total):**

| Alias | Full Model Path | Provider | Notes |
|-------|-----------------|----------|-------|
| qwen3-next-80b | nvidia_nim/qwen/qwen3-next-80b-a3b-instruct | NVIDIA NIM | Qwen3 80B |
| qwen3-thinking | nvidia_nim/qwen/qwen3-next-80b-a3b-thinking | NVIDIA NIM | Qwen3 80B (reasoning) |
| nemotron-super-49b | nvidia_nim/nvidia/nemotron-super-49b-v1 | NVIDIA NIM | Nemotron Super 49B |
| nemotron-nano-9b | nvidia_nim/nvidia/nemotron-nano-8b-v1 | NVIDIA NIM | Nemotron Nano 9B |
| gpt-oss-20b | nvidia_nim/openai/gpt-oss-20b | NVIDIA NIM | GPT-OSS 20B |
| o4-mini | openai/o4-mini | OpenAI | O4 Mini |
| claude-sonnet-4-5 | anthropic/claude-sonnet-4-5-20241022 | Anthropic | Claude Sonnet 4.5 |
| claude-haiku-4-5 | anthropic/claude-haiku-4-5-20241022 | Anthropic | Claude Haiku 4.5 |

```bash
python runner.py config/capabilities.yaml \
  --models qwen3-next-80b,qwen3-thinking,nemotron-super-49b,nemotron-nano-9b,gpt-oss-20b,o4-mini,claude-sonnet-4-5,claude-haiku-4-5
```

### 2.3 Viewer Statistics Updates

| Feature | Description |
|---------|-------------|
| Per-test pass rate | Show 8/10 passed format |
| Confidence intervals | Visual indication of sample variance |
| Model comparison matrix | Capability x Model heatmap |
| Method correctness breakdown | Separate from output correctness |

### 2.4 Slice and Filter

- Filter by: model, capability category, pass/fail, optimal/suboptimal
- Compare: same capability across models
- Trend: track improvement across runs

---

## Phase 3: Prompt Optimization

**Success Criteria:**
- Systematic reduction of prompt size while maintaining performance
- Viewer tracks prompt versions and their performance

### 3.1 Create Prompt Variants

Since Phase 0 made prompts customizable via dataclass, just create variant instances:

```python
# In test runner or test config
from agent006.strategies.pure_python import PurePythonStrategy, PurePythonConfig

# Variant 1: Original (default)
original = PurePythonStrategy()

# Variant 2: Condensed
condensed = PurePythonStrategy(config=PurePythonConfig(
    instructions="Output Python code. No markdown. Define target method to complete.",
    error_empty="Empty. Define `{method}`.",
    # ... other shortened prompts
))

# Variant 3: Minimal
minimal = PurePythonStrategy(config=PurePythonConfig(
    instructions="Python code only. Define {method}.",
    error_empty="Define `{method}`.",
    error_syntax="Syntax error.",
))
```

### 3.3 Run Optimization Experiments

```bash
# Test prompt variant
python runner.py config/capabilities.yaml \
  --models qwen3-next-80b \
  --prompt-version v2_condensed
```

### 3.4 Viewer Prompt Tracking

| Feature | Description |
|---------|-------------|
| Prompt version label | Show which prompt produced results |
| A/B comparison | Side-by-side same test, different prompts |
| Degradation alerts | Highlight when shorter prompt hurts performance |
| Pareto frontier | Visualize size vs performance tradeoff |

### 3.5 Optimization Strategy

1. **Baseline**: Run all tests with original prompt, establish pass rates
2. **Ablation**: Remove sections one at a time, measure impact
3. **Compress**: Rewrite remaining sections more concisely
4. **Model-specific**: Some models may need more/less guidance
5. **Final**: Select smallest prompt that maintains >95% of baseline performance

### 3.6 Prompt Optimization Agent

Build an agent006-based agent that analyzes traces and proposes prompt improvements:

```python
@agent
class PromptOptimizer:
    """Analyzes test traces and proposes prompt optimizations."""

    traces: list[Trace]  # Input traces from capability test runs
    current_config: PurePythonConfig  # Current prompt configuration

    @plan
    def analyze_failures(self) -> PromptAnalysis:
        """Analyze traces where tests failed or used suboptimal methods.

        Identify patterns:
        - Where models misunderstood instructions
        - Unnecessary verbosity in prompts
        - Missing guidance that caused errors
        - Redundant instructions models already follow
        """
        ...

    @plan
    def propose_changes(self, analysis: PromptAnalysis) -> list[PromptChange]:
        """Propose specific prompt changes based on failure analysis.

        Each change includes:
        - Which config field to modify
        - Proposed new text
        - Rationale from trace evidence
        - Expected impact on test results
        """
        ...

    @plan
    def generate_variant(self, changes: list[PromptChange]) -> PurePythonConfig:
        """Generate a new PurePythonConfig with proposed changes applied."""
        ...
```

**Workflow:**
1. Run capability tests, collect traces
2. PromptOptimizer reads traces + current config
3. Agent identifies failure patterns and proposes changes
4. Generate new config variant
5. Re-run tests with new variant
6. Compare results, iterate

**Benefits:**
- Automated hypothesis generation from trace data
- Systematic exploration of prompt space
- Evidence-based rationale for each change
- Reproducible optimization process

---

## Implementation Order

### Phase 0: Prompt Refactor (COMPLETE)

1. [x] Add `PurePythonConfig` dataclass to `pure_python.py`
2. [x] Use `runtime.expand_variables()` for task templates
3. [x] Delete `python_task.py` - consolidated into `task_message_mode=True`
4. [x] Export `PurePythonConfig` from `strategies/__init__.py`
5. [x] Run existing tests to verify no regressions (484 passed)
6. [ ] Add `StructuredOutputConfig` dataclass to `structured_output.py`
7. [ ] Add `ReflexionConfig` dataclass to `reflexion.py`

### Phase 1: Get Capability Tests Working (COMPLETE - 2025-12-07)

1. [x] Validate runner with single capability test
2. [x] Add method correctness checks to evaluators (`evaluators/method_judge.py`)
3. [x] Run all 14 tests, fix failures (11/14 passing)
4. [ ] Create capability renderer for viewer
5. [ ] Verify viewer displays all results correctly
6. [ ] (Optional) Evaluate agent006-based runner for Phase 2+

### Phase 2: Scale Up

1. [ ] Update configs for 10 samples
2. [ ] Run across 3+ models
3. [ ] Add statistics views to viewer
4. [ ] Implement model comparison matrix
5. [ ] Add filtering by method correctness

### Phase 3: Prompt Optimization

1. [ ] Create prompt variants (condensed, minimal, ablated)
2. [ ] Add `--prompt-version` to runner
3. [ ] Run optimization experiments
4. [ ] Add prompt version tracking to viewer
5. [ ] Document optimal prompts per model
6. [ ] Build PromptOptimizer agent (trace analysis → prompt proposals)
7. [ ] Integrate optimizer into experiment workflow

---

## Key Files Modified (Phase 0)

| File | Changes |
|------|---------|
| `src/agent006/strategies/pure_python.py` | `PurePythonConfig` dataclass, `runtime.expand_variables()` for templates |
| `src/agent006/strategies/__init__.py` | Export `PurePythonConfig` |
| `src/agent006/strategies/python_task.py` | DELETED |
| `src/agent006/decorators.py` | Updated docstring examples |
| `src/agent006/strategies/reflexion.py` | Updated docstring examples |
| `tests/strategies/test_pure_python_strategy.py` | Updated for config-based API |
| `tests/strategies/test_python_task_strategy.py` | Tests `task_message_mode=True` behavior |
| `tests/strategies/test_reflexion_strategy.py` | Updated for config-based API |
| `tests/test_decorator_strategy.py` | Updated for config-based API |

### Phases 1-3 (Capability Tests + Optimization)

| File | Changes |
|------|---------|
| `util/prompt-optimization/evaluators/capability_tests.py` | Add method correctness + LLM judge |
| `util/prompt-optimization/runner.py` | Add `--prompt-version` flag |
| `util/prompt-optimization/viewer/frontend/js/renderers/capability.js` | New renderer |
| `util/prompt-optimization/viewer/frontend/js/views.js` | Statistics views |
| `util/prompt-optimization/config/capabilities.yaml` | Samples count |

---

## Success Metrics

| Metric | Phase 1 | Phase 2 | Phase 3 |
|--------|---------|---------|---------|
| Tests passing | 13/13 | 13/13 across models | 13/13 with optimized prompts |
| Samples per test | 1 | 10 | 10 |
| Models tested | 1 | 3+ | 3+ |
| Prompt size | baseline | baseline | minimal viable |
| Viewer completeness | Basic | Statistics | Full tracking |

---

## Open Questions

1. **LLM-as-judge cost**: Should we use a cheap model (haiku) for method correctness checks?
2. **Prompt caching**: Can we leverage prompt caching for repeated tests?
3. **Async parallelism**: Should runner execute tests in parallel per model?
4. **Regression tracking**: How to persist baselines for comparison across runs?
