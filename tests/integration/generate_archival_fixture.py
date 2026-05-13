#!/usr/bin/env python3
"""Generate a test fixture: events that fill ~95% of a 262K context window.

Fills events into an agent, measures tokens via litellm, saves when ≥95%.
Run ONCE to generate the fixture. The test loads it at runtime.

Usage:
    python tests/integration/generate_archival_fixture.py
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from nemo_oo_agents import Agent
from nemo_oo_agents.context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
from nemo_oo_agents.events import PythonOutput
from nemo_oo_agents.runtime.actor import _current_llm_var, _current_method_var
from nemo_oo_agents.unifiedllm import CompletionClient

_MODEL_NAME = "nvidia/nvidia/Nemotron-3-Nano-30B-A3B"
_API_BASE = "https://inference-api.nvidia.com/v1"
_CONTEXT_WINDOW = 262_144
_TARGET_FRACTION = 0.95

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "archival_95pct.json")


def _fill_events(agent, n_events: int, payload_words: int = 200):
    """Add n_events tool-call + output pairs."""
    base = len(list(agent.event_manager.keys()))
    for i in range(n_events):
        idx = base + i
        tc_id = f"call_{idx}"
        payload = f"data_{idx} " * payload_words
        agent.event_manager.add(
            ToolCallEvent(
                tool_call_id=tc_id,
                name="execute_python",
                arguments={"code": payload},
                result=ToolResult(
                    tool_call_id=tc_id,
                    content=payload,
                    result_status=ResultStatus.COMPLETE,
                ),
            )
        )
        agent.event_manager.add(
            PythonOutput(
                tool_call_id=tc_id,
                execution_count=idx,
                stdout=payload,
                stderr="",
                execution_status=ResultStatus.COMPLETE,
            )
        )


async def _measure_tokens(agent, llm):
    method = type(agent).respond
    llm_token = _current_llm_var.set(llm)
    method_token = _current_method_var.set(method)
    try:
        await agent.runtime._build_messages(
            method, call_args=(agent, "hi"), call_kwargs={}, tools=[]
        )
    finally:
        _current_llm_var.reset(llm_token)
        _current_method_var.reset(method_token)
    return agent.runtime._last_context_stats


async def main():
    api_key = os.environ.get("NVIDIA_INTERNAL_API_KEY", "")
    if not api_key:
        print("ERROR: NVIDIA_INTERNAL_API_KEY not set")
        sys.exit(1)

    llm = CompletionClient(
        model=_MODEL_NAME,
        api_base=_API_BASE,
        api_key=api_key,
        temperature=0,
    )
    ctx_window = llm.context_window or _CONTEXT_WINDOW
    target_tokens = int(ctx_window * _TARGET_FRACTION)
    print(f"Target: {target_tokens:,} tokens ({_TARGET_FRACTION:.0%} of {ctx_window:,})")

    class A(Agent, llm=llm):
        async def respond(self, prompt: str) -> str:
            """Respond to {prompt}."""
            ...

    agent = A()
    batch = 50

    while True:
        _fill_events(agent, batch, payload_words=200)
        n_events = len(list(agent.event_manager.keys()))
        print(f"  Events: {n_events}...", end=" ", flush=True)

        stats = await _measure_tokens(agent, llm)
        print(f"tokens: {stats.total_tokens:,} / {target_tokens:,}")

        if stats.total_tokens >= target_tokens:
            break
        if n_events > 10000:
            print("ERROR: Could not reach target in 10000 events")
            sys.exit(1)

    # Serialize events
    events_data = []
    for tag in agent.event_manager.keys():
        ev = agent.event_manager.get(tag)
        events_data.append({
            "event_type": type(ev).__name__,
            "data": ev.model_dump(mode="json"),
        })

    os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
    with open(FIXTURE_PATH, "w") as f:
        json.dump({
            "model": _MODEL_NAME,
            "context_window": ctx_window,
            "target_fraction": _TARGET_FRACTION,
            "total_tokens": stats.total_tokens,
            "n_events": len(events_data),
            "events": events_data,
        }, f, indent=2)

    print(f"\nFixture saved: {FIXTURE_PATH}")
    print(f"  Events: {len(events_data)}")
    print(f"  Tokens: {stats.total_tokens:,}")
    print(f"  Size: {os.path.getsize(FIXTURE_PATH) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    asyncio.run(main())
