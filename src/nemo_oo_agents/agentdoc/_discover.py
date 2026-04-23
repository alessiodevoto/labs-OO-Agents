# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Discover referenced types from classes for documentation.

This module discovers custom types referenced in a class's interface
(method parameters, return types, field types) for inclusion in doc() output.
"""

import inspect
import typing
from typing import Any

# Builtins to exclude from referenced types
BUILTIN_TYPE_NAMES = {
    "str",
    "int",
    "float",
    "bool",
    "bytes",
    "bytearray",
    "list",
    "dict",
    "set",
    "tuple",
    "frozenset",
    "type",
    "object",
    "None",
    "NoneType",
    "Any",
}

# Stdlib modules to exclude
STDLIB_MODULES = {
    "datetime",
    "pathlib",
    "collections",
    "contextlib",
    "typing",
    "re",
    "json",
    "os",
    "sys",
    "io",
    "enum",
    "dataclasses",
    "abc",
    "functools",
    "itertools",
}


def discover_referenced_types(
    obj: type | Any,
    *,
    seen: set[type] | None = None,
) -> list[type]:
    """Discover all custom types referenced in a class or callable's interface.

    Searches:
    - For classes: all fields (class-level, __init__, non-annotated attrs), method signatures
    - For functions/methods: parameter and return types

    Uses the same field extraction as extract_type_info to ensure consistency.

    Filters out builtins, stdlib types, and typing constructs.

    Args:
        obj: The class, function, or method to scan for referenced types
        seen: Optional set of already-seen types to exclude from results.
            Used for deduplication when documenting multiple types together.
            Types in this set will not be included in the returned list.

    Returns:
        List of unique custom type objects not in `seen`, sorted by name
    """
    discovered: set[type] = set()

    # Handle callable (function/method) directly
    if inspect.isfunction(obj) or inspect.ismethod(obj):
        try:
            sig = inspect.signature(obj)

            # Extract from parameters
            for param in sig.parameters.values():
                if param.annotation is not inspect.Parameter.empty:
                    _extract_types_from_hint(param.annotation, discovered)

            # Extract from return type
            if sig.return_annotation is not inspect.Signature.empty:
                _extract_types_from_hint(sig.return_annotation, discovered)
        except (ValueError, TypeError):
            pass  # Can't introspect this callable

        # Filter to only custom types, excluding already-seen types
        custom_types = [
            t for t in discovered if _is_custom_type(t) and (seen is None or t not in seen)
        ]
        return sorted(custom_types, key=lambda t: t.__name__)

    # Handle class type
    if not isinstance(obj, type):
        return []  # Not a class or callable

    type_obj = obj

    # 1. Discover from ALL fields (class-level annotations, __init__, non-annotated attrs)
    # Use the same extraction that extract_type_info uses for consistency
    from nemo_oo_agents.agentdoc._structured import extract_type_info

    type_info = extract_type_info(type_obj)
    for field in type_info.fields:
        # Parse the type string back to extract types
        # For fields, we need to look at the original type hints where possible
        _extract_types_from_field(type_obj, field.name, field.type, discovered)

    # 2. Discover from method signatures
    for method_info in type_info.methods:
        # Find the actual method to get its signature
        method_name = method_info.name.split(".")[-1]  # Strip class prefix if present
        try:
            attr = getattr(type_obj, method_name)
        except AttributeError:
            continue

        if not (inspect.isfunction(attr) or inspect.ismethod(attr)):
            continue

        try:
            sig = inspect.signature(attr)

            # Extract from parameters
            for param in sig.parameters.values():
                if param.annotation is not inspect.Parameter.empty:
                    _extract_types_from_hint(param.annotation, discovered)

            # Extract from return type
            if sig.return_annotation is not inspect.Signature.empty:
                _extract_types_from_hint(sig.return_annotation, discovered)
        except (ValueError, TypeError):
            # Skip methods we can't introspect
            continue

    # Filter to only custom types, excluding already-seen types
    custom_types = [t for t in discovered if _is_custom_type(t) and (seen is None or t not in seen)]
    return sorted(custom_types, key=lambda t: t.__name__)


def _extract_types_from_field(
    cls: type, field_name: str, field_type_str: str, discovered: set[type]
) -> None:
    """Extract types from a field, looking at class attributes and annotations.

    For fields that are type objects (like child agent classes), we need to check
    the actual attribute value, not just the annotation.

    Args:
        cls: The class containing the field
        field_name: Name of the field
        field_type_str: Formatted type string (for reference)
        discovered: Set to add discovered types to (modified in place)
    """
    # 1. Check if field is a class-level annotation
    annotations = getattr(cls, "__annotations__", {})
    if field_name in annotations:
        _extract_types_from_hint(annotations[field_name], discovered)

    # 2. Check if the field value itself is a type (like child agent classes)
    # e.g., WorkerAgent = WorkerAgent where the value IS a class
    if field_name in cls.__dict__:
        value = cls.__dict__[field_name]
        if isinstance(value, type) and _is_custom_type(value):
            discovered.add(value)
        elif not isinstance(value, type) and not callable(value):
            # Instance attribute - check its type
            value_type = type(value)
            if _is_custom_type(value_type):
                discovered.add(value_type)

    # 3. Check __init__ for instance attribute annotations
    # This is handled by looking at the type_info fields which already include __init__ fields
    # We just need to evaluate the type string if it wasn't in class annotations
    if field_name not in annotations:
        _extract_types_from_type_string(cls, field_type_str, discovered)


def _extract_types_from_type_string(cls: type, type_str: str, discovered: set[type]) -> None:
    """Try to evaluate a type string and extract types from it.

    This handles cases like "list[WorkerAgent]" where the type came from __init__.

    Args:
        cls: The class for context (to get module globals)
        type_str: Type as a string (e.g., "list[WorkerAgent]")
        discovered: Set to add discovered types to
    """
    import inspect as inspect_module

    # Build evaluation context
    eval_context: dict[str, Any] = {}

    # Add typing module
    eval_context.update(vars(typing))

    # Add the class's module globals
    module = inspect_module.getmodule(cls)
    if module:
        eval_context.update(vars(module))

    # Try to evaluate and extract
    try:
        type_hint = eval(type_str, eval_context)
        _extract_types_from_hint(type_hint, discovered)
    except (NameError, AttributeError, TypeError, SyntaxError, ValueError):
        pass  # Can't evaluate - might be a complex expression or unavailable type


def _extract_types_from_hint(type_hint: Any, discovered: set[type]) -> None:
    """Extract all type objects from a type hint, recursively.

    Handles:
    - Simple types: MyClass
    - Generics: list[MyClass], dict[str, MyClass]
    - Unions: MyClass | OtherClass, Optional[MyClass]
    - Annotated: Annotated[MyClass, "description"]

    Args:
        type_hint: Type hint to extract from
        discovered: Set to add discovered types to (modified in place)
    """
    if type_hint is None or type_hint is type(None):
        return

    # Handle Annotated - unwrap to get actual type
    origin = typing.get_origin(type_hint)
    args = typing.get_args(type_hint)

    if origin is not None:
        # Special handling for Annotated
        if hasattr(typing, "Annotated") and origin is typing.Annotated:
            if args:
                # First arg is the actual type, rest is metadata
                _extract_types_from_hint(args[0], discovered)
            return

        # For generic types, recursively extract from args
        # e.g., list[MyClass] -> extract MyClass
        # e.g., dict[str, MyClass] -> extract MyClass (but not str)
        for arg in args:
            _extract_types_from_hint(arg, discovered)

        # Also check if the origin itself is a custom type
        # e.g., MyGeneric[T] where MyGeneric is custom
        # But skip typing constructs (UnionType, etc.)
        if isinstance(origin, type) and _is_custom_type(origin):
            discovered.add(origin)
    elif isinstance(type_hint, type):
        # Simple type (not generic)
        if _is_custom_type(type_hint):
            discovered.add(type_hint)


def _is_custom_type(type_obj: type) -> bool:
    """Check if a type is custom (user-defined or third-party).

    Returns False for:
    - Builtin types (str, int, list, dict, etc.)
    - Stdlib types (datetime, Path, etc.)
    - Typing constructs (Union, Optional, etc.)

    Returns True for:
    - User-defined classes
    - Third-party library classes (Pydantic, etc.)

    Args:
        type_obj: Type to check

    Returns:
        True if custom type, False if builtin/stdlib
    """
    if not isinstance(type_obj, type):
        return False

    # Types that opt out of expansion (e.g. Skill subclasses show brief one-liner only)
    if getattr(type_obj, "__agentdoc_skip__", False):
        return False

    # Check type name against builtins
    type_name = getattr(type_obj, "__name__", "")
    if type_name in BUILTIN_TYPE_NAMES:
        return False

    # Exclude typing internal constructs (UnionType for X | Y syntax)
    if type_name in ("UnionType", "_UnionGenericAlias", "_SpecialForm", "_GenericAlias"):
        return False

    # Check module
    module = getattr(type_obj, "__module__", None)
    if not module:
        return False

    # Exclude builtins module
    if module in ("builtins", "__builtin__"):
        return False

    # Exclude types module (contains UnionType)
    if module == "types":
        return False

    # Exclude stdlib modules
    module_root = module.split(".")[0]
    if module_root in STDLIB_MODULES:
        return False

    # Exclude typing module constructs, include everything else (user code, third-party)
    return module != "typing"
