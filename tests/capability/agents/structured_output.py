"""Structured output capability tests using PredictStrategy with Pydantic models."""

from typing import Annotated

from pydantic import BaseModel, Field

from nemo_oo_agents import Agent, strategy
from nemo_oo_agents.strategies import PredictStrategy


class UserInfo(BaseModel):
    """User information extracted from text."""

    name: str = Field(..., description="Full name of the user")
    age: int = Field(..., description="Age in years")
    email: str = Field(..., description="Email address")


class ReviewInfo(BaseModel):
    """Product review information extracted from text."""

    product_name: str = Field(..., description="Name of the product being reviewed")
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5 stars")
    would_recommend: bool = Field(..., description="Whether the reviewer recommends this product")
    key_points: list[str] = Field(..., description="Key points from the review")


class CombinedResult(BaseModel):
    """Combined extraction result with user and review information."""

    user: UserInfo = Field(..., description="Extracted user information")
    review: ReviewInfo = Field(..., description="Extracted review information")
    summary: str = Field(..., description="Brief summary combining user context and review")


class PredictAgent(Agent):
    """Agent that tests PredictStrategy with composed Pydantic models.

    This agent demonstrates extracting multiple structured pieces from rich text
    and composing them into a single result.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @strategy(PredictStrategy())
    async def extract_user_info(
        self, text: Annotated[str, "Text containing user information"]
    ) -> UserInfo:
        """Extract user information from the given text."""
        ...

    @strategy(PredictStrategy())
    async def extract_review_info(
        self, text: Annotated[str, "Text containing review information"]
    ) -> ReviewInfo:
        """Extract product review information from the given text."""
        ...

    @strategy(PredictStrategy())
    async def combine_extraction(self, user: UserInfo, review: ReviewInfo) -> CombinedResult:
        """Combine user and review information into a single result with summary.

        Create a CombinedResult that includes:
        - The user information
        - The review information
        - A summary that mentions the user's name, age, product, and recommendation
        """
        ...

    async def process_review(self, text: str) -> CombinedResult:
        """Orchestrator method that extracts and combines structured information.

        This regular (non-generation) method:
        1. Extracts user info using extract_user_info()
        2. Extracts review info using extract_review_info()
        3. Combines them using combine_extraction()

        Args:
            text: Rich text containing both user and review information

        Returns:
            CombinedResult object with all extracted and combined information
        """
        # Step 1: Extract user information
        user_info = await self.extract_user_info(text)

        # Step 2: Extract review information
        review_info = await self.extract_review_info(text)

        # Step 3: Combine them with LLM-generated summary
        result = await self.combine_extraction(user_info, review_info)

        return result
