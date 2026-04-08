"""Load eval pipeline configuration from YAML."""

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import get_type_hints

import yaml

from .eval_types import ModelSpec
from .models import Task


def get_method_schema(cls: type, method_name: str) -> tuple[dict[str, str], set[str]]:
    """Extract input schema from method signature.

    Args:
        cls: The agent class
        method_name: Name of the method to introspect

    Returns:
        Tuple of (kwargs dict, optional set):
        - kwargs: {param_name: type_str}
        - optional: set of param names with defaults
    """
    method = getattr(cls, method_name)
    sig = inspect.signature(method)

    # Try to get type hints, fall back to empty dict
    try:
        hints = get_type_hints(method)
    except Exception:
        hints = {}

    kwargs = {}
    optional = set()

    for name, param in sig.parameters.items():
        # Skip self, *args, **kwargs
        if name == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        # Get type string
        if name in hints:
            type_hint = hints[name]
            type_str = getattr(type_hint, "__name__", str(type_hint))
        else:
            type_str = "Any"

        kwargs[name] = type_str

        # Track if optional (has default)
        if param.default is not inspect.Parameter.empty:
            optional.add(name)

    return kwargs, optional


@dataclass
class TestConfig:
    """Configuration for a single test."""

    name: str
    description: str
    agent_module: str
    agent_class: str
    agent_method: str
    data_file: Path
    limit: int | None
    scorers: list[dict]


@dataclass
class EvalConfig:
    """Full evaluation configuration.

    Attributes:
        name: Experiment name
        description: Description of the evaluation
        output_dir: Directory for output files
        models: Dict of model_id -> ModelSpec for all models used
        agent_models: List of model IDs to use for agent evaluation
        tests: List of test configurations
    """

    name: str
    description: str
    output_dir: Path
    models: dict[str, ModelSpec]  # All models defined in config, keyed by ID
    agent_models: list[str]  # Which model IDs to use for agent evaluation
    tests: list[TestConfig]


def load_config(config_path: Path) -> EvalConfig:
    """Load configuration from YAML file.

    Model format (dict keyed by ID):
        models:
          gpt-oss-20b:
            model_name: openai/nvidia/openai/gpt-oss-20b
            endpoint: https://inference-api.nvidia.com/v1
            api_key_env: NVIDIA_INFERENCE_API_KEY
            max_tokens: 16384
          nemotron3-nano-30b:
            model_name: openai/nvidia/nvidia/Nemotron-3-Nano-30B-A3B
            ...

        # Which models to use for agent evaluation
        agent_models:
          - gpt-oss-20b
          - nemotron3-nano-30b

    Scorers can reference models by ID:
        scorers:
          - name: method_judge
            class: LLMJudgeScorer
            model: nemotron3-nano-30b  # References key from models
    """
    with open(config_path) as f:
        data = yaml.safe_load(f)

    # Parse models as dict keyed by ID
    models: dict[str, ModelSpec] = {}
    models_data = data.get("models", {})

    if not isinstance(models_data, dict):
        raise ValueError("models must be a dict keyed by model ID")

    for model_id, model_cfg in models_data.items():
        models[model_id] = ModelSpec(
            id=model_id,
            model_name=model_cfg["model_name"],
            endpoint=model_cfg.get("endpoint"),
            api_key_env=model_cfg.get("api_key_env"),
            max_tokens=model_cfg.get("max_tokens"),
            reasoning_effort=model_cfg.get("reasoning_effort"),
            max_thinking_tokens=model_cfg.get("max_thinking_tokens"),
            max_retries=model_cfg.get("max_retries"),
            retry_on_empty_content=model_cfg.get("retry_on_empty_content", False),
        )

    # Which models to use for agent evaluation
    agent_models = data.get("agent_models", list(models.keys()))

    tests = []
    for test in data.get("test_suite", []):
        agent = test.get("agent", {})
        tests.append(
            TestConfig(
                name=test["name"],
                description=test.get("description", ""),
                agent_module=agent.get("module", ""),
                agent_class=agent.get("class", ""),
                agent_method=test.get("method", "run"),
                data_file=Path(test.get("data_file", "")),
                limit=test.get("limit"),
                scorers=test.get("scorers", []),
            )
        )

    return EvalConfig(
        name=data.get("name", "eval"),
        description=data.get("description", ""),
        output_dir=Path(data.get("output_dir", "experiments")),
        models=models,
        agent_models=agent_models,
        tests=tests,
    )


def load_tasks(data_file: Path, limit: int | None = None) -> list[Task]:
    """Load tasks from JSONL data file.

    Data file format (each line):
        {"args": [...], "kwargs": {...}, "expected": ...}

    Args:
        data_file: Path to JSONL file
        limit: Max tasks to load

    Returns:
        List of Tasks with input as (args, kwargs) tuple
    """
    tasks = []
    with open(data_file) as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            data = json.loads(line)
            args = tuple(data.get("args", []))
            kwargs = data.get("kwargs", {})
            tasks.append(
                Task(
                    id=f"{data_file.stem}_{i + 1:03d}",
                    input=(args, kwargs),
                    expected=data["expected"],
                )
            )
    return tasks


def validate_tasks(
    tasks: list[Task],
    schema_kwargs: dict[str, str],
    schema_optional: set[str],
) -> list[str]:
    """Validate all tasks against schema.

    Args:
        tasks: Tasks to validate
        schema_kwargs: Expected kwargs from method introspection
        schema_optional: Set of optional kwargs (have defaults)

    Returns:
        List of error messages (empty if all valid)
    """
    errors = []

    for task in tasks:
        _, kwargs = task.input

        # Check required kwargs are present
        for kwarg_name in schema_kwargs:
            if kwarg_name not in schema_optional and kwarg_name not in kwargs:
                errors.append(f"{task.id}: missing required kwarg '{kwarg_name}'")

        # Check for unexpected kwargs
        for kwarg_name in kwargs:
            if kwarg_name not in schema_kwargs:
                errors.append(f"{task.id}: unexpected kwarg '{kwarg_name}'")

    return errors
