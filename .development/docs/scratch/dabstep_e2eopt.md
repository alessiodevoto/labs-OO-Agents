# DABStep E2E Optimization Plan

## Overview

This document outlines the approach for systematically improving the DABStep agent through:
1. **Generate answers** working through each training question to derive and document solutions
2. **trace_analyzer** diagnosing agent failures and root-causing them
3. **E2E optimizer** automatically rewriting agents based on failure patterns

## Goal

Improve DABStep benchmark performance (currently SOTA ~16% with o3-mini) through:
1. Deep understanding of each training question's solution
2. Root cause analysis of agent failures
3. Automated agent rewriting in an optimization loop

---

## Phase 1: Solution Analysis

### Objective
Derive the right answer for each training set question + the golden path (rules, data, etc)

### Inputs
- **Training set**: HuggingFace `adyen/DABstep` dev split (~450 questions)
- **Context files**: `~/.cache/dabstep/data/context/`
  - `payments.csv` - 138k transactions
  - `fees.json` - 1000 fee structures
  - `manual.md` - Domain knowledge
  - `merchant_data.json`, `merchant_category_codes.csv`, `acquirer_countries.csv`

### Outputs
- Solution files in `experiments/evaluation-ablations/dabstep_solutions/`
- Each file named `dabstep_{task_id}.md`

### Solution File Format

```markdown
# DABStep Sample {task_id} - Solution Analysis

## Question
> {exact question text}

## Expected Answer
`{expected answer}`

## Key Insight
{The critical domain knowledge or computation required}

## Step-by-Step Solution
1. {Step 1: What data to load/filter}
2. {Step 2: What rules to apply from manual.md}
3. {Step 3: Exact computation}
4. {Step 4: Format the answer}

## Relevant Rules
- **{Rule Name}** (manual.md line {N}): {exact quote}
- **{Rule Name}**: {exact quote}

## Data Files Used
- {file1}: {what columns/fields}
- {file2}: {what columns/fields}

## Common Mistakes
- {Mistake 1}: {why it's wrong}
- {Mistake 2}: {why it's wrong}
```

---

## Phase 2: Trace Analysis & Root Cause Diagnosis

### Objective
For each failed evaluation run, diagnose why the agent got it wrong.

### Inputs
- **Traces**: `experiments/evaluation-ablations/results/*/traces/*.006trace.jsonl`
- **Eval results**: `experiments/evaluation-ablations/results/*/*.006eval.json`
- **Solution docs**: From Phase 1

### Tool: trace_analyzer

Located at `util/e2e_optimization/src/e2e_optimization/trace_analyzer.py`

```python
from e2e_optimization.trace_analyzer import analyze_trace_failure

analysis = await analyze_trace_failure(
    rendered_trace=trace_markdown,
    eval_details={"input": ..., "expected": ..., "output": ..., "error": ...},
    model="anthropic/claude-3-5-haiku-latest",  # or other small model
)

print(analysis.to_condensed_markdown())
```

### Failure Categories (from trace_analyzer)

1. **Prompt Clarity Issues**: LLM misunderstands task, produces wrong approach
2. **Missing Context**: LLM doesn't know about available tools/methods
3. **Output Format Errors**: Correct logic but wrong return type/format
4. **Multi-Turn Confusion**: LLM loses track across turns, repeats work
5. **Tool Misuse**: Calls tools with wrong arguments, ignores tool results
6. **Subagent Coordination Issues**: Parent doesn't properly delegate
7. **Forbidden Operations**: Code uses `import`, forbidden syntax, or infinite loops

### Process
1. Run evaluation with tracing enabled
2. For each failed sample:
   a. Load the trace file
   b. Run `analyze_trace_failure()` to get structured diagnosis
   c. Cross-reference with solution doc from Phase 1
   d. Categorize: Was it prompting? Data access? Wrong rule? Computation error?
3. Aggregate failure patterns across samples

### Output: Failure Analysis Report
```markdown
## DABStep Failure Analysis - {date}

### Summary
- Total samples: N
- Passed: X (Y%)
- Failed: Z

### Failure Category Breakdown
| Category | Count | % of Failures |
|----------|-------|---------------|
| Prompt Clarity | 10 | 25% |
| Missing Context | 8 | 20% |
| ...

### Top Failure Patterns
1. **{Pattern}**: {description} - {N} samples
   - Samples: {list}
   - Root cause: {analysis}
   - Fix hypothesis: {idea}
```

---

## Phase 3: E2E Optimization Loop

### Objective
Automatically improve the agent by rewriting prompts/architecture based on failure analysis.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    E2E Optimization Loop                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │ Eval Runner │───▶│ Trace Store │───▶│ Failure Analyzer    │  │
│  │             │    │             │    │ (trace_analyzer.py) │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
│         ▲                                       │               │
│         │                                       ▼               │
│         │                              ┌─────────────────────┐  │
│         │                              │ Failure Report      │  │
│         │                              │ - Categories        │  │
│         │                              │ - Patterns          │  │
│         │                              │ - Fix hypotheses    │  │
│         │                              └─────────────────────┘  │
│         │                                       │               │
│         │                                       ▼               │
│  ┌──────┴──────┐                       ┌─────────────────────┐  │
│  │ Agent Code  │◀──────────────────────│ Agent Rewriter      │  │
│  │ (v002, etc) │                       │ (LLM-powered)       │  │
│  └─────────────┘                       └─────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Components

1. **Eval Runner**: `run_ablation.py` - runs agent on benchmark, produces traces
2. **Trace Store**: JSONL files in `results/*/traces/`
3. **Failure Analyzer**: `trace_analyzer.py` - diagnoses each failure
4. **Failure Report**: Aggregated analysis with fix hypotheses
5. **Agent Rewriter**: LLM that modifies agent code based on failure patterns

### Agent Rewriter Design

The rewriter takes:
- Current agent code (`dabstep_agent00X.py`)
- Failure analysis report
- Solution examples from `dabstep_solutions/`

And produces:
- New agent code (`dabstep_agent00{X+1}.py`)
- Changelog explaining what was modified and why

### Rewrite Strategies

1. **Prompt Engineering**: Modify docstrings/system prompts
2. **Context Adjustment**: Add/remove context blocks
3. **Architecture Changes**: Add/remove subagents, change flow
4. **Tool Additions**: Add helper methods for common operations

---

## Current State

### Agents
- `dabstep_agent000.py` - Single-method baseline
- `dabstep_agent001.py` - Multi-step workflow
- `dabstep_agent002.py` - Subagent architecture (RulesLawyer, SolutionVerifier)

### Solutions Documented (10 total)
- `dabstep_5.md` - Card scheme data extraction
- `dabstep_49.md` - Transaction filtering
- `dabstep_70.md` - Payment method analysis
- `dabstep_1273.md` - Fee rule matching
- `dabstep_1305.md` - Merchant category analysis
- `dabstep_1464.md` - Volume calculation
- `dabstep_1681.md` - ACI code interpretation
- `dabstep_1753.md` - Multi-field filtering
- `dabstep_1871.md` - Fee calculation edge case (complex fee matching)
- `dabstep_2697.md` - Rate computation
- `analysis_20260114.md` - Aggregated findings from 10-sample analysis

### Known Issues (from dabstep_1871 analysis)
1. **Undocumented fee matching**: Multiple fee rules can match a transaction, precedence rules not documented
2. **ACI specificity**: More specific ACI lists may take precedence over broader ones
3. **Answer precision**: Some expected answers have precision that's hard to derive from documented rules

---

## Next Steps

### Immediate (Phase 1 Start) - COMPLETED 2026-01-14
1. [x] Load DABStep training set via HuggingFace
2. [x] Download context files to `~/.cache/dabstep/`
4. [x] Analyzed 10 questions from eval run (4 passed, 6 failed)
5. [x] Documented key findings in `dabstep_solutions/analysis_20260114.md`

**Key Findings:**
- "Not Applicable" misinterpretation - agent answers "no" instead
- Empty list `[]` = applies to all (undocumented but essential)
- Monthly volume constraints require actual calculation
- Fee matching algorithm has undocumented specificity rules

### Short-term (Phase 2)
1. [ ] Run evaluation with current agent (v002)
2. [ ] Collect traces for all failures
3. [ ] Run trace_analyzer on each failure
4. [ ] Produce failure analysis report

### Medium-term (Phase 3)
1. [ ] Build Agent Rewriter component
2. [ ] Implement rewrite strategies
3. [ ] Run optimization loop
4. [ ] Track improvement over iterations

---

## Key Files

| File | Purpose |
|------|---------|
| `evaluation/adapters/dabstep.py` | Benchmark adapter, data loading, scoring |
| `experiments/evaluation-ablations/agents/dabstep_agent*.py` | Agent implementations |
| `experiments/evaluation-ablations/dabstep_solutions/` | Manual solution docs |
| `util/e2e_optimization/src/e2e_optimization/trace_analyzer.py` | Failure diagnosis |
| `experiments/evaluation-ablations/run_ablation.py` | Evaluation runner |

---

## Success Metrics

1. **Pass rate improvement**: Track % correct across iterations
2. **Failure category reduction**: Reduce specific failure types
3. **Solution coverage**: % of training questions with documented solutions
4. **Agent complexity**: Balance improvement vs. agent simplicity
