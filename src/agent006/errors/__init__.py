"""Error taxonomy for agent006.

Provides clear error categories for:
- Generation failures
- Validation failures
- Runtime errors

Also provides error formatting utilities for LLM feedback.
"""

from agent006.errors.formatting import (
    IPythonErrorFormatter as IPythonErrorFormatter,
)
from agent006.errors.formatting import (
    format_error_for_llm as format_error_for_llm,
)


class Agent006Error(Exception):
    """Base exception for all agent006 errors."""

    pass


# =============================================================================
# Generation Errors
# =============================================================================


class GenerationError(Agent006Error):
    """Error during LLM code generation."""

    pass


class GenerationAborted(GenerationError):
    """Generation was explicitly aborted by the agent (via task.abort() in generated code)."""

    pass


# =============================================================================
# Validation Errors
# =============================================================================


class ValidationError(Agent006Error):
    """Error validating generated code."""

    pass


class XMLFormatError(ValidationError):
    """LLM output contains XML/HTML tags instead of plain Python code.

    This error provides a clear message to the LLM that XML tags are not
    supported, helping it correct its output format.
    """

    pass


class RestrictedCodeError(ValidationError):
    """Generated code uses restricted/forbidden features.

    Raised when LLM-generated code violates safety rules such as:
    - Using import statements (modules are pre-imported)
    - Using exec(), eval(), or compile()
    - Using lambda expressions
    - Accessing dunder attributes (__class__, __dict__, etc.)
    - Using reflection (getattr with computed names, etc.)

    The error message explains what's forbidden and suggests alternatives.
    """

    def __init__(
        self,
        message: str,
        line_number: int | None = None,
        original_exception: Exception | None = None,
    ):
        super().__init__(message)
        self.line_number = line_number
        self.original_exception = original_exception


# =============================================================================
# Runtime Errors
# =============================================================================


class Agent006RuntimeError(Agent006Error):
    """Error in agent runtime system.

    Named to avoid shadowing Python's built-in RuntimeError.
    """

    pass


# Serialization / Storage Errors
from agent006.errors.storage import (  # noqa: E402, I001
    SerializationError as SerializationError,
    SnapshotNotFoundError as SnapshotNotFoundError,
    StorageNotConfiguredError as StorageNotConfiguredError,
)
