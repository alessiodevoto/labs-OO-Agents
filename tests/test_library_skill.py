"""Tests for LibrarySkill — construction, dir forwarding, and doc() support."""

import sys

import pytest

from nemo_oo_agents.library_skill import LibrarySkill, _extract_library_skill_info

# ---------------------------------------------------------------------------
# Fixture: create a temporary library package on disk and in sys.path
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_lib(tmp_path):
    """Create a minimal library package and add it to sys.path."""
    lib_dir = tmp_path / "mylib"
    lib_dir.mkdir()
    (lib_dir / "__init__.py").write_text(
        '''"""My test library — does cool stuff."""

def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}!"


class Helper:
    """A helper class."""

    def assist(self) -> str:
        return "helping"
'''
    )

    # Add tmp_path to sys.path so importlib can find mylib
    sys.path.insert(0, str(tmp_path))
    yield lib_dir
    # Cleanup
    sys.path.remove(str(tmp_path))
    for key in [k for k in sys.modules if k == "mylib" or k.startswith("mylib.")]:
        del sys.modules[key]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_library_skill_init(tmp_lib):
    skill = LibrarySkill(path=tmp_lib)
    assert skill.path == tmp_lib
    # Dynamic class name is the library name
    assert type(skill).__name__ == "mylib"
    # Docstring from module
    assert "cool stuff" in (type(skill).__doc__ or "")


def test_library_skill_dir_forwards_to_module(tmp_lib):
    skill = LibrarySkill(path=tmp_lib)
    d = dir(skill)
    assert "greet" in d
    assert "Helper" in d


# ---------------------------------------------------------------------------
# agentdoc extractor
# ---------------------------------------------------------------------------


def test_extractor_for_type():
    """Calling the extractor with the type returns TypeInfo."""
    info = _extract_library_skill_info(LibrarySkill)
    assert info.name == "LibrarySkill"
    assert any(f.name == "path" for f in info.fields)


def test_extractor_for_instance(tmp_lib):
    """Calling the extractor with an instance returns (TypeInfo, values)."""
    skill = LibrarySkill(path=tmp_lib)
    result = _extract_library_skill_info(skill)
    assert isinstance(result, tuple)
    type_info, values = result
    assert type_info.name == "mylib"
    assert values["path"] == tmp_lib

    # Should include module's public API
    method_names = [m.name for m in type_info.methods]
    assert "greet" in method_names or any("greet" in n for n in method_names)
    # Should include Helper class
    assert "Helper" in method_names or any("Helper" in n for n in method_names)


def test_extractor_registered():
    """The extractor should be registered with agentdoc registry."""
    from agentdoc.registry import get_type_info_extractor

    extractor = get_type_info_extractor(LibrarySkill)
    assert extractor is _extract_library_skill_info
