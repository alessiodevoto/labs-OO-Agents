"""Tests for LibraryWriting, LibraryManager, and Skill(module) fallback."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nemo_oo_agents.library_manager import LibraryManager
from nemo_oo_agents.skill import Skill
from nemo_oo_agents.tools.library_writing_lib import LibraryWriting, LintReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Minimal agent whose module is this test module."""

    runtime = SimpleNamespace()

    def __setattr__(self, name: str, value: object) -> None:
        object.__setattr__(self, name, value)

    def __getattr__(self, name: str) -> object:
        raise AttributeError(name)


def _make_agent() -> _FakeAgent:
    return _FakeAgent()


SIMPLE_SOURCE = """\
def add(a: int, b: int) -> int:
    return a + b
"""

DESCRIPTION = "Simple arithmetic library."


def _make_lib_dir(
    tmp_path: Path, lib_name: str, source: str, description: str = DESCRIPTION
) -> Path:
    """Create a minimal library directory: pyproject.toml + __init__.py + a .py module."""
    lib_dir = tmp_path / lib_name
    lib_dir.mkdir()
    pyproject = f'[project]\nname = "{lib_name}"\nversion = "0.1.0"\ndependencies = []\n'
    (lib_dir / "pyproject.toml").write_text(pyproject)
    (lib_dir / "__init__.py").write_text(f'"""{description}"""\nfrom .{lib_name} import *\n')
    (lib_dir / f"{lib_name}.py").write_text(source)
    if str(tmp_path) not in sys.path:
        sys.path.insert(0, str(tmp_path))
    return lib_dir


# ---------------------------------------------------------------------------
# LintReport
# ---------------------------------------------------------------------------


def test_lint_report_ok():
    assert str(LintReport(written=True, loaded=True)) == "OK — written and loaded"


def test_lint_report_not_written():
    s = str(LintReport(written=False, errors=["E001: exec() is forbidden (line 1)"]))
    assert "ERROR" in s
    assert "E001" in s


def test_lint_report_written_not_loaded():
    s = str(LintReport(written=True, loaded=False, warnings=["E002: 'numpy' not in allowed set"]))
    assert "WARNING" in s
    assert "E002" in s


# ---------------------------------------------------------------------------
# LibraryManager._import_module
# ---------------------------------------------------------------------------


def test_import_module_registers_in_sys_modules(tmp_path: Path):
    """_import_module imports the package and registers it in sys.modules."""
    lib_dir = _make_lib_dir(tmp_path, "ts_load", SIMPLE_SOURCE)
    agent = _make_agent()
    mgr = LibraryManager(agent, tmp_path)
    mgr._import_module(lib_dir)
    assert "ts_load" in sys.modules
    assert sys.modules["ts_load"].add(1, 2) == 3


def test_import_module_cache_busts(tmp_path: Path):
    """A second _import_module picks up changes made to the package on disk."""
    lib_dir = _make_lib_dir(tmp_path, "ts_reload", SIMPLE_SOURCE)
    agent = _make_agent()
    mgr = LibraryManager(agent, tmp_path)
    mgr._import_module(lib_dir)

    (lib_dir / "ts_reload.py").write_text("def add(a, b): return a + b + 99\n")
    mgr._import_module(lib_dir)

    assert sys.modules["ts_reload"].add(0, 0) == 99


def test_skill_module_fallback_has_module_dir(tmp_path: Path):
    """Skill(module) fallback exposes module names via __dir__."""
    _make_lib_dir(tmp_path, "ts_dir", SIMPLE_SOURCE)
    agent = _make_agent()
    LibraryManager.install(agent, libs_dir=tmp_path)
    assert "add" in dir(agent.ts_dir)


def test_skill_module_fallback_description(tmp_path: Path):
    """Skill(module) fallback uses the module docstring."""
    _make_lib_dir(tmp_path, "ts_desc", SIMPLE_SOURCE, description="My description.")
    agent = _make_agent()
    LibraryManager.install(agent, libs_dir=tmp_path)
    assert "My description." in (type(agent.ts_desc).__doc__ or "")


# ---------------------------------------------------------------------------
# LibraryManager
# ---------------------------------------------------------------------------


def test_library_manager_discover_finds_pyproject_dirs(tmp_path: Path):
    """discover() returns names of directories that contain a pyproject.toml."""
    _make_lib_dir(tmp_path, "lm_a", SIMPLE_SOURCE)
    _make_lib_dir(tmp_path, "lm_b", SIMPLE_SOURCE)
    (tmp_path / "no_pyproject").mkdir()  # no pyproject.toml — must not appear

    assert LibraryManager.discover(tmp_path) == ["lm_a", "lm_b"]


def test_library_manager_discover_empty(tmp_path: Path):
    """discover() returns an empty list when no libraries exist."""
    assert LibraryManager.discover(tmp_path) == []


def test_library_manager_install_attaches_libraries(tmp_path: Path):
    """install() attaches each library as a Skill attribute on the agent."""
    _make_lib_dir(tmp_path, "lm_install", SIMPLE_SOURCE)
    agent = _make_agent()
    LibraryManager.install(agent, libs_dir=tmp_path)
    assert hasattr(agent, "lm_install")
    assert isinstance(agent.lm_install, Skill)


def test_library_manager_install_skips_missing_dir(tmp_path: Path):
    """install() does nothing when the libs directory does not exist."""
    agent = _make_agent()
    mgr = LibraryManager.install(agent, libs_dir=tmp_path / "nonexistent")
    assert mgr._installed == []


def test_library_manager_install_skips_existing_attrs(tmp_path: Path):
    """install() does not overwrite an attribute already present on the agent."""
    _make_lib_dir(tmp_path, "lm_skip", SIMPLE_SOURCE)
    agent = _make_agent()
    sentinel = object()
    agent.lm_skip = sentinel  # type: ignore[attr-defined]
    LibraryManager.install(agent, libs_dir=tmp_path)
    assert agent.lm_skip is sentinel


def test_library_manager_install_skips_bad_library(tmp_path: Path):
    """install() logs a warning and continues when a library fails to import."""
    bad = tmp_path / "lm_bad"
    bad.mkdir()
    (bad / "pyproject.toml").write_text('[project]\nname = "lm_bad"\n')
    (bad / "__init__.py").write_text("raise RuntimeError('broken')\n")
    _make_lib_dir(tmp_path, "lm_good", SIMPLE_SOURCE)

    agent = _make_agent()
    mgr = LibraryManager.install(agent, libs_dir=tmp_path)
    assert "lm_good" in mgr._installed
    assert "lm_bad" not in mgr._installed


def test_library_manager_reload(tmp_path: Path):
    """reload() re-imports the library and the agent sees updated code."""
    lib_dir = _make_lib_dir(tmp_path, "lm_reload", SIMPLE_SOURCE)
    agent = _make_agent()
    mgr = LibraryManager.install(agent, libs_dir=tmp_path)

    (lib_dir / "lm_reload.py").write_text("def add(a, b): return a + b + 100\n")
    mgr._reload("lm_reload")

    assert sys.modules["lm_reload"].add(1, 2) == 103


def test_library_manager_reload_all(tmp_path: Path):
    """reload_all() reloads every installed library."""
    dir_a = _make_lib_dir(tmp_path, "lm_all_a", SIMPLE_SOURCE)
    dir_b = _make_lib_dir(tmp_path, "lm_all_b", SIMPLE_SOURCE)
    agent = _make_agent()
    mgr = LibraryManager.install(agent, libs_dir=tmp_path)

    (dir_a / "lm_all_a.py").write_text("def add(a, b): return a + b + 10\n")
    (dir_b / "lm_all_b.py").write_text("def add(a, b): return a + b + 20\n")
    mgr.reload()

    assert sys.modules["lm_all_a"].add(0, 0) == 10
    assert sys.modules["lm_all_b"].add(0, 0) == 20


# ---------------------------------------------------------------------------
# LibraryWriting.create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_writes_pyproject_and_init(tmp_path: Path):
    """create() writes pyproject.toml (with name/version) and __init__.py (with docstring)."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("mylib", DESCRIPTION)

    pyproject = (tmp_path / "mylib" / "pyproject.toml").read_text()
    assert 'name = "mylib"' in pyproject
    assert 'version = "0.1.0"' in pyproject

    init_py = (tmp_path / "mylib" / "__init__.py").read_text()
    assert DESCRIPTION in init_py


@pytest.mark.asyncio
async def test_create_does_not_write_source_file(tmp_path: Path):
    """create() only scaffolds the package — no code files are written."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("mylib", DESCRIPTION)
    py_files = [p for p in (tmp_path / "mylib").iterdir() if p.suffix == ".py"]
    assert py_files == [tmp_path / "mylib" / "__init__.py"]


# ---------------------------------------------------------------------------
# LibraryWriting.list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_lib_names(tmp_path: Path):
    """list() returns sorted names of libraries detected by pyproject.toml."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("alpha", "Alpha lib")
    await libs.create("beta", "Beta lib")
    (tmp_path / "empty_dir").mkdir()  # no pyproject.toml — must not appear
    assert await libs.list() == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_list_empty_when_no_libs(tmp_path: Path):
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    assert await libs.list() == []


# ---------------------------------------------------------------------------
# LibraryWriting.write_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_py_lints_and_loads(tmp_path: Path):
    """write_file() on a .py file lints, writes, and hot-reloads on clean source."""
    import importlib

    agent = _make_agent()
    libs = LibraryWriting(agent, path=tmp_path)
    await libs.create("wf_load", DESCRIPTION)
    report_str = await libs.write_file("wf_load", "wf_load.py", SIMPLE_SOURCE)

    assert "OK" in report_str
    assert "wf_load" in sys.modules
    # The function lives in the wf_load.wf_load submodule
    submod = importlib.import_module("wf_load.wf_load")
    assert submod.add(2, 3) == 5


@pytest.mark.asyncio
async def test_write_file_py_syntax_error_not_written(tmp_path: Path):
    """write_file() with a syntax error returns an ERROR report and does not write the file."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("wf_bad", DESCRIPTION)
    report_str = await libs.write_file("wf_bad", "wf_bad.py", "def broken(:\n    pass\n")

    assert "ERROR" in report_str
    assert not (tmp_path / "wf_bad" / "wf_bad.py").exists()


@pytest.mark.asyncio
async def test_write_file_init_py_lints_but_allows_star_imports(tmp_path: Path):
    """write_file() on __init__.py lints it but treats star imports as a warning, not an error."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("wf_init", DESCRIPTION)
    result = await libs.write_file(
        "wf_init", "__init__.py", '"""updated"""\nfrom .wf_init import *\n'
    )
    # E003 is a warning for __init__.py — file is written, not blocked
    assert "ERROR" not in result
    assert (
        tmp_path / "wf_init" / "__init__.py"
    ).read_text() == '"""updated"""\nfrom .wf_init import *\n'


@pytest.mark.asyncio
async def test_write_file_pyproject_checks_deps(tmp_path: Path):
    """write_file() on pyproject.toml validates declared dependencies."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("wf_deps", DESCRIPTION)
    content = '[project]\nname = "wf_deps"\nversion = "0.1.0"\ndependencies = ["numpy"]\n'
    report_str = await libs.write_file("wf_deps", "pyproject.toml", content)
    # numpy is not in the test agent's importable modules → E002 warning
    assert "E002" in report_str or "numpy" in report_str


@pytest.mark.asyncio
async def test_write_file_other_path_written_plainly(tmp_path: Path):
    """write_file() on a non-.py, non-pyproject path just writes the file."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("wf_plain", DESCRIPTION)
    result = await libs.write_file("wf_plain", "README.md", "# hello\n")
    assert (tmp_path / "wf_plain" / "README.md").read_text() == "# hello\n"
    assert "bytes" in result


@pytest.mark.asyncio
async def test_write_file_adds_libs_dir_to_sys_path(tmp_path: Path):
    """LibraryWriting.__init__ adds the libs directory to sys.path."""
    LibraryWriting(_make_agent(), path=tmp_path)
    assert str(tmp_path) in sys.path


# ---------------------------------------------------------------------------
# LibraryWriting.edit_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_file_patches_content(tmp_path: Path):
    """edit_file() applies the search/replace and updates the file on disk."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("ef_patch", DESCRIPTION)
    await libs.write_file("ef_patch", "ef_patch.py", SIMPLE_SOURCE)
    await libs.edit_file("ef_patch", "ef_patch.py", "return a + b", "return a + b + 0  # patched")
    assert "patched" in (tmp_path / "ef_patch" / "ef_patch.py").read_text()


@pytest.mark.asyncio
async def test_edit_file_returns_lint_report(tmp_path: Path):
    """edit_file() on a .py file returns a LintReport string."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("ef_report", DESCRIPTION)
    await libs.write_file("ef_report", "ef_report.py", SIMPLE_SOURCE)
    result = await libs.edit_file("ef_report", "ef_report.py", "return a + b", "return a + b + 0")
    assert any(kw in result for kw in ("OK", "WARNING", "ERROR"))


@pytest.mark.asyncio
async def test_edit_file_pyproject_checks_deps(tmp_path: Path):
    """edit_file() on pyproject.toml validates declared dependencies and returns a LintReport."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("ef_deps", DESCRIPTION)
    result = await libs.edit_file(
        "ef_deps",
        "pyproject.toml",
        "dependencies = []",
        'dependencies = ["numpy"]',
    )
    # numpy is not in the test agent's importable modules → E002 warning
    assert "E002" in result or "numpy" in result


@pytest.mark.asyncio
async def test_edit_file_non_py_returns_plain_result(tmp_path: Path):
    """edit_file() on a non-.py file returns the raw edit result string."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("ef_plain", DESCRIPTION)
    await libs.write_file("ef_plain", "notes.txt", "hello world\n")
    result = await libs.edit_file("ef_plain", "notes.txt", "hello", "goodbye")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# LibraryWriting.view_file / grep / repo_tree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_file_returns_contents(tmp_path: Path):
    """view_file() returns the contents of a file in the library."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("vf_lib", DESCRIPTION)
    await libs.write_file("vf_lib", "vf_lib.py", SIMPLE_SOURCE)
    contents = await libs.view_file("vf_lib", "vf_lib.py")
    assert "def add" in contents


@pytest.mark.asyncio
async def test_grep_searches_library_files(tmp_path: Path):
    """grep() searches across library files and returns matching lines."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("grep_lib", DESCRIPTION)
    await libs.write_file("grep_lib", "grep_lib.py", SIMPLE_SOURCE)
    result = await libs.grep("def add", "grep_lib")
    assert "add" in result


@pytest.mark.asyncio
async def test_repo_tree_returns_directory_structure(tmp_path: Path):
    """repo_tree() returns the directory tree of the libs root."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("tree_lib", DESCRIPTION)
    result = await libs.repo_tree()
    assert "tree_lib" in result


# ---------------------------------------------------------------------------
# LibraryWriting lint — E003 star imports
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_star_import_is_warning_not_error(tmp_path: Path):
    """write_file() on a .py file with a star import returns a warning — file is still written."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("si_lib", DESCRIPTION)
    report_str = await libs.write_file("si_lib", "si_lib.py", "from os import *\n")
    assert "E003" in report_str
    assert "ERROR" not in report_str
    assert (tmp_path / "si_lib" / "si_lib.py").exists()


# ---------------------------------------------------------------------------
# LibraryWriting.run_tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tests_passes(tmp_path: Path):
    """run_tests() on a library with a passing test returns output containing 'passed'."""
    libs = LibraryWriting(_make_agent(), path=tmp_path)
    await libs.create("rt_math", DESCRIPTION)
    await libs.write_file("rt_math", "rt_math.py", SIMPLE_SOURCE)
    await libs.write_file(
        "rt_math",
        "tests/test_rt_math.py",
        "from rt_math.rt_math import add\ndef test_add(): assert add(1, 2) == 3\n",
    )
    output = await libs.run_tests("rt_math")
    assert "passed" in output.lower(), output
