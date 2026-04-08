# E2E Optimization Process

## Overview

The e2e optimization loop iteratively improves agent prompts/code based on evaluation results.

## Process Diagram

```mermaid
flowchart TD
    subgraph Setup["Phase 1: Setup"]
        A[Load Config] --> B[Copy Seed Agents to Gen 0]
        B --> C[Load Training Data]
    end

    subgraph Eval["Phase 2: Evaluate"]
        D[Run Agent on Training Samples] --> E[Score Outputs]
        E --> F[Record Results & Traces]
    end

    subgraph Analyze["Phase 3: Analyze"]
        G[Identify Failures] --> H[Select Differential Samples]
        H --> I[Extract Failure Patterns]
    end

    subgraph Reflect["Phase 4: Reflect"]
        J[Analyze Traces] --> K[Generate Hypotheses]
        K --> L[Propose Code Changes]
    end

    subgraph Accept["Phase 5: Accept/Reject"]
        M[Apply Proposed Changes] --> N[Re-evaluate]
        N --> O{Better Score?}
        O -->|Yes| P[Accept & Save to Gen N+1]
        O -->|No| Q[Reject & Rollback]
    end

    Setup --> Eval
    Eval --> Analyze
    Analyze --> Reflect
    Reflect --> Accept
    P --> D
    Q --> Reflect
```

## Detailed Iteration Flow

```mermaid
sequenceDiagram
    participant U as User
    participant O as Optimizer
    participant A as Agent
    participant S as Scorer
    participant R as Reflector

    U->>O: run_eval(n_runs=1)
    loop For each sample
        O->>A: _run_evaluation(task_input)
        A-->>O: {response, trace}
        O->>S: score(expected, actual)
        S-->>O: ScoreResult
    end
    O-->>U: Evaluation complete

    U->>O: analyze()
    O->>O: Find differential samples
    O-->>U: Show failures & patterns

    U->>O: reflect()
    O->>R: Analyze traces + failures
    R-->>O: Proposed code changes
    O-->>U: Review proposed changes

    U->>O: accept_or_reject()
    O->>O: Apply changes
    O->>A: Re-evaluate
    alt Score improved
        O->>O: Save to new generation
        O-->>U: Accepted!
    else Score worse
        O->>O: Rollback changes
        O-->>U: Rejected
    end
```

## DABStep Optimization Configuration

```mermaid
graph LR
    subgraph Input
        TD[train_data.jsonl<br/>10 samples]
        AG[agent.py<br/>DABStepAgent]
    end

    subgraph Optimizer
        CF[config.yaml]
        OPT[Optimizer]
    end

    subgraph Output
        GEN[Generation N<br/>Improved Agent]
        RES[Results<br/>Pass Rate]
    end

    TD --> OPT
    AG --> OPT
    CF --> OPT
    OPT --> GEN
    OPT --> RES
```

## Key Components

| Component | File | Purpose |
|-----------|------|---------|
| Optimizer | `optimizer.py` | Orchestrates the optimization loop |
| Config | `config.yaml` | Defines target files, scorers, objectives |
| Agent | `agent.py` | The code being optimized |
| Scorer | `scorers/*.py` | Evaluates agent outputs |
| Reflector | `agents/analyze_agent.py` | Analyzes failures, proposes changes |

## User Checkpoints

The optimization is designed for user-in-the-loop operation:

1. **After Eval**: Review pass rates and failures
2. **After Analyze**: Review differential samples
3. **After Reflect**: Review proposed code changes
4. **After Accept/Reject**: Decide to continue or stop

## Current DABStep Status

- **Baseline**: agent000 at 50% (5/10)
- **Target**: Improve pass rate through iterative optimization
- **Pareto criteria**: Success rate only (no token limits)
