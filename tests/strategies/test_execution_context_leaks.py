# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for gl-78: Execution context must not leak framework internals.

The ``<execution_context>`` block in the system prompt lists symbols available
to the LLM in the REPL.  Framework-internal names should never appear there.

Three classes of leak are covered:

1. **Hidden class attrs** — ``_abc_impl``, ``_execution_config``, ``_agent_llm``,
   ``_enable_tracing`` leak via ``_iter_agent_attrs``
   because they lack ``Annotated[..., hidden]`` annotations on Agent.

2. **Module dict pollution** — ``_import_dynamic_classes`` writes discovered
   types into ``agent_module.__dict__``, permanently mutating the module.

3. **Agent's own class** — the agent class itself shows up in "Available types"
   even though the LLM never needs to construct itself.
"""

from __future__ import annotations

import sys
import types
from typing import TypedDict

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.prompts import build_prompt_data
from nemo_oo_agents.strategies.codeact import CodeActStrategy, _iter_agent_attrs
from nemo_oo_agents.unifiedllm import FakeLLMClient

_LLM = FakeLLMClient()

FRAMEWORK_INTERNALS = {
    "_abc_data",
    "ExecutionConfig",
    "CompletionClient",
}

# ---------------------------------------------------------------------------
# Agent defined directly in this test module — inspect.getmodule() works
# because the class's __module__ points to this file which is in sys.modules.
# ---------------------------------------------------------------------------


class OrderResult(TypedDict):
    ok: bool


class _TestAgent(Agent, llm=_LLM):
    def get_stock(self, item: str) -> int:
        return 0

    async def check_order(self, items: list[str]) -> OrderResult:
        """Check order."""
        ...


# ---------------------------------------------------------------------------
# Helper for module-mutation tests — needs a separate synthetic module
# ---------------------------------------------------------------------------

_MODULE_SOURCE = """\
from typing import TypedDict
from nemo_oo_agents import Agent

class OrderResult(TypedDict):
    ok: bool

class InventoryAgent(Agent, llm=_llm):
    def get_stock(self, item: str) -> int:
        return 0

    async def check_order(self, items: list[str]) -> OrderResult:
        '''Check order.'''
        ...
"""


def _make_agent_in_fresh_module():
    """Create an agent inside a fresh synthetic module and return (agent, module).

    The module stays in sys.modules so inspect.getmodule() can find it.
    """
    mod = types.ModuleType("_test_agent_module")
    mod.__file__ = "<test>"
    sys.modules[mod.__name__] = mod
    mod.__dict__["_llm"] = _LLM
    exec(compile(_MODULE_SOURCE, "<test>", "exec"), mod.__dict__)
    agent = mod.InventoryAgent()  # type: ignore[attr-defined]
    return agent, mod


# ---------------------------------------------------------------------------
# 1. _iter_agent_attrs must not yield framework internal attribute values
# ---------------------------------------------------------------------------


class TestIterAgentAttrsHidesInternals:
    """_iter_agent_attrs should skip attrs annotated Annotated[..., hidden]."""

    def test_no_abc_impl_value(self):
        agent = _TestAgent()
        type_names = {type(v).__name__ for v in _iter_agent_attrs(agent)}
        assert "_abc_data" not in type_names, (
            "_abc_impl leaked via _iter_agent_attrs — "
            "annotate _abc_impl: Annotated[Any, hidden] on Agent"
        )

    def test_no_execution_config_value(self):
        agent = _TestAgent()
        type_names = {type(v).__name__ for v in _iter_agent_attrs(agent)}
        assert "ExecutionConfig" not in type_names

    def test_no_completion_client_value(self):
        agent = _TestAgent()
        type_names = {type(v).__name__ for v in _iter_agent_attrs(agent)}
        assert "CompletionClient" not in type_names


# ---------------------------------------------------------------------------
# 2. _import_dynamic_classes must not mutate agent_module.__dict__
# ---------------------------------------------------------------------------


class TestImportDynamicClassesNoModuleMutation:
    """_import_dynamic_classes should not write to agent_module.__dict__."""

    def test_module_dict_unchanged_after_extract(self):
        agent, mod = _make_agent_in_fresh_module()
        before = set(mod.__dict__.keys())

        strategy = CodeActStrategy()
        strategy._extract_module_context(mod, agent=agent)

        after = set(mod.__dict__.keys())
        # __annotations__ may be added by typing.get_type_hints() as a Python
        # runtime side-effect; filter_module_globals already skips dunders.
        added = after - before - {"__annotations__"}
        assert not added, (
            f"_extract_module_context wrote {added} into module.__dict__ — "
            "store discovered types on the agent/runtime, not the module"
        )


# ---------------------------------------------------------------------------
# 3. execution_context() rendered text must be clean
# ---------------------------------------------------------------------------


class TestExecutionContextRendering:
    """The execution_context block in the prompt must not contain internals."""

    @pytest.mark.asyncio
    async def test_no_framework_internals_in_prompt(self):
        agent = _TestAgent()
        data = await build_prompt_data(agent.check_order, ["apple"])
        ec = self._extract_execution_context(data.system_prompt)

        for name in FRAMEWORK_INTERNALS:
            assert name not in ec, f"Framework internal '{name}' leaked into execution_context"

    @pytest.mark.asyncio
    async def test_return_type_still_visible(self):
        """User-defined return types must remain visible."""
        agent = _TestAgent()
        data = await build_prompt_data(agent.check_order, ["apple"])
        ec = self._extract_execution_context(data.system_prompt)
        assert "OrderResult" in ec

    @staticmethod
    def _extract_execution_context(system_prompt: str) -> str:
        """Pull just the <execution_context> block from the system prompt."""
        start = system_prompt.find("<execution_context")
        if start < 0:
            pytest.fail("No <execution_context> block found in system prompt")
        end = system_prompt.find("</execution_context>", start)
        return system_prompt[start : end + len("</execution_context>")]


# ---------------------------------------------------------------------------
# 4. Module-level functions defined in the agent's module are visible by
#    default (regression: previously only `type` instances survived the
#    extraction, so standalone @strategy functions and plain helpers were
#    invisible to the agent and absent from exec_globals).
# ---------------------------------------------------------------------------

_MODULE_WITH_FUNCS = """\
from typing import Literal
from nemo_oo_agents import Agent, hidden, strategy
from nemo_oo_agents.strategies import PredictStrategy

Label = Literal["a", "b"]


@strategy(PredictStrategy())
async def classify_item(text: str) -> Label:
    '''Classify the item.'''
    ...


def plain_helper(x: int) -> int:
    return x + 1


@hidden
def secret_helper() -> int:
    return 0


class FuncAgent(Agent, llm=_llm):
    async def run(self) -> str:
        '''Run.'''
        ...
"""


def _make_func_agent_in_fresh_module():
    mod = types.ModuleType("_test_func_agent_module")
    mod.__file__ = "<test>"
    sys.modules[mod.__name__] = mod
    mod.__dict__["_llm"] = _LLM
    exec(compile(_MODULE_WITH_FUNCS, "<test>", "exec"), mod.__dict__)
    agent = mod.FuncAgent()  # type: ignore[attr-defined]
    return agent, mod


class TestModuleLevelFunctionsVisible:
    """Standalone @strategy functions and plain module-level helpers must be
    surfaced to the agent (in exec_globals and the execution_context block),
    while @hidden functions stay excluded."""

    def test_local_functions_in_extracted_context(self):
        agent, mod = _make_func_agent_in_fresh_module()
        ctx = CodeActStrategy()._extract_module_context(mod, agent=agent)
        assert "classify_item" in ctx, "standalone @strategy function not in exec context"
        assert "plain_helper" in ctx, "plain module-level function not in exec context"
        assert callable(ctx["classify_item"]) and callable(ctx["plain_helper"])

    def test_hidden_function_excluded_from_context(self):
        agent, mod = _make_func_agent_in_fresh_module()
        ctx = CodeActStrategy()._extract_module_context(mod, agent=agent)
        assert "secret_helper" not in ctx, "@hidden function leaked into exec context"

    @pytest.mark.asyncio
    async def test_local_functions_rendered_in_prompt(self):
        agent, _ = _make_func_agent_in_fresh_module()
        data = await build_prompt_data(agent.run)
        ec = TestExecutionContextRendering._extract_execution_context(data.system_prompt)
        assert "Available functions" in ec
        assert "classify_item" in ec
        assert "plain_helper" in ec
        assert "secret_helper" not in ec
