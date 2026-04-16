# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Debug signal handler for nemo_oo_agents.

Provides SIGUSR2 handler that dumps:
1. Full Python traceback with source lines
2. All registered Cell code from linecache
3. Pending LLM calls (if any)
4. Debug file location

Usage:
    from nemo_oo_agents.runtime.debug_handler import install_debug_handler
    install_debug_handler()  # Call once at startup

Then send SIGUSR2 to dump debug info:
    kill -USR2 <pid>

Debug output is written to:
    debug_dump_<pid>.txt  (in the current working directory, or dump_dir if set)

LLM Call Tracking:
    from nemo_oo_agents.runtime.debug_handler import llm_call_context

    with llm_call_context(model="gpt-4", prompt_tokens=1500):
        response = client.complete(...)
"""

import faulthandler
import linecache
import logging
import os
import signal
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Registry for tracking pending LLM calls
# Key: unique call ID, Value: dict with metadata (model, start_time, prompt_tokens, etc.)
_pending_llm_calls: dict[str, dict[str, Any]] = {}
_llm_call_lock = threading.Lock()
_llm_call_counter = 0


def register_llm_call(
    model: str,
    prompt_tokens: int | None = None,
    endpoint: str | None = None,
    **extra_metadata,
) -> str:
    """Register a pending LLM call for debug tracking.

    Args:
        model: Model name (e.g., "gpt-4", "claude-3-opus")
        prompt_tokens: Approximate number of prompt tokens
        endpoint: API endpoint being called
        **extra_metadata: Any additional metadata to track

    Returns:
        Call ID to use when unregistering
    """
    global _llm_call_counter
    with _llm_call_lock:
        _llm_call_counter += 1
        call_id = f"llm_{_llm_call_counter}"
        _pending_llm_calls[call_id] = {
            "model": model,
            "start_time": time.monotonic(),
            "start_timestamp": datetime.now().isoformat(),
            "prompt_tokens": prompt_tokens,
            "endpoint": endpoint,
            "thread": threading.current_thread().name,
            **extra_metadata,
        }
        return call_id


def unregister_llm_call(call_id: str) -> None:
    """Unregister a completed LLM call."""
    with _llm_call_lock:
        _pending_llm_calls.pop(call_id, None)


@contextmanager
def llm_call_context(
    model: str,
    prompt_tokens: int | None = None,
    endpoint: str | None = None,
    **extra_metadata,
):
    """Context manager for tracking LLM calls.

    Usage:
        with llm_call_context(model="gpt-4", prompt_tokens=1500):
            response = client.complete(...)
    """
    call_id = register_llm_call(
        model=model,
        prompt_tokens=prompt_tokens,
        endpoint=endpoint,
        **extra_metadata,
    )
    try:
        yield call_id
    finally:
        unregister_llm_call(call_id)


def _dump_pending_llm_calls(file=None):
    """Dump information about pending LLM calls."""
    if file is None:
        file = sys.stderr

    file.write("\n" + "=" * 60 + "\n")
    file.write("PENDING LLM CALLS\n")
    file.write("=" * 60 + "\n")

    with _llm_call_lock:
        calls = list(_pending_llm_calls.items())

    if not calls:
        file.write("No pending LLM calls registered.\n")
    else:
        now = time.monotonic()
        for call_id, info in calls:
            elapsed = now - info["start_time"]
            model = info.get("model", "unknown")
            tokens = info.get("prompt_tokens")
            endpoint = info.get("endpoint", "")
            thread = info.get("thread", "")
            started = info.get("start_timestamp", "")

            file.write(f"\n--- {call_id} ---\n")
            file.write(f"  Model: {model}\n")
            file.write(f"  Waiting: {elapsed:.1f}s\n")
            file.write(f"  Started: {started}\n")
            if tokens:
                file.write(f"  Prompt tokens: ~{tokens}\n")
            if endpoint:
                file.write(f"  Endpoint: {endpoint}\n")
            if thread:
                file.write(f"  Thread: {thread}\n")

    file.write("\n")
    file.flush()


# Patterns that indicate we're in an LLM-related call
_LLM_STACK_PATTERNS = [
    # HTTP clients
    ("httpx", "HTTP client (httpx)"),
    ("aiohttp", "HTTP client (aiohttp)"),
    ("urllib3", "HTTP client (urllib3)"),
    ("requests/", "HTTP client (requests)"),
    # LLM libraries
    ("litellm", "LiteLLM"),
    ("openai/", "OpenAI SDK"),
    ("anthropic/", "Anthropic SDK"),
    ("google/generativeai", "Google AI SDK"),
    # Our wrappers
    ("unifiedllm", "UnifiedLLM"),
    ("completion_client", "CompletionClient"),
]


def _detect_llm_in_stack(frame) -> list[str]:
    """Analyze stack frames to detect if we're in an LLM call.

    Returns list of detected LLM-related contexts.
    """
    detected = []
    seen = set()

    try:
        # Walk up the stack
        current = frame
        while current is not None:
            filename = current.f_code.co_filename
            funcname = current.f_code.co_name

            for pattern, description in _LLM_STACK_PATTERNS:
                if pattern in filename and description not in seen:
                    # Get more context
                    lineno = current.f_lineno
                    detected.append(f"{description} at {filename}:{lineno} in {funcname}()")
                    seen.add(description)

            current = current.f_back
    except Exception:
        pass  # Don't fail if stack inspection fails

    return detected


def _get_debug_dump_path() -> Path:
    """Get debug dump path with PID. Uses _dump_dir, defaulting to cwd."""
    return _dump_dir / f"debug_dump_{os.getpid()}.txt"


def _dump_cell_code(file=None):
    """Dump all Cell code registered in linecache."""
    if file is None:
        file = sys.stderr

    file.write("\n" + "=" * 60 + "\n")
    file.write("REGISTERED CELL CODE (from linecache)\n")
    file.write("=" * 60 + "\n")

    # Find all Cell entries in linecache (format: "Cell <exec_id>[N]" or "Cell In[N]")
    cell_entries = sorted(
        [(name, data) for name, data in linecache.cache.items() if name.startswith("Cell ")],
        key=lambda x: x[0],
    )

    if not cell_entries:
        file.write("No Cell code registered in linecache.\n")
        return

    for cell_name, cell_data in cell_entries:
        _size, _mtime, lines, _fullname = cell_data  # type: ignore[misc]
        file.write(f"\n--- {cell_name} ({len(lines)} lines) ---\n")
        for i, line in enumerate(lines, 1):
            file.write(f"{i:4d}: {line.rstrip()}\n")

    file.write("\n" + "=" * 60 + "\n")
    file.flush()


def _debug_signal_handler(signum, frame):
    """Handle SIGUSR2 by dumping debug information."""
    # Write to both stderr and a debug file for reliability
    debug_file_path = _get_debug_dump_path()

    # Detect LLM calls in stack before we do anything else
    llm_in_stack = _detect_llm_in_stack(frame)

    try:
        with open(debug_file_path, "w") as debug_file:
            for file in [sys.stderr, debug_file]:
                file.write("\n")
                file.write("=" * 60 + "\n")
                file.write(f"DEBUG DUMP at {datetime.now().isoformat()}\n")
                file.write(f"Signal: {signum} (SIGUSR2)\n")
                file.write(f"Dump file: {debug_file_path}\n")
                file.write("=" * 60 + "\n\n")

                # 1. Quick summary - are we stuck in an LLM call?
                if llm_in_stack or _pending_llm_calls:
                    file.write("⚠️  LIKELY STUCK IN LLM CALL\n")
                    file.write("-" * 40 + "\n")
                    if llm_in_stack:
                        file.write("Detected in stack:\n")
                        for item in llm_in_stack:
                            file.write(f"  • {item}\n")
                    if _pending_llm_calls:
                        file.write(f"Registered pending calls: {len(_pending_llm_calls)}\n")
                    file.write("\n")

                # 2. Standard traceback (like faulthandler but with source lines)
                file.write("CURRENT TRACEBACK:\n")
                file.write("-" * 40 + "\n")
                try:
                    # Use traceback module which uses linecache for source lines
                    traceback.print_stack(frame, file=file)
                except Exception as e:
                    file.write(f"Error getting traceback: {e}\n")

                # 3. Dump pending LLM calls (registered via llm_call_context)
                _dump_pending_llm_calls(file)

                # 4. Dump all registered cell code
                _dump_cell_code(file)

                file.write("\nDEBUG DUMP COMPLETE\n")
                file.write("=" * 60 + "\n\n")
                file.flush()
    except Exception as e:
        # If file write fails, still try stderr
        sys.stderr.write(f"Debug dump error: {e}\n")
        sys.stderr.flush()


_handler_installed = False
_dump_dir: Path = Path(".")


def install_debug_handler(dump_dir: Path | None = None):
    """Install SIGUSR2 handler for debug dumps.

    Safe to call multiple times - only installs once.

    Args:
        dump_dir: Directory to write debug_dump_<pid>.txt files. Defaults to cwd.
    """
    global _handler_installed, _dump_dir
    # Update _dump_dir before the early-return so callers can redirect the dump
    # location even after the handler is already installed (e.g. second call
    # with a different dump_dir).  Handler re-installation is intentionally
    # skipped; only the output path changes.
    if dump_dir is not None:
        _dump_dir = dump_dir
    if _handler_installed:
        return

    # Enable basic faulthandler for segfaults etc.
    # Best-effort: skip when stderr lacks a real file descriptor (Textual TUI,
    # Jupyter notebooks, or any host that replaces sys.stderr).
    try:
        faulthandler.enable()
    except (RuntimeError, AttributeError, OSError) as exc:
        logger.debug("faulthandler.enable() skipped: %s", exc)

    # Install SIGUSR2 handler (less commonly used than SIGUSR1)
    try:
        signal.signal(signal.SIGUSR2, _debug_signal_handler)
        _handler_installed = True
    except (ValueError, OSError):
        # Can't set signal handler (not main thread, or platform issue)
        pass


def dump_debug_info(file=None):
    """Manually dump debug info (for programmatic use)."""
    if file is None:
        file = sys.stderr

    file.write("\n--- Manual debug dump ---\n")
    _dump_pending_llm_calls(file)
    _dump_cell_code(file)
