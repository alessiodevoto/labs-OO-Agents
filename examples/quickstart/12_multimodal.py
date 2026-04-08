# ruff: noqa: E402,F403,F405
"""Quickstart 12: Multimodal — images with CodeAct and PredictStrategy.

Demonstrates both strategies seeing images:
- CodeAct: prefill auto-calls show() for Image params → LLM sees the image
- PredictStrategy: Image params attached as content blocks to the Task event

Usage:
  uv run python examples/quickstart/12_multimodal.py
"""

import asyncio
import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore", message=".*coroutine.*was never awaited.*", category=RuntimeWarning
)

from dotenv import load_dotenv
from pydantic import BaseModel

from nemo_oo_agents import Agent, strategy
from nemo_oo_agents.media import Image
from nemo_oo_agents.strategies import PredictStrategy
from unifiedllm.registry import get_llm_client

load_dotenv(override=True)

# Vision-capable model
llm = get_llm_client("azure/openai/gpt-5.2")

ASSETS = Path(__file__).parent.parent / "assets"


# ---------------------------------------------------------------------------
# CodeAct agent — LLM generates code, show() injects images
# ---------------------------------------------------------------------------


class ImageDescriber(Agent, llm=llm):
    """You are an agent that describes images concisely."""

    async def describe(self, image: Image) -> str:
        """Describe what you see in this image. Be concise (1-2 sentences)."""
        ...


# ---------------------------------------------------------------------------
# PredictStrategy agent — single-shot structured output with image
# ---------------------------------------------------------------------------


class ImageAnalysis(BaseModel):
    """Structured analysis of an image."""

    colors: list[str]
    text_content: list[str]
    description: str


class ImageAnalyzer(Agent, llm=llm):
    """You are an image analysis agent that returns structured data."""

    @strategy(PredictStrategy())
    async def analyze(self, image: Image) -> ImageAnalysis:
        """Analyze this image. Extract all colors, any text content, and a brief description."""
        ...


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main():
    print(f"Model: {llm.model}\n")

    # --- CodeAct: describe the shapes image ---
    shapes_image = Image.from_file(ASSETS / "test_image.png")
    print("=== CodeAct: describe(shapes_image) ===")
    describer = ImageDescriber()
    description = await describer.describe(shapes_image)
    print(f"  {description}\n")

    # --- PredictStrategy: structured analysis of the text image ---
    text_image = Image.from_file(ASSETS / "test_text.png")
    print("=== PredictStrategy: analyze(text_image) ===")
    analyzer = ImageAnalyzer()
    analysis = await analyzer.analyze(text_image)
    print(f"  Colors: {analysis.colors}")
    print(f"  Text:   {analysis.text_content}")
    print(f"  Desc:   {analysis.description}\n")


if __name__ == "__main__":
    asyncio.run(main())
