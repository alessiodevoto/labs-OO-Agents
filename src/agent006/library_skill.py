"""LibrarySkill — documentation handle for a persistent agent library."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

from agent006.skill import Skill


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

        object.__setattr__(self, "_lib_path", path)
        super().__init__(content=description)
        self.__class__ = type(lib_name, (LibrarySkill,), {"__doc__": description})  # type: ignore[assignment]

    def __dir__(self) -> list[str]:
        lib_name = object.__getattribute__(self, "_lib_path").name
        mod = sys.modules.get(lib_name)
        return dir(mod) if mod is not None else list(super().__dir__())
