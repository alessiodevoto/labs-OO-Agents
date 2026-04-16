# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Error formatting for LLM feedback.

Formats errors to match IPython/Jupyter-style output:
- Uses "Cell In[N], line X" format (stripping File "..." wrapper)
- Adjusts line numbers to account for wrapper code offset
- Filters out framework tracebacks, showing only user code
- Source code lines are shown automatically via linecache registration

Usage:
    from nemo_oo_agents.errors.formatting import format_error_for_llm
    result = format_error_for_llm(error, code, line_offset=2)

    # Or use the formatter class directly
    from nemo_oo_agents.errors.formatting import IPythonErrorFormatter
    formatter = IPythonErrorFormatter()
    result = formatter.format(error, code, line_offset=2)
"""

import re
import traceback

# Framework path markers - frames containing these are filtered out
_FRAMEWORK_MARKERS = ("nemo_oo_agents/", "site-packages/", "lib/python", "<frozen")

# User code filename pattern - matches "Cell In[N]" format
_CELL_PATTERN = re.compile(r"^Cell In\[\d+\]$")

# Internal wrapper function names to replace with <module>
_WRAPPER_NAMES = ("__repl_wrapper__", "__wrapper__")


def _is_user_code_frame(filename: str) -> bool:
    """Check if a traceback frame is from user code (not framework internals).

    Returns True for:
    - Cell In[N] format (IPython-style REPL code)
    - <execute_code> format (legacy)
    - Any file NOT in framework paths (user's own agent files, etc.)

    Returns False for:
    - Framework paths (nemo_oo_agents/, site-packages/, lib/python, etc.)
    """
    # Filter out framework paths first
    if any(marker in filename for marker in _FRAMEWORK_MARKERS):
        return False

    # Everything else is user code (REPL cells, user's agent files, etc.)
    return True


def _is_validation_error(error: Exception) -> bool:
    """Check if error is a static validation error (no traceback needed)."""
    try:
        from nemo_oo_agents.errors import RestrictedCodeError, ValidationError

        return isinstance(error, (ValidationError, RestrictedCodeError))
    except ImportError:
        return type(error).__name__ in ("ValidationError", "RestrictedCodeError")


def _strip_file_prefix(text: str) -> str:
    """Strip 'File "..."' wrapper to match IPython output.

    Transforms:
        File "Cell In[1]", line 1 → Cell In[1], line 1
    """
    return re.sub(r'File "([^"]+)"', r"\1", text)


def _replace_wrapper_names(text: str) -> str:
    """Replace internal wrapper function names with <module>.

    Transforms:
        in __repl_wrapper__ → in <module>
        in __wrapper__ → in <module>
    """
    for name in _WRAPPER_NAMES:
        text = text.replace(f"in {name}", "in <module>")
    return text


def _adjust_line_numbers(text: str, offset: int) -> str:
    """Adjust line numbers in formatted output by subtracting offset.

    Transforms:
        Cell In[1], line 5 → Cell In[1], line 3 (with offset=2)
        line 5 → line 3 (with offset=2)
    """
    if offset <= 0:
        return text

    def adjust_match(match: re.Match[str]) -> str:
        prefix = match.group(1)
        line_num = int(match.group(2))
        adjusted = max(1, line_num - offset)  # Never go below line 1
        return f"{prefix}{adjusted}"

    # Match patterns like "Cell In[1], line 5" or "line 5"
    return re.sub(r"((?:Cell In\[\d+\], )?line )(\d+)", adjust_match, text)


class IPythonErrorFormatter:
    """IPython/Jupyter-style error formatter.

    Formats errors to match IPython output:
    - Uses "Cell In[N], line X" format (stripping File "..." wrapper)
    - Adjusts line numbers to account for wrapper code offset
    - Replaces internal wrapper names with <module>
    - Filters out framework tracebacks, showing only user code
    - Shows syntax errors with caret pointing to error location
    - Source lines shown automatically (via linecache registration in actor.py)
    """

    def format(self, error: Exception, code: str | None = None, *, line_offset: int = 0) -> str:
        """Format an error for LLM feedback.

        Args:
            error: The exception to format.
            code: Optional source code (used for syntax errors if text is missing).
            line_offset: Number of wrapper lines to subtract from line numbers.

        Returns:
            Formatted error string with adjusted line numbers.
        """
        if isinstance(error, SyntaxError):
            return self._format_syntax_error(error, code, line_offset)

        if _is_validation_error(error):
            return f"{type(error).__name__}: {error}"

        return self._format_runtime_error(error, line_offset)

    def _format_syntax_error(self, error: SyntaxError, code: str | None, line_offset: int) -> str:
        """Format SyntaxError using Python's traceback module, IPython-style.

        Output format:
            Cell In[1], line 1
              <invalid_code>
              ^
            SyntaxError: invalid syntax
        """
        # If error.text is missing but we have code, extract the line
        if not error.text and code and error.lineno:
            lines = code.split("\n")
            if 1 <= error.lineno <= len(lines):
                error.text = lines[error.lineno - 1]

        # Use Python's standard traceback formatting
        formatted = "".join(traceback.format_exception_only(type(error), error)).rstrip()

        # Strip the File "..." prefix to match IPython
        formatted = _strip_file_prefix(formatted)

        # Adjust line numbers for wrapper offset
        return _adjust_line_numbers(formatted, line_offset)

    def _format_runtime_error(self, error: Exception, line_offset: int) -> str:
        """Format runtime error using Python's traceback module, IPython-style.

        Filters to show only user code frames (excludes framework internals).
        Source lines are shown automatically via linecache registration.

        Output format:
            Cell In[1], line 3, in <module>
                x = 1 / 0
                    ~~^~~
            ZeroDivisionError: division by zero
        """
        if not error.__traceback__:
            return f"{type(error).__name__}: {error}"

        # Extract all frames as FrameSummary objects (filterable)
        extracted = traceback.extract_tb(error.__traceback__)

        # Filter to user code frames only
        user_frames = [frame for frame in extracted if _is_user_code_frame(frame.filename)]

        if not user_frames:
            # No user frames found, just show the error type and message
            return f"{type(error).__name__}: {error}"

        # Format the filtered frames + exception
        formatted_frames = traceback.format_list(user_frames)
        formatted_exception = traceback.format_exception_only(type(error), error)
        formatted = "".join(formatted_frames + formatted_exception).rstrip()

        # Strip the File "..." prefix to match IPython
        formatted = _strip_file_prefix(formatted)

        # Replace internal wrapper names with <module>
        formatted = _replace_wrapper_names(formatted)

        # Adjust line numbers for wrapper offset
        return _adjust_line_numbers(formatted, line_offset)


# Default formatter instance
_default_formatter = IPythonErrorFormatter()


def format_error_for_llm(error: Exception, code: str | None = None, *, line_offset: int = 0) -> str:
    """Format an error for LLM feedback.

    Uses IPython-style formatting:
    - Syntax errors show caret pointing to error location
    - Validation errors show clean messages without tracebacks
    - Runtime errors filter to user code frames only
    - Line numbers are adjusted to account for wrapper code
    - Internal wrapper function names are replaced with <module>
    - Source code lines are shown (via linecache registration in actor.py)

    Args:
        error: The exception to format.
        code: Optional source code (used for syntax errors if text is missing).
        line_offset: Number of wrapper lines to subtract from line numbers.
            This compensates for lines added by the async wrapper (e.g.,
            "async def __repl_wrapper__():", "try:", etc.).

    Returns:
        Formatted error string suitable for LLM consumption.
    """
    return _default_formatter.format(error, code, line_offset=line_offset)
