#!/usr/bin/env python3
"""E2E token calibration measurement: compare litellm estimate vs API actual.

Uses the nemo_oo_agents framework's own rendering path to produce properly
formatted messages, then compares litellm estimate vs response.usage.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import litellm
from nemo_oo_agents import Agent
from nemo_oo_agents.context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
from nemo_oo_agents.events import PythonOutput
from nemo_oo_agents.unifiedllm import get_llm_client


def make_agent_with_events(model_name: str, n_turns: int = 20):
    """Create an agent with n_turns of tool-call events using a real LLM."""
    llm = get_llm_client(model_name)

    class A(Agent, llm=llm):
        async def respond(self, prompt: str) -> str:
            """Respond to {prompt}."""
            ...

    agent = A()
    for i in range(n_turns):
        tc_id = f"call_{i}"
        agent.event_manager.add(
            ToolCallEvent(
                tool_call_id=tc_id,
                name="execute_python",
                arguments={"code": f"result_{i} = sum(range({i * 100}))\nprint(f'Step {i}: {{result_{i}}}')"},
                result=ToolResult(
                    tool_call_id=tc_id,
                    content=f"Step {i}: {sum(range(i * 100))}",
                    result_status=ResultStatus.COMPLETE,
                ),
            )
        )
        agent.event_manager.add(
            PythonOutput(
                tool_call_id=tc_id,
                execution_count=i,
                stdout=f"Step {i}: {sum(range(i * 100))}",
                stderr="",
                execution_status=ResultStatus.COMPLETE,
            )
        )
    return agent


async def measure_model(model_name: str, n_turns: int, label: str) -> dict:
    """Create an agent, add events, trigger a real LLM call, measure tokens."""
    print(f"\n{'='*60}")
    print(f"Testing: {label} ({model_name}, {n_turns} turns)")
    print(f"{'='*60}")

    try:
        agent = make_agent_with_events(model_name, n_turns)

        # Trigger a real LLM call through the framework
        result = await agent.respond("Say OK.")

        # Read back stats and usage
        runtime = agent.runtime
        stats = runtime._last_context_stats
        litellm_estimate = stats.total_tokens if stats else None

        # The calibration ratio should have been set by our new code
        ratio = runtime._token_calibration_ratio
        last_estimate = runtime._last_litellm_estimate

        result_dict = {
            "model": model_name,
            "label": label,
            "n_turns": n_turns,
            "litellm_estimate": litellm_estimate,
            "calibration_ratio": ratio,
            "last_estimate_used": last_estimate,
            "events_tokens": stats.events_tokens if stats else None,
            "events_count": stats.events_count if stats else None,
            "status": "ok",
        }

        print(f"  litellm estimate (total):  {litellm_estimate:,}" if litellm_estimate else "  litellm estimate: N/A")
        print(f"  events tokens:             {stats.events_tokens:,}" if stats else "  events: N/A")
        print(f"  events count:              {stats.events_count}" if stats else "")
        print(f"  calibration ratio:         {ratio:.4f}" if ratio else "  calibration ratio: N/A (no usage in response)")

    except Exception as e:
        result_dict = {
            "model": model_name,
            "label": label,
            "n_turns": n_turns,
            "status": f"error: {type(e).__name__}: {e}",
        }
        print(f"  ERROR: {type(e).__name__}: {e}")

    return result_dict


async def main():
    models = [
        ("claude-sonnet", "Sonnet"),
        ("claude-opus", "Opus"),
        ("gpt-5.1-codex", "GPT 5.1 Codex"),
        ("nemotron-3-super-v3", "Nemotron Super v3"),
    ]

    results = []
    for model_name, label in models:
        for n_turns in [5, 20]:
            r = await measure_model(model_name, n_turns, f"{label} ({n_turns}t)")
            results.append(r)

    # Summary table
    print(f"\n\n{'='*70}")
    print("SUMMARY: Token Calibration Ratios")
    print(f"{'='*70}")
    print(f"{'Label':<30} {'litellm':>10} {'Ratio':>8} {'Status':<20}")
    print("-" * 70)
    for r in results:
        est = f"{r.get('litellm_estimate', 'N/A'):,}" if r.get('litellm_estimate') else "N/A"
        ratio = f"{r['calibration_ratio']:.4f}" if r.get('calibration_ratio') else "N/A"
        status = r.get("status", "?")[:20]
        print(f"{r['label']:<30} {est:>10} {ratio:>8} {status:<20}")

    # Save results
    with open("token_calibration_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to token_calibration_results.json")


if __name__ == "__main__":
    asyncio.run(main())
