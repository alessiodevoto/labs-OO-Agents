# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for RepoTools skill."""

# Use sys.path to import from worktree
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import nooa_cli.tools.repo_tools as repo_tools_mod
from nooa_cli.tools.repo_tools import (
    RepoTools,
    _detect_lang,
    _extract_symbols,
    _symbol_anchor_pairs,
)

from nooa.tools._bash_session import BashSession
from nooa.tools.shell_tools import Match


@pytest.fixture
def sample_repo(tmp_path):
    """Create a sample repository structure."""
    # Python files
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        textwrap.dedent("""\
        import os

        class Application:
            def __init__(self, name):
                self.name = name

            def run(self):
                print(f"Running {self.name}")

            async def start(self):
                await self.setup()

        def create_app(name):
            return Application(name)

        def helper():
            pass
    """)
    )

    (tmp_path / "src" / "utils.py").write_text(
        textwrap.dedent("""\
        class Config:
            pass

        def load_config(path):
            return Config()

        def validate(data):
            return True
    """)
    )

    # Go file
    (tmp_path / "src" / "server.go").write_text(
        textwrap.dedent("""\
        package main

        type Server struct {
            Port int
        }

        type Handler interface {
            Handle()
        }

        func NewServer(port int) *Server {
            return &Server{Port: port}
        }

        func (s *Server) Start() error {
            return nil
        }
    """)
    )

    # JavaScript file
    (tmp_path / "src" / "app.js").write_text(
        textwrap.dedent("""\
        export class Router {
            constructor() {}
        }

        export function createRouter() {
            return new Router();
        }

        const handleRequest = (req) => {
            return null;
        };
    """)
    )

    # TypeScript file
    (tmp_path / "src" / "types.ts").write_text(
        textwrap.dedent("""\
        export interface User {
            name: string;
        }

        export type Config = {
            port: number;
        };

        export class Service {
            start() {}
        }
    """)
    )

    return tmp_path


# ==========================================================================
# Language detection
# ==========================================================================
class TestLanguageDetection:
    def test_python(self):
        assert _detect_lang(Path("foo.py")) == "python"

    def test_go(self):
        assert _detect_lang(Path("main.go")) == "go"

    def test_typescript(self):
        assert _detect_lang(Path("app.ts")) == "typescript"
        assert _detect_lang(Path("app.tsx")) == "tsx"

    def test_swebench_pro_languages(self):
        """SWE-bench Pro covers Python, JavaScript/TypeScript, and Go."""
        assert _detect_lang(Path("app.py")) == "python"
        assert _detect_lang(Path("app.js")) == "javascript"
        assert _detect_lang(Path("app.jsx")) == "javascript"
        assert _detect_lang(Path("app.ts")) == "typescript"
        assert _detect_lang(Path("app.tsx")) == "tsx"
        assert _detect_lang(Path("main.go")) == "go"

    def test_unknown(self):
        assert _detect_lang(Path("data.csv")) == "unknown"


# ==========================================================================
# Symbol extraction
# ==========================================================================
class TestSymbolExtraction:
    def test_python_symbols(self, sample_repo):
        symbols = _extract_symbols(sample_repo / "src" / "main.py", "python")
        text = "\n".join(symbols)
        assert "class Application" in text
        assert "function run" in text or "function __init__" in text
        assert "async function start" in text
        assert "function create_app" in text

    def test_go_symbols(self, sample_repo):
        symbols = _extract_symbols(sample_repo / "src" / "server.go", "go")
        text = "\n".join(symbols)
        assert "struct Server" in text
        assert "interface Handler" in text
        assert "function NewServer" in text

    def test_js_symbols(self, sample_repo):
        symbols = _extract_symbols(sample_repo / "src" / "app.js", "javascript")
        text = "\n".join(symbols)
        assert "class Router" in text
        assert "function createRouter" in text

    def test_ts_symbols(self, sample_repo):
        symbols = _extract_symbols(sample_repo / "src" / "types.ts", "typescript")
        text = "\n".join(symbols)
        assert "type User" in text or "interface User" in text  # interface may match as type
        assert "class Service" in text

    def test_unknown_language_returns_empty(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n1,2,3\n")
        assert _extract_symbols(f, "unknown") == []


# ==========================================================================
# filemap
# ==========================================================================
class TestFilemap:
    async def test_filemap_python(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt._filemap("src/main.py")
        assert r.language == "python"
        assert len(r.symbols) > 0
        assert "Application" in r.text

    async def test_filemap_includes_shell_match_anchors(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt._filemap("src/main.py")
        assert r.anchors
        assert len(r.anchors) == len(r.symbols)
        assert all(isinstance(anchor, Match) for anchor in r.anchors)
        assert any("class Application" in anchor.text for anchor in r.anchors)

    def test_symbol_anchor_pairs_preserve_symbol_anchor_alignment(self, sample_repo):
        fpath = sample_repo / "src" / "main.py"
        pairs = _symbol_anchor_pairs(
            fpath,
            [
                "   3 class Application",
                "not-a-line invalid symbol",
                "  13 function create_app",
            ],
        )

        assert [symbol.strip() for symbol, _ in pairs] == [
            "3 class Application",
            "13 function create_app",
        ]
        assert [anchor.start for _, anchor in pairs] == [3, 13]
        assert "class Application" in pairs[0][1].text
        assert "def create_app" in pairs[1][1].text

    async def test_filemap_go(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt._filemap("src/server.go")
        assert r.language == "go"
        assert "Server" in r.text

    async def test_filemap_nonexistent(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt._filemap("nonexistent.py")
        assert "not found" in r.text.lower() or "error" in r.text.lower()


# ==========================================================================
# repo_map
# ==========================================================================
class TestRepoMap:
    async def test_repo_map_basic(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt._repo_map(paths=["src/"])
        assert r.num_files > 0
        assert "Application" in r.summary or "main.py" in r.summary

    async def test_repo_map_includes_shell_match_anchors(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt._repo_map(paths=["src/"])
        assert r.anchors
        assert all(isinstance(anchor, Match) for anchor in r.anchors)
        assert any("class Application" in anchor.text for anchor in r.anchors)

    async def test_repo_map_with_session(self, sample_repo):
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            r = await rt._repo_map(paths=["src/"])
            assert r.num_files > 0
        finally:
            await session.close()


# ==========================================================================
# search_symbol
# ==========================================================================
class TestSearchSymbol:
    async def test_search_with_session(self, sample_repo):
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            r = await rt._search_symbol("Application", path="src/")
            assert r.total_matches > 0
            assert any("Application" in m for m in r.matches)
        finally:
            await session.close()

    async def test_search_no_results(self, sample_repo):
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            r = await rt._search_symbol("zzz_nonexistent_zzz", path="src/")
            assert r.total_matches == 0
        finally:
            await session.close()

    async def test_search_fallback_no_session(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt._search_symbol("Config")
        assert r.total_matches > 0

    async def test_search_symbol_includes_shell_match_anchors(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt._search_symbol("Config")
        assert r.anchors
        assert len(r.anchors) == len(r.matches)
        assert all(isinstance(anchor, Match) for anchor in r.anchors)
        assert any("class Config" in anchor.text for anchor in r.anchors)


# ==========================================================================
# symbols / refs preferred API
# ==========================================================================
class TestPreferredApi:
    async def test_symbols_file_returns_shelltools_matches(self, sample_repo):
        rt = RepoTools(root=sample_repo)

        r = await rt.symbols("src/main.py", query="Application")

        assert r.query == "Application"
        assert r.total_matches >= 1
        assert r.lines
        assert r.matches
        assert len(r.lines) == len(r.matches)
        assert all(isinstance(match, Match) for match in r.matches)
        assert any("class Application" in match.text for match in r.matches)
        assert "src/main.py:" in r.text

    async def test_symbols_directory_query_adapts_search_symbol(self, sample_repo):
        rt = RepoTools(root=sample_repo)

        r = await rt.symbols("src/", query="Config")

        assert r.total_matches >= 1
        assert r.lines
        assert r.matches
        assert len(r.lines) == len(r.matches)
        assert all(isinstance(match, Match) for match in r.matches)
        assert any("class Config" in match.text for match in r.matches)

    async def test_symbols_directory_without_query_returns_editable_anchors(self, sample_repo):
        rt = RepoTools(root=sample_repo)

        r = await rt.symbols("src/", max_results=5)

        assert r.lines
        assert r.matches
        assert len(r.lines) == len(r.matches)
        assert all(isinstance(match, Match) for match in r.matches)
        assert any("Application" in match.text for match in r.matches)

    async def test_refs_returns_shelltools_matches(self, sample_repo):
        rt = RepoTools(root=sample_repo)

        r = await rt.refs("Application", path="src/")

        assert r.query == "Application"
        assert r.total_matches >= 1
        assert r.lines
        assert r.matches
        assert len(r.lines) == len(r.matches)
        assert all(isinstance(match, Match) for match in r.matches)
        assert any("Application" in match.text for match in r.matches)


# ============================================================================
# constructor tree-sitter policy
# ============================================================================
class TestConstructorTreeSitterPolicy:
    def test_warns_when_tree_sitter_unavailable(self, sample_repo, monkeypatch, caplog):
        monkeypatch.setattr(repo_tools_mod, "_tree_sitter_available", lambda: False)
        caplog.set_level("WARNING", logger="nooa_cli.tools.repo_tools")

        RepoTools(root=sample_repo)

        assert "tree-sitter is not available" in caplog.text
        assert "regex/rg fallbacks" in caplog.text

    def test_require_tree_sitter_raises_when_unavailable(self, sample_repo, monkeypatch):
        monkeypatch.setattr(repo_tools_mod, "_tree_sitter_available", lambda: False)

        with pytest.raises(RuntimeError, match="tree-sitter is not available"):
            RepoTools(root=sample_repo, require_tree_sitter=True)

    def test_no_warning_when_tree_sitter_available(self, sample_repo, monkeypatch, caplog):
        monkeypatch.setattr(repo_tools_mod, "_tree_sitter_available", lambda: True)
        caplog.set_level("WARNING", logger="nooa_cli.tools.repo_tools")

        RepoTools(root=sample_repo)

        assert "tree-sitter is not available" not in caplog.text


# ============================================================================
# repr
# ============================================================================
class TestRepr:
    def test_repr(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = repr(rt)
        assert "RepoTools" in r
        assert "root=" in r


# ==========================================================================
# rg availability fallback
# ==========================================================================
class TestRgFallback:
    """Test that repo_map and search_symbol work both with and without rg."""

    async def test_repo_map_rg_path(self, sample_repo):
        """When rg is available, repo_map uses the rg code path."""
        import shutil

        if not shutil.which("rg"):
            pytest.skip("rg not installed")
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            rt._has_rg = True  # force rg path
            r = await rt._repo_map(paths=["src/"])
            assert r.num_files > 0
            assert "main.py" in r.summary or "Application" in r.summary
        finally:
            await session.close()

    async def test_repo_map_fallback_path(self, sample_repo):
        """When rg is NOT available, repo_map falls back to directory walking."""
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            rt._has_rg = False  # force fallback path
            r = await rt._repo_map(paths=["src/"])
            assert r.num_files > 0
            assert "main.py" in r.summary or "Application" in r.summary
        finally:
            await session.close()

    async def test_repo_map_no_session_uses_fallback(self, sample_repo):
        """Without a session, repo_map always uses directory walking."""
        rt = RepoTools(root=sample_repo)
        r = await rt._repo_map(paths=["src/"])
        assert r.num_files > 0

    async def test_search_symbol_rg_path(self, sample_repo):
        """When rg is available, search_symbol uses the rg code path."""
        import shutil

        if not shutil.which("rg"):
            pytest.skip("rg not installed")
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            rt._has_rg = True  # force rg path
            r = await rt._search_symbol("Application", path="src/")
            assert r.total_matches > 0
        finally:
            await session.close()

    async def test_search_symbol_fallback_path(self, sample_repo):
        """When rg is NOT available, search_symbol falls back to file walking."""
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            rt._has_rg = False  # force fallback path
            r = await rt._search_symbol("Application", path="src/")
            assert r.total_matches > 0
            assert any("Application" in m for m in r.matches)
        finally:
            await session.close()

    async def test_check_rg_caches_result(self, sample_repo):
        """_check_rg caches its result after first call."""
        rt = RepoTools(root=sample_repo)
        assert rt._has_rg is None
        result = await rt._check_rg()
        assert rt._has_rg is not None
        assert rt._has_rg == result
        # Second call uses cache (doesn't re-check)
        result2 = await rt._check_rg()
        assert result == result2
