"""Tests for TUIAgent method signatures and decorators."""

import inspect

from nemo_oo_agents_cli.tui.models import (
    BrainstormResult,
    DiagnosisResult,
    Plan,
    ReviewResult,
    StepResult,
    VerificationResult,
)


def _get_method(name):
    """Return the named method from TUIAgent, asserting it exists."""
    from nemo_oo_agents_cli.tui.agent import TUIAgent

    method = getattr(TUIAgent, name, None)
    assert method is not None, f"TUIAgent must have {name} method"
    return method


class TestAnswerQuestion:
    def test_method_exists(self):
        _get_method("answer_question")

    def test_return_type_is_none(self):
        sig = inspect.signature(_get_method("answer_question"))
        assert sig.return_annotation in (None, "None")

    def test_accepts_user_message(self):
        params = list(inspect.signature(_get_method("answer_question")).parameters.keys())
        assert "user_message" in params


class TestBrainstorm:
    def test_method_exists(self):
        _get_method("_legacy_brainstorm")

    def test_return_type(self):
        sig = inspect.signature(_get_method("_legacy_brainstorm"))
        assert sig.return_annotation in (BrainstormResult, "BrainstormResult")

    def test_accepts_request(self):
        params = list(inspect.signature(_get_method("_legacy_brainstorm")).parameters.keys())
        assert "request" in params


class TestWritePlan:
    def test_method_exists(self):
        _get_method("write_plan")

    def test_return_type(self):
        sig = inspect.signature(_get_method("write_plan"))
        assert sig.return_annotation in (Plan, "Plan")

    def test_accepts_spec(self):
        params = list(inspect.signature(_get_method("write_plan")).parameters.keys())
        assert "spec" in params


class TestImplementStep:
    def test_method_exists(self):
        _get_method("implement_step")

    def test_return_type(self):
        sig = inspect.signature(_get_method("implement_step"))
        assert sig.return_annotation in (StepResult, "StepResult")

    def test_accepts_step(self):
        params = list(inspect.signature(_get_method("implement_step")).parameters.keys())
        assert "step" in params


class TestDebugIssue:
    def test_method_exists(self):
        _get_method("debug_issue")

    def test_return_type(self):
        sig = inspect.signature(_get_method("debug_issue"))
        assert sig.return_annotation in (DiagnosisResult, "DiagnosisResult")

    def test_accepts_description(self):
        params = list(inspect.signature(_get_method("debug_issue")).parameters.keys())
        assert "description" in params


class TestVerifyWork:
    def test_method_exists(self):
        _get_method("verify_work")

    def test_return_type(self):
        sig = inspect.signature(_get_method("verify_work"))
        assert sig.return_annotation in (VerificationResult, "VerificationResult")


class TestReviewChanges:
    def test_method_exists(self):
        _get_method("review_changes")

    def test_return_type(self):
        sig = inspect.signature(_get_method("review_changes"))
        assert sig.return_annotation in (ReviewResult, "ReviewResult")

    def test_accepts_plan(self):
        params = list(inspect.signature(_get_method("review_changes")).parameters.keys())
        assert "plan" in params
