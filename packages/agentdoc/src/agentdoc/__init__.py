"""agentdoc — runtime Python documentation for Python objects.

Two-step mental model:

  1. spec() — specify how the type renders: descriptions, visibility, hints.
  2. doc()  — get the documentation: the API contract, ready for a prompt.

Quick start:

    from agentdoc import spec, hidden, doc, pformat, safe_pformat

    class MyAgent:
        api_key: Annotated[str, hidden] = ""          # excluded from documentation
        label: Annotated[str, spec(description="Display name")] = "agent"

    agent = MyAgent()
    doc(MyAgent)   # → API contract (type view)
    doc(agent)     # → API contract with current values substituted
    pformat(agent) # → compact repr: MyAgent(label='agent')
"""

from typing import Annotated, Any

from agentdoc._docs import spec
from agentdoc._pformat import _pformat
from agentdoc._visibility import hidden
from agentdoc.core import doc
from agentdoc.doc_config import DocConfig

__version__ = "0.2.0"

__submodules__ = ["ext", "introspect", "visibility", "adapters"]


_SAFE_PFORMAT_MAX_CHARS: int = 500_000


def safe_pformat(
    obj: Annotated[Any, "Object to format"],
    *,
    max_chars: Annotated[int, "Hard character cap on total output"] = _SAFE_PFORMAT_MAX_CHARS,
    **kwargs: Any,
) -> str:
    """Format *obj* as a string, bounded to prevent OOM.

    Like :func:`pformat` but with a hard ``max_chars`` cap on total output length.
    Uses abort-early budget for non-strings so that formatting is stopped as soon
    as the budget is exhausted — avoiding building a huge intermediate string.

    When the cap fires, a prose notice is prepended so the consumer knows the
    value was large and how much was kept.

    String fast-path: plain strings skip pformat entirely — the cap is applied
    directly.  When truncated, both the head and tail are retained (head+tail
    format preserves context from both ends of the string).

    Raises:
        ValueError: if ``max_chars`` is <= 0.
    """
    if max_chars <= 0:
        raise ValueError(f"safe_pformat max_chars must be > 0, got {max_chars}")

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
            f"Output too large ({len(obj):,} chars). "
            f"Showing first {head_chars:,} and last {tail_chars:,} chars.\n\n"
            f"{head}\n\n"
            f"... {dropped:,} chars not shown ...\n\n"
            f"{tail}"
        )

    # Non-strings: use pformat with abort-early budget.
    # Strip reserved pformat-internal kwargs to avoid TypeError on duplicate keyword args.
    kwargs.pop("max_total_chars", None)
    kwargs.pop("_truncated_out", None)
    truncated_out: list[bool] = [False]
    # Default max_string to max_chars so strings inside objects aren't silently
    # truncated by pformat's internal 150-char default.  The abort-early budget
    # (max_total_chars) handles overall size bounding — max_string just prevents
    # individual string fields from being clipped before the budget kicks in.
    kwargs.setdefault("max_string", max_chars)
    text = pformat(obj, max_total_chars=max_chars, _truncated_out=truncated_out, **kwargs)

    # Hard post-cap: even when max_string lets individual fields through, the
    # total output must never exceed max_chars.  This covers multi-field objects
    # where each field is under max_string but their sum exceeds the budget.
    if len(text) <= max_chars:
        return text

    n_head = max_chars // 2
    n_tail = max_chars - n_head
    dropped = len(text) - n_head - n_tail
    return (
        f"Output too large ({len(text):,} chars). "
        f"Showing first {n_head:,} and last {n_tail:,} chars.\n\n"
        f"{text[:n_head]}\n\n"
        f"... {dropped:,} chars not shown ...\n\n"
        f"{text[-n_tail:]}"
    )


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
    instance_mode: Annotated[str, "Instance format: 'repr' for repr-style, 'type' for type structure"] = "repr",
    max_total_chars: int | None = None,
    _truncated_out: list[bool] | None = None,
) -> str:
    """Format an object as a string with smart truncation.

    Drop-in replacement for ``rich.pretty.pformat()``.
    For user-defined instances, ``hidden`` fields are automatically excluded.
    Respects ``@spec(expand=False)`` on field types — shown as ``ClassName()`` rather than expanded.

    ``console`` and ``indent_guides`` are accepted for Rich API compatibility but have no effect.

    ``max_total_chars``: when set, formatting is aborted early once the budget is
    exhausted, stopping iteration in containers.  ``_truncated_out`` is a mutable
    ``[bool]`` list; after the call its first element is set to ``True`` when the
    budget was exhausted.
    """
    # console and indent_guides are intentionally ignored for Rich compatibility
    del console, indent_guides

    budget: list[int] | None = None
    if max_total_chars is not None:
        budget = [max_total_chars]

    # Only the `case _:` (regular value) path supports budget threading.
    # For other paths (TypeInfo, type, module, function, structured instance)
    # we fall back to the normal _pformat call.
    result = _pformat(
        obj,
        max_length=max_length,
        max_string=max_string,
        max_depth=max_depth,
        expand_all=expand_all,
        concise=concise,
        instance_mode=instance_mode,
        _budget=budget,
    )

    if _truncated_out is not None and budget is not None:
        _truncated_out[0] = budget[0] <= 0

    return result


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
    instance_mode: Annotated[str, "Instance format: 'repr' for repr-style, 'type' for type structure"] = "repr",
) -> None:
    """Pretty-print an object with smart truncation. Prints to stdout.

    Drop-in replacement for ``rich.pretty.pprint()``.
    ``console`` and ``indent_guides`` are accepted for Rich API compatibility but have no effect.
    """
    print(
        pformat(
            obj,
            console=console,
            indent_guides=indent_guides,
            max_length=max_length,
            max_string=max_string,
            max_depth=max_depth,
            expand_all=expand_all,
            concise=concise,
            instance_mode=instance_mode,
        )
    )


__all__ = [
    "spec",
    "hidden",
    "doc",
    "DocConfig",
    "pformat",
    "pprint",
    "safe_pformat",
]
