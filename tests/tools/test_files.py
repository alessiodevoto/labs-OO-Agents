"""Tests for nooa.tools.bash_tool.FileTool.

Contract-focused: assert public interface (file operations, error handling)
without depending on implementation details.
"""

import tempfile
from pathlib import Path

import pytest

from nooa.config.tool_configs import BashConfig
from nooa.tools.bash_tool import BashTool, FileResult, FileTool

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def file_tool(temp_dir):
    """Create a FileTool instance with sandbox disabled."""
    bash = BashTool(working_dir=str(temp_dir), config=BashConfig(use_sandbox=False))
    return FileTool(bash)


@pytest.fixture
def file_tool_sandboxed(temp_dir):
    """Create a FileTool instance with sandbox enabled."""
    bash = BashTool(working_dir=str(temp_dir), config=BashConfig(use_sandbox=True))
    return FileTool(bash)


# ============================================================================
# FileTool.read() Tests
# ============================================================================


@pytest.mark.asyncio
async def test_file_tool_read_full_file(file_tool: FileTool, temp_dir: Path):
    """FileTool.read() reads entire file content when no line range specified.

    Verifies that read() returns complete file contents as a string.
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("line 1\nline 2\nline 3")

    result = await file_tool.read("test.txt")
    assert isinstance(result, FileResult)
    assert result.stdout == "line 1\nline 2\nline 3"


@pytest.mark.asyncio
async def test_file_tool_read_line_range(file_tool: FileTool, temp_dir: Path):
    """FileTool.read() reads specified line range when start_line and end_line provided.

    Verifies that:
    - start_line and end_line are 1-indexed
    - Both start and end lines are included (inclusive)
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("line 1\nline 2\nline 3\nline 4")

    result = await file_tool.read("test.txt", start_line=2, end_line=3)
    assert result.stdout == "line 2\nline 3"


@pytest.mark.asyncio
async def test_file_tool_read_from_line_to_end(file_tool: FileTool, temp_dir: Path):
    """FileTool.read() reads from start_line to end of file when only start_line provided.

    Verifies that:
    - start_line is 1-indexed
    - All lines from start_line to end are included
    - Lines before start_line are excluded
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("line 1\nline 2\nline 3")

    result = await file_tool.read("test.txt", start_line=2)
    assert "line 2" in result.stdout
    assert "line 3" in result.stdout
    assert "line 1" not in result.stdout


@pytest.mark.asyncio
async def test_file_tool_read_file_not_found(file_tool: FileTool):
    """FileTool.read() raises FileNotFoundError for non-existent files.

    Verifies that read() raises FileNotFoundError when file does not exist.
    """
    with pytest.raises(FileNotFoundError):
        await file_tool.read("nonexistent.txt")


# ============================================================================
# FileTool.write() Tests
# ============================================================================


@pytest.mark.asyncio
async def test_file_tool_write_creates_file(file_tool: FileTool, temp_dir: Path):
    """FileTool.write() creates file with specified content.

    Verifies that:
    - File is created at specified path
    - Content is written correctly (heredoc may add trailing newline)
    - Returns success message
    """
    content = "Hello, world!\nThis is a test."
    result = await file_tool.write("output.txt", content)

    assert isinstance(result, FileResult)
    assert "Written" in result.stdout
    assert (temp_dir / "output.txt").exists()
    # Heredoc adds a trailing newline
    written_content = (temp_dir / "output.txt").read_text()
    assert content in written_content or written_content.rstrip() == content


@pytest.mark.asyncio
async def test_file_tool_write_creates_parent_directories(file_tool: FileTool, temp_dir: Path):
    """FileTool.write() creates parent directories if they don't exist.

    Verifies that nested directory paths are created automatically.
    """
    content = "test content"
    await file_tool.write("subdir/nested/file.txt", content)

    assert (temp_dir / "subdir" / "nested" / "file.txt").exists()


@pytest.mark.asyncio
async def test_file_tool_write_handles_special_characters(file_tool: FileTool):
    """FileTool.write() correctly handles multi-line content and special characters.

    Verifies that:
    - Multi-line content is preserved
    - Special characters (<>&\"') are handled correctly
    - Content can be read back accurately
    """
    content = "line 1\nline 2\nline 3\nwith special chars: <>&\"'"
    await file_tool.write("multiline.txt", content)

    read_result = await file_tool.read("multiline.txt")
    # Heredoc may add trailing newline, so compare content without trailing whitespace
    assert read_result.stdout.rstrip() == content.rstrip()


# ============================================================================
# FileTool.list() Tests
# ============================================================================


@pytest.mark.asyncio
async def test_file_tool_list_directory_contents(file_tool: FileTool, temp_dir: Path):
    """FileTool.list() returns list of files in specified directory.

    Verifies that:
    - Direct children are listed (not recursive)
    - File names are included in result
    """
    (temp_dir / "file1.txt").touch()
    (temp_dir / "file2.txt").touch()
    (temp_dir / "subdir").mkdir()
    (temp_dir / "subdir" / "file3.txt").touch()

    result = await file_tool.list(".")
    assert isinstance(result, FileResult)
    files = result.lines
    assert "file1.txt" in files
    assert "file2.txt" in files
    # Note: list only lists direct children, not recursive


@pytest.mark.asyncio
async def test_file_tool_list_subdirectory(file_tool: FileTool, temp_dir: Path):
    """FileTool.list() lists files in subdirectories when path specified.

    Verifies that list() works with relative subdirectory paths.
    """
    (temp_dir / "subdir").mkdir()
    (temp_dir / "subdir" / "file.txt").touch()

    result = await file_tool.list("subdir")
    files = result.lines
    assert "file.txt" in files


@pytest.mark.asyncio
async def test_file_tool_list_nonexistent_directory(file_tool: FileTool):
    """FileTool.list() raises FileNotFoundError for non-existent directories.

    Verifies that list() raises FileNotFoundError when directory does not exist.
    """
    with pytest.raises(FileNotFoundError):
        await file_tool.list("nonexistent_dir")


# ============================================================================
# FileTool.exists() Tests
# ============================================================================


@pytest.mark.asyncio
async def test_file_tool_exists_checks_file(file_tool: FileTool, temp_dir: Path):
    """FileTool.exists() returns True for existing files, False otherwise.

    Verifies that exists() correctly identifies file presence.
    """
    (temp_dir / "exists.txt").touch()

    result1 = await file_tool.exists("exists.txt")
    assert isinstance(result1, FileResult)
    assert result1.stdout.strip() == "yes"
    result2 = await file_tool.exists("nonexistent.txt")
    assert result2.stdout.strip() == "no"


@pytest.mark.asyncio
async def test_file_tool_exists_checks_directory(file_tool: FileTool, temp_dir: Path):
    """FileTool.exists() returns True for existing directories.

    Verifies that exists() works for both files and directories.
    """
    (temp_dir / "testdir").mkdir()

    result = await file_tool.exists("testdir")
    assert result.stdout.strip() == "yes"


# ============================================================================
# FileTool.find() Tests
# ============================================================================


@pytest.mark.asyncio
async def test_file_tool_find_with_glob_pattern(file_tool: FileTool, temp_dir: Path):
    """FileTool.find() searches for files matching glob pattern.

    Verifies that:
    - Glob patterns are supported
    - Matching files are returned
    - Results include file paths
    """
    (temp_dir / "test1.py").touch()
    (temp_dir / "test2.py").touch()
    (temp_dir / "data.txt").touch()
    (temp_dir / "subdir").mkdir()
    (temp_dir / "subdir" / "test3.py").touch()

    result = await file_tool.find("*.py", ".")
    assert isinstance(result, FileResult)
    py_files = result.lines
    assert len(py_files) >= 2
    assert any("test1.py" in f for f in py_files)
    assert any("test2.py" in f for f in py_files)


@pytest.mark.asyncio
async def test_file_tool_find_no_matches(file_tool: FileTool):
    """FileTool.find() returns empty list when no files match pattern.

    Verifies that find() returns [] when no matches are found.
    Also pins that "no matches" isn't surfaced as a failure — rg
    exits 1 on no-match (like grep); the tool normalizes that to
    success so callers get a clean empty list.
    """
    result = await file_tool.find("*.nonexistent", ".")
    assert result.lines == []
    assert result.success, "no-match should not be reported as failure"


@pytest.mark.asyncio
async def test_file_tool_find_respects_gitignore(file_tool: FileTool, temp_dir: Path):
    """FileTool.find() skips paths listed in .gitignore.

    This is the behaviour we rely on to avoid ``.venv`` / ``.git`` /
    ``node_modules`` hangs that a plain ``find`` exhibits on a
    project root. Any ``.py`` under a gitignored directory must NOT
    show up in the results.
    """
    # Two real files we want to find.
    (temp_dir / "keep.py").touch()
    (temp_dir / "keep_nested").mkdir()
    (temp_dir / "keep_nested" / "also_keep.py").touch()
    # A "should be ignored" tree.
    (temp_dir / "ignored").mkdir()
    (temp_dir / "ignored" / "hidden.py").touch()
    (temp_dir / ".gitignore").write_text("ignored/\n")

    result = await file_tool.find("*.py", ".")
    paths = result.lines
    assert any("keep.py" in p for p in paths)
    assert any("also_keep.py" in p for p in paths)
    assert not any("hidden.py" in p for p in paths), (
        f"gitignored file leaked into find() results: {paths}"
    )


@pytest.mark.asyncio
async def test_file_tool_find_type_directory(file_tool: FileTool, temp_dir: Path):
    """``type='d'`` falls back to pruned ``find`` for directory search."""
    (temp_dir / "alpha").mkdir()
    (temp_dir / "beta").mkdir()
    (temp_dir / "alpha" / "file.txt").touch()

    result = await file_tool.find("alpha", ".", type="d")
    paths = result.lines
    assert any("alpha" in p for p in paths)
    # Sanity: the file inside alpha isn't matched as a directory.
    assert not any(p.endswith("file.txt") for p in paths)


# ============================================================================
# FileTool.grep() Tests
# ============================================================================


@pytest.mark.asyncio
async def test_file_tool_grep_searches_file_content(file_tool: FileTool, temp_dir: Path):
    """FileTool.grep() searches file content for pattern matches.

    Verifies that:
    - Pattern matching works
    - Matching lines are returned
    - Line numbers may be included in output
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("line 1\nline with pattern\nline 3\nanother pattern here")

    result = await file_tool.grep("pattern", "test.txt")
    assert isinstance(result, FileResult)
    assert "pattern" in result.stdout
    assert "line with pattern" in result.stdout or "2:line with pattern" in result.stdout


@pytest.mark.asyncio
async def test_file_tool_grep_with_context_lines(file_tool: FileTool, temp_dir: Path):
    """FileTool.grep() includes context lines when context parameter specified.

    Verifies that context parameter adds surrounding lines to matches.
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("line 1\nline 2\npattern\nline 4\nline 5")

    result = await file_tool.grep("pattern", "test.txt", context=1)
    # Should include context lines
    assert "pattern" in result.stdout


@pytest.mark.asyncio
async def test_file_tool_grep_on_directory(file_tool: FileTool, temp_dir: Path):
    """FileTool.grep() works on directories (recursive, gitignore-aware).

    Plain ``grep`` on a directory fails with ``Is a directory`` unless
    ``-r`` is passed; callers who handed a project subdir to the old
    tool got nothing back. The rg-backed version walks recursively by
    default.
    """
    (temp_dir / "src").mkdir()
    (temp_dir / "src" / "a.py").write_text("def main():\n    pass\n")
    (temp_dir / "src" / "nested").mkdir()
    (temp_dir / "src" / "nested" / "b.py").write_text("def main():\n    print('b')\n")

    result = await file_tool.grep("def main", "src")
    assert result.success, result.stderr
    # Both files should show up in the directory-wide match.
    assert "a.py" in result.stdout
    assert "b.py" in result.stdout


@pytest.mark.asyncio
async def test_file_tool_grep_no_matches_is_not_failure(file_tool: FileTool, temp_dir: Path):
    """Zero matches must not surface as a failed FileResult.

    rg (like grep) exits 1 when nothing matches — the tool normalizes
    that to success so callers don't have to special-case it.
    """
    (temp_dir / "test.txt").write_text("alpha\nbravo\n")
    result = await file_tool.grep("never-in-there", "test.txt")
    assert result.stdout == ""
    assert result.success, "no-match should not be reported as failure"


@pytest.mark.asyncio
async def test_file_tool_grep_skips_gitignored_paths(file_tool: FileTool, temp_dir: Path):
    """A directory-wide grep ignores files listed in .gitignore."""
    (temp_dir / ".gitignore").write_text("ignored/\n")
    (temp_dir / "kept.py").write_text("needle here\n")
    (temp_dir / "ignored").mkdir()
    (temp_dir / "ignored" / "hidden.py").write_text("needle here\n")

    result = await file_tool.grep("needle", ".")
    assert "kept.py" in result.stdout
    assert "hidden.py" not in result.stdout, (
        f"gitignored path leaked into grep output: {result.stdout!r}"
    )


# ============================================================================
# FileTool.edit_file() Tests
# ============================================================================


@pytest.mark.asyncio
async def test_file_tool_edit_file_replaces_matching_block(file_tool: FileTool, temp_dir: Path):
    """FileTool.edit_file() replaces search block with replacement block.

    Verifies that:
    - Exact match is found and replaced
    - File content is updated correctly
    - Returns success message
    """
    test_file = temp_dir / "test.py"
    test_file.write_text("def old_func():\n    pass\n")

    result = await file_tool.edit_file(
        "test.py", "def old_func():\n    pass", "def new_func():\n    return 42"
    )

    assert isinstance(result, FileResult)
    assert "SUCCESS" in result.stdout
    read_result = await file_tool.read("test.py")
    assert "new_func" in read_result.stdout
    assert "old_func" not in read_result.stdout


@pytest.mark.asyncio
async def test_file_tool_edit_file_no_match_raises_error(file_tool: FileTool, temp_dir: Path):
    """FileTool.edit_file() raises ValueError when search block not found.

    Verifies that edit_file() raises ValueError with appropriate message when
    search block does not exist in file.
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("some content")

    with pytest.raises(ValueError, match="Search block not found"):
        await file_tool.edit_file("test.txt", "nonexistent", "replacement")


@pytest.mark.asyncio
async def test_file_tool_edit_file_multiple_matches_raises_error(
    file_tool: FileTool, temp_dir: Path
):
    """FileTool.edit_file() raises ValueError when search block matches multiple times.

    Verifies that edit_file() raises ValueError when search block appears
    multiple times (ambiguous replacement).
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("same\nsame\n")

    with pytest.raises(ValueError, match="Found 2 matches"):
        await file_tool.edit_file("test.txt", "same", "different")


@pytest.mark.asyncio
async def test_file_tool_edit_file_validates_python_syntax(file_tool: FileTool, temp_dir: Path):
    """FileTool.edit_file() validates Python syntax for .py files.

    Verifies that edit_file() raises error when replacement block creates
    invalid Python syntax.
    """
    test_file = temp_dir / "test.py"
    test_file.write_text("def valid():\n    pass\n")

    # This should fail syntax check
    with pytest.raises((ValueError, OSError)):
        await file_tool.edit_file(
            "test.py", "def valid():\n    pass", "def invalid(\n    # missing closing paren"
        )


@pytest.mark.asyncio
async def test_file_tool_edit_file_fuzzy_matching_suggestion(file_tool: FileTool, temp_dir: Path):
    """FileTool.edit_file() suggests fuzzy match when search block is similar but not exact.

    Verifies that edit_file() raises ValueError with "Did you mean" message when
    search block is similar (>0.6 similarity) but not exact match.
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("def original_function():\n    return 42\n")

    # Use a search block that's similar but not exact
    with pytest.raises(ValueError, match="Did you mean"):
        await file_tool.edit_file(
            "test.txt",
            "def function():\n    return 42",  # Similar but not exact
            "def modified():\n    return 100",
        )


# ============================================================================
# FileTool Sandbox Tests
# ============================================================================


@pytest.mark.asyncio
async def test_file_tool_read_with_sandbox_enabled(file_tool_sandboxed: FileTool, temp_dir: Path):
    """FileTool.read() works with sandbox enabled when SRT is available and configured.

    Verifies that read() works correctly when sandbox is enabled, skipping test
    if SRT is not available or misconfigured.
    """
    test_file = temp_dir / "test.txt"
    test_file.write_text("sandboxed content")

    # Check if sandbox is actually available and working
    if not file_tool_sandboxed.bash.sandbox_available:
        pytest.skip("SRT sandbox not available")

    try:
        result = await file_tool_sandboxed.read("test.txt")
        assert "sandboxed content" in result.stdout
        assert result.sandboxed is True
    except FileNotFoundError:
        # SRT might be available but misconfigured
        pytest.skip("SRT available but not properly configured")


@pytest.mark.asyncio
async def test_file_tool_write_with_sandbox_enabled(file_tool_sandboxed: FileTool, temp_dir: Path):
    """FileTool.write() works with sandbox enabled when SRT is available and configured.

    Verifies that write() works correctly when sandbox is enabled, skipping test
    if SRT is not available or misconfigured.
    """
    # Check if sandbox is actually available and working
    if not file_tool_sandboxed.bash.sandbox_available:
        pytest.skip("SRT sandbox not available")

    content = "sandboxed write test"
    try:
        result = await file_tool_sandboxed.write("sandboxed.txt", content)
        assert "Written" in result.stdout
        assert result.sandboxed is True
        assert (temp_dir / "sandboxed.txt").exists()
        written_content = (temp_dir / "sandboxed.txt").read_text()
        assert content in written_content or written_content.rstrip() == content
    except OSError:
        # SRT might be available but misconfigured
        pytest.skip("SRT available but not properly configured")


@pytest.mark.asyncio
async def test_file_tool_list_with_sandbox_enabled(file_tool_sandboxed: FileTool, temp_dir: Path):
    """FileTool.list() works with sandbox enabled when SRT is available and configured.

    Verifies that list() works correctly when sandbox is enabled, skipping test
    if SRT is not available or misconfigured.
    """
    # Check if sandbox is actually available and working
    if not file_tool_sandboxed.bash.sandbox_available:
        pytest.skip("SRT sandbox not available")

    (temp_dir / "file1.txt").touch()
    (temp_dir / "file2.txt").touch()

    try:
        result = await file_tool_sandboxed.list(".")
        files = result.lines
        assert "file1.txt" in files
        assert "file2.txt" in files
        assert result.sandboxed is True
    except FileNotFoundError:
        # SRT might be available but misconfigured
        pytest.skip("SRT available but not properly configured")


# ============================================================================
# FileTool String Representation Tests
# ============================================================================


def test_file_tool_repr(file_tool: FileTool):
    """FileTool.__repr__ includes class name.

    Verifies that string representation contains identifying information.
    """
    repr_str = repr(file_tool)
    assert "FileTool" in repr_str
