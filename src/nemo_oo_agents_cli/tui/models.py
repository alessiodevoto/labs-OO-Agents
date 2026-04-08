"""Pydantic return types for TUI agent workflow methods."""

from typing import Literal

from pydantic import BaseModel, Field


class Intent(BaseModel):
    """Classified user intent."""

    task_type: Literal["question", "feature", "bugfix", "refactor"]
    summary: str = Field(description="One-sentence description of what the user wants")


class BrainstormResult(BaseModel):
    """Result of a brainstorming phase iteration."""

    complete: bool = Field(description="True when ready to plan, False when still exploring")
    summary: str = Field(description="What we understand so far")
    decisions: list[str] = Field(default_factory=list, description="Key decisions made")
    constraints: list[str] = Field(default_factory=list, description="Identified constraints")
    scope: str = Field(default="", description="What's in/out of scope")
    pending_question: str | None = Field(default=None, description="Question asked to user, if any")


class PlanStep(BaseModel):
    """A single step in an implementation plan."""

    number: int
    description: str
    files: list[str] = Field(description="Files to create or modify")


class Plan(BaseModel):
    """Implementation plan with ordered steps."""

    steps: list[PlanStep]
    summary: str


class StepResult(BaseModel):
    """Result of implementing a single plan step."""

    step_number: int
    test_file: str
    implementation_files: list[str]
    tests_pass: bool


class DiagnosisResult(BaseModel):
    """Result of debugging an issue."""

    root_cause: str
    fix_applied: str
    verified: bool


class VerificationResult(BaseModel):
    """Result of running verification checks."""

    tests_pass: bool
    test_output: str
    lint_clean: bool
    diff_summary: str


class ReviewResult(BaseModel):
    """Result of reviewing changes against a plan."""

    complete: bool
    issues: list[str]
    summary: str
