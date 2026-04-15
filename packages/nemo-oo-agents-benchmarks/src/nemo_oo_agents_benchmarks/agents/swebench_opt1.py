# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
SWEBench optimised agent (opt1) for nemo-oo-agents-benchmarks.

Multi-phase pipeline:
  1. Understand — repo overview + clarify issue + root-cause analysis
  2. Implement  — complete fix with test validation
  3. Review     — FeedbackAgent loop (up to 3 iterations)

Adapted from ``harbor-agent006/agents/swebench_opt1.py`` (originally by Gaia).
"""

from __future__ import annotations

import logging
import textwrap
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, field_validator

from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.config import CodeActConfig
from unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured output models
# ---------------------------------------------------------------------------


class ClarifiedRequirements(BaseModel):
    """Symptom-to-root-cause transformation of a GitHub issue."""

    title: str
    """Clear, concise title (what needs to be done, not what failed)"""
    description: str
    """Root-cause-focused description of the problem"""
    files_to_modify: list[str]
    """All files/modules that need changes (be comprehensive)"""
    acceptance_criteria: list[str]
    """Checklist of what must be true for this to be complete"""
    notes: str
    """Edge cases, dependencies, expected behaviour, resolved contradictions"""

    @field_validator("title", "description", "notes")
    @classmethod
    def validate_not_empty(cls, v: str) -> str:
        if not v or len(v.strip()) < 10:
            raise ValueError(f"Value too short: '{v}'. Must provide detailed clarification.")
        return v

    @field_validator("files_to_modify", "acceptance_criteria")
    @classmethod
    def validate_not_empty_list(cls, v: list) -> list:
        if not v:
            raise ValueError("Must provide at least one item.")
        return v

    def __str__(self) -> str:
        return (
            f"# {self.title}\n\n"
            f"## Description\n{self.description}\n\n"
            f"## Files to Modify\n"
            + "\n".join(f"- {f}" for f in self.files_to_modify)
            + "\n\n## Acceptance Criteria\n"
            + "\n".join(f"- [ ] {c}" for c in self.acceptance_criteria)
            + f"\n\n## Notes\n{self.notes}\n"
        )


class Overview(BaseModel):
    """High-level summary of the repository."""

    version: str
    description: str
    other_relevant_information: dict[str, str]

    def __str__(self) -> str:
        lines = [
            "# Repository Overview\n",
            f"**Version**: {self.version}\n",
            f"**Description**: {self.description}\n",
        ]
        if self.other_relevant_information:
            lines.append("\n## Other Information\n")
            for k, v in self.other_relevant_information.items():
                lines.append(f"- **{k}**: {v}\n")
        return "".join(lines)


class RootCauseAnalysis(BaseModel):
    """Structured root-cause analysis."""

    symptom_location: str
    """Where the error/bug appears"""
    root_cause_location: str
    """Where the error/bug originates"""
    all_affected_files: list[str]
    """All files that need modifications"""
    justification: str
    """Why this is the root cause and why these files need changes"""

    @field_validator("symptom_location", "root_cause_location", "justification")
    @classmethod
    def validate_not_empty(cls, v: str) -> str:
        if not v or len(v.strip()) < 10:
            raise ValueError(f"Value too short: '{v}'. Must provide detailed analysis.")
        return v

    def __str__(self) -> str:
        files = "\n".join(f"- {f}" for f in self.all_affected_files) or "- None"
        return (
            f"# Root Cause Analysis\n\n"
            f"## Symptom Location\n{self.symptom_location}\n\n"
            f"## Root Cause Location\n{self.root_cause_location}\n\n"
            f"## Affected Files\n{files}\n\n"
            f"## Justification\n{self.justification}\n"
        )


# ---------------------------------------------------------------------------
# Feedback / reviewer sub-agent
# ---------------------------------------------------------------------------


class FeedbackAgent(Agent, llm=FakeLLMClient()):
    """Harsh reviewer that verifies a proposed fix is complete.

    ## Core Philosophy: Complete Fixes Over Minimal Fixes

    # Anti-patterns
    - ❌ "The issue is about x so I will not fix y even if it is related"
    - ❌ "This is not in scope"
    - ❌ "All tests pass so the fix is complete"

    # Adopt these mindsets:
    - ✅ Fix the COMPLETE problem, not just symptoms
    - ✅ Fix ALL affected locations, not just some
    - ✅ Handle ALL edge cases, not just test cases

    **Key principle**: A complete fix across 10 files beats an incomplete fix in 1.
    """

    swebench: Any  # SWEBenchLocalTools injected by the runner

    def __init__(self, swebench: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.swebench = swebench
        from agentdoc import doc

        tool_doc = (
            "To navigate the repository and modify files, use the following tools:\n\n"
            + doc(self.swebench)
        )
        self.context["tool_instructions"] = tool_doc

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100, max_retries=10)))
    async def create_test_cases(self, problem_statement: str) -> str:
        """Create a complete set of test cases for the problem statement.

        Cover all edge cases and all possible inputs, even those not explicitly
        mentioned in the problem statement.  Return the test cases as a string.
        """
        ...

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=50, max_retries=5)))
    async def review_solution(
        self,
        diff: str,
        problem_statement: str,
        root_cause_analysis: str = "",
        repo_overview: Overview | None = None,
    ) -> list[str]:
        """Review the diff to verify the solution.

        Be as harsh and nitpicky as possible.

        ## CRITICAL: Independent Verification First
        Before following the steps below, ask yourself:
        1. Does the root_cause_analysis make sense?  Could it be missing something?
        2. Are the identified files actually the right ones to modify?
        3. Should I verify the claims independently?

        ## Steps

        ### Step 1: Question the Analysis (do NOT skip)
        - Read the problem statement yourself — does analysis match your reading?
        - Trace the actual call chain via tools.
        - Trust your investigation over previous results if they conflict.

        ### Step 2: Verify ROOT CAUSE is addressed (not just symptoms)
        - Trace the call hierarchy: where can exceptions originate?
        - Does the fix address the root cause, or just hide the symptom?

        ### Step 3: Verify ALL edge cases are handled
        - Use ``await self.swebench.find_references(symbol, search_dir)``
        - Test edge cases: empty inputs, None values, boundary conditions.

        ### Step 4: Verify ALL locations are fixed
        - Search for ALL occurrences of the pattern you fixed.
        - Check subclasses, related files, similar code paths.

        ### Step 5: Run the SPECIFIC tests mentioned in the problem statement
        - Extract test names (look for ``test_*``, ``Test*``, pytest patterns).
        - Run: ``await self.swebench.run_tests(pattern)``
        - If tests fail, analyse WHY and report specific issues.

        Return ``[]`` if the fix is complete and correct.
        Return a list of SPECIFIC issues found with evidence.
        """
        ...


# ---------------------------------------------------------------------------
# Main opt1 agent
# ---------------------------------------------------------------------------


class SWEBenchOpt1Agent(Agent, llm=FakeLLMClient()):
    """SWEBench optimised agent: clarify → root-cause → implement → review loop.

    ## Core Philosophy: Complete Fixes Over Minimal Fixes

    **CRITICAL**: Do NOT bias toward "minimal" or "surgical" fixes.

    # Anti-patterns
    - ❌ "The issue is about x so I will not fix y even if related"
    - ❌ "I'll avoid touching too many files"
    - ❌ "All tests pass so the fix is complete"

    # Adopt these mindsets:
    - ✅ Fix the COMPLETE problem, not just symptoms
    - ✅ Fix ALL affected locations
    - ✅ Handle ALL edge cases

    **Key principle**: A complete fix across 10 files beats an incomplete fix in 1.
    """

    swebench: Any  # SWEBenchLocalTools injected at runtime by the runner

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        self._llm = llm

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner."""
        from agentdoc import doc

        self.instructions = task_input.get("system_prompt", "")
        self.context["instructions"] = self.instructions

        tool_doc = (
            "To navigate the repository and modify files, use the following tools"
            " (call via ``self.swebench.<method>(...)``:\n\n" + doc(self.swebench)
        )
        self.context["tool_instructions"] = tool_doc

        self.initial_observation = task_input.get("initial_observation", "")
        self.context["initial_observation"] = self.initial_observation

        self.response_format = task_input.get("response_format", "")
        self.context["response_format"] = self.response_format

        problem_statement = task_input.get("problem_statement", "")
        self.feedback = FeedbackAgent(llm=self._llm, swebench=self.swebench)

        try:
            result = await self.solve_task(problem_statement, self.response_format)
            result_str = str(result) if result is not None else ""
            if self.response_format == "code" and result_str:
                result_str = textwrap.dedent(result_str)
            return {"response": result_str, "success": True, "result": result}
        except Exception as e:
            return {"response": "", "success": False, "error": str(e)}

    # -----------------------------------------------------------------------
    # Phase 1: Understand
    # -----------------------------------------------------------------------

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=40, max_retries=3)))
    async def clarify_issue(self, raw_issue: str) -> ClarifiedRequirements:
        """Transform a symptom-focused issue into a root-cause-focused requirement.

        ## Your Mission
        Transform symptom language into general feature/capability language.

        ## Common Mistakes to Avoid

        ### ❌ BAD: symptom-focused, narrow scope
        - "Add X to Y **writer**"  →  focuses on one component
        - "Fix error in method Z"  →  focuses on symptom

        ### ✅ GOOD: root-cause-focused, general scope
        - "Implement X in Y **functionality**"
        - "Add X support throughout Y **handling**"

        ## Rules

        1. NEVER mention specific components (writer, reader, parser…).
        2. NEVER mention implementation details (methods, parameters, classes…).
        3. Focus on FEATURES not SYMPTOMS.
        4. Use comprehensive scope words: "functionality", "module", "handling",
           "operations", "throughout", "capability".

        ## Transformation pattern
        - Symptom "writer errors"        → Feature "flexible handling throughout"
        - Symptom "does not accept X"    → Feature "lacks support for X"
        - Symptom "method Y fails"       → Feature "capability Z missing"
        - Narrow "writer"                → General "functionality" or "module"

        Return :class:`ClarifiedRequirements` formatted as a clear git issue.
        """
        ...

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=30, max_retries=3)))
    async def analyze_root_cause(self, clarified_requirements: str) -> RootCauseAnalysis:
        """Trace from symptom to root cause and identify ALL affected files.

        ## Task
        1. Trace from symptom to root cause.
        2. Find ALL files that need modification (use grep/search extensively).
        3. Verify architectural scope — do multiple layers need fixes?
        4. Include ALL related aspects; don't narrow scope without evidence.

        ## Instructions
        1. Read the clarified requirements, but DON'T assume they're complete.
        2. Find the error/bug location yourself using code search.
        3. Trace backwards: symptom → caller → underlying layer → root origin.
        4. Search ENTIRE codebase for similar patterns needing the same fix.
        5. Check parent classes, subclasses, helper functions, related modules.
        6. Sanity check: does my analysis explain ALL symptoms?
        """
        ...

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=30, max_retries=3)))
    async def get_repo_overview(self) -> Overview:
        """Get a high-level overview of the repository.

        Find README, version, and other relevant information.
        Pin useful context for later phases.
        """
        ...

    # -----------------------------------------------------------------------
    # Phase 2: Implement
    # -----------------------------------------------------------------------

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100, max_retries=10)))
    async def implement_complete_fix(
        self,
        clarified_requirements: str,
        root_cause: RootCauseAnalysis,
        test_cases: str,
    ) -> None:
        """Implement the complete fix across all affected files.

        ## Task
        Given the root-cause analysis, implement the fix across all affected
        files.  Think about ripple effects and fix related locations too.
        Fix COMPLETE functionality, not just the fragments mentioned in tests.
        Run the test cases to verify correctness.

        ## Instructions
        For EACH file in ``root_cause.all_affected_files``:
          a. Read the file and understand current implementation.
          b. Verify the root-cause analysis is correct for this file.
          c. Check existing tests to understand expected behaviour.
          d. Apply fix that addresses the real problem (not just test cases).
          e. After editing, verify affected code.
          f. Run the test cases.

        ## Root Cause
        - Symptom:    {root_cause.symptom_location}
        - Root cause: {root_cause.root_cause_location}
        - Files:      {root_cause.all_affected_files}
        - Reason:     {root_cause.justification}

        When done, return.
        """
        ...

    # -----------------------------------------------------------------------
    # Phase 3: Review
    # -----------------------------------------------------------------------

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=30, max_retries=5)))
    async def fix_review_issues(
        self,
        clarified_requirements: str,
        issues: list[str],
    ) -> None:
        """Apply fixes identified by the reviewer.

        ## CRITICAL: Trust the reviewer's feedback.
        Assume the reviewer is correct and apply the suggested fix.

        When done, return.
        """
        ...

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _create_fallback_root_cause(self) -> RootCauseAnalysis:
        return RootCauseAnalysis(
            symptom_location="Unknown — analysis failed",
            root_cause_location="Unknown — analysis failed",
            all_affected_files=[],
            justification="Analysis phase failed; implementing directly from issue description.",
        )

    # -----------------------------------------------------------------------
    # Orchestrator
    # -----------------------------------------------------------------------

    async def solve_task(self, description: str, response_format: str = "") -> Any:
        """Orchestrate: clarify → analyse → implement → review → iterate."""
        self._repo_overview: Overview | None = None
        self._root_cause: RootCauseAnalysis | None = None
        self._clarified_text: str = description

        # Phase 1a: repo overview
        try:
            logger.info("Phase 1a: Getting repo overview...")
            self._repo_overview = await self.get_repo_overview()
            self.context["repo_overview"] = self._repo_overview
        except Exception as e:
            logger.warning("Repo overview failed: %s", e)

        # Phase 1b: clarify + root cause
        try:
            logger.info("Phase 1b: Clarifying requirements...")
            clarified = await self.clarify_issue(description)
            self._clarified = clarified
            self._clarified_text = str(clarified)
            self.context["reworded_problem_statement"] = self._clarified

            logger.info("Phase 1c: Analysing root cause...")
            self._root_cause = await self.analyze_root_cause(self._clarified_text)
            self.context["root_cause_analysis"] = self._root_cause
        except Exception as e:
            logger.warning("Analysis phase failed: %s", e)
            self._clarified_text = description
            self._root_cause = self._create_fallback_root_cause()

        # Phase 2: implement
        try:
            logger.info("Phase 2: Implementing fix...")
            root_cause = self._root_cause or self._create_fallback_root_cause()
            test_cases = await self.feedback.create_test_cases(self._clarified_text)
            await self.implement_complete_fix(self._clarified_text, root_cause, test_cases)
        except Exception as e:
            logger.warning("Phase 2 failed: %s", e)

        # Phase 3: review loop
        for iteration in range(3):
            diff = await self.swebench.git_diff()
            try:
                logger.info("Phase 3: Reviewing solution (iteration %d/3)...", iteration + 1)
                issues = await self.feedback.review_solution(
                    diff=diff,
                    repo_overview=self._repo_overview,
                    problem_statement=self._clarified_text,
                    root_cause_analysis=str(self._root_cause) if self._root_cause else "",
                )
            except Exception as e:
                logger.warning("Phase 3 review failed: %s", e)
                issues = []

            if not issues:
                logger.info("Phase 3: Review passed — no issues found.")
                break

            try:
                logger.info("Phase 3: Fixing %d review issues...", len(issues))
                await self.fix_review_issues(self._clarified_text, issues)
            except Exception as e:
                logger.warning("Phase 3 fix failed: %s", e)
                break

        return await self.swebench.git_diff()
