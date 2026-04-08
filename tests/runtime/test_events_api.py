"""Tests for EventsApi — Skill wrapper for event queries."""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.events import Task
from unifiedllm import FakeLLMClient

_LLM = FakeLLMClient()


class _TestAgent(Agent, llm=_LLM):
    pass


@pytest.fixture
def api_with_event():
    agent = _TestAgent()
    event = Task(prompt="test")
    agent.event_manager.add(event)
    api = agent.events
    tag = list(agent.event_manager.keys())[0]
    return api, tag


def test_query_works():
    agent = _TestAgent()
    assert isinstance(agent.events.query(), list)


def test_get_single_returns_event(api_with_event):
    api, tag = api_with_event
    assert api.get(tag) is not None


def test_get_list_returns_found(api_with_event):
    api, tag = api_with_event
    result = api.get([tag, "missing"])
    assert len(result) == 1


def test_get_list_empty_for_all_missing():
    agent = _TestAgent()
    assert agent.events.get(["nonexistent-1", "nonexistent-2"]) == []


def test_getitem_returns_event(api_with_event):
    api, tag = api_with_event
    assert api[tag] is not None


def test_getitem_list_returns_events(api_with_event):
    api, tag = api_with_event
    result = api[[tag]]
    assert len(result) == 1


def test_getitem_raises_for_missing():
    agent = _TestAgent()
    with pytest.raises(KeyError):
        _ = agent.events["nonexistent"]


def test_getitem_list_raises_for_missing():
    agent = _TestAgent()
    with pytest.raises(KeyError):
        _ = agent.events[["nonexistent-1", "nonexistent-2"]]


def test_contains(api_with_event):
    api, tag = api_with_event
    assert tag in api
    assert "missing" not in api


def test_repr(api_with_event):
    api, _ = api_with_event
    assert "EventsApi" in repr(api)
