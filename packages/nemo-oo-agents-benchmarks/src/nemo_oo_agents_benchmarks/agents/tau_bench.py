# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tau Bench agent opt2 — ported from agent006.

Ported from:
  agent006/experiments/evaluation-ablations/agents/tau_bench_opt2.py
  (class TauBenchAgent)

Multi-turn customer service agent for the tau-bench benchmark.
Requires Harbor multi-turn support (gl-23) to run end-to-end via Harbor.

OPT2 improvement: filter product variants by `available=True` before
reporting counts to customers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from nooa import Agent, CodeActStrategy, strategy
from nooa.agent import INHERIT, _InheritSentinel
from nooa.config import CodeActConfig, FormatConfig, TruncationConfig
from nooa.context_blocks import DynamicContext
from nooa.unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from nooa.unifiedllm import UnifiedLLM

# Module-level imports available to LLM-generated code at runtime
import datetime  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sys  # noqa: F401
import textwrap  # noqa: F401

MAX_CONVERSATION_TURNS = 50


class UserResponse(BaseModel):
    """Response to send to the customer.

    Attributes:
        message: The message to send to the customer.
        session_complete: Set to True when the customer indicates they're done
            (e.g., says "thank you", "goodbye", "that's all", "no more questions").
    """

    message: str
    session_complete: bool = False


class TauBenchAgent(
    Agent,
    llm=FakeLLMClient(),
    context={
        "python_tools": DynamicContext(expr="doc(self.taubench)"),
        "domain_policy": DynamicContext(expr="self.taubench.policy"),
        "domain_tools": DynamicContext(expr="self.taubench.tools"),
    },
    truncation=TruncationConfig(event_format=FormatConfig(max_string=5000)),
):
    taubench: Any  # TauBenchTools injected at runtime by the runner

    """Tau Bench customer service agent for multi-turn tool-calling benchmark.

    You are a customer service agent helping customers. Follow the domain-specific
    policy in the <domain_policy> section carefully.

    ## CRITICAL: How to Respond to Customers

    You MUST return a UserResponse object. Your response.message is sent to the customer!

    ```python
    # ALWAYS end your code with this pattern:
    return UserResponse(
        message="What you want to say to the customer",
        session_complete=False  # Keep False unless customer is done
    )
    ```

    - The `message` field = your reply that the customer will read
    - Set `session_complete=False` to continue the conversation (most cases!)
    - Set `session_complete=True` ONLY when customer says "goodbye", "thanks, that's all", etc.

    ## Workflow for Each Message

    1. Read the customer's message (passed as `user_message` parameter)
    2. Use self.taubench tools to look up info or execute actions if needed
    3. Return UserResponse with your reply message and session_complete=False

    ## When to Set session_complete=True

    ONLY when customer clearly indicates they're done:
    - "Thank you, that's all I needed"
    - "No, nothing else"
    - "Goodbye"
    - "That's it, thanks"

    If customer asks ANY question or makes ANY request → session_complete=False

    ## Available Tools

    See the <domain_tools> section for available tools on self.taubench.

    ## IMPORTANT: Product Availability

    When a customer asks how many options/variants are available for a product,
    you MUST check the `available` field on each variant and only count variants
    where `available` is True. Do NOT report the total number of variants — only
    report the number of AVAILABLE variants. Always filter product data for
    availability before communicating counts to customers.
    """

    def __init__(self, llm: UnifiedLLM | _InheritSentinel = INHERIT, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point for evaluation framework.

        Manages the conversation loop:
        1. Get initial user message
        2. Call handle_user_message to get response
        3. Send response to customer via respond_to_user
        4. Repeat until session_complete or environment is done
        """
        user_message: str | None = (
            task_input.get("initial_observation")
            or task_input.get("user_prompt")
            or task_input.get("user_message")
            or task_input.get("description", "")
        )

        if not user_message:
            return {"response": "", "success": False, "error": "No user message provided"}

        try:
            turn = 0
            while turn < MAX_CONVERSATION_TURNS:
                turn += 1

                if os.getenv("DEBUG_TRACE_EXPORT"):
                    print(f"\n TURN {turn}: Processing message: {user_message[:50]}...")

                response = await self.handle_user_message(user_message)

                if os.getenv("DEBUG_TRACE_EXPORT"):
                    print(
                        f"   Response: {response.message[:50]}... "
                        f"(complete={response.session_complete})"
                    )

                customer_reply = await self.respond_to_user(response.message)

                if response.session_complete or self.taubench.is_done:
                    if os.getenv("DEBUG_TRACE_EXPORT"):
                        print(f"   Session complete after {turn} turns")
                    return {
                        "response": response.message,
                        "success": True,
                        "result": response.message,
                    }

                user_message = customer_reply

            return {
                "response": "",
                "success": False,
                "error": f"Exceeded maximum conversation turns ({MAX_CONVERSATION_TURNS})",
            }

        except Exception as e:
            if os.getenv("DEBUG_TRACE_EXPORT"):
                print(f"   Exception in conversation loop: {e}")
            return {"response": "", "success": False, "error": str(e)}

    async def respond_to_user(self, agent_message: str) -> str:
        return await self.taubench.respond_to_user(agent_message)

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100, max_retries=5)))
    async def handle_user_message(self, user_message: str) -> UserResponse:
        """Process the customer's message and respond.

        The customer said: {user_message}

        Your job:
        1. Read what the customer said (in user_message)
        2. Use self.taubench tools if needed to look up info or take actions
        3. Return UserResponse with YOUR reply to the customer

        Use the REPL to iterate and validate your solution before returning.

        You MUST return a UserResponse like this:
        ```
        return UserResponse(
            message="Your reply to the customer goes here",
            session_complete=False  # True only if customer said goodbye/thanks/done
        )
        ```

        The `message` field is what the customer will see as your response.
        Set `session_complete=False` to continue the conversation.
        Set `session_complete=True` only when customer says goodbye or has no more requests.
        """
        ...
