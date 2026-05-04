# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for RepoTools skill."""

# Use sys.path to import from worktree
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nemo_oo_agents.tools._bash_session import BashSession
from nemo_oo_agents.tools.repo_tools import (
    RepoTools,
    _detect_lang,
    _extract_symbols,
)


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
        assert _detect_lang(Path("app.tsx")) == "typescript"

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
        r = await rt.filemap("src/main.py")
        assert r.language == "python"
        assert len(r.symbols) > 0
        assert "Application" in r.text

    async def test_filemap_go(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt.filemap("src/server.go")
        assert r.language == "go"
        assert "Server" in r.text

    async def test_filemap_nonexistent(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt.filemap("nonexistent.py")
        assert "not found" in r.text.lower() or "error" in r.text.lower()


# ==========================================================================
# repo_map
# ==========================================================================
class TestRepoMap:
    async def test_repo_map_basic(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt.repo_map(paths=["src/"])
        assert r.num_files > 0
        assert "Application" in r.summary or "main.py" in r.summary

    async def test_repo_map_with_session(self, sample_repo):
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            r = await rt.repo_map(paths=["src/"])
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
            r = await rt.search_symbol("Application", path="src/")
            assert r.total_matches > 0
            assert any("Application" in m for m in r.matches)
        finally:
            await session.close()

    async def test_search_no_results(self, sample_repo):
        session = BashSession(cwd=sample_repo)
        await session.start()
        try:
            rt = RepoTools(root=sample_repo, session=session)
            r = await rt.search_symbol("zzz_nonexistent_zzz", path="src/")
            assert r.total_matches == 0
        finally:
            await session.close()

    async def test_search_fallback_no_session(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = await rt.search_symbol("Config")
        assert r.total_matches > 0


# ==========================================================================
# repr
# ==========================================================================
class TestRepr:
    def test_repr(self, sample_repo):
        rt = RepoTools(root=sample_repo)
        r = repr(rt)
        assert "RepoTools" in r
        assert "root=" in r
