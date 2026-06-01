# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Provides some quickstart settings to get you started with some reasonable defaults and to make the quickstart examples in the README.md more concise.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from nemo_oo_agents import Agent, hidden, strategy
from nemo_oo_agents.strategies import CodeActStrategy, PredictStrategy
from nemo_oo_agents.unifiedllm.registry import get_llm_client

# Load environment variables
load_dotenv(override=True)

# Default model for all examples - change this to switch models
# Uses litellm-native routing; set OPENAI_API_KEY in .env
MODEL = "gpt-5-mini"

# Pre-configured LLM client
llm = get_llm_client(MODEL)


# Decorator for running example entry points
def autorun(func: Callable[[], Coroutine[Any, Any, Any]]) -> Callable[[], Coroutine[Any, Any, Any]]:
    # Mark the entry point hidden BEFORE running so it never leaks into any
    # agent's execution context as a callable tool (module-level functions are
    # visible-by-default; the example `main()` is harness glue, not a tool).
    hidden(func)
    print("\n\nEXAMPLE OUTPUT:")
    asyncio.run(func())
    return func


@dataclass
class Artwork:
    """A piece of art with an appraisal."""

    def __init__(self, title: str, artist: str, appraised_value: float):
        self.title = title
        self.artist = artist
        self._appraised_value = appraised_value

    def get_appraisal(self) -> dict[str, Any]:
        """Get the appraisal details including current market value."""
        return {
            "title": self.title,
            "artist": self.artist,
            "value": self._appraised_value,
            "currency": "USD",
        }


@dataclass
class StockHolding:
    """A stock position with shares and price."""

    def __init__(self, symbol: str, shares: int, price_per_share: float):
        self.symbol = symbol
        self._shares = shares
        self._price_per_share = price_per_share

    def get_total_value(self) -> float:
        """Calculate total value of the holding."""
        return self._shares * self._price_per_share


@dataclass
class Jewelry:
    """A piece of jewelry valued by carats."""

    def __init__(self, description: str, carats: float, rate_per_carat: float):
        self.description = description
        self._carats = carats
        self._rate_per_carat = rate_per_carat

    def compute_value(self) -> float:
        """Compute total value based on carats and rate."""
        return self._carats * self._rate_per_carat


@dataclass
class Collectible:
    """A collectible item whose value depends on condition."""

    def __init__(self, name: str, base_value: float, condition: str):
        self.name = name
        self._base_value = base_value
        self.condition = condition

    def estimate_value(self) -> float:
        """Estimate value based on condition. Returns adjusted value."""
        multipliers = {"mint": 1.0, "excellent": 0.85, "good": 0.7, "fair": 0.5}
        return self._base_value * multipliers.get(self.condition, 0.5)


# Export everything needed for examples
__all__ = [
    # LLM
    "llm",
    "MODEL",
    # Core
    "Agent",
    "strategy",
    # Strategies
    "CodeActStrategy",
    "PredictStrategy",
    # Pydantic
    "BaseModel",
    "Field",
    # Async
    "asyncio",
    # Example runner
    "autorun",
    # Types
    "Artwork",
    "StockHolding",
    "Jewelry",
    "Collectible",
]
