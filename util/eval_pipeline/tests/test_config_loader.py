"""Tests for config schema validation."""

from pathlib import Path

import pytest

from eval_pipeline.config import (
    get_method_schema,
    load_config,
    load_tasks,
    validate_tasks,
)
from eval_pipeline.models import Task

# ============================================================
# Test fixtures - sample agent classes
# ============================================================


class SimpleAgent:
    """Agent with simple required kwargs."""

    async def process(self, text: str) -> str:
        return text


class MultiArgAgent:
    """Agent with multiple required kwargs."""

    async def calculate(self, a: int, b: int) -> int:
        return a * b


class OptionalArgAgent:
    """Agent with optional kwargs."""

    async def greet(self, name: str, greeting: str = "Hello") -> str:
        return f"{greeting}, {name}!"


class AllOptionalAgent:
    """Agent where all kwargs are optional."""

    async def configure(self, timeout: int = 30, retries: int = 3) -> dict:
        return {"timeout": timeout, "retries": retries}


class NoTypeHintsAgent:
    """Agent without type hints."""

    async def run(self, data):
        return data


# ============================================================
# Tests for get_method_schema
# ============================================================


class TestGetMethodSchema:
    """Tests for get_method_schema function."""

    def test_simple_required_kwarg(self):
        """Single required kwarg is extracted correctly."""
        kwargs, optional = get_method_schema(SimpleAgent, "process")
        assert kwargs == {"text": "str"}
        assert optional == set()

    def test_multiple_required_kwargs(self):
        """Multiple required kwargs are extracted."""
        kwargs, optional = get_method_schema(MultiArgAgent, "calculate")
        assert kwargs == {"a": "int", "b": "int"}
        assert optional == set()

    def test_optional_kwargs_detected(self):
        """Optional kwargs (with defaults) are tracked."""
        kwargs, optional = get_method_schema(OptionalArgAgent, "greet")
        assert kwargs == {"name": "str", "greeting": "str"}
        assert optional == {"greeting"}

    def test_all_optional_kwargs(self):
        """All kwargs can be optional."""
        kwargs, optional = get_method_schema(AllOptionalAgent, "configure")
        assert kwargs == {"timeout": "int", "retries": "int"}
        assert optional == {"timeout", "retries"}

    def test_no_type_hints_uses_any(self):
        """Missing type hints default to 'Any'."""
        kwargs, optional = get_method_schema(NoTypeHintsAgent, "run")
        assert kwargs == {"data": "Any"}
        assert optional == set()

    def test_self_is_excluded(self):
        """'self' parameter is not included in schema."""
        kwargs, _ = get_method_schema(SimpleAgent, "process")
        assert "self" not in kwargs


# ============================================================
# Tests for validate_tasks
# ============================================================


class TestValidateTasks:
    """Tests for validate_tasks function."""

    def test_valid_tasks_no_errors(self):
        """Valid tasks produce no errors."""
        tasks = [
            Task(id="task_001", input=((), {"text": "hello"}), expected="hello"),
            Task(id="task_002", input=((), {"text": "world"}), expected="world"),
        ]
        schema_kwargs = {"text": "str"}
        schema_optional = set()

        errors = validate_tasks(tasks, schema_kwargs, schema_optional)
        assert errors == []

    def test_missing_required_kwarg(self):
        """Missing required kwarg produces error."""
        tasks = [
            Task(id="task_001", input=((), {}), expected="result"),  # missing 'text'
        ]
        schema_kwargs = {"text": "str"}
        schema_optional = set()

        errors = validate_tasks(tasks, schema_kwargs, schema_optional)
        assert len(errors) == 1
        assert "task_001" in errors[0]
        assert "missing required kwarg 'text'" in errors[0]

    def test_missing_multiple_required_kwargs(self):
        """Multiple missing kwargs produce multiple errors."""
        tasks = [
            Task(id="task_001", input=((), {}), expected="result"),  # missing both
        ]
        schema_kwargs = {"a": "int", "b": "int"}
        schema_optional = set()

        errors = validate_tasks(tasks, schema_kwargs, schema_optional)
        assert len(errors) == 2
        assert any("missing required kwarg 'a'" in e for e in errors)
        assert any("missing required kwarg 'b'" in e for e in errors)

    def test_unexpected_kwarg(self):
        """Unexpected kwarg produces error."""
        tasks = [
            Task(
                id="task_001",
                input=((), {"text": "hello", "extra": "oops"}),
                expected="result",
            ),
        ]
        schema_kwargs = {"text": "str"}
        schema_optional = set()

        errors = validate_tasks(tasks, schema_kwargs, schema_optional)
        assert len(errors) == 1
        assert "task_001" in errors[0]
        assert "unexpected kwarg 'extra'" in errors[0]

    def test_optional_kwarg_can_be_missing(self):
        """Optional kwargs don't produce errors when missing."""
        tasks = [
            Task(id="task_001", input=((), {"name": "Alice"}), expected="Hello, Alice!"),
        ]
        schema_kwargs = {"name": "str", "greeting": "str"}
        schema_optional = {"greeting"}

        errors = validate_tasks(tasks, schema_kwargs, schema_optional)
        assert errors == []

    def test_optional_kwarg_can_be_provided(self):
        """Optional kwargs can be provided without error."""
        tasks = [
            Task(
                id="task_001",
                input=((), {"name": "Alice", "greeting": "Hi"}),
                expected="Hi, Alice!",
            ),
        ]
        schema_kwargs = {"name": "str", "greeting": "str"}
        schema_optional = {"greeting"}

        errors = validate_tasks(tasks, schema_kwargs, schema_optional)
        assert errors == []

    def test_all_optional_empty_kwargs_valid(self):
        """When all kwargs are optional, empty kwargs is valid."""
        tasks = [
            Task(id="task_001", input=((), {}), expected={"timeout": 30, "retries": 3}),
        ]
        schema_kwargs = {"timeout": "int", "retries": "int"}
        schema_optional = {"timeout", "retries"}

        errors = validate_tasks(tasks, schema_kwargs, schema_optional)
        assert errors == []

    def test_multiple_tasks_multiple_errors(self):
        """Errors from multiple tasks are all reported."""
        tasks = [
            Task(id="task_001", input=((), {}), expected="result"),  # missing 'text'
            Task(id="task_002", input=((), {"text": "ok"}), expected="ok"),  # valid
            Task(id="task_003", input=((), {"wrong": "key"}), expected="result"),  # wrong key
        ]
        schema_kwargs = {"text": "str"}
        schema_optional = set()

        errors = validate_tasks(tasks, schema_kwargs, schema_optional)
        assert len(errors) == 3  # task_001 missing, task_003 missing + unexpected
        assert any("task_001" in e and "missing" in e for e in errors)
        assert any("task_003" in e and "missing" in e for e in errors)
        assert any("task_003" in e and "unexpected" in e for e in errors)

    def test_empty_tasks_no_errors(self):
        """Empty task list produces no errors."""
        errors = validate_tasks([], {"text": "str"}, set())
        assert errors == []


# ============================================================
# Tests for load_config
# ============================================================


class TestLoadConfig:
    """Tests for load_config function."""

    def test_loads_basic_config(self, tmp_path):
        """Basic config is loaded correctly with dict model format."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
name: test_eval
description: "Test evaluation"
output_dir: experiments
models:
  gpt-4:
    model_name: openai/gpt-4
    endpoint: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    max_tokens: 8192
  claude-3:
    model_name: anthropic/claude-3
    endpoint: https://api.anthropic.com/v1
    api_key_env: ANTHROPIC_API_KEY

agent_models:
  - gpt-4
  - claude-3

test_suite:
  - name: test1
    description: "First test"
    agent:
      module: my_module
      class: MyAgent
    method: run
    data_file: data.jsonl
    scorers:
      - name: exact_match
        class: ExactMatchScorer
        weight: 1.0
""")
        config = load_config(config_file)

        assert config.name == "test_eval"
        assert config.description == "Test evaluation"
        assert config.output_dir == Path("experiments")
        # Models are now a dict keyed by ID
        assert len(config.models) == 2
        from eval_pipeline.eval_types import ModelSpec

        assert all(isinstance(m, ModelSpec) for m in config.models.values())
        assert "gpt-4" in config.models
        assert "claude-3" in config.models
        assert config.models["gpt-4"].model_name == "openai/gpt-4"
        # agent_models specifies which to use
        assert config.agent_models == ["gpt-4", "claude-3"]
        assert len(config.tests) == 1

        test = config.tests[0]
        assert test.name == "test1"
        assert test.agent_module == "my_module"
        assert test.agent_class == "MyAgent"
        assert test.agent_method == "run"
        assert test.data_file == Path("data.jsonl")
        assert len(test.scorers) == 1

    def test_loads_config_without_models(self, tmp_path):
        """Config without models defaults to empty list."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
name: test_eval
test_suite:
  - name: test1
    agent:
      module: my_module
      class: MyAgent
    method: run
    data_file: data.jsonl
    scorers: []
""")
        config = load_config(config_file)
        assert config.models == {}

    def test_loads_config_with_limit(self, tmp_path):
        """Limit is loaded from config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
name: test_eval
test_suite:
  - name: test1
    agent:
      module: my_module
      class: MyAgent
    method: run
    data_file: data.jsonl
    limit: 10
    scorers: []
""")
        config = load_config(config_file)
        assert config.tests[0].limit == 10

    def test_defaults_for_missing_fields(self, tmp_path):
        """Missing optional fields get defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
test_suite:
  - name: test1
    agent:
      module: my_module
      class: MyAgent
    data_file: data.jsonl
""")
        config = load_config(config_file)

        assert config.name == "eval"  # default
        assert config.description == ""  # default
        assert config.output_dir == Path("experiments")  # default
        assert config.tests[0].agent_method == "run"  # default
        assert config.tests[0].limit is None  # default
        assert config.tests[0].scorers == []  # default

    def test_multiple_tests(self, tmp_path):
        """Multiple tests are loaded."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
name: multi_test
test_suite:
  - name: test1
    agent:
      module: mod1
      class: Agent1
    data_file: data1.jsonl
  - name: test2
    agent:
      module: mod2
      class: Agent2
    data_file: data2.jsonl
  - name: test3
    agent:
      module: mod3
      class: Agent3
    data_file: data3.jsonl
""")
        config = load_config(config_file)

        assert len(config.tests) == 3
        assert [t.name for t in config.tests] == ["test1", "test2", "test3"]


# ============================================================
# Tests for load_tasks
# ============================================================


class TestLoadTasks:
    """Tests for load_tasks function."""

    def test_loads_tasks_from_jsonl(self, tmp_path):
        """Tasks are loaded from JSONL file."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text(
            '{"args": [], "kwargs": {"text": "hello"}, "expected": "positive"}\n'
            '{"args": [], "kwargs": {"text": "world"}, "expected": "negative"}\n'
        )
        tasks = load_tasks(data_file)

        assert len(tasks) == 2
        assert tasks[0].input == ((), {"text": "hello"})
        assert tasks[0].expected == "positive"
        assert tasks[1].input == ((), {"text": "world"})
        assert tasks[1].expected == "negative"

    def test_generates_task_ids(self, tmp_path):
        """Task IDs are generated from filename and index."""
        data_file = tmp_path / "my_data.jsonl"
        data_file.write_text(
            '{"args": [], "kwargs": {"x": 1}, "expected": 1}\n'
            '{"args": [], "kwargs": {"x": 2}, "expected": 2}\n'
            '{"args": [], "kwargs": {"x": 3}, "expected": 3}\n'
        )
        tasks = load_tasks(data_file)

        assert tasks[0].id == "my_data_001"
        assert tasks[1].id == "my_data_002"
        assert tasks[2].id == "my_data_003"

    def test_respects_limit(self, tmp_path):
        """Limit parameter restricts number of tasks."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text(
            '{"args": [], "kwargs": {"x": 1}, "expected": 1}\n'
            '{"args": [], "kwargs": {"x": 2}, "expected": 2}\n'
            '{"args": [], "kwargs": {"x": 3}, "expected": 3}\n'
            '{"args": [], "kwargs": {"x": 4}, "expected": 4}\n'
            '{"args": [], "kwargs": {"x": 5}, "expected": 5}\n'
        )
        tasks = load_tasks(data_file, limit=2)

        assert len(tasks) == 2
        assert tasks[0].id == "data_001"
        assert tasks[1].id == "data_002"

    def test_handles_empty_args(self, tmp_path):
        """Missing args defaults to empty tuple."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"kwargs": {"text": "hello"}, "expected": "result"}\n')
        tasks = load_tasks(data_file)

        assert tasks[0].input == ((), {"text": "hello"})

    def test_handles_empty_kwargs(self, tmp_path):
        """Missing kwargs defaults to empty dict."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"args": ["hello"], "expected": "result"}\n')
        tasks = load_tasks(data_file)

        assert tasks[0].input == (("hello",), {})

    def test_handles_args_and_kwargs(self, tmp_path):
        """Both args and kwargs are loaded."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text(
            '{"args": ["arg1", "arg2"], "kwargs": {"key": "value"}, "expected": "result"}\n'
        )
        tasks = load_tasks(data_file)

        assert tasks[0].input == (("arg1", "arg2"), {"key": "value"})

    def test_empty_file_returns_empty_list(self, tmp_path):
        """Empty file returns empty list."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text("")
        tasks = load_tasks(data_file)

        assert tasks == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
