# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LibrarySkill — documentation handle for a persistent agent library."""

import ast
import importlib
import sys
from pathlib import Path

from nemo_oo_agents.skill import Skill


class LibrarySkill(Skill):
    """A loaded agent library — documentation handle for a Python module.

    The library is accessible as a bare module name in exec_globals.
    This Skill provides documentation via doc(self.<lib_name>).

        doc(self.stats)             # describes the stats module's API
        stats.percentile(data, 95)  # call directly via module name
    """

    def __init__(self, *, path: Path) -> None:
        lib_name = path.name

        # Clear submodule caches so that any edited submodules are re-read from disk,
        # then (re-)import the top-level package.
        prefix = lib_name + "."
        for key in [k for k in sys.modules if k == lib_name or k.startswith(prefix)]:
            del sys.modules[key]
        pkg = importlib.import_module(lib_name)

        # Description from __init__.py module docstring (fallback: parse AST directly).
        description = pkg.__doc__ or ""
        if not description:
            init_py = path / "__init__.py"
            if init_py.exists():
                try:
                    tree = ast.parse(init_py.read_text())
                    description = ast.get_docstring(tree) or ""
                except SyntaxError:
                    pass

        # Store path as string for JSON serializability (snapshot support)
        object.__setattr__(self, "_lib_path", str(path))
        super().__init__(content=description)
        self.__class__ = type(lib_name, (LibrarySkill,), {"__doc__": description})  # pyright: ignore[reportAttributeAccessIssue]

    @property
    def path(self) -> Path:
        """Return the library path as a Path object."""
        return Path(object.__getattribute__(self, "_lib_path"))

    def __dir__(self) -> list[str]:
        lib_name = Path(object.__getattribute__(self, "_lib_path")).name
        mod = sys.modules.get(lib_name)
        return dir(mod) if mod is not None else list(super().__dir__())


# ---------------------------------------------------------------------------
# Type-info extractor — makes doc(self.<lib>) show the module's public API
# ---------------------------------------------------------------------------


def _extract_library_skill_info(obj):
    """Extractor for LibrarySkill instances.

    Returns (TypeInfo, values_dict) showing the module's public API:
    functions, classes, and the path field.
    """
    from agentdoc._info import CallableInfo, FieldInfo, TypeInfo
    from agentdoc._structured import extract_callable_info, extract_module_info

    # When called with the type itself (not an instance), return minimal info.
    if isinstance(obj, type):
        return TypeInfo(
            name=obj.__name__,
            base=None,
            fields=[
                FieldInfo(
                    name="path", type="Path", default=..., description="Library directory path"
                )
            ],
            methods=[],
            docstring=obj.__doc__,
        )

    lib_path = obj.path
    lib_name = lib_path.name
    mod = sys.modules.get(lib_name)

    methods: list[CallableInfo] = []

    # Build the module's public API info
    if mod is not None:
        module_info = extract_module_info(mod)
        # Add module functions
        methods.extend(module_info.functions)
        # Add module classes as constructors
        for cls_name, cls_doc in module_info.classes:
            cls_obj = getattr(mod, cls_name, None)
            if cls_obj is not None:
                try:
                    info = extract_callable_info(cls_obj)
                    methods.append(info)
                except Exception:
                    methods.append(
                        CallableInfo(
                            name=cls_name,
                            signature="(...)",
                            return_type=cls_name,
                            docstring=cls_doc,
                        )
                    )
            else:
                methods.append(
                    CallableInfo(
                        name=cls_name,
                        signature="(...)",
                        return_type=cls_name,
                        docstring=cls_doc,
                    )
                )

    # Fields
    fields = [
        FieldInfo(
            name="path",
            type="Path",
            default=...,
            description="Library directory path",
        ),
    ]

    type_info = TypeInfo(
        name=lib_name,
        base=None,
        fields=fields,
        methods=methods,
        docstring=type(obj).__doc__,
    )

    values = {"path": lib_path}
    return (type_info, values)


def _register_extractor() -> None:
    """Register the LibrarySkill extractor with agentdoc."""
    try:
        from agentdoc.registry import register_type_info_extractor

        register_type_info_extractor(LibrarySkill)(_extract_library_skill_info)
    except ImportError:
        pass


_register_extractor()
