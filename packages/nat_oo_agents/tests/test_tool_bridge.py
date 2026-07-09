# SPDX-License-Identifier: Apache-2.0
"""Tests for the NAT tool bridge against the current public API.

Exercises ``create_tool_method`` / ``inject_nat_tools`` (the real API);
the legacy ``create_native_tool_class`` / ``create_tool_instance`` /
``_to_pascal_case`` helpers no longer exist. The tool bridge uses lazy
imports, so these tests do not require the NAT runtime.
"""

from __future__ import annotations

import inspect

import pytest
from nat.plugins.nooa.tool_bridge import (
    _DUMMY_PARAM_NAMES,
    create_tool_method,
    inject_nat_tools,
)
from pydantic import create_model


class FakeNATFunction:
    """Minimal stand-in for a NAT Function (Pydantic input schema + ainvoke)."""

    def __init__(self, description: str, input_fields: dict, result: str = "RESULT"):
        field_defs = {name: (typ, ...) for name, typ in input_fields.items()}
        self._schema = create_model("FakeInput", **field_defs)
        self.description = description
        self._result = result
        self.calls: list = []

    @property
    def input_schema(self):
        return self._schema

    async def ainvoke(self, value, to_type=None):
        self.calls.append(value)
        return self._result


class FakeAgent:
    """Bare agent object; injected methods land on the class."""


# ---------------------------------------------------------------------------
# create_tool_method
# ---------------------------------------------------------------------------


def test_create_tool_method_returns_name_and_async_method():
    fn = FakeNATFunction("Search the web", {"query": str, "max_results": int})
    name, method = create_tool_method("web_search", fn)

    assert name == "web_search"
    assert method.__name__ == "web_search"
    assert inspect.iscoroutinefunction(method)
    assert method.__doc__ == "Search the web"


def test_create_tool_method_signature_has_real_params():
    fn = FakeNATFunction("Search", {"query": str, "max_results": int})
    _, method = create_tool_method("web_search", fn)

    params = list(inspect.signature(method).parameters)
    assert params == ["self", "query", "max_results"]


def test_create_tool_method_filters_dummy_params():
    # Every dummy name NAT tools use should be stripped from the signature.
    for dummy in _DUMMY_PARAM_NAMES:
        fn = FakeNATFunction("Now", {"query": str, dummy: str})
        _, method = create_tool_method("tool", fn)
        params = list(inspect.signature(method).parameters)
        assert dummy not in params
        assert params == ["self", "query"]


def test_create_tool_method_no_params_signature():
    fn = FakeNATFunction("Current time", {})
    _, method = create_tool_method("current_time", fn)
    assert list(inspect.signature(method).parameters) == ["self"]


@pytest.mark.asyncio
async def test_generated_method_invokes_backing_function_with_kwargs():
    fn = FakeNATFunction("Search", {"query": str}, result="hit")
    name, method = create_tool_method("web_search", fn)

    agent = FakeAgent()
    type(agent)._nat_fns = {name: fn}
    bound = method.__get__(agent, type(agent))

    result = await bound(query="hello")
    assert result == "hit"
    assert fn.calls == [{"query": "hello"}]


@pytest.mark.asyncio
async def test_generated_no_param_method_invokes_with_empty_string():
    fn = FakeNATFunction("Now", {}, result="2026")
    name, method = create_tool_method("current_time", fn)

    agent = FakeAgent()
    type(agent)._nat_fns = {name: fn}
    bound = method.__get__(agent, type(agent))

    assert await bound() == "2026"
    assert fn.calls == [""]


# ---------------------------------------------------------------------------
# inject_nat_tools
# ---------------------------------------------------------------------------


class FakeBuilder:
    def __init__(self, fns: dict):
        self._fns = fns

    async def get_function(self, name: str):
        if name not in self._fns:
            raise KeyError(f"no such tool: {name}")
        return self._fns[name]


@pytest.mark.asyncio
async def test_inject_nat_tools_sets_callable_methods_on_class():
    time_fn = FakeNATFunction("Now", {}, result="12:00")
    search_fn = FakeNATFunction("Search", {"query": str}, result="found")
    builder = FakeBuilder({"current_time": time_fn, "web_search": search_fn})

    class MyAgent:
        pass

    agent = MyAgent()
    await inject_nat_tools(agent, ["current_time", "web_search"], builder)

    assert callable(MyAgent.current_time)
    assert callable(MyAgent.web_search)
    assert set(MyAgent._nat_fns) == {"current_time", "web_search"}

    assert await agent.current_time() == "12:00"
    assert await agent.web_search(query="q") == "found"
    assert search_fn.calls == [{"query": "q"}]


@pytest.mark.asyncio
async def test_inject_nat_tools_skips_unresolved_tool_without_raising():
    builder = FakeBuilder({})  # get_function will raise KeyError

    class MyAgent:
        pass

    agent = MyAgent()
    # Should log a warning and continue, not raise.
    await inject_nat_tools(agent, ["missing_tool"], builder)
    assert not hasattr(MyAgent, "missing_tool")
