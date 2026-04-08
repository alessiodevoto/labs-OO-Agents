"""Annotation markers for snapshot serialization.

Usage::

    from nemo_oo_agents.storage.markers import nosnapshot

    class MyAgent(Agent, llm=llm):
        db_conn: Annotated[Connection, nosnapshot]  # skipped during snapshot
        cache: Annotated[dict, nosnapshot]           # skipped during snapshot
        memory: list[str]                            # snapshotted normally
"""

from __future__ import annotations

import sys
import typing
from typing import Annotated, Any


class _NoSnapshot:
    """Annotation marker for fields that should be excluded from snapshots.

    Use as ``Annotated[T, nosnapshot]`` on class fields. The snapshot
    extraction logic checks for this marker and skips annotated fields.

    Unlike ``hidden`` (which controls LLM visibility), ``nosnapshot``
    controls persistence — a field can be visible to the LLM but excluded
    from snapshots, or hidden from the LLM but included in snapshots.
    """

    def __repr__(self) -> str:
        return "nosnapshot"


nosnapshot = _NoSnapshot()


def is_nosnapshot_field(cls: type, name: str) -> bool:
    """Check if a field is annotated with Annotated[T, nosnapshot].

    Walks the MRO so subclasses can override parent annotations.
    """
    for klass in cls.__mro__:
        if name not in klass.__dict__.get("__annotations__", {}):
            continue
        try:
            resolved = typing.get_type_hints(klass, include_extras=True)
        except Exception:
            resolved = None

        hint = resolved.get(name) if resolved is not None else _resolve_single(klass, name)

        if hint is None:
            return False
        origin = typing.get_origin(hint)
        if origin is Annotated:
            args = typing.get_args(hint)
            return any(isinstance(arg, _NoSnapshot) for arg in args[1:])
        return False
    return False


def _resolve_single(klass: type, name: str) -> Any:
    """Resolve a single string annotation when full get_type_hints fails."""
    raw = klass.__dict__.get("__annotations__", {}).get(name)
    if raw is None or not isinstance(raw, str):
        return raw
    mod = sys.modules.get(klass.__module__)
    ns = vars(mod) if mod else {}
    try:
        return eval(raw, ns)  # noqa: S307
    except Exception:
        return None
