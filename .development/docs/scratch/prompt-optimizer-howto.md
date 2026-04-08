# Prompt Optimizer - How To Guide

A framework for testing and comparing different prompt configurations for agent006 agents.

## Quick Start

```bash
cd util/prompt-optimization

# Start the viewer (in one terminal)
cd viewer/backend && python main.py

# Run tests (in another terminal)
python runner.py config/capabilities.yaml --models qwen3-next-80b --test sentiment_single
```

View results at: http://localhost:8080

## Running Tests

### Single Test
```bash
python runner.py config/capabilities.yaml --models qwen3-next-80b --test sentiment_single
```

### All Tests with One Model
```bash
python runner.py config/capabilities.yaml --models qwen3-next-80b
```

### Named Experiment (Recommended)
```bash
python runner.py config/capabilities.yaml --experiment prompt_comparison
```

Named experiments are defined in the config file and specify which models and strategy configs to compare.

### Compare Strategy Configs (A/B Testing)
```bash
python runner.py config/capabilities.yaml --models qwen3-next-80b --strategy-config system,task
```

This runs all tests twice - once with each strategy config - and saves combined results for comparison.

## Configuration Files

### Main Config: `config/capabilities.yaml`

Defines test cases, strategy configs, and experiments:

```yaml
name: capability_tests
description: "Tests for agent006 capabilities"

# Strategy configs define how prompts are constructed
strategy_configs:
  system:
    description: "Instructions in system message"
    system_message_template: |
      {instructions}

      {self.doc()}
    task_message_template: |
      # Task
      {task}

      {method_info}
      {current_call}

  task:
    description: "Instructions in task message (no self.doc())"
    system_message_template: ""
    task_message_template: |
      # Instructions
      {instructions}

      # Task
      {task}

      {method_info}
      {current_call}

# Named experiments
experiments:
  prompt_comparison:
    description: "Compare system vs task message modes"
    models: [qwen3-next-80b]
    strategy_configs: [system, task]

  multi_model:
    description: "Compare across models"
    models: [qwen3-next-80b, claude-sonnet-4-5]
    strategy_configs: [system, task]

# Test cases
test_cases:
  sentiment_single:
    name: "Sentiment x1 - Answer Directly"
    test_type: custom
    agent: test_agents.capability_tests.SentimentAnalyzer
    test_func: test_functions.capability_tests.test_sentiment_single
    evaluator_func: evaluators.capability_tests.evaluate_sentiment_single
```

### Models Config: `config/models.yaml`

Defines available LLM models:

```yaml
models:
  - id: qwen3-next-80b
    model_name: nvidia_nim/qwen/qwen3-next-80b-a3b-instruct
    api_base: https://integrate.api.nvidia.com/v1
    api_key_env: NVIDIA_API_KEY
    temperature: 0.7
    max_tokens: 4000
    enabled: true

  - id: claude-sonnet-4-5
    model_name: openai/aws/anthropic/bedrock-claude-sonnet-4-5-v1
    api_base_env: NVIDIA_INFERENCE_ENDPOINT
    api_key_env: NVIDIA_INFERENCE_API_KEY
    temperature: 0.7
    max_tokens: 4000
    enabled: true
```

## Adding New Tests

### 1. Create the Agent

In `test_agents/capability_tests.py`:

```python
from agent006 import Agent
from unifiedllm import UnifiedLLM

llm = UnifiedLLM(model="...")

class MyNewAgent(Agent, llm=llm):
    """Agent for testing X capability."""

    async def do_something(self, input_data: str) -> str:
        """
        Process the input and return a result.

        Args:
            input_data: The data to process

        Returns:
            The processed result
        """
        ...
```

### 2. Create the Test Function

In `test_functions/capability_tests.py`:

```python
async def test_my_new_test(agent_class, llm_client, state=None):
    """Test function that runs the agent."""
    agent = agent_class(llm_client=llm_client)
    if state is not None:
        state["agent"] = agent

    # Call the agent method
    result = await agent.do_something("test input")

    return agent, {"input": "test input", "expected": "expected output"}
```

### 3. Create the Evaluator

In `evaluators/capability_tests.py`:

```python
def evaluate_my_new_test(result, test_config, extra_args):
    """Evaluate the test result."""
    output = result.get("output")
    expected = extra_args.get("expected")

    passed = output == expected

    return {
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "metrics": {
            "output_correct": passed,
        },
        "reasoning": f"Output {'matches' if passed else 'does not match'} expected"
    }
```

### 4. Add to Config

In `config/capabilities.yaml`:

```yaml
test_cases:
  my_new_test:
    name: "My New Test - Description"
    test_type: custom
    agent: test_agents.capability_tests.MyNewAgent
    test_func: test_functions.capability_tests.test_my_new_test
    evaluator_func: evaluators.capability_tests.evaluate_my_new_test
    timeout: 60  # optional, defaults to 120s
```

## Adding New Experiments

In `config/capabilities.yaml`:

```yaml
experiments:
  my_experiment:
    description: "What this experiment tests"
    models: [qwen3-next-80b, claude-sonnet-4-5]
    strategy_configs: [system, task]
```

Then run:
```bash
python runner.py config/capabilities.yaml --experiment my_experiment
```

## Adding New Strategy Configs

Strategy configs control how prompts are constructed:

```yaml
strategy_configs:
  verbose:
    description: "Verbose prompts with detailed instructions"
    system_message_template: |
      You are a helpful AI assistant.

      {instructions}

      {self.doc()}
    task_message_template: |
      # Your Task
      {task}

      # Method Information
      {method_info}

      # Current Call
      {current_call}

      Please think step by step.

  concise:
    description: "Minimal prompts"
    system_message_template: "{instructions}"
    task_message_template: "{task}\n{current_call}"
```

### Template Variables

- `{instructions}` - Base agent instructions from class docstring
- `{task}` - Task description from method docstring
- `{method_info}` - Method signature and docstring
- `{current_call}` - Current method call with arguments
- `{self.doc()}` - Full agent documentation (runtime-expanded)

## Viewing Results

### Start the Viewer

```bash
cd viewer/backend
python main.py
```

Access at http://localhost:8080

### Features

- **Experiment List**: See all test runs with pass/fail counts
- **Test Details**: View input, output, evaluation, and LLM traces
- **Filtering**: Filter by model, test type, pass/fail status, variant
- **Playground**: Edit and re-run prompts with different models
- **Per-test Traces**: Each test creates its own trace file for detailed inspection

### Trace Viewer Integration

Click "View Trace" on any test to open it in the trace viewer (runs on port 5001).

## Directory Structure

```
util/prompt-optimization/
├── config/
│   ├── capabilities.yaml    # Test definitions and experiments
│   └── models.yaml          # Model configurations
├── test_agents/             # Agent classes for testing
├── test_functions/          # Test execution functions
├── evaluators/              # Evaluation functions
├── results/                 # Test result JSON files
├── traces/                  # Per-test OTEL trace files
├── viewer/
│   ├── backend/             # FastAPI server
│   └── frontend/            # Static web UI
└── runner.py                # Main test runner
```

## Troubleshooting

### Tests not appearing in viewer
- Ensure results are saved to `results/` directory
- Check that the viewer backend is running
- Click "Clear Filters" if filters are hiding results

### "No tests match current filters"
- Persisted filters may exclude new tests
- Click "Clear Filters" button in the viewer

### Model API errors
- Check API keys in `.env` file
- Verify model is enabled in `config/models.yaml`
- For NVIDIA Inference Gateway models, ensure `openai/` prefix in model_name

### Trace files not linking correctly
- Traces are stored in `traces/` with format: `{variant}_{model}_{test}_{timestamp}.jsonl`
- Ensure trace viewer config includes `../prompt-optimization/traces/`
