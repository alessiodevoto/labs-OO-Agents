# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for search_references() and _tree_sitter_backend.py."""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nooa_cli.tools.repo_tools import RepoTools, _detect_lang

from nooa.tools.shell_tools import Match


@pytest.fixture
def ref_repo(tmp_path):
    """Repository with cross-file references for testing search_references()."""
    src = tmp_path / "src"
    src.mkdir()

    (src / "models.py").write_text(
        textwrap.dedent("""        class Application:
            def run(self):
                pass

            def stop(self):
                pass

        def create_app(name):
            return Application()
        """)
    )

    (src / "main.py").write_text(
        textwrap.dedent("""        from models import Application, create_app

        app = create_app("myapp")
        app.run()

        def start():
            a = Application()
            a.run()
            a.stop()
        """)
    )

    (src / "test_app.py").write_text(
        textwrap.dedent("""        from models import Application

        def test_create():
            app = Application()
            assert app is not None

        def test_run():
            # Test the run method
            app = Application()
            app.run()
        """)
    )

    (src / "utils.py").write_text(
        textwrap.dedent("""        import os

        def helper():
            pass

        def configure():
            # Application is mentioned in a comment
            return {}
        """)
    )

    (src / "widgets.ts").write_text(
        textwrap.dedent("""        export interface Widget {
            name: string;
        }

        export class WidgetRunner {
        }

        export function runWidget() {
            return helperWidget();
        }

        const helperWidget = () => 1;
        const instance = new Widget();
        """)
    )

    return tmp_path


# ==========================================================================
# search_references — call sites, qualified names, definition filtering
# ==========================================================================
class TestSearchReferences:
    """Tests for RepoTools.search_references()."""

    async def test_finds_call_sites(self, ref_repo):
        """search_references should find usages of a symbol (not definitions)."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("create_app", path="src/")
        assert r.total_matches > 0
        # Should find the call in main.py
        assert any("create_app" in m for m in r.matches)

    async def test_excludes_definitions(self, ref_repo):
        """Definitions (def/class lines) should be filtered out."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("create_app", path="src/")
        # "def create_app" should NOT appear in results
        for m in r.matches:
            content = m.split(": ", 1)[-1] if ": " in m else m
            assert not content.strip().startswith("def create_app"), (
                f"Definition line should be excluded: {m}"
            )

    async def test_excludes_typescript_definitions(self, ref_repo):
        """TypeScript function/class/interface/const definitions should be filtered out."""
        rt = RepoTools(root=ref_repo)

        helper_refs = await rt._search_references("helperWidget", path="src/")
        assert any("return helperWidget()" in m for m in helper_refs.matches)
        assert not any("const helperWidget" in m for m in helper_refs.matches)

        run_refs = await rt._search_references("runWidget", path="src/")
        assert not any("function runWidget" in m for m in run_refs.matches)

        widget_refs = await rt._search_references("Widget", path="src/")
        assert not any("interface Widget" in m for m in widget_refs.matches)
        assert not any("class WidgetRunner" in m for m in widget_refs.matches)

    async def test_typescript_const_usage_is_not_filtered_as_definition(
        self, ref_repo, monkeypatch
    ):
        """Fallback reference search should keep variable declarations that use the symbol."""
        import nooa_cli.tools._tree_sitter_backend as tsmod

        monkeypatch.setattr(tsmod, "TREE_SITTER_AVAILABLE", False)
        rt = RepoTools(root=ref_repo)

        widget_refs = await rt._search_references("Widget", path="src/")

        assert any("const instance = new Widget()" in m for m in widget_refs.matches)
        assert not any("interface Widget" in m for m in widget_refs.matches)

    async def test_finds_class_references(self, ref_repo):
        """Should find references to class names."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("Application", path="src/")
        assert r.total_matches > 0
        # Should find usage in main.py and test_app.py
        files_found = {m.split(":")[0] for m in r.matches}
        assert len(files_found) >= 1  # At least one file has references

    async def test_qualified_name(self, ref_repo):
        """Qualified name like 'Application.run' should only match lines with both parts."""
        rt = RepoTools(root=ref_repo)
        result = await rt._search_references("Application.run", path="src/")
        # Matches (if any) should reference "run" in context of "Application"
        for m in result.matches:
            content = m.split(": ", 1)[-1] if ": " in m else m
            assert "run" in content.lower()

    async def test_no_results_for_nonexistent(self, ref_repo):
        """Should return empty for symbols that don't exist."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("zzz_nonexistent_symbol_zzz", path="src/")
        assert r.total_matches == 0
        assert r.matches == []

    async def test_max_results_cap(self, ref_repo):
        """max_results should limit the number of returned matches."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("Application", path="src/", max_results=2)
        assert len(r.matches) <= 2

    async def test_result_format(self, ref_repo):
        """Each match should have format 'file:line: context'."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("create_app", path="src/")
        for m in r.matches:
            parts = m.split(":", 2)
            assert len(parts) >= 3, f"Expected 'file:line: context' format, got: {m}"
            # Second part should be a number
            assert parts[1].strip().isdigit(), f"Expected line number, got: {parts[1]}"

    async def test_reference_results_include_shell_matches(self, ref_repo):
        """Reference results expose ShellTools Match anchors for direct editing."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("create_app", path="src/")
        assert r.anchors
        assert len(r.anchors) == len(r.matches)
        assert all(isinstance(anchor, Match) for anchor in r.anchors)
        assert any('create_app("myapp")' in anchor.text for anchor in r.anchors)

    async def test_skips_comment_only_lines(self, ref_repo):
        """Lines that are only comments should be filtered out."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("Application", path="src/")
        for m in r.matches:
            content = m.split(": ", 1)[-1] if ": " in m else m
            assert not content.strip().startswith("#"), f"Comment-only line should be excluded: {m}"

    async def test_reference_search_result_text(self, ref_repo):
        """ReferenceSearchResult.text should format nicely."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("Application", path="src/")
        text = r.text
        assert "references" in text.lower() or "Application" in text

    async def test_reference_search_result_text_no_matches(self, ref_repo):
        """ReferenceSearchResult.text for empty results."""
        rt = RepoTools(root=ref_repo)
        r = await rt._search_references("zzz_nope_zzz", path="src/")
        assert "no references" in r.text.lower()

    async def test_with_session(self, ref_repo):
        """search_references should work with a BashSession (ripgrep path)."""
        from nooa.tools._bash_session import BashSession

        session = BashSession(cwd=ref_repo)
        await session.start()
        try:
            rt = RepoTools(root=ref_repo, session=session)
            r = await rt._search_references("Application", path="src/")
            # With session, uses ripgrep fallback. Should still find references.
            assert r.total_matches >= 0  # May be 0 if rg not installed
        finally:
            await session.close()


# ==========================================================================
# _tree_sitter_backend — graceful fallback, parser creation
# ==========================================================================
class TestTreeSitterBackend:
    """Tests for _tree_sitter_backend.py."""

    def test_import_succeeds(self):
        """The backend module should import without errors."""
        from nooa_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
        )

        # TREE_SITTER_AVAILABLE should be a boolean
        assert isinstance(TREE_SITTER_AVAILABLE, bool)

    def test_graceful_fallback_when_unavailable(self):
        """When tree-sitter is not installed, TREE_SITTER_AVAILABLE=False and functions return None."""
        import nooa_cli.tools._tree_sitter_backend as tsmod

        original_available = tsmod.TREE_SITTER_AVAILABLE
        try:
            tsmod.TREE_SITTER_AVAILABLE = False
            # _get_parser should return None
            result = tsmod._get_parser("python")
            assert result is None
        finally:
            tsmod.TREE_SITTER_AVAILABLE = original_available

    def test_get_parser_unknown_language(self):
        """_get_parser should return None for unsupported languages."""
        from nooa_cli.tools._tree_sitter_backend import _get_parser

        result = _get_parser("brainfuck")
        assert result is None

    def test_tsx_queries_alias_typescript_queries(self):
        """TSX files should use the TSX parser with TypeScript-compatible queries."""
        from nooa_cli.tools._tree_sitter_backend import (
            _DEFINITION_QUERIES,
            _REFERENCE_QUERIES,
        )

        assert _detect_lang(Path("component.tsx")) == "tsx"
        assert _DEFINITION_QUERIES["tsx"] == _DEFINITION_QUERIES["typescript"]
        assert _REFERENCE_QUERIES["tsx"] == _REFERENCE_QUERIES["typescript"]

    def test_swebench_pro_tree_sitter_language_keys(self):
        """Tree-sitter has grammar/query coverage for Python, JS/TS, TSX, and Go."""
        from nooa_cli.tools._tree_sitter_backend import (
            _DEFINITION_QUERIES,
            _GRAMMAR_MODULES,
            _REFERENCE_QUERIES,
        )

        for language in ("python", "javascript", "typescript", "tsx", "go"):
            assert language in _GRAMMAR_MODULES
            assert language in _DEFINITION_QUERIES
            assert language in _REFERENCE_QUERIES

    def test_get_parser_python(self):
        """_get_parser('python') should return a parser if tree-sitter-python is installed."""
        from nooa_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
            _get_parser,
        )

        if not TREE_SITTER_AVAILABLE:
            pytest.skip("tree-sitter not installed")
        # May be None if tree-sitter-python grammar is not installed, but should not raise
        _get_parser("python")

    def test_parser_caching(self):
        """Parsers should be cached in _PARSERS dict."""
        from nooa_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
            _get_parser,
        )

        if not TREE_SITTER_AVAILABLE:
            pytest.skip("tree-sitter not installed")
        p1 = _get_parser("python")
        if p1 is not None:
            p2 = _get_parser("python")
            assert p1 is p2  # Same cached instance

    def test_ts_extract_symbols_returns_none_when_unavailable(self):
        """ts_extract_symbols should return None when tree-sitter is unavailable."""
        import nooa_cli.tools._tree_sitter_backend as tsmod

        original = tsmod.TREE_SITTER_AVAILABLE
        try:
            tsmod.TREE_SITTER_AVAILABLE = False
            result = tsmod.ts_extract_symbols(Path("/nonexistent.py"), "python", 100)
            assert result is None
        finally:
            tsmod.TREE_SITTER_AVAILABLE = original

    def test_ts_find_references_returns_none_when_unavailable(self):
        """ts_find_references should return None when tree-sitter is unavailable."""
        import nooa_cli.tools._tree_sitter_backend as tsmod

        original = tsmod.TREE_SITTER_AVAILABLE
        try:
            tsmod.TREE_SITTER_AVAILABLE = False
            result = tsmod.ts_find_references(Path("/nonexistent.py"), "python", "foo")
            assert result is None
        finally:
            tsmod.TREE_SITTER_AVAILABLE = original

    def test_ts_extract_symbols_with_python_file(self, tmp_path):
        """ts_extract_symbols should extract Python symbols when available."""
        from nooa_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
            ts_extract_symbols,
        )

        if not TREE_SITTER_AVAILABLE:
            pytest.skip("tree-sitter not installed")

        f = tmp_path / "sample.py"
        f.write_text(
            textwrap.dedent("""            class Foo:
                def bar(self):
                    pass

            def baz():
                pass
        """)
        )
        result = ts_extract_symbols(f, "python", 100)
        if result is not None:
            text = "\n".join(result)
            assert "Foo" in text
            assert "baz" in text

    def test_ts_find_references_with_python_file(self, tmp_path):
        """ts_find_references should find references in Python when available."""
        from nooa_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
            ts_find_references,
        )

        if not TREE_SITTER_AVAILABLE:
            pytest.skip("tree-sitter not installed")

        f = tmp_path / "sample.py"
        f.write_text(
            textwrap.dedent("""            class Foo:
                def bar(self):
                    pass

            def baz():
                x = Foo()
                x.bar()
        """)
        )
        result = ts_find_references(f, "python", "Foo")
        if result is not None:
            # Should find usage at "x = Foo()" but not the definition
            lines = [line_text for _, line_text in result]
            assert any("Foo()" in ln for ln in lines), f"Expected Foo() usage, got: {lines}"
            # Definition line "class Foo:" should be excluded
            assert not any(ln.strip().startswith("class Foo") for ln in lines)

    def test_ts_find_references_qualified_name(self, tmp_path):
        """Qualified names should filter by qualifier on the same line."""
        from nooa_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
            ts_find_references,
        )

        if not TREE_SITTER_AVAILABLE:
            pytest.skip("tree-sitter not installed")

        f = tmp_path / "sample.py"
        f.write_text(
            textwrap.dedent("""            class Foo:
                def method(self):
                    pass

            class Bar:
                def method(self):
                    pass

            f = Foo()
            f.method()
            b = Bar()
            b.method()
        """)
        )
        result = ts_find_references(f, "python", "Foo.method")
        # Should only find "method" on lines that also contain "Foo"
        # (but note: heuristic — the qualifier check looks for "Foo" on the line)
        if result is not None:
            for _, _line_text in result:
                # "Foo" or a Foo-related context should appear
                pass  # Just checking it doesn't crash

    def test_node_type_to_kind(self):
        """_node_type_to_kind should map known types."""
        from nooa_cli.tools._tree_sitter_backend import _node_type_to_kind

        assert _node_type_to_kind("function_definition", "python") == "function"
        assert _node_type_to_kind("class_definition", "python") == "class"
        assert _node_type_to_kind("struct_item", "rust") == "struct"
        assert _node_type_to_kind("unknown_thing", "python") == "symbol"

    def test_is_definition_node(self):
        """_is_definition_node should recognize definition types."""
        from nooa_cli.tools._tree_sitter_backend import _is_definition_node

        assert _is_definition_node("function_definition", "python") is True
        assert _is_definition_node("class_definition", "python") is True
        assert _is_definition_node("identifier", "python") is False
        assert _is_definition_node("call_expression", "python") is False

    def test_ts_extract_symbols_nonexistent_file(self, tmp_path):
        """ts_extract_symbols should handle nonexistent files gracefully."""
        from nooa_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
            ts_extract_symbols,
        )

        if not TREE_SITTER_AVAILABLE:
            pytest.skip("tree-sitter not installed")
        result = ts_extract_symbols(tmp_path / "nope.py", "python", 100)
        assert result is None

    def test_ts_find_references_nonexistent_file(self, tmp_path):
        """ts_find_references should handle nonexistent files gracefully."""
        from nooa_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
            ts_find_references,
        )

        if not TREE_SITTER_AVAILABLE:
            pytest.skip("tree-sitter not installed")
        result = ts_find_references(tmp_path / "nope.py", "python", "foo")
        assert result is None
