"""Tests for Agent.__init__ render_config= parameter (Task 17)."""

from context_blocks.formatter import MarkdownBlockFormatter, XMLBlockFormatter
from context_blocks.render_config import RenderConfig
from unifiedllm import FakeLLMClient


def make_llm():
    return FakeLLMClient.with_tool_call("return_result", {"result": "done"})


def test_agent_accepts_render_config():
    from agent006 import Agent

    rc = RenderConfig(block_formatter=MarkdownBlockFormatter())

    class MyAgent(Agent, llm=make_llm()):
        pass

    agent = MyAgent(render_config=rc)
    assert agent.render_config is rc
    assert isinstance(agent.render_config.block_formatter, MarkdownBlockFormatter)


def test_agent_default_render_config():
    from agent006 import Agent

    class MyAgent(Agent, llm=make_llm()):
        pass

    agent = MyAgent()
    assert isinstance(agent.render_config, RenderConfig)
    assert isinstance(agent.render_config.block_formatter, XMLBlockFormatter)
