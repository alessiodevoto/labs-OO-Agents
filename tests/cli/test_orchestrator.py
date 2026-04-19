"""Tests for the TUI agent orchestrator (respond method routing and phase tracking)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

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


@pytest.fixture
def mock_agent():
    """Create a TUIAgent-like mock with all workflow methods."""
    agent = MagicMock()
    agent._phase = "idle"
    agent._workflow_state = {}
    agent.context = MagicMock()

    # Mock all strategy methods as async
    agent.classify_intent = AsyncMock()
    agent.answer_question = AsyncMock()
    agent._legacy_brainstorm = AsyncMock()
    agent.write_plan = AsyncMock()
    agent.implement_step = AsyncMock()
    agent.debug_issue = AsyncMock()
    agent.verify_work = AsyncMock()
    agent.review_changes = AsyncMock()
    return agent


class TestIntentRouting:
    """Test that classify_intent routes to the correct workflow."""

    @pytest.mark.asyncio
    async def test_question_routes_to_answer(self, mock_agent):
        mock_agent.classify_intent.return_value = Intent(
            task_type="question", summary="How does X work?"
        )
        from nemo_oo_agents_cli.tui.agent import _orchestrate

        await _orchestrate(mock_agent, "How does X work?")

        mock_agent.answer_question.assert_called_once_with("How does X work?")
        mock_agent._legacy_brainstorm.assert_not_called()

    @pytest.mark.asyncio
    async def test_feature_starts_brainstorming(self, mock_agent):
        mock_agent.classify_intent.return_value = Intent(task_type="feature", summary="Add login")
        mock_agent._legacy_brainstorm.return_value = BrainstormResult(
            complete=False, summary="Exploring", pending_question="Which auth?"
        )
        from nemo_oo_agents_cli.tui.agent import _orchestrate

        await _orchestrate(mock_agent, "Add a login page")

        mock_agent._legacy_brainstorm.assert_called_once()
        assert mock_agent._phase == "brainstorming"

    @pytest.mark.asyncio
    async def test_bugfix_routes_to_debug(self, mock_agent):
        mock_agent.classify_intent.return_value = Intent(
            task_type="bugfix", summary="Login is broken"
        )
        mock_agent.debug_issue.return_value = DiagnosisResult(
            root_cause="Null pointer", fix_applied="Added null check", verified=True
        )
        mock_agent.verify_work.return_value = VerificationResult(
            tests_pass=True, test_output="OK", lint_clean=True, diff_summary="1 file"
        )
        from nemo_oo_agents_cli.tui.agent import _orchestrate

        await _orchestrate(mock_agent, "Login is broken")

        mock_agent.debug_issue.assert_called_once()
        mock_agent.verify_work.assert_called_once()


class TestPhaseTracking:
    """Test that phase tracking resumes workflows correctly."""

    @pytest.mark.asyncio
    async def test_brainstorming_resumes(self, mock_agent):
        """When phase is brainstorming, route directly to brainstorm."""
        mock_agent._phase = "brainstorming"
        mock_agent._legacy_brainstorm.return_value = BrainstormResult(
            complete=True,
            summary="Build OAuth login",
            decisions=["Use Google"],
            scope="Login only",
        )
        mock_agent.write_plan.return_value = Plan(
            summary="OAuth plan",
            steps=[PlanStep(number=1, description="Setup", files=["auth.py"])],
        )
        from nemo_oo_agents_cli.tui.agent import _orchestrate

        await _orchestrate(mock_agent, "Use Google OAuth")

        # Should NOT call classify_intent (we're mid-workflow)
        mock_agent.classify_intent.assert_not_called()
        mock_agent._legacy_brainstorm.assert_called_once()

    @pytest.mark.asyncio
    async def test_awaiting_plan_approval_approved(self, mock_agent):
        """When phase is awaiting_plan_approval, 'yes' should execute plan."""
        mock_agent._phase = "awaiting_plan_approval"
        plan = Plan(
            summary="The plan",
            steps=[PlanStep(number=1, description="Do it", files=["f.py"])],
        )
        mock_agent._workflow_state = {"plan": plan}
        mock_agent.implement_step.return_value = StepResult(
            step_number=1, test_file="t.py", implementation_files=["f.py"], tests_pass=True
        )
        mock_agent.verify_work.return_value = VerificationResult(
            tests_pass=True, test_output="OK", lint_clean=True, diff_summary="1 file"
        )
        mock_agent.review_changes.return_value = ReviewResult(
            complete=True, issues=[], summary="OK"
        )
        from nemo_oo_agents_cli.tui.agent import _orchestrate

        await _orchestrate(mock_agent, "looks good, go ahead")

        mock_agent.implement_step.assert_called_once()
        mock_agent.verify_work.assert_called_once()
        assert mock_agent._phase == "idle"

    @pytest.mark.asyncio
    async def test_idle_after_completion(self, mock_agent):
        """After full workflow, phase returns to idle."""
        mock_agent.classify_intent.return_value = Intent(
            task_type="refactor", summary="Clean up auth"
        )
        mock_agent.write_plan.return_value = Plan(
            summary="Refactor plan",
            steps=[PlanStep(number=1, description="Extract", files=["auth.py"])],
        )
        mock_agent.implement_step.return_value = StepResult(
            step_number=1, test_file="t.py", implementation_files=["auth.py"], tests_pass=True
        )
        mock_agent.verify_work.return_value = VerificationResult(
            tests_pass=True, test_output="OK", lint_clean=True, diff_summary="1 file"
        )
        mock_agent.review_changes.return_value = ReviewResult(
            complete=True, issues=[], summary="OK"
        )
        from nemo_oo_agents_cli.tui.agent import _orchestrate

        await _orchestrate(mock_agent, "Refactor the auth module")

        assert mock_agent._phase == "idle"
        assert mock_agent._workflow_state == {}


class TestFeatureWorkflow:
    """Test the complete feature workflow: brainstorm -> plan -> implement -> verify -> review."""

    @pytest.mark.asyncio
    async def test_brainstorm_complete_proceeds_to_plan(self, mock_agent):
        mock_agent.classify_intent.return_value = Intent(task_type="feature", summary="Add feature")
        spec = BrainstormResult(
            complete=True,
            summary="Build X",
            decisions=["Use Y"],
            scope="Module Z",
        )
        mock_agent._legacy_brainstorm.return_value = spec
        plan = Plan(
            summary="Plan",
            steps=[PlanStep(number=1, description="Step 1", files=["f.py"])],
        )
        mock_agent.write_plan.return_value = plan

        from nemo_oo_agents_cli.tui.agent import _orchestrate

        await _orchestrate(mock_agent, "Build feature X")

        mock_agent._legacy_brainstorm.assert_called_once()
        mock_agent.write_plan.assert_called_once()
        # Plan presented -- waiting for approval
        assert mock_agent._phase == "awaiting_plan_approval"
        assert mock_agent._workflow_state["plan"] == plan
