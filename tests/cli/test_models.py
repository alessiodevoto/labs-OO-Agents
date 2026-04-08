"""Tests for TUI agent Pydantic return types."""

import pytest
from pydantic import ValidationError

from nemo_oo_agents_cli.tui.models import (
    BrainstormResult,
    DiagnosisResult,
    Intent,
    Plan,
    PlanStep,
    ReviewResult,
    StepResult,
    VerificationResult,
)


class TestIntent:
    def test_valid_feature(self):
        intent = Intent(task_type="feature", summary="Add login page")
        assert intent.task_type == "feature"

    def test_valid_question(self):
        intent = Intent(task_type="question", summary="How does auth work?")
        assert intent.task_type == "question"

    def test_invalid_task_type(self):
        with pytest.raises(ValidationError):
            Intent(task_type="invalid", summary="test")  # type: ignore[arg-type]

    def test_missing_summary(self):
        with pytest.raises(ValidationError):
            Intent(task_type="feature")  # type: ignore[arg-type]


class TestBrainstormResult:
    def test_complete(self):
        result = BrainstormResult(
            complete=True,
            summary="Build OAuth login",
            decisions=["Use Google OAuth"],
            constraints=["Must support SSO"],
            scope="Login page only",
        )
        assert result.complete is True
        assert result.pending_question is None

    def test_incomplete_with_question(self):
        result = BrainstormResult(
            complete=False,
            summary="Exploring login options",
            pending_question="Which OAuth provider?",
        )
        assert result.complete is False
        assert result.pending_question == "Which OAuth provider?"

    def test_defaults(self):
        result = BrainstormResult(complete=True, summary="Done")
        assert result.decisions == []
        assert result.constraints == []
        assert result.scope == ""


class TestPlan:
    def test_plan_with_steps(self):
        plan = Plan(
            summary="Add login feature",
            steps=[
                PlanStep(number=1, description="Create auth module", files=["src/auth.py"]),
                PlanStep(number=2, description="Add tests", files=["tests/test_auth.py"]),
            ],
        )
        assert len(plan.steps) == 2
        assert plan.steps[0].number == 1


class TestStepResult:
    def test_passing_step(self):
        result = StepResult(
            step_number=1,
            test_file="tests/test_auth.py",
            implementation_files=["src/auth.py"],
            tests_pass=True,
        )
        assert result.tests_pass is True


class TestDiagnosisResult:
    def test_verified_fix(self):
        result = DiagnosisResult(
            root_cause="Off-by-one in loop",
            fix_applied="Changed range(n) to range(n+1)",
            verified=True,
        )
        assert result.verified is True


class TestVerificationResult:
    def test_all_pass(self):
        result = VerificationResult(
            tests_pass=True,
            test_output="5 passed",
            lint_clean=True,
            diff_summary="2 files changed",
        )
        assert result.tests_pass is True


class TestReviewResult:
    def test_complete_with_no_issues(self):
        result = ReviewResult(complete=True, issues=[], summary="All good")
        assert result.complete is True
        assert result.issues == []
