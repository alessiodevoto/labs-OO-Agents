# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for skill generation methods — @strategy methods on Skills route through agent runtime."""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.ellipsis_detection import has_ellipsis_body
from nemo_oo_agents.skill import Skill
from nemo_oo_agents.skill_generation import (
    _make_skill_adapter,
    _wrap_skill_generation_method,
    bind_generation_methods,
    is_generation_method,
)

# ---------------------------------------------------------------------------
# Fixtures: Skill classes with generation methods
# ---------------------------------------------------------------------------


class CategorizationSkill(Skill):
    """A skill with a PredictStrategy generation method."""

    id = "categorize"

    @strategy()
    async def classify(self, text: str) -> str:
        """Classify {text} into one of: positive, negative, neutral."""
        ...

    async def helper(self, x: int) -> int:
        """A regular (non-generation) method."""
        return x + 1


class CodeGenSkill(Skill):
    """A skill with a CodeActStrategy generation method."""

    @strategy()
    async def solve(self, problem: str) -> str:
        """Solve {problem} step by step using code execution."""
        ...

    @strategy()
    async def analyze(self, data: list, question: str) -> dict:
        """Analyze {data} to answer {question}. Return a dict with findings."""
        ...


class PlainSkill(Skill):
    """A skill with no generation methods."""

    async def do_work(self) -> str:
        return "done"


# ---------------------------------------------------------------------------
# Tests: is_generation_method
# ---------------------------------------------------------------------------


class TestIsGenerationMethod:
    def test_detects_strategy_decorated_method(self):
        assert is_generation_method(CategorizationSkill.classify)

    def test_rejects_regular_async_method(self):
        assert not is_generation_method(CategorizationSkill.helper)

    def test_rejects_non_async(self):
        def sync_fn(): ...

        assert not is_generation_method(sync_fn)

    def test_rejects_none(self):
        assert not is_generation_method(None)

    def test_detects_multiple_generation_methods(self):
        assert is_generation_method(CodeGenSkill.solve)
        assert is_generation_method(CodeGenSkill.analyze)


# ---------------------------------------------------------------------------
# Tests: _make_skill_adapter
# ---------------------------------------------------------------------------


class TestMakeSkillAdapter:
    def test_adapter_has_self_param(self):
        adapter = _make_skill_adapter(CategorizationSkill.classify)
        sig = inspect.signature(adapter)
        params = list(sig.parameters.keys())
        assert params[0] == "self"

    def test_adapter_preserves_original_params(self):
        adapter = _make_skill_adapter(CategorizationSkill.classify)
        sig = inspect.signature(adapter)
        params = list(sig.parameters.keys())
        # self (agent) + text
        assert params == ["self", "text"]

    def test_adapter_preserves_docstring(self):
        adapter = _make_skill_adapter(CategorizationSkill.classify)
        assert "Classify" in adapter.__doc__

    def test_adapter_preserves_return_annotation(self):
        adapter = _make_skill_adapter(CategorizationSkill.classify)
        assert adapter.__annotations__.get("return") is str

    def test_adapter_has_ellipsis_body(self):
        adapter = _make_skill_adapter(CategorizationSkill.classify)
        assert has_ellipsis_body(adapter)

    def test_adapter_has_needs_generation_flag(self):
        adapter = _make_skill_adapter(CategorizationSkill.classify)
        assert adapter._needs_generation is True

    def test_adapter_multi_param_method(self):
        adapter = _make_skill_adapter(CodeGenSkill.analyze)
        sig = inspect.signature(adapter)
        params = list(sig.parameters.keys())
        assert params == ["self", "data", "question"]

    def test_adapter_preserves_strategy(self):
        # Get the strategy from the original method
        original_strategy = getattr(
            CategorizationSkill.classify, "_plan_strategy", None
        ) or getattr(CategorizationSkill.classify, "_strategy_override", None)
        adapter = _make_skill_adapter(CategorizationSkill.classify, strategy=original_strategy)
        assert adapter._plan_strategy is original_strategy


# ---------------------------------------------------------------------------
# Tests: bind_generation_methods
# ---------------------------------------------------------------------------


class TestBindGenerationMethods:
    def test_binds_generation_methods(self):
        skill = CategorizationSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()

        bound = bind_generation_methods(skill, agent)
        assert "classify" in bound
        assert "helper" not in bound

    def test_sets_agent_reference(self):
        skill = CategorizationSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()

        bind_generation_methods(skill, agent)
        assert skill._agent is agent

    def test_binds_multiple_methods(self):
        skill = CodeGenSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()

        bound = bind_generation_methods(skill, agent)
        assert "solve" in bound
        assert "analyze" in bound
        assert len(bound) == 2

    def test_no_generation_methods_returns_empty(self):
        skill = PlainSkill()
        agent = MagicMock()

        bound = bind_generation_methods(skill, agent)
        assert bound == []

    def test_wrapped_method_is_callable(self):
        skill = CategorizationSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()

        bind_generation_methods(skill, agent)
        assert callable(skill.classify)
        assert inspect.iscoroutinefunction(skill.classify)

    def test_wrapped_method_has_generation_flag(self):
        skill = CategorizationSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()

        bind_generation_methods(skill, agent)
        assert getattr(skill.classify, "_needs_generation", False)
        assert getattr(skill.classify, "_skill_generation", False)


# ---------------------------------------------------------------------------
# Tests: wrapper execution routes through agent runtime
# ---------------------------------------------------------------------------


class TestWrapperExecution:
    @pytest.mark.asyncio
    async def test_calls_agent_runtime(self):
        skill = CategorizationSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()
        agent.runtime._execute_with_generation = AsyncMock(return_value="positive")

        bind_generation_methods(skill, agent)
        result = await skill.classify("I love this")

        agent.runtime._execute_with_generation.assert_called_once()
        assert result == "positive"

    @pytest.mark.asyncio
    async def test_passes_args_correctly(self):
        skill = CodeGenSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()
        agent.runtime._execute_with_generation = AsyncMock(return_value={"answer": 42})

        bind_generation_methods(skill, agent)
        result = await skill.analyze([1, 2, 3], question="what is the sum?")

        call_args = agent.runtime._execute_with_generation.call_args
        # args[1] is the positional args tuple, args[2] is kwargs
        assert call_args[0][1] == ([1, 2, 3],)  # positional args
        assert call_args[0][2] == {"question": "what is the sum?"}
        assert result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_raises_when_no_agent(self):
        skill = CategorizationSkill()
        # Don't bind to any agent

        # Manually create a wrapper without binding

        wrapper = _wrap_skill_generation_method(skill, "classify", CategorizationSkill.classify)
        # skill._agent is not set
        with pytest.raises(RuntimeError, match="not attached to an agent"):
            await wrapper("test text")

    @pytest.mark.asyncio
    async def test_method_name_passed_correctly(self):
        skill = CategorizationSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()
        agent.runtime._execute_with_generation = AsyncMock(return_value="result")

        bind_generation_methods(skill, agent)
        await skill.classify("test")

        call_args = agent.runtime._execute_with_generation.call_args
        # Fourth arg is method_name
        assert call_args[0][3] == "classify"


# ---------------------------------------------------------------------------
# Tests: Skill.attach() integration
# ---------------------------------------------------------------------------


class TestSkillAttach:
    def test_attach_calls_bind(self):
        skill = CategorizationSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()

        skill.attach(agent)
        assert skill._agent is agent
        # classify should now be a wrapper
        assert getattr(skill.classify, "_skill_generation", False)

    def test_detach_clears_agent(self):
        skill = CategorizationSkill()
        agent = MagicMock()
        agent.runtime = MagicMock()

        skill.attach(agent)
        skill.detach()
        assert skill._agent is None
