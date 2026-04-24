# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""agentdoc — runtime Python documentation for Python objects.

Two-step mental model:

  1. spec() — specify how the type renders: descriptions, visibility, hints.
  2. doc()  — get the documentation: the API contract, ready for a prompt.

Quick start:

    from agentdoc import spec, hidden, doc, pformat, truncating_pformat

    class MyAgent:
        api_key: Annotated[str, hidden] = ""          # excluded from documentation
        label: Annotated[str, spec(description="Display name")] = "agent"

    agent = MyAgent()
    doc(MyAgent)   # → API contract (type view)
    doc(agent)     # → API contract with current values substituted
    pformat(agent) # → compact repr: MyAgent(label='agent')
"""

import io
import sys
from typing import Annotated, Any

from nemo_oo_agents.agentdoc._docs import spec
from nemo_oo_agents.agentdoc._pformat import _pformat
from nemo_oo_agents.agentdoc._truncating_stream import TruncatingStringIO
from nemo_oo_agents.agentdoc._visibility import hidden
from nemo_oo_agents.agentdoc.core import doc
from nemo_oo_agents.agentdoc.doc_config import DocConfig

__version__ = "0.2.0"

__submodules__ = ["ext", "introspect", "visibility", "adapters"]


_TRUNCATING_PFORMAT_MAX_CHARS: int = 500_000


def truncating_pformat(
    obj: Annotated[Any, "Object to format"],
    *,
    max_chars: Annotated[int, "Hard character cap on total output"] = _TRUNCATING_PFORMAT_MAX_CHARS,
    **kwargs: Any,
) -> str:
    """Format *obj* as a string, bounded to prevent OOM.

    Like :func:`pformat` but with a hard ``max_chars`` cap on total output length.
    Uses :class:`TruncatingStringIO` internally so the cap fires *during* formatting —
    not after building a potentially-huge intermediate string.

    When the cap fires, a prose notice is prepended so the consumer knows the
    value was large and how much was kept.  Both the head and tail are retained.

    String fast-path: plain strings skip pformat entirely — the cap is applied
    directly.  When truncated, both the head and tail are retained.

    Raises:
        ValueError: if ``max_chars`` is <= 0.
    """
    if max_chars <= 0:
        raise ValueError(f"truncating_pformat max_chars must be > 0, got {max_chars}")

    if isinstance(obj, str):
        if len(obj) <= max_chars:
            return obj
        # Head+tail for strings (already in memory)
        head_chars = max_chars // 2
        tail_chars = max_chars - head_chars
        head = obj[:head_chars]
        tail = obj[-tail_chars:]
        dropped = len(obj) - head_chars - tail_chars
        return (
            f"<truncated-output>\n"
            f"Output too large ({len(obj):,} chars). "
            f"Showing first {head_chars:,} and last {tail_chars:,} chars.\n\n"
            f"{head}\n\n"
            f"... {dropped:,} chars not shown ...\n\n"
            f"{tail}\n"
            f"</truncated-output>"
        )

    # Non-strings: delegate to _pformat with a TruncatingStringIO cap.
    # Default max_string to max_chars so individual string fields don't get
    # silently clipped before the overall cap fires.
    kwargs.setdefault("max_string", max_chars)
    stream = TruncatingStringIO(limit=max_chars)
    _pformat(obj, stream, **kwargs)
    return stream.getvalue()


def pformat(
    obj: Annotated[Any, "Object to format"],
    *,
    console: Any = None,
    indent_guides: bool = True,
    max_length: Annotated[int | None, "Max elements per container; None = unlimited"] = None,
    max_string: Annotated[int | None, "Max string chars; None = unlimited"] = None,
    max_depth: Annotated[int | None, "Max nesting depth; None = unlimited"] = None,
    expand_all: Annotated[bool, "Always expand containers to multiple lines"] = False,
    concise: Annotated[bool, "Show first-line docstrings only"] = False,
    instance_mode: Annotated[
        str, "Instance format: 'repr' for repr-style, 'type' for type structure"
    ] = "repr",
) -> str:
    """Format an object as a string with smart truncation.

    Drop-in replacement for ``rich.pretty.pformat()``.
    For user-defined instances, ``hidden`` fields are automatically excluded.
    Respects ``@spec(expand=False)`` on field types — shown as ``ClassName()`` rather than expanded.

    ``console`` and ``indent_guides`` are accepted for Rich API compatibility but have no effect.

    To cap total output size (preventing OOM on huge objects), use :func:`truncating_pformat`
    which applies a hard ``max_chars`` limit via :class:`TruncatingStringIO`.
    """
    # console and indent_guides are intentionally ignored for Rich compatibility
    del console, indent_guides

    stream: io.StringIO = io.StringIO()

    _pformat(
        obj,
        stream,
        max_length=max_length,
        max_string=max_string,
        max_depth=max_depth,
        expand_all=expand_all,
        concise=concise,
        instance_mode=instance_mode,
    )

    return stream.getvalue()


def pprint(
    obj: Annotated[Any, "Object to print"],
    *,
    console: Any = None,
    indent_guides: bool = True,
    max_length: Annotated[int | None, "Max elements per container; None = unlimited"] = None,
    max_string: Annotated[int | None, "Max string chars; None = unlimited"] = None,
    max_depth: Annotated[int | None, "Max nesting depth; None = unlimited"] = None,
    expand_all: Annotated[bool, "Always expand containers to multiple lines"] = False,
    concise: Annotated[bool, "Show first-line docstrings only"] = False,
    instance_mode: Annotated[
        str, "Instance format: 'repr' for repr-style, 'type' for type structure"
    ] = "repr",
) -> None:
    """Pretty-print an object with smart truncation. Prints to stdout.

    Drop-in replacement for ``rich.pretty.pprint()``.
    Writes directly to ``sys.stdout`` via stream-based formatting so that
    stdout capture (via ``ContextVarStream``) bounds output during formatting.

    ``console`` and ``indent_guides`` are accepted for Rich API compatibility but have no effect.
    """
    # console and indent_guides are intentionally ignored for Rich compatibility
    del console, indent_guides

    _pformat(
        obj,
        sys.stdout,
        max_length=max_length,
        max_string=max_string,
        max_depth=max_depth,
        expand_all=expand_all,
        concise=concise,
        instance_mode=instance_mode,
    )
    sys.stdout.write("\n")


__all__ = [
    "spec",
    "hidden",
    "doc",
    "DocConfig",
    "pformat",
    "pprint",
    "truncating_pformat",
    "TruncatingStringIO",
]
