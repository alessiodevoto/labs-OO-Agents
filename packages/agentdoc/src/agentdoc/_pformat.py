"""Shared pprint formatting for nemo_oo_agents and agentdoc.

This module defines the canonical ``_pformat`` implementation used by:

- ``packages/agentdoc/src/agentdoc/format.py`` for value summaries
- ``src/nemo_oo_agents/runtime/pprint.py`` for the public ``pprint`` API

The unified pformat() function handles:
- Regular values (list, dict, str, etc.) with truncation
- Types (classes) with Python class syntax
- Functions/methods with signature and docstring
- Modules with docstring and public functions
- Info objects (TypeInfo, CallableInfo, ModuleInfo) directly
"""

import importlib
import inspect
import re
from typing import Any

from agentdoc._info import REQUIRED, CallableInfo, ModuleInfo, TypeInfo
from agentdoc._metadata import is_expand_false
from agentdoc._structured import _ClassRef, _InstanceRef
from agentdoc.protocols import SupportsInstanceValues


def _truncate_docstring_lines(docstring: str | None, max_lines: int = 1) -> str:
    """Truncate a docstring to a specified number of lines."""
    if not docstring:
        return ""
    lines = docstring.strip().split("\n")
    if len(lines) <= max_lines:
        return docstring.strip()
    return "\n".join(lines[:max_lines]).strip()


def _resolve_field_type_class(field_default: Any, context_obj: type | None) -> type | None:
    """Resolve a field's default marker to the actual type class, or None."""
    if field_default is REQUIRED or field_default is ...:
        return None

    if isinstance(field_default, (_InstanceRef, _ClassRef)):
        class_name = getattr(field_default, "class_name", None) or getattr(field_default, "name", None)
        if class_name and context_obj is not None:
            _mod_name = getattr(context_obj, "__module__", None)
            try:
                _mod_ns = vars(importlib.import_module(_mod_name)) if _mod_name else None
            except ImportError:
                _mod_ns = None
            for ns in (vars(context_obj), _mod_ns):
                if ns and class_name in ns and isinstance(ns[class_name], type):
                    return ns[class_name]
        return None
    elif isinstance(field_default, type) and field_default.__module__ != "builtins":
        return field_default
    elif not isinstance(field_default, (str, int, float, bool, bytes, type(None))):
        cls = type(field_default)
        return None if cls.__module__ == "builtins" else cls
    return None


def _resolve_field_type_by_name(type_name: str, context_obj: type | None) -> type | None:
    """Resolve a type name string to a class via context_obj's module namespace."""
    if not type_name or context_obj is None:
        return None
    _mod_name = getattr(context_obj, "__module__", None)
    try:
        _mod_ns = vars(importlib.import_module(_mod_name)) if _mod_name else {}
    except (ImportError, Exception):
        _mod_ns = {}
    for ns in (vars(context_obj), _mod_ns):
        candidate = ns.get(type_name)
        if isinstance(candidate, type):
            return candidate
    return None


def _collect_referenced_types_transitive(
    seed_types: set[type],
    exclude: type | None = None,
) -> list[type]:
    """Collect all referenced types transitively via BFS, deduplicated and sorted.

    Args:
        seed_types: Initial set of types to start from (already filtered).
        exclude: Primary type to exclude from results (avoid self-reference).

    Returns:
        Sorted list of all reachable custom types (no duplicates).
    """
    from agentdoc._discover import discover_referenced_types

    all_types: set[type] = set()
    frontier = set(seed_types)
    while frontier:
        all_types.update(frontier)
        next_frontier: set[type] = set()
        for t in frontier:
            for new_t in discover_referenced_types(t):
                if new_t not in all_types and not is_expand_false(new_t) and new_t is not exclude:
                    next_frontier.add(new_t)
        frontier = next_frontier
    return sorted(all_types, key=lambda t: t.__name__)


def _field_type_docstring(field_default: Any, context_obj: type | None, type_name: str | None = None) -> str | None:
    """Return the first line of the docstring of a field's type."""
    cls = _resolve_field_type_class(field_default, context_obj)
    if cls is None and type_name:
        cls = _resolve_field_type_by_name(type_name, context_obj)
    if cls is None:
        return None
    # Use cls.__doc__ directly — inspect.getdoc() walks MRO and can inherit
    # irrelevant parent docstrings (e.g. BaseModel's MkDocs admonition markup).
    raw_doc = cls.__doc__
    if not raw_doc:
        return None
    docstring = inspect.cleandoc(raw_doc)
    return docstring.split("\n")[0].strip()


def _pformat(
    _object: Any,
    *,
    max_length: int | None = None,
    max_string: int | None = None,
    max_depth: int | None = None,
    concise: bool = False,
    inline_depth: int | None = None,
    expand_all: bool = False,
    instance_mode: str = "repr",
    _depth: int = 0,
    _indent: int = 0,
    _budget: list[int] | None = None,
) -> str:
    """Return pretty-formatted string with smart truncation.

    Args:
        _object: Object to format.
        max_length: Max elements per container (None=unlimited).
        max_string: Max string chars (None=unlimited).
        max_depth: Max nesting depth (None=unlimited).
        concise: If True, show first-line docstrings only.
        inline_depth: How deep to expand referenced types inline.
            - 0: No referenced types shown
            - 1: Direct references only
            - 2+: Transitive references
            - None: Auto (0 if concise=True, 1 if concise=False)
        expand_all: Always expand containers.
        instance_mode: How to format instances - "repr" for repr-style, "type" for type structure.
        _depth: Current nesting depth (internal).
        _indent: Current indentation level (internal).
        _budget: Optional mutable [remaining_chars] budget; stops early when depleted.
            Note: _budget is only threaded through plain value paths (_format_value,
            _format_dict, _format_sequence) — NOT through _format_type_info,
            _format_instance_repr, or _format_nested_instance. Those structured-type
            paths are already bounded by max_length (field count). The budget is
            specifically for unbounded plain containers (large lists/dicts of plain
            values).
            Budget accounting: each level deducts exactly the chars it contributes to
            the output (the full item string including key/separators/indentation).
            Recursive calls pass the budget down so inner containers can stop early
            too, but the parent resets the budget based on actual output length —
            avoiding double-counting across nesting levels.

    Returns:
        Formatted string representation.
    """
    # Resolve inline_depth default
    if inline_depth is None:
        inline_depth = 0 if concise else 1

    # Use match statement for clean dispatch on Info types and Python objects
    match _object:
        case TypeInfo():
            return _format_type_info(
                _object,
                concise=concise,
                type_depth=inline_depth,
                max_length=max_length,
                indent=_indent,
                context_obj=None,
            )
        case CallableInfo():
            return _format_callable_info(
                _object,
                concise=concise,
                type_depth=inline_depth,
                indent=_indent,
            )
        case ModuleInfo():
            return _format_module_info(_object, concise=concise, indent=_indent)
        case _ if _object.__class__.__name__ == "Environment" and hasattr(_object, "render"):
            return _object.render()
        case type():
            # Python type (class) - extract and format
            from agentdoc._structured import extract_type_info

            type_info = extract_type_info(_object)
            return _format_type_info(
                type_info,
                concise=concise,
                type_depth=inline_depth,
                max_length=max_length,
                indent=_indent,
                context_obj=_object,  # Pass the original type for discovery
            )
        case _ if inspect.ismodule(_object):
            from agentdoc.registry import get_module_info_extractor

            extractor = get_module_info_extractor(_object)
            if extractor is not None:
                module_info = extractor(_object)
            else:
                from agentdoc._structured import extract_module_info

                module_info = extract_module_info(_object)
            return _format_module_info(module_info, concise=concise, indent=_indent)
        case _ if inspect.isfunction(_object) or inspect.ismethod(_object):
            from agentdoc._structured import extract_callable_info

            callable_info = extract_callable_info(_object)
            return _format_callable_info(
                callable_info,
                concise=concise,
                type_depth=inline_depth,
                indent=_indent,
                context_obj=_object,  # Pass the function/method for type discovery
            )
        case _ if _is_structured_instance(_object):
            # Instance of a structured type
            if instance_mode == "type":
                # Show type structure with runtime values (for doc())
                from agentdoc._structured import extract_type_info
                from agentdoc._visibility import is_hidden_field
                from agentdoc.protocols import SupportsInstanceValues
                from agentdoc.registry import get_type_info_extractor

                obj_type = type(_object)
                # Check if instance has spec() overrides that could unhide class-hidden fields.
                # spec(self, "field", hidden=False) in __init__ → per-instance opt-in.
                _instance_fields_meta = vars(_object).get("_agentdoc_fields_docs") or {}
                _has_instance_overrides = any(meta.get("hidden") is False for meta in _instance_fields_meta.values())
                extractor = get_type_info_extractor(obj_type)
                if extractor:
                    result = extractor(_object)
                    if isinstance(result, tuple):
                        type_info, values = result
                    else:
                        type_info = result
                        values = _extract_instance_values(_object, result)
                    # Filter with instance (supports instance-level spec() overrides)
                    type_info = TypeInfo(
                        name=type_info.name,
                        base=type_info.base,
                        fields=[f for f in type_info.fields if not is_hidden_field(_object, f.name)],
                        methods=type_info.methods,
                        docstring=type_info.docstring,
                    )
                elif isinstance(_object, SupportsInstanceValues):
                    if _has_instance_overrides:
                        # Get all fields (including class-hidden) then re-filter by instance.
                        # Use _skip_protocol to get raw fields; take methods from protocol path.
                        raw_info = extract_type_info(obj_type, _skip_protocol=True, _include_hidden=True)
                        protocol_info = extract_type_info(obj_type)
                        type_info = TypeInfo(
                            name=protocol_info.name,
                            base=protocol_info.base,
                            fields=[f for f in raw_info.fields if not is_hidden_field(_object, f.name)],
                            methods=protocol_info.methods,
                            docstring=protocol_info.docstring,
                        )
                    else:
                        type_info = extract_type_info(obj_type)
                    values = _object.__instance_values__()
                else:
                    if _has_instance_overrides:
                        raw_info = extract_type_info(obj_type, _skip_protocol=True, _include_hidden=True)
                        protocol_info = extract_type_info(obj_type)
                        type_info = TypeInfo(
                            name=protocol_info.name,
                            base=protocol_info.base,
                            fields=[f for f in raw_info.fields if not is_hidden_field(_object, f.name)],
                            methods=protocol_info.methods,
                            docstring=protocol_info.docstring,
                        )
                    else:
                        type_info = extract_type_info(obj_type)
                    values = _extract_instance_values(_object, type_info)

                return _format_type_info(
                    type_info,
                    concise=concise,
                    type_depth=inline_depth,
                    max_length=max_length,
                    indent=_indent,
                    context_obj=obj_type,
                    instance_values=values,
                )
            else:
                # Show repr-style (for pprint())
                return _format_instance_repr(
                    _object,
                    max_length=max_length,
                    max_string=max_string,
                    max_depth=max_depth,
                    indent=_indent,
                )
        case _:
            # Regular value - use truncation formatting
            return _format_value(
                _object,
                max_length=max_length,
                max_string=max_string,
                max_depth=max_depth,
                expand_all=expand_all,
                depth=_depth,
                indent=_indent,
                _budget=_budget,
            )


def _is_structured_instance(obj: Any) -> bool:
    """Check if object is an instance that should be formatted with type info.

    Returns True for:
    - Pydantic models
    - dataclasses
    - NamedTuples
    - attrs classes
    - Any custom class instance with __dict__ (not built-in types)

    Returns False for:
    - Types (classes themselves)
    - Built-in types (str, int, list, dict, etc.)
    - None
    """
    if isinstance(obj, type):
        return False

    # Skip built-in types and None
    if obj is None:
        return False

    if isinstance(obj, (str, int, float, bool, bytes, bytearray)):
        return False

    from agentdoc._structured import _ClassRef, _InstanceRef

    if isinstance(obj, (_ClassRef, _InstanceRef)):
        return False

    obj_type = type(obj)

    # Check for NamedTuple BEFORE checking for regular tuples
    # NamedTuples have a _fields attribute
    if (
        hasattr(obj_type, "_fields")
        and isinstance(getattr(obj_type, "_fields", None), tuple)
        and isinstance(obj, tuple)
    ):
        return True

    # Now safe to exclude regular tuples, lists, sets, dicts
    if isinstance(obj, (list, tuple, set, frozenset, dict)):
        return False

    # Skip if it's a built-in type
    if obj_type.__module__ == "builtins":
        return False

    # Pydantic
    if hasattr(obj_type, "model_fields"):
        return True

    # dataclass
    import dataclasses

    if dataclasses.is_dataclass(obj_type):
        return True

    # attrs
    if hasattr(obj_type, "__attrs_attrs__"):
        return True

    # __slots__-only classes have no __dict__ but do have public slots
    if not hasattr(obj, "__dict__"):
        return any(
            slot
            for klass in obj_type.__mro__
            if klass is not object
            for slot in getattr(klass, "__slots__", ())
            if not slot.startswith("_")
        )

    # Any other custom class instance with __dict__
    return True


def _format_type_info(
    info: TypeInfo,
    *,
    concise: bool,
    type_depth: int = 0,
    max_length: int | None,
    indent: int,
    context_obj: type | None = None,
    instance_values: dict[str, Any] | None = None,
) -> str:
    """Format TypeInfo as Python class syntax.

    Shows:
    - Class name and base (if any)
    - Field names, types, defaults, descriptions
    - Extra instance attributes (if instance_values provided)
    - Method signatures and docstrings
    - Referenced Types section (only if context_obj provided and type_depth > 0)

    Args:
        info: TypeInfo to format
        concise: If True, show first-line docstrings only
        type_depth: How deep to recurse into referenced types (0 = none)
        max_length: Max fields/methods to show before truncation
        indent: Indentation level
        context_obj: Original type object for type discovery (None if formatting TypeInfo directly)
        instance_values: Runtime instance values to show instead of static defaults.
            When provided, field values come from this dict (current state)
            rather than from field.default (source code defaults).

    Returns:
        Python-style class definition string
    """
    lines = []
    ind = "    " * indent

    # Class header
    if info.base == "@dataclass":
        lines.append(f"{ind}@dataclass")
        lines.append(f"{ind}class {info.name}:")
    elif info.base == "@attrs":
        lines.append(f"{ind}@attrs")
        lines.append(f"{ind}class {info.name}:")
    elif info.base:
        lines.append(f"{ind}class {info.name}({info.base}):")
    else:
        lines.append(f"{ind}class {info.name}:")

    # Docstring
    if info.docstring:
        doc_text = _truncate_docstring_lines(info.docstring, max_lines=1 if concise else 9999)
        if "\n" in doc_text:
            lines.append(f'{ind}    """')
            for line in doc_text.split("\n"):
                lines.append(f"{ind}    {line}")
            lines.append(f'{ind}    """')
        else:
            lines.append(f'{ind}    """{doc_text}"""')
        lines.append("")

    # Handle Enum specially - show members as assignments
    if info.base == "Enum":
        fields = info.fields
        truncated = 0
        if max_length and len(fields) > max_length:
            truncated = len(fields) - max_length
            fields = fields[:max_length]

        for field in fields:
            value_str = _pformat(
                field.default,
                max_length=5,
                max_string=50,
                max_depth=1,
                inline_depth=0,
            )
            lines.append(f"{ind}    {field.name} = {value_str}")

        if truncated:
            lines.append(f"{ind}    ... +{truncated}")

        return "\n".join(lines)

    # Regular structured types - show fields as annotations
    fields = info.fields
    truncated_fields = 0
    if max_length and len(fields) > max_length:
        truncated_fields = len(fields) - max_length
        fields = fields[:max_length]

    # Show fields with instance values if available
    seen_field_names = {f.name for f in fields}
    fields_start_len = len(lines)
    for field in fields:
        # Skip fields marked repr=False (Pydantic field parameter — intentionally hidden)
        if not field.repr:
            continue
        if instance_values is not None and field.name not in instance_values:
            continue
        line = f"{ind}    {field.name}: {field.type}"
        if instance_values is not None and field.name in instance_values:
            default_str = _format_value(
                instance_values[field.name],
                max_length=3,
                max_string=50,
                max_depth=1,
                expand_all=False,
                depth=0,
                indent=0,
            )
            line += f" = {default_str}"
        elif instance_values is None and field.default is not ...:
            default_str = _format_value(
                field.default,
                max_length=3,
                max_string=50,
                max_depth=1,
                expand_all=False,
                depth=0,
                indent=0,
            )
            line += f" = {default_str}"
        if field.description:
            line += f"  # {field.description}"
        elif type_doc := _field_type_docstring(field.default, context_obj, type_name=field.type):
            line += f"  # {type_doc}"
        lines.append(line)

    if truncated_fields:
        lines.append(f"{ind}    ... +{truncated_fields}")

    # Add extra instance attributes not in type fields (like tools assigned at runtime)
    if instance_values:
        extra_attrs = []
        from agentdoc._visibility import is_hidden_field as _is_hidden_field

        for name, value in sorted(instance_values.items()):
            if name in seen_field_names:
                continue
            if name.startswith("_"):
                continue
            if context_obj is not None and _is_hidden_field(context_obj, name):
                continue
            # Skip callables unless they're classes
            if callable(value) and not isinstance(value, type):
                continue
            extra_attrs.append((name, value))

        if extra_attrs:
            if fields:
                lines.append("")
            # Truncate extra attrs if needed
            truncated_extra = 0
            if max_length and len(extra_attrs) > max_length:
                truncated_extra = len(extra_attrs) - max_length
                extra_attrs = extra_attrs[:max_length]

            for name, value in extra_attrs:
                type_name = type(value).__name__
                value_str = _format_value(
                    value,
                    max_length=3,
                    max_string=100,
                    max_depth=1,
                    expand_all=False,
                    depth=0,
                    indent=0,
                )
                lines.append(f"{ind}    {name}: {type_name} = {value_str}")

            if truncated_extra:
                lines.append(f"{ind}    ... +{truncated_extra} more instance attributes")

    # Methods
    if info.methods:
        if len(lines) > fields_start_len:
            lines.append("")

        methods = info.methods
        truncated_methods = 0
        if max_length and len(methods) > max_length:
            truncated_methods = len(methods) - max_length
            methods = methods[:max_length]

        for method in methods:
            method_lines = _format_callable_info(
                method,
                concise=concise,
                type_depth=0,  # Don't show nested references for methods within a class
                indent=indent + 1,
                as_method=True,
            )
            lines.append(method_lines)

        if truncated_methods:
            lines.append(f"{ind}    # ... +{truncated_methods} more methods")

    # Add Referenced Types section if we have context and type_depth > 0
    if context_obj is not None and type_depth > 0:
        from agentdoc._discover import _extract_types_from_hint, _is_custom_type, discover_referenced_types
        from agentdoc._structured import extract_type_info

        seed_set: set[type] = {t for t in discover_referenced_types(context_obj) if not is_expand_false(t)}

        # Also discover types from extra instance attributes
        if instance_values:
            extra_discovered: set[type] = set()

            for name, value in instance_values.items():
                if name.startswith("_"):
                    continue
                # Skip callables unless they're classes
                if callable(value) and not isinstance(value, type):
                    continue

                # Extract type from the value
                value_type = type(value)
                if isinstance(value, type):
                    # It's a class itself (like WorkerAgent = WorkerAgent)
                    value_type = value

                # Extract types from the value's type
                _extract_types_from_hint(value_type, extra_discovered)

            # Filter to only custom types and add to seed_set
            for extra_type in extra_discovered:
                if _is_custom_type(extra_type) and not is_expand_false(extra_type):
                    seed_set.add(extra_type)

        # Collect all referenced types transitively (BFS), render flat
        referenced_types = _collect_referenced_types_transitive(seed_set, exclude=context_obj)
        if referenced_types:
            lines.append(f"{ind}## Referenced Types")

            for ref_type in referenced_types:
                ref_info = extract_type_info(ref_type)
                ref_doc = _format_type_info(
                    ref_info,
                    concise=True,
                    type_depth=0,  # All types already collected — no nested ## sections
                    max_length=max_length,
                    indent=indent,
                    context_obj=ref_type,
                )
                lines.append(ref_doc)
                lines.append("")

    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip("\n")


def _format_callable_info(
    info: CallableInfo,
    *,
    concise: bool,
    type_depth: int = 0,
    indent: int,
    as_method: bool = False,
    context_obj: Any = None,
) -> str:
    """Format CallableInfo as Python function/method syntax.

    Args:
        info: CallableInfo to format
        concise: If True, show first-line docstrings only
        type_depth: How deep to recurse into referenced types (0 = none)
        indent: Indentation level
        as_method: If True, format as method (no standalone header)
        context_obj: Original callable object for type discovery (None if formatting CallableInfo directly)

    Returns:
        Python-style function definition string
    """
    lines = []
    ind = "    " * indent

    # Build signature line
    # Determine the display name based on context
    method_name = info.name

    if as_method and "." in method_name:
        # When shown within class, strip class name prefix
        # "ClassName.method_name" -> "method_name"
        method_name = method_name.split(".")[-1]
    elif ".<locals>." in method_name:
        # For nested functions (closures), strip the .<locals>. part
        # "main.<locals>.example_function" -> "example_function"
        method_name = method_name.split(".<locals>.")[-1]
    # Otherwise, keep the full qualified name (e.g., "ClassName.method", "top_level_function")

    if info.is_classmethod:
        lines.append(f"{ind}@classmethod")
    async_prefix = "async def " if info.is_async else "def "
    return_str = f" -> {info.return_type}" if info.return_type else ""
    sig_line = f"{ind}{async_prefix}{method_name}{info.signature}{return_str}:"
    lines.append(sig_line)

    # Docstring
    if info.docstring:
        doc_text = _truncate_docstring_lines(info.docstring, max_lines=1 if concise else 9999)
        if "\n" in doc_text and not concise:
            lines.append(f'{ind}    """')
            for line in doc_text.split("\n"):
                lines.append(f"{ind}    {line}")
            lines.append(f'{ind}    """')
        else:
            lines.append(f'{ind}    """{doc_text}"""')
    else:
        lines.append(f"{ind}    ...")

    # Add Referenced Types section if we have context and type_depth > 0
    # (not when formatting as a method within a class)
    if context_obj is not None and type_depth > 0 and not as_method:
        from agentdoc._discover import discover_referenced_types
        from agentdoc._structured import extract_type_info

        seed_set = {t for t in discover_referenced_types(context_obj) if not is_expand_false(t)}
        referenced_types = _collect_referenced_types_transitive(seed_set, exclude=context_obj)

        if referenced_types:
            lines.append("")
            lines.append(f"{ind}## Referenced Types")

            for ref_type in referenced_types:
                ref_info = extract_type_info(ref_type)
                ref_doc = _format_type_info(
                    ref_info,
                    concise=True,
                    type_depth=0,  # All types already collected — no nested ## sections
                    max_length=None,
                    indent=indent,
                    context_obj=ref_type,
                )
                lines.append(ref_doc)
                lines.append("")

    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip("\n")


def _format_module_info(info: ModuleInfo, *, concise: bool, indent: int) -> str:
    """Format ModuleInfo as Python module syntax.

    Args:
        info: ModuleInfo to format
        concise: If True, show first-line docstrings only
        indent: Indentation level

    Returns:
        Python-style module documentation string
    """
    lines = []
    ind = "    " * indent

    # Module header
    lines.append(f"{ind}# {info.name}")
    lines.append("")

    # Module docstring
    if info.docstring:
        doc_text = _truncate_docstring_lines(info.docstring, max_lines=1 if concise else 9999)
        lines.append(f'{ind}"""')
        for line in doc_text.split("\n"):
            lines.append(f"{ind}{line}")
        lines.append(f'{ind}"""')
        lines.append("")

    # Submodules section
    if info.submodules:
        lines.append(f"{ind}# Submodules:")
        for sub_name, sub_doc in info.submodules:
            if sub_doc:
                lines.append(f"{ind}#   {sub_name} — {sub_doc}")
            else:
                lines.append(f"{ind}#   {sub_name}")
        lines.append("")

    # Build lookup dicts so we can render in __all__ declaration order
    cls_map = {name: cls_doc for name, cls_doc in info.classes}
    func_map = {func.name: func for func in info.functions}
    val_map = {name: val_repr for name, val_repr in info.values}

    # Use ordered_names to interleave classes, callables, and values in
    # their __all__ declaration order rather than grouping by type.
    render_names = (
        info.ordered_names
        if info.ordered_names
        else ([n for n, _ in info.classes] + [f.name for f in info.functions] + [n for n, _ in info.values])
    )

    # Track whether we're in a value-run (dense, no blank lines between values)
    in_value_run = False
    for sym_name in render_names:
        if sym_name in cls_map:
            if in_value_run:
                lines.append("")
                in_value_run = False
            cls_doc = cls_map[sym_name]
            if cls_doc:
                lines.append(f"{ind}class {sym_name}:  # {cls_doc}")
            else:
                lines.append(f"{ind}class {sym_name}")
            lines.append("")
        elif sym_name in func_map:
            if in_value_run:
                lines.append("")
                in_value_run = False
            func = func_map[sym_name]
            func_lines = _format_callable_info(func, concise=concise, indent=indent)
            lines.append(func_lines)
            lines.append("")
        elif sym_name in val_map:
            lines.append(f"{ind}{sym_name} = {val_map[sym_name]}")
            in_value_run = True
        else:
            if in_value_run:
                lines.append("")
                in_value_run = False
            lines.append(f"{ind}# {sym_name}: (not accessible)")
            lines.append("")

    return "\n".join(lines).rstrip()


def _format_instance_repr(
    obj: Any,
    *,
    max_length: int | None,
    max_string: int | None,
    max_depth: int | None,
    indent: int,
) -> str:
    """Format an instance in repr-style.

    Shows: ClassName(field1=value1, field2=value2, ...)

    Args:
        obj: Instance to format
        max_length: Max fields to show
        max_string: Max string chars
        max_depth: Max nesting depth
        indent: Indentation level

    Returns:
        Repr-style string
    """
    from enum import Enum

    from agentdoc._structured import extract_type_info
    from agentdoc.registry import get_type_info_extractor

    obj_type = type(obj)
    type_name = obj_type.__name__

    # Special handling for Enums
    if isinstance(obj, Enum):
        return f"{type_name}.{obj.name}"

    # Special handling for NamedTuples - show constructor style
    if (
        hasattr(obj_type, "_fields")
        and isinstance(getattr(obj_type, "_fields", None), tuple)
        and isinstance(obj, tuple)
    ):
        fields = obj_type._fields
        values = []
        for i, (field_name, value) in enumerate(zip(fields, obj, strict=True)):
            if max_length and i >= max_length:
                values.append("...")
                break
            value_str = _format_value(
                value,
                max_length=5,
                max_string=max_string or 150,
                max_depth=(max_depth - 1) if max_depth else None,
                expand_all=False,
                depth=0,
                indent=0,
            )
            values.append(f"{field_name}={value_str}")
        return f"{type_name}({', '.join(values)})"

    # Get field values
    extractor = get_type_info_extractor(obj)
    if extractor:
        result = extractor(obj)
        if isinstance(result, tuple):
            type_info, values = result
        else:
            type_info = result
            values = _extract_instance_values(obj, type_info)
        # Registry extractors may return unfiltered fields; apply hidden rules.
        # Local import avoids a circular dependency: _pformat ← _visibility ← _metadata ← _pformat.
        from agentdoc._visibility import is_hidden_field  # noqa: PLC0415

        type_info = TypeInfo(
            name=type_info.name,
            base=type_info.base,
            fields=[f for f in type_info.fields if not is_hidden_field(obj_type, f.name)],
            methods=type_info.methods,
            docstring=type_info.docstring,
        )
    else:
        type_info = extract_type_info(obj_type)
        if isinstance(obj, SupportsInstanceValues):
            values = obj.__instance_values__()
        else:
            values = _extract_instance_values(obj, type_info)

    # Determine if __instance_values__ was used (gives full control over what's shown)
    uses_instance_values_protocol = isinstance(obj, SupportsInstanceValues)

    # Build repr-style output
    parts = []
    field_count = 0
    eligible_fields = [f for f in type_info.fields if f.repr]
    for field in eligible_fields:
        if max_length and field_count >= max_length:
            omitted = len(eligible_fields) - field_count
            parts.append(f"... +{omitted}")
            break

        # If __instance_values__ is implemented, only show fields it returned
        # (the protocol allows hiding fields by omitting them)
        if uses_instance_values_protocol and field.name not in values:
            continue

        # Get value from values dict, or use getattr for non-protocol extraction.
        # Wrap in try/except: properties may raise non-AttributeError exceptions.
        if field.name in values:
            value = values[field.name]
        else:
            try:
                value = getattr(obj, field.name)
            except Exception:
                # Attribute not set or property raises — skip this field
                continue
        # Skip callable instance attributes (e.g. assigned lambdas/functions);
        # only keep types (classes), since those are intentional class-level tools.
        if callable(value) and not isinstance(value, type):
            continue
        value_str = _format_value(
            value,
            max_length=5,
            max_string=max_string or 150,
            max_depth=(max_depth - 1) if max_depth else None,
            expand_all=False,
            depth=0,
            indent=0,
        )
        parts.append(f"{field.name}={value_str}")
        field_count += 1

    # Add non-field attributes from __dict__ (skip hidden so they are not re-added)
    if hasattr(obj, "__dict__"):
        from agentdoc._visibility import is_hidden_field as _is_hidden_field

        for name, value in sorted(values.items()):
            if any(f.name == name for f in type_info.fields):
                continue
            if name.startswith("_"):
                continue
            if _is_hidden_field(obj_type, name):
                continue
            if max_length and field_count >= max_length:
                break

            value_str = _format_value(
                value,
                max_length=5,
                max_string=max_string or 150,
                max_depth=(max_depth - 1) if max_depth else None,
                expand_all=False,
                depth=0,
                indent=0,
            )
            parts.append(f"{name}={value_str}")
            field_count += 1

    return f"{type_name}({', '.join(parts)})"


def _extract_instance_values(obj: Any, type_info: TypeInfo) -> dict[str, Any]:
    """Extract current field values from an instance.

    Args:
        obj: Instance to extract values from
        type_info: TypeInfo describing the type

    Returns:
        Dictionary mapping field names to current values
    """
    values = {}

    # First, get values for type fields
    for field in type_info.fields:
        try:
            if hasattr(obj, field.name):
                values[field.name] = getattr(obj, field.name)
            elif isinstance(obj, dict) and field.name in obj:
                # TypedDict instances are dicts
                values[field.name] = obj[field.name]
        except Exception:
            # Skip fields whose properties raise (ValueError, RuntimeError, etc.)
            pass

    # Also include all __dict__ attributes (for instances without annotations)
    if hasattr(obj, "__dict__"):
        from agentdoc._visibility import is_hidden_field as _is_hidden_field

        obj_type = type(obj)
        for name, value in obj.__dict__.items():
            if (
                name not in values
                and not name.startswith("_")
                and not callable(value)
                and not _is_hidden_field(obj_type, name)
            ):
                values[name] = value

    return values


def _format_nested_instance(
    obj: Any,
    *,
    max_length: int | None,
    max_string: int | None,
    max_depth: int | None,
    depth: int,
    indent: int,
) -> str:
    """Format a structured instance in compact one-line form for nested display.

    Used when a structured instance (dataclass, Pydantic, etc.) appears inside
    a container (list, dict). Shows: ClassName(field1=val1, field2=val2, ... +N)

    Args:
        obj: Structured instance to format
        max_length: Max fields to show (default 3 for compact display)
        max_string: Max string chars per field value
        max_depth: Max nesting depth
        depth: Current nesting depth
        indent: Indentation level (unused for compact format)

    Returns:
        Compact one-line representation
    """
    from agentdoc._structured import extract_type_info
    from agentdoc.registry import get_type_info_extractor

    obj_type = type(obj)
    type_name = obj_type.__name__

    # Use tighter defaults for nested display
    nested_max_length = max_length if max_length is not None else 3
    nested_max_string = max_string if max_string is not None else 150

    # Get type info and values
    extractor = get_type_info_extractor(obj)
    if extractor:
        result = extractor(obj)
        if isinstance(result, tuple):
            type_info, values = result
        else:
            type_info = result
            values = _extract_instance_values(obj, type_info)
    else:
        type_info = extract_type_info(obj_type)
        if isinstance(obj, SupportsInstanceValues):
            values = obj.__instance_values__()
        else:
            values = _extract_instance_values(obj, type_info)

    # Collect field names in order (type fields first, then extra __dict__ attrs)
    # Only include fields that exist in values — fields absent from values are skipped
    # in the render loop below, so excluding them keeps truncated_count accurate.
    field_names = [f.name for f in type_info.fields if f.name in values]
    for name in values:
        if name not in field_names and not name.startswith("_"):
            field_names.append(name)

    # Truncate fields if needed
    truncated_count = 0
    if len(field_names) > nested_max_length:
        truncated_count = len(field_names) - nested_max_length
        field_names = field_names[:nested_max_length]

    # Format each field=value pair
    parts = []
    for name in field_names:
        if name in values:
            value = values[name]
            # Recursively format the value with tighter limits
            value_str = _format_value(
                value,
                max_length=2,
                max_string=nested_max_string,
                max_depth=(max_depth - 1) if max_depth else 1,
                expand_all=False,
                depth=depth + 1,
                indent=0,
            )
            parts.append(f"{name}={value_str}")

    result = f"{type_name}({', '.join(parts)}"
    if truncated_count:
        result += f", ... +{truncated_count}"
    result += ")"

    return result


# ============================================================================
# Value formatting (for regular Python values)
# ============================================================================


def _format_value(
    _object: Any,
    *,
    max_length: int | None,
    max_string: int | None,
    max_depth: int | None,
    expand_all: bool,
    depth: int,
    indent: int,
    _budget: list[int] | None = None,
) -> str:
    """Format a regular Python value with truncation.

    Args:
        _object: Value to format
        max_length: Max elements per container
        max_string: Max string chars
        max_depth: Max nesting depth
        expand_all: Always expand containers
        depth: Current nesting depth
        indent: Current indentation level
        _budget: Optional mutable [remaining_chars] budget; stops early when depleted.

    Returns:
        Formatted string representation
    """
    # Check depth limit
    if max_depth is not None and depth >= max_depth:
        return _format_shallow(_object, max_string)

    # Handle by type
    if isinstance(_object, str):
        return _format_string(_object, max_string)

    if isinstance(_object, dict):
        return _format_dict(
            _object,
            max_length=max_length,
            max_string=max_string,
            max_depth=max_depth,
            expand_all=expand_all,
            depth=depth,
            indent=indent,
            _budget=_budget,
        )

    if isinstance(_object, (list, tuple, set, frozenset)):
        return _format_sequence(
            _object,
            max_length=max_length,
            max_string=max_string,
            max_depth=max_depth,
            expand_all=expand_all,
            depth=depth,
            indent=indent,
            _budget=_budget,
        )

    # Handle marker classes from _structured.py (they have clean __repr__)
    from agentdoc._structured import _ClassRef, _InstanceRef

    if isinstance(_object, (_ClassRef, _InstanceRef)):
        return repr(_object)

    # Handle class objects (like child agent classes) - show just the name
    if isinstance(_object, type):
        return _object.__name__

    # Structured instances (dataclass, Pydantic, etc.) - format recursively
    if _is_structured_instance(_object):
        return _format_nested_instance(
            _object,
            max_length=max_length,
            max_string=max_string,
            max_depth=max_depth,
            depth=depth,
            indent=indent,
        )

    # Fallback to repr for other types
    try:
        result = repr(_object)
    except Exception:
        return f"<{type(_object).__name__}>"
    if max_string and len(result) > max_string:
        return result[:max_string] + f"... +{len(result) - max_string}"
    return result


def _format_string(s: str, max_string: int | None) -> str:
    """Format a string, potentially truncating.

    Automatically uses triple-quoted multiline format for complex strings:
    - Contains newlines
    - Contains both single and double quotes (would require escaping)
    """
    # Apply truncation first if needed
    truncated = False
    remaining = 0
    if max_string is not None and len(s) > max_string:
        remaining = len(s) - max_string
        s = s[:max_string]
        truncated = True

    # Detect if string is "complex" and needs triple-quote multiline format
    has_newlines = "\n" in s
    has_single_quotes = "'" in s
    has_double_quotes = '"' in s
    needs_multiline = has_newlines or (has_single_quotes and has_double_quotes)

    result = _format_multiline_string(s) if needs_multiline else repr(s)

    if truncated:
        result = f"{result}+{remaining}"

    return result


def _format_multiline_string(s: str) -> str:
    """Format a string using triple quotes for better readability.

    Chooses quote style to avoid escaping:
    - Use ''' if string doesn't contain '''
    - Use \"\"\" if string doesn't contain \"\"\"
    - Fall back to repr() if string contains both
    """
    has_triple_single = "'''" in s
    has_triple_double = '"""' in s

    if has_triple_single and has_triple_double:
        # Both triple quote styles present - fall back to repr
        return repr(s)

    # Choose quote style that doesn't need escaping
    quote = '"""' if has_triple_single else "'''"

    # Format with actual newlines preserved
    return f"{quote}{s}{quote}"


def _format_shallow(_object: Any, max_string: int | None) -> str:
    """Format object shallowly (at max depth)."""
    type_name = type(_object).__name__

    if isinstance(_object, dict):
        if not _object:  # Empty dict - just show {}
            return "{}"
        return f"{{{type_name}: {len(_object)} items}}"
    if isinstance(_object, (list, tuple, set, frozenset)):
        brackets = _get_brackets(type(_object))
        if not _object:  # Empty container - just show [], (), etc.
            return brackets[0] + brackets[1]
        return f"{brackets[0]}{type_name}: {len(_object)} items{brackets[1]}"
    if isinstance(_object, str):
        return _format_string(_object, max_string)

    try:
        return repr(_object)
    except Exception:
        return f"<{type(_object).__name__}>"


def _format_dict(
    d: dict,
    *,
    max_length: int | None,
    max_string: int | None,
    max_depth: int | None,
    expand_all: bool,
    depth: int,
    indent: int,
    _budget: list[int] | None = None,
) -> str:
    """Format a dictionary."""
    if not d:
        return "{}"

    items = list(d.items())
    truncated_count = 0
    if max_length is not None and len(items) > max_length:
        truncated_count = len(items) - max_length
        items = items[:max_length]

    # Compact format when not expand_all (Rich pprint style)
    if not expand_all:
        # Snapshot budget and truncated_count before the compact trial.  If the
        # result is too long (>= 120 chars) we discard it and fall through to the
        # expanded path; the expanded path re-formats every item from scratch, so it
        # must start with the original budget — not one depleted by the discarded trial.
        budget_snapshot = _budget[0] if _budget is not None else 0
        truncated_snapshot = truncated_count
        parts = []
        remaining_items = len(items)
        for k, v in items:
            remaining_items -= 1
            k_str = _format_string(str(k), 50) if isinstance(k, str) else repr(k)
            before = _budget[0] if _budget is not None else 0
            v_str = _format_value(
                v,
                max_length=max_length,
                max_string=max_string,
                max_depth=max_depth,
                expand_all=expand_all,
                depth=depth + 1,
                indent=0,
                _budget=_budget,
            )
            item_str = f"{k_str}: {v_str}"
            parts.append(item_str)
            if _budget is not None:
                # Set budget to (before - actual output chars), not -= len(item_str).
                # The recursive _format_value call may have already deducted chars for
                # nested containers; using the delta avoids double-counting.
                _budget[0] = before - len(item_str)
                if _budget[0] <= 0:
                    total_remaining = truncated_count + remaining_items
                    parts.append(f"... +{total_remaining} more")
                    truncated_count = 0
                    break
        if truncated_count > 0:
            parts.append(f"... +{truncated_count}")
        result = "{" + ", ".join(parts) + "}"
        if len(result) < 120:  # Allow longer single lines like Rich
            return result
        # Discard compact trial; restore state for expanded format.
        if _budget is not None:
            _budget[0] = budget_snapshot
        truncated_count = truncated_snapshot

    # Expanded format (when expand_all=True or line too long)
    lines = ["{"]
    inner_indent = "    " * (indent + 1)
    remaining_items = len(items)
    for k, v in items:
        remaining_items -= 1
        k_str = _format_string(str(k), 50) if isinstance(k, str) else repr(k)
        before = _budget[0] if _budget is not None else 0
        v_str = _format_value(
            v,
            max_length=max_length,
            max_string=max_string,
            max_depth=max_depth,
            expand_all=expand_all,
            depth=depth + 1,
            indent=indent + 1,
            _budget=_budget,
        )
        item_str = f"{inner_indent}{k_str}: {v_str},"
        lines.append(item_str)
        if _budget is not None:
            _budget[0] = before - len(item_str)
            if _budget[0] <= 0:
                total_remaining = truncated_count + remaining_items
                lines.append(f"{inner_indent}... +{total_remaining} more")
                truncated_count = 0
                break

    if truncated_count > 0:
        lines.append(f"{inner_indent}... +{truncated_count}")

    lines.append("    " * indent + "}")
    return "\n".join(lines)


def _format_sequence(
    seq,
    *,
    max_length: int | None,
    max_string: int | None,
    max_depth: int | None,
    expand_all: bool,
    depth: int,
    indent: int,
    _budget: list[int] | None = None,
) -> str:
    """Format a sequence (list, tuple, set, frozenset)."""
    brackets = _get_brackets(type(seq))

    if not seq:
        return brackets[0] + brackets[1]

    items = list(seq)
    truncated_count = 0
    if max_length is not None and len(items) > max_length:
        truncated_count = len(items) - max_length
        items = items[:max_length]

    # Compact format when not expand_all (Rich pprint style)
    if not expand_all:
        # Snapshot state before the compact trial; restore if we fall through to expanded.
        budget_snapshot = _budget[0] if _budget is not None else 0
        truncated_snapshot = truncated_count
        parts = []
        remaining_items = len(items)
        for x in items:
            remaining_items -= 1
            before = _budget[0] if _budget is not None else 0
            item_str = _format_value(
                x,
                max_length=max_length,
                max_string=max_string,
                max_depth=max_depth,
                expand_all=expand_all,
                depth=depth + 1,
                indent=0,
                _budget=_budget,
            )
            parts.append(item_str)
            if _budget is not None:
                _budget[0] = before - len(item_str)
                if _budget[0] <= 0:
                    total_remaining = truncated_count + remaining_items
                    parts.append(f"... +{total_remaining} more")
                    truncated_count = 0
                    break
        if truncated_count > 0:
            parts.append(f"... +{truncated_count}")
        result = brackets[0] + ", ".join(parts) + brackets[1]
        if len(result) < 120:  # Allow longer single lines like Rich
            return result
        # Discard compact trial; restore state for expanded format.
        if _budget is not None:
            _budget[0] = budget_snapshot
        truncated_count = truncated_snapshot

    # Expanded format (when expand_all=True or line too long)
    lines = [brackets[0]]
    inner_indent = "    " * (indent + 1)
    remaining_items = len(items)
    for item in items:
        remaining_items -= 1
        before = _budget[0] if _budget is not None else 0
        item_str = _format_value(
            item,
            max_length=max_length,
            max_string=max_string,
            max_depth=max_depth,
            expand_all=expand_all,
            depth=depth + 1,
            indent=indent + 1,
            _budget=_budget,
        )
        line = f"{inner_indent}{item_str},"
        lines.append(line)
        if _budget is not None:
            _budget[0] = before - len(line)
            if _budget[0] <= 0:
                total_remaining = truncated_count + remaining_items
                lines.append(f"{inner_indent}... +{total_remaining} more")
                truncated_count = 0
                break

    if truncated_count > 0:
        lines.append(f"{inner_indent}... +{truncated_count}")

    lines.append("    " * indent + brackets[1])
    return "\n".join(lines)


def _get_brackets(seq_type: type) -> tuple[str, str]:
    """Get opening and closing brackets for sequence type."""
    if seq_type is list:
        return "[", "]"
    if seq_type is tuple:
        return "(", ")"
    if seq_type is set:
        return "{", "}"
    if seq_type is frozenset:
        return "frozenset({", "})"
    return "[", "]"
