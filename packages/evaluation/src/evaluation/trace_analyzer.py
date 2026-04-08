"""
Trace analyzer for extracting failure patterns and generating improvement suggestions.

This module reads JSONL trace files produced by the agent framework and:
1. Extracts validation errors, exceptions, and failed tool calls
2. Identifies patterns across multiple failures
3. Generates improvement context for the next iteration
"""

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluation.protocol import (
    ErrorCategory,
    EvalResult,
    ModelUsageStats,
    TaskUsageStats,
)

logger = logging.getLogger(__name__)


@dataclass
class FailurePattern:
    """
    A pattern identified across multiple failures.

    Attributes:
        category: Type of error
        frequency: How often this pattern occurred
        example_traces: Paths to traces exhibiting this pattern
        error_messages: Representative error messages
        suggested_refinement: LLM-generated or rule-based improvement hint
    """

    category: ErrorCategory
    frequency: int
    example_traces: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    suggested_refinement: str | None = None

    def __post_init__(self):
        # Deduplicate while preserving order
        self.example_traces = list(dict.fromkeys(self.example_traces))
        self.error_messages = list(dict.fromkeys(self.error_messages))


@dataclass
class TraceEvent:
    """A single event extracted from a trace."""

    event_type: str
    timestamp: str | None
    data: dict[str, Any]
    span_id: str | None = None
    trace_id: str | None = None


@dataclass
class ExtractedFailure:
    """A failure extracted from a trace."""

    category: ErrorCategory
    message: str
    context: dict[str, Any]
    trace_path: str
    span_id: str | None = None


class TraceAnalyzer:
    """
    Analyzes execution traces to identify failure patterns and suggest improvements.

    This class reads JSONL trace files and extracts structured information about
    what went wrong, enabling the self-improvement loop to provide better context
    for subsequent attempts.
    """

    # Event types that indicate failures
    FAILURE_EVENT_TYPES = {
        "GENERATION_VALIDATION_FAILED",
        "GENERATION_VALIDATION_RETRY",
        "EXECUTION_ERROR",
        "TOOL_ERROR",
        "AGENT_ERROR",
    }

    # Patterns for classifying errors
    ERROR_PATTERNS = [
        (r"SyntaxError", ErrorCategory.SYNTAX_ERROR),
        (r"forbidden.*operation|not allowed", ErrorCategory.FORBIDDEN_OPERATION),
        (r"validation.*failed|invalid.*code", ErrorCategory.VALIDATION_ERROR),
        (r"wrong.*function|incorrect.*tool|unknown.*function", ErrorCategory.WRONG_TOOL),
        (r"missing.*argument|required.*parameter", ErrorCategory.MISSING_ARGUMENTS),
        (r"unexpected.*argument|extra.*parameter", ErrorCategory.EXTRA_ARGUMENTS),
        (r"TypeError|ValueError|AttributeError", ErrorCategory.WRONG_ARGUMENTS),
        (r"TimeoutError|timed? ?out", ErrorCategory.TIMEOUT),
        (r"AssertionError|assertion.*failed", ErrorCategory.ASSERTION_FAILED),
        (r"RuntimeError|Exception", ErrorCategory.RUNTIME_ERROR),
        (r"policy.*violation|constraint.*violated", ErrorCategory.POLICY_VIOLATION),
        (r"incomplete|partial|not.*finished", ErrorCategory.INCOMPLETE_SOLUTION),
        (r"wrong.*output|incorrect.*result|mismatch", ErrorCategory.WRONG_OUTPUT),
    ]

    def __init__(self, llm_client: Any | None = None):
        """
        Initialize the trace analyzer.

        Args:
            llm_client: Optional LLM client for generating improvement suggestions.
                       If not provided, uses rule-based suggestions.
        """
        self.llm_client = llm_client

    def extract_failures(self, trace_path: str) -> list[ExtractedFailure]:
        """
        Extract all failures from a trace file.

        Args:
            trace_path: Path to the JSONL trace file

        Returns:
            List of extracted failures with categorization
        """
        failures = []
        path = Path(trace_path)

        if not path.exists():
            return failures

        with open(path) as f:
            for line in f:
                try:
                    span = json.loads(line.strip())
                    failure = self._extract_failure_from_span(span, trace_path)
                    if failure:
                        failures.append(failure)
                except json.JSONDecodeError:
                    continue

        return failures

    def _extract_failure_from_span(
        self, span: dict[str, Any], trace_path: str
    ) -> ExtractedFailure | None:
        """Extract failure information from a single span."""
        # Check span status
        status = span.get("status", {})
        if status.get("status_code") == "ERROR":
            message = status.get("description", "Unknown error")
            return ExtractedFailure(
                category=self._classify_error(message),
                message=message,
                context=self._extract_context(span),
                trace_path=trace_path,
                span_id=span.get("span_id"),
            )

        # Check for failure events in span events
        for event in span.get("events", []):
            event_name = event.get("name", "")
            if any(ft in event_name for ft in self.FAILURE_EVENT_TYPES):
                attrs = event.get("attributes", {})
                message = attrs.get("error", attrs.get("message", event_name))
                return ExtractedFailure(
                    category=self._classify_error(str(message)),
                    message=str(message),
                    context={
                        "event": event_name,
                        "attributes": attrs,
                        **self._extract_context(span),
                    },
                    trace_path=trace_path,
                    span_id=span.get("span_id"),
                )

        # Check attributes for validation errors
        attrs = span.get("attributes", {})
        if attrs.get("validation_error") or attrs.get("error"):
            message = attrs.get("validation_error") or attrs.get("error")
            return ExtractedFailure(
                category=self._classify_error(str(message)),
                message=str(message),
                context=self._extract_context(span),
                trace_path=trace_path,
                span_id=span.get("span_id"),
            )

        return None

    def _extract_context(self, span: dict[str, Any]) -> dict[str, Any]:
        """Extract relevant context from a span for debugging."""
        attrs = span.get("attributes", {})
        return {
            "span_name": span.get("name"),
            "code": attrs.get("code"),
            "tool_name": attrs.get("tool_name"),
            "method_name": attrs.get("method_name"),
            "attempt": attrs.get("attempt"),
        }

    def _classify_error(self, message: str) -> ErrorCategory:
        """Classify an error message into a category."""
        message_lower = message.lower()
        for pattern, category in self.ERROR_PATTERNS:
            if re.search(pattern, message_lower, re.IGNORECASE):
                return category
        return ErrorCategory.UNKNOWN

    def identify_patterns(self, failures: list[ExtractedFailure]) -> list[FailurePattern]:
        """
        Identify common patterns across multiple failures.

        Args:
            failures: List of extracted failures

        Returns:
            List of failure patterns, sorted by frequency
        """
        # Group by category
        by_category: dict[ErrorCategory, list[ExtractedFailure]] = defaultdict(list)
        for failure in failures:
            by_category[failure.category].append(failure)

        patterns = []
        for category, category_failures in by_category.items():
            pattern = FailurePattern(
                category=category,
                frequency=len(category_failures),
                example_traces=[f.trace_path for f in category_failures[:3]],
                error_messages=[f.message for f in category_failures[:5]],
                suggested_refinement=self._generate_refinement_for_category(
                    category, category_failures
                ),
            )
            patterns.append(pattern)

        # Sort by frequency (most common first)
        patterns.sort(key=lambda p: p.frequency, reverse=True)
        return patterns

    def _generate_refinement_for_category(
        self, category: ErrorCategory, failures: list[ExtractedFailure]
    ) -> str:
        """Generate improvement suggestions based on error category."""
        # Rule-based suggestions (can be enhanced with LLM)
        suggestions = {
            ErrorCategory.SYNTAX_ERROR: (
                "Check code syntax carefully. Ensure proper indentation, "
                "matching brackets, and valid Python syntax."
            ),
            ErrorCategory.VALIDATION_ERROR: (
                "The code contains forbidden operations. Review the planning language "
                "constraints and avoid using restricted features."
            ),
            ErrorCategory.FORBIDDEN_OPERATION: (
                "Avoid using exec, eval, __import__, or accessing dunder attributes. "
                "Use only the allowed built-in functions and modules."
            ),
            ErrorCategory.WRONG_TOOL: (
                "Review the available tools and their purposes. "
                "Make sure to call the correct function for the task."
            ),
            ErrorCategory.WRONG_ARGUMENTS: (
                "Check the function signatures and argument types. "
                "Ensure arguments match the expected types and formats."
            ),
            ErrorCategory.MISSING_ARGUMENTS: (
                "The function call is missing required arguments. "
                "Review the function signature and provide all required parameters."
            ),
            ErrorCategory.EXTRA_ARGUMENTS: (
                "The function call has unexpected arguments. "
                "Remove any arguments not defined in the function signature."
            ),
            ErrorCategory.TIMEOUT: (
                "The operation timed out. Consider breaking the task into smaller steps "
                "or optimizing the approach for efficiency."
            ),
            ErrorCategory.ASSERTION_FAILED: (
                "The output didn't match expected values. "
                "Review the task requirements and verify the logic produces correct results."
            ),
            ErrorCategory.RUNTIME_ERROR: (
                "A runtime error occurred. Check for edge cases, null values, "
                "and ensure all variables are properly initialized."
            ),
            ErrorCategory.POLICY_VIOLATION: (
                "The solution violated policy constraints. "
                "Review the policy guidelines and ensure compliance."
            ),
            ErrorCategory.INCOMPLETE_SOLUTION: (
                "The solution is incomplete. Make sure to address all aspects "
                "of the task and provide a complete implementation."
            ),
            ErrorCategory.WRONG_OUTPUT: (
                "The output is incorrect. Review the expected format and values, "
                "and trace through the logic to find the error."
            ),
            ErrorCategory.UNKNOWN: (
                "An unexpected error occurred. Review the error message carefully "
                "and consider alternative approaches."
            ),
        }

        base_suggestion = suggestions.get(category, suggestions[ErrorCategory.UNKNOWN])

        # Add specific context from failures if available
        if failures:
            error_examples = [f.message for f in failures[:2] if f.message]
            if error_examples:
                base_suggestion += "\n\nSpecific errors encountered:\n"
                for i, ex in enumerate(error_examples, 1):
                    # Truncate long messages
                    truncated = ex[:200] + "..." if len(ex) > 200 else ex
                    base_suggestion += f"{i}. {truncated}\n"

        return base_suggestion

    def generate_improvement_context(
        self, trace_paths: list[str], results: list[EvalResult]
    ) -> str:
        """
        Generate improvement context from previous attempts.

        This is the main entry point for the self-improvement loop. It analyzes
        all previous traces and results to generate context that helps the agent
        perform better on the next attempt.

        Args:
            trace_paths: Paths to trace files from previous attempts
            results: Evaluation results from previous attempts

        Returns:
            Formatted string to include in agent's context
        """
        # Extract all failures
        all_failures = []
        for path in trace_paths:
            all_failures.extend(self.extract_failures(path))

        # Also include explicit errors from results
        for result in results:
            if not result.success and result.error_message:
                all_failures.append(
                    ExtractedFailure(
                        category=result.error_category or ErrorCategory.UNKNOWN,
                        message=result.error_message,
                        context={"from_result": True},
                        trace_path=result.trace_path or "",
                    )
                )

        if not all_failures:
            return ""

        # Identify patterns
        patterns = self.identify_patterns(all_failures)

        # Build improvement context
        context_parts = [
            "## Previous Attempt Analysis\n",
            f"Analyzed {len(trace_paths)} previous attempt(s) with {len(all_failures)} failure(s).\n",
        ]

        if patterns:
            context_parts.append("\n### Error Patterns Identified\n")
            for pattern in patterns[:3]:  # Top 3 patterns
                context_parts.append(
                    f"\n**{pattern.category.value}** (occurred {pattern.frequency} time(s)):\n"
                    f"{pattern.suggested_refinement}\n"
                )

        # Add attempt history summary
        context_parts.append("\n### Attempt History\n")
        for i, result in enumerate(results, 1):
            status = "Success" if result.success else "Failed"
            score_str = f" (score: {result.score:.2f})" if result.score > 0 else ""
            error_str = f" - {result.error_message[:100]}..." if result.error_message else ""
            context_parts.append(f"- Attempt {i}: {status}{score_str}{error_str}\n")

        return "".join(context_parts)

    async def analyze_and_suggest(
        self,
        trace_path: str,
        error_type: ErrorCategory | None,
        error_message: str | None,
        previous_context: str = "",
    ) -> str:
        """
        Analyze a single trace and generate suggestions for the next attempt.

        This is called after each failed attempt to update the improvement context.

        Args:
            trace_path: Path to the most recent trace
            error_type: Category of the error (if known)
            error_message: Error message (if available)
            previous_context: Context from previous iterations

        Returns:
            Updated improvement context string
        """
        failures = self.extract_failures(trace_path)

        # Add explicit error if provided
        if error_message:
            failures.append(
                ExtractedFailure(
                    category=error_type or ErrorCategory.UNKNOWN,
                    message=error_message,
                    context={},
                    trace_path=trace_path,
                )
            )

        if not failures:
            return previous_context

        patterns = self.identify_patterns(failures)

        # Build new context
        new_context_parts = []

        if previous_context:
            new_context_parts.append(previous_context)
            new_context_parts.append("\n---\n")

        new_context_parts.append("\n### Latest Attempt Analysis\n")

        for pattern in patterns[:2]:
            new_context_parts.append(
                f"\n**Issue: {pattern.category.value}**\n{pattern.suggested_refinement}\n"
            )

        return "".join(new_context_parts)

    def get_failure_summary(self, trace_paths: list[str]) -> dict[str, Any]:
        """
        Get a structured summary of failures across traces.

        Useful for reporting and metrics.

        Args:
            trace_paths: List of trace file paths

        Returns:
            Dict with failure statistics
        """
        all_failures = []
        for path in trace_paths:
            all_failures.extend(self.extract_failures(path))

        category_counts = Counter(f.category for f in all_failures)

        return {
            "total_failures": len(all_failures),
            "by_category": {cat.value: count for cat, count in category_counts.items()},
            "unique_traces": len({f.trace_path for f in all_failures}),
            "patterns": [
                {
                    "category": p.category.value,
                    "frequency": p.frequency,
                    "suggestion": p.suggested_refinement,
                }
                for p in self.identify_patterns(all_failures)
            ],
        }

    # ===========================
    # Usage Statistics Extraction
    # ===========================
    # These methods implement the TraceAnalyzer protocol for extracting
    # usage statistics (tokens, latency, costs) from OTel traces.

    def analyze_trace(self, trace_path: str) -> TaskUsageStats:
        """
        Analyze a trace file and extract usage statistics.

        Implements the TraceAnalyzer protocol to extract:
        - Token counts per model
        - LLM call latencies
        - Total runtime
        - Model usage breakdown

        Args:
            trace_path: Path to .jsonl trace file

        Returns:
            TaskUsageStats with extracted metrics
        """
        path = Path(trace_path)
        if not path.exists():
            # Return empty stats for missing trace
            return TaskUsageStats(
                task_id=path.stem,
                total_runtime_seconds=0.0,
                models_used=[],
                total_llm_calls=0,
            )

        # Track stats per model
        model_stats: dict[str, ModelUsageStats] = {}
        trace_start_time: float | None = None
        trace_end_time: float | None = None
        total_llm_calls = 0

        with open(path) as f:
            for line in f:
                try:
                    span = json.loads(line.strip())

                    # Track overall trace timing
                    start_time = span.get("start_time_unix_nano", 0) / 1e9
                    end_time = span.get("end_time_unix_nano", 0) / 1e9

                    if trace_start_time is None or start_time < trace_start_time:
                        trace_start_time = start_time
                    if trace_end_time is None or end_time > trace_end_time:
                        trace_end_time = end_time

                    # Look for LLM spans
                    attrs = span.get("attributes", {})
                    span_name = span.get("name", "")

                    # Check if this is an LLM call span
                    if self._is_llm_span(span_name, attrs):
                        total_llm_calls += 1

                        # Extract model name
                        model_name = attrs.get(
                            "llm.model", attrs.get("gen_ai.request.model", "unknown")
                        )

                        # Initialize model stats if not seen before
                        if model_name not in model_stats:
                            model_stats[model_name] = ModelUsageStats(model_name=model_name)

                        stats = model_stats[model_name]

                        # Extract token counts with validation
                        prompt_tokens = attrs.get(
                            "llm.token_count.prompt", attrs.get("gen_ai.usage.input_tokens", 0)
                        )
                        completion_tokens = attrs.get(
                            "llm.token_count.completion", attrs.get("gen_ai.usage.output_tokens", 0)
                        )

                        # Validate and parse token counts
                        prompt_count = self._parse_token_count(prompt_tokens, "prompt_tokens", path)
                        completion_count = self._parse_token_count(
                            completion_tokens, "completion_tokens", path
                        )

                        stats.prompt_tokens += prompt_count
                        stats.completion_tokens += completion_count
                        stats.total_tokens += prompt_count + completion_count
                        stats.call_count += 1

                        # Calculate latency in milliseconds
                        if start_time > 0 and end_time > 0:
                            latency_ms = (end_time - start_time) * 1000
                            stats.latencies_ms.append(latency_ms)

                except json.JSONDecodeError:
                    continue

        # Calculate total runtime
        runtime_seconds = 0.0
        if trace_start_time and trace_end_time:
            runtime_seconds = trace_end_time - trace_start_time

        return TaskUsageStats(
            task_id=path.stem,
            total_runtime_seconds=runtime_seconds,
            models_used=list(model_stats.values()),
            total_llm_calls=total_llm_calls,
        )

    def _is_llm_span(self, span_name: str, attributes: dict[str, Any]) -> bool:
        """Check if a span represents an LLM API call."""
        # Check span name
        llm_span_names = ["llm", "chat", "completion", "generation", "inference"]
        if any(name in span_name.lower() for name in llm_span_names):
            return True

        # Check for LLM-related attributes
        llm_attrs = [
            "llm.model",
            "llm.token_count.prompt",
            "llm.token_count.completion",
            "gen_ai.request.model",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.output_tokens",
        ]
        return any(attr in attributes for attr in llm_attrs)

    def _parse_token_count(self, value: Any, field_name: str, trace_path: Path) -> int:
        """Parse a token count value with validation.

        Args:
            value: The raw value from the trace (could be int, str, None, etc.)
            field_name: Name of the field for logging
            trace_path: Path to trace file for logging context

        Returns:
            Parsed integer token count, or 0 if invalid
        """
        if value is None:
            return 0

        if isinstance(value, int):
            return value

        if isinstance(value, float):
            return int(value)

        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                logger.warning(
                    f"Invalid {field_name} value '{value}' in trace {trace_path.name}, using 0"
                )
                return 0

        logger.warning(
            f"Unexpected type {type(value).__name__} for {field_name} in trace "
            f"{trace_path.name}, using 0"
        )
        return 0
