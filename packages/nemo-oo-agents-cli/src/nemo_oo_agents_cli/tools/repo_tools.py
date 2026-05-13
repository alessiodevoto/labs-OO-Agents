# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""RepoTools — repository analysis and code intelligence.

Provides tools for understanding codebases at a higher level than individual
file operations: repository mapping, file structure overviews, and symbol search.

Attach to an agent::

    class MyAgent(Agent, llm=llm):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.repo = RepoTools(root="/path/to/repo")
"""

import logging
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from nemo_oo_agents.agentdoc import spec
from nemo_oo_agents.skill import Skill
from nemo_oo_agents.tools._bash_session import BashSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class FileMapResult:
    path: Annotated[str, spec(description="File path")]
    language: Annotated[str, spec(description="Detected language")]
    symbols: Annotated[
        list[str], spec(description="Formatted symbol lines (name, kind, line number)")
    ]
    truncated: Annotated[bool, spec(description="True if symbols were capped")] = False

    @property
    def text(self) -> str:
        if not self.symbols:
            return f"[{self.path}] ({self.language}) — no symbols found"
        header = f"[{self.path}] ({self.language}, {len(self.symbols)} symbols)"
        return f"{header}\n" + "\n".join(self.symbols)

    def __str__(self) -> str:
        return self.text


@dataclass
class RepoMapResult:
    root: Annotated[str, spec(description="Repository root path")]
    summary: Annotated[str, spec(description="Formatted repo map with key files and their exports")]
    num_files: Annotated[int, spec(description="Total files analyzed")]
    truncated: Annotated[bool, spec(description="True if output was capped")] = False

    @property
    def text(self) -> str:
        return self.summary

    def __str__(self) -> str:
        return self.text


@dataclass
class SymbolSearchResult:
    query: Annotated[str, spec(description="Search query")]
    matches: Annotated[list[str], spec(description="Matching symbol lines (file:line: kind name)")]
    total_matches: Annotated[int, spec(description="Total matches found")]
    truncated: Annotated[bool, spec(description="True if results were capped")] = False

    @property
    def text(self) -> str:
        if not self.matches:
            return f'No symbols matching "{self.query}" found.'
        parts = list(self.matches)
        if self.truncated:
            parts.append(f"\n... ({self.total_matches} total, showing first {len(self.matches)})")
        else:
            parts.append(f"\n({self.total_matches} matches)")
        return "\n".join(parts)

    def __str__(self) -> str:
        return self.text


@dataclass
class ReferenceSearchResult:
    query: Annotated[str, spec(description="Symbol name searched")]
    matches: Annotated[list[str], spec(description="Reference lines (file:line: context)")]
    total_matches: Annotated[int, spec(description="Total references found")]
    truncated: Annotated[bool, spec(description="True if results were capped")] = False

    @property
    def text(self) -> str:
        if not self.matches:
            return f'No references to "{self.query}" found.'
        parts = list(self.matches)
        if self.truncated:
            parts.append(f"\n... ({self.total_matches} total, showing first {len(self.matches)})")
        else:
            parts.append(f"\n({self.total_matches} references)")
        return "\n".join(parts)

    def __str__(self) -> str:
        return self.text


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------
_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "bash",
    ".lua": "lua",
    ".r": "r",
}

# Regex patterns for extracting symbols per language
_SYMBOL_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"^(\s*)def\s+(\w+)\s*\(", "function"),
        (r"^(\s*)async\s+def\s+(\w+)\s*\(", "async function"),
        (r"^(\s*)class\s+(\w+)", "class"),
    ],
    "javascript": [
        (r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^\s*(?:export\s+)?class\s+(\w+)", "class"),
        (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(", "function"),
    ],
    "typescript": [
        (r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^\s*(?:export\s+)?class\s+(\w+)", "class"),
        (r"^\s*(?:export\s+)?(?:interface|type)\s+(\w+)", "type"),
        (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(", "function"),
    ],
    "go": [
        (r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", "function"),
        (r"^type\s+(\w+)\s+struct", "struct"),
        (r"^type\s+(\w+)\s+interface", "interface"),
    ],
    "rust": [
        (r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", "function"),
        (r"^\s*(?:pub\s+)?struct\s+(\w+)", "struct"),
        (r"^\s*(?:pub\s+)?trait\s+(\w+)", "trait"),
        (r"^\s*(?:pub\s+)?enum\s+(\w+)", "enum"),
        (r"^\s*impl(?:<[^>]*>)?\s+(\w+)", "impl"),
    ],
    "java": [
        (
            r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:abstract\s+)?class\s+(\w+)",
            "class",
        ),
        (
            r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:abstract\s+)?interface\s+(\w+)",
            "interface",
        ),
        (r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\]]+\s+(\w+)\s*\(", "method"),
    ],
    "ruby": [
        (r"^\s*def\s+(\w+)", "method"),
        (r"^\s*class\s+(\w+)", "class"),
        (r"^\s*module\s+(\w+)", "module"),
    ],
}

# Add common aliases
_SYMBOL_PATTERNS["tsx"] = _SYMBOL_PATTERNS["typescript"]
_SYMBOL_PATTERNS["jsx"] = _SYMBOL_PATTERNS["javascript"]
_SYMBOL_PATTERNS["cpp"] = [
    (r"^\s*(?:virtual\s+)?[\w:]+\s+(\w+)\s*\(", "function"),
    (r"^\s*class\s+(\w+)", "class"),
    (r"^\s*struct\s+(\w+)", "struct"),
    (r"^\s*namespace\s+(\w+)", "namespace"),
]
_SYMBOL_PATTERNS["c"] = _SYMBOL_PATTERNS["cpp"]


def _detect_lang(path: Path) -> str:
    return _LANG_MAP.get(path.suffix.lower(), "unknown")


def _extract_symbols(path: Path, lang: str, max_symbols: int = 200) -> list[str]:
    """Extract symbol definitions from a file using tree-sitter AST (with regex fallback)."""
    # Try tree-sitter first (AST-aware, more accurate)
    try:
        from nemo_oo_agents_cli.tools._tree_sitter_backend import (
            TREE_SITTER_AVAILABLE,
            ts_extract_symbols,
        )

        if TREE_SITTER_AVAILABLE:
            ts_result = ts_extract_symbols(path, lang, max_symbols)
            if ts_result is not None:
                return ts_result
    except ImportError:
        pass

    # Fallback: regex-based extraction
    patterns = _SYMBOL_PATTERNS.get(lang, [])
    if not patterns:
        return []

    try:
        text = path.read_text(errors="replace")
    except (OSError, PermissionError):
        return []

    symbols: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if len(symbols) >= max_symbols:
            break
        for pattern, kind in patterns:
            m = re.match(pattern, line)
            if m:
                # Get the name from the last capturing group
                groups = [g for g in m.groups() if g is not None]
                name = groups[-1] if groups else "?"
                indent = len(line) - len(line.lstrip())
                prefix = "  " * (indent // 4) if indent > 0 else ""
                symbols.append(f"  {i:4d} {prefix}{kind} {name}")
                break
    return symbols


# ---------------------------------------------------------------------------
# RepoTools Skill
# ---------------------------------------------------------------------------
class RepoTools(Skill):
    """Repository analysis and code intelligence.

    Tools for understanding codebases: file structure overviews,
    repository maps showing key files and their symbols, cross-file
    symbol search, and reference finding.

    Tools:
        filemap(path)              — show file symbols (functions, classes)
        repo_map(paths, depth)     — overview of key files and their exports
        search_symbol(name)        — find function/class definitions across files
        search_references(name)    — find call sites / usages of a symbol
    """

    __nosnapshot__ = True

    def __init__(self, root: str | Path = ".", session: BashSession | None = None) -> None:
        self._root = Path(root).resolve()
        self._session = session  # shared session with ShellTools (optional)

    def __repr__(self) -> str:
        return f"RepoTools(root={str(self._root)!r})"

    # ------------------------------------------------------------------
    # filemap — show symbols in a single file
    # ------------------------------------------------------------------
    async def filemap(self, path: str, max_symbols: int = 200) -> FileMapResult:
        """Show the structure of a file: function and class definitions with line numbers.

        Useful for getting an overview of a file without reading every line.
        Shows definitions but not their bodies.

        Args:
            path: File path (relative to repo root).
            max_symbols: Maximum symbols to show (default: 200).

        Returns:
            FileMapResult with symbol listing.

        Examples:
            r = await self.repo.filemap("src/main.py")
            r = await self.repo.filemap("internal/llm/tools/edit.go")
        """
        resolved = self._resolve(path)
        if not resolved.is_file():
            from nemo_oo_agents.runtime.harness_metrics import get_harness_metrics

            get_harness_metrics().repo_failure(
                "filemap:file_not_found", f"File not found: {path}", path
            )
            return FileMapResult(
                path=path, language="unknown", symbols=[f"Error: {path} not found"]
            )

        lang = _detect_lang(resolved)
        symbols = _extract_symbols(resolved, lang, max_symbols=max_symbols)
        truncated = len(symbols) >= max_symbols

        return FileMapResult(
            path=path,
            language=lang,
            symbols=symbols,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # repo_map — overview of repository structure and key symbols
    # ------------------------------------------------------------------
    async def repo_map(
        self,
        paths: list[str] | None = None,
        depth: int = 3,
        max_files: int = 50,
        max_symbols_per_file: int = 20,
    ) -> RepoMapResult:
        """Generate a repository map showing key files and their top-level symbols.

        Scans the repository (or specified paths) and produces a concise
        overview of the most important files and their exports/definitions.
        Files are sorted by relevance (recently modified first).

        Args:
            paths: Specific directories to map (default: repo root).
            depth: Directory depth to scan (default: 3).
            max_files: Maximum files to include (default: 50).
            max_symbols_per_file: Max symbols per file in the map (default: 20).

        Returns:
            RepoMapResult with formatted repository overview.

        Examples:
            r = await self.repo.repo_map()
            r = await self.repo.repo_map(paths=["src/", "lib/"])
            r = await self.repo.repo_map(max_files=100, depth=4)
        """
        # Find source files, sorted by modification time (newest first)
        search_paths = paths or ["."]
        all_files: list[Path] = []

        for sp in search_paths:
            resolved = self._resolve(sp)
            if not resolved.is_dir():
                continue
            # Use rg to find files respecting gitignore, or fall back to walking
            if self._session:
                stdout, _, _ = await self._session.run(
                    f"rg --files --sort modified {shlex.quote(str(resolved))} 2>/dev/null | head -{max_files * 3}",
                    timeout=15,
                )
                for line in stdout.splitlines():
                    p = Path(line.strip())
                    if p.is_file() and _detect_lang(p) != "unknown":
                        all_files.append(p)
            else:
                # Fallback: walk directory
                for ext in _LANG_MAP:
                    for p in sorted(resolved.rglob(f"*{ext}"))[:max_files]:
                        all_files.append(p)

        # Deduplicate and limit
        seen: set[str] = set()
        unique_files: list[Path] = []
        for f in all_files:
            key = str(f.resolve())
            if key not in seen:
                seen.add(key)
                unique_files.append(f)
        unique_files = unique_files[:max_files]

        # Build the map
        sections: list[str] = []
        for fpath in unique_files:
            try:
                rel = fpath.relative_to(self._root)
            except ValueError:
                rel = fpath
            lang = _detect_lang(fpath)
            symbols = _extract_symbols(fpath, lang, max_symbols=max_symbols_per_file)
            if symbols:
                sections.append(f"\n{rel}:")
                sections.extend(symbols)
            else:
                sections.append(f"\n{rel}: ({lang})")

        summary = "\n".join(sections).strip()
        if not summary:
            from nemo_oo_agents.runtime.harness_metrics import get_harness_metrics

            get_harness_metrics().repo_failure(
                "repo_map:no_files", "No source files found", str(search_paths)[:200]
            )
            summary = "(no source files found)"

        return RepoMapResult(
            root=str(self._root),
            summary=summary,
            num_files=len(unique_files),
            truncated=len(all_files) > max_files,
        )

    # ------------------------------------------------------------------
    # search_symbol — find definitions across the codebase
    # ------------------------------------------------------------------
    async def search_symbol(
        self,
        name: str,
        path: str = ".",
        max_results: int = 50,
    ) -> SymbolSearchResult:
        """Search for function, class, or type definitions by name.

        Searches across all source files for symbol definitions matching
        the query (case-insensitive substring match).

        Args:
            name: Symbol name or partial name to search for.
            path: Directory to search (default: repo root).
            max_results: Maximum results (default: 50).

        Returns:
            SymbolSearchResult with matching definitions.

        Examples:
            r = await self.repo.search_symbol("calculate_score")
            r = await self.repo.search_symbol("Handler", path="src/")
        """
        resolved = self._resolve(path)
        matches: list[str] = []

        # Use grep to find definition patterns matching the name
        if self._session:
            # Build a regex that matches common definition patterns
            pattern = f"(def|class|function|func|struct|trait|interface|type|impl|module|const)\\s+\\w*{re.escape(name)}\\w*"
            stdout, _, code = await self._session.run(
                f"rg -n -i --color=never {shlex.quote(pattern)} {shlex.quote(str(resolved))} 2>/dev/null | head -{max_results * 2}",
                timeout=30,
            )
            if stdout:
                for line in stdout.splitlines():
                    if line.strip():
                        matches.append(line.strip())
        else:
            # Fallback: walk files and check symbols
            name_lower = name.lower()
            for ext in _LANG_MAP:
                for fpath in resolved.rglob(f"*{ext}"):
                    lang = _detect_lang(fpath)
                    symbols = _extract_symbols(fpath, lang, max_symbols=200)
                    for sym in symbols:
                        if name_lower in sym.lower():
                            try:
                                rel = fpath.relative_to(self._root)
                            except ValueError:
                                rel = fpath
                            matches.append(f"{rel}:{sym.strip()}")
                            if len(matches) >= max_results:
                                break
                    if len(matches) >= max_results:
                        break
                if len(matches) >= max_results:
                    break

        total = len(matches)
        if total == 0:
            from nemo_oo_agents.runtime.harness_metrics import get_harness_metrics

            get_harness_metrics().repo_failure(
                "search_symbol:no_results", f"No matches for '{name}'", f"path={path}"
            )
        truncated = total >= max_results
        return SymbolSearchResult(
            query=name,
            matches=matches[:max_results],
            total_matches=total,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # search_references — find call sites / usages
    # ------------------------------------------------------------------
    async def search_references(
        self,
        name: Annotated[
            str,
            spec(
                description="Symbol name to find references for (e.g. 'from_file' or 'TraceExplorer.from_file')"
            ),
        ],
        path: Annotated[str, spec(description="Directory to search")] = ".",
        max_results: Annotated[int, spec(description="Maximum results")] = 50,
    ) -> ReferenceSearchResult:
        """Find all references (call sites, usages) of a symbol — excludes definitions.

        Uses tree-sitter AST analysis when available for accurate results,
        falling back to ripgrep with heuristic filtering.

        Args:
            name: Symbol name or qualified name (e.g. 'TraceExplorer.from_file').
            path: Directory to search (default: repo root).
            max_results: Maximum results (default: 50).

        Returns:
            ReferenceSearchResult with matching reference lines.

        Examples:
            r = await self.repo.search_references("from_file")
            r = await self.repo.search_references("TraceExplorer.from_file")
        """
        resolved = self._resolve(path)

        # Try tree-sitter first for accurate AST-aware reference finding
        try:
            from nemo_oo_agents_cli.tools._tree_sitter_backend import (
                TREE_SITTER_AVAILABLE,
                ts_find_references,
            )

            if TREE_SITTER_AVAILABLE:
                all_matches: list[str] = []
                for fpath in self._iter_source_files(resolved, max_files=500):
                    lang = _detect_lang(fpath)
                    if lang == "unknown":
                        continue
                    refs = ts_find_references(
                        fpath, lang, name, max_results=max_results - len(all_matches)
                    )
                    if refs:
                        rel = fpath.relative_to(self._root)
                        for line_no, context in refs:
                            all_matches.append(f"{rel}:{line_no}: {context}")
                    if len(all_matches) >= max_results:
                        break
                if all_matches:
                    truncated = len(all_matches) >= max_results
                    return ReferenceSearchResult(
                        query=name,
                        matches=all_matches[:max_results],
                        total_matches=len(all_matches),
                        truncated=truncated,
                    )
        except ImportError:
            pass

        # Fallback: ripgrep-based reference search with heuristic filtering
        search_name = name.replace(".", "\\.")  # escape dots for regex
        pattern = f"\\b{search_name}\\b"

        if self._session:
            cmd = f"rg -n --color=never {shlex.quote(pattern)} {shlex.quote(str(resolved))} 2>/dev/null | head -{max_results * 3}"
            stdout, _, code = await self._session.run(cmd, timeout=30)
            if code != 0 or not stdout:
                return ReferenceSearchResult(query=name, matches=[], total_matches=0)
            raw_lines = stdout.strip().splitlines()
        else:
            raw_lines = []
            for fpath in self._iter_source_files(resolved, max_files=200):
                try:
                    text = fpath.read_text(errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if re.search(rf"\b{re.escape(name)}\b", line):
                            rel = fpath.relative_to(self._root)
                            raw_lines.append(f"{rel}:{i}:{line}")
                except OSError:
                    continue

        # Filter out definitions, comments, and string-only lines
        def_patterns = [
            r"^\s*(def|class|async\s+def|func|fn|type|interface|struct|trait|impl|enum)\s+",
        ]
        matches: list[str] = []
        for raw in raw_lines:
            if len(matches) >= max_results:
                break
            # Parse file:line:content
            parts = raw.split(":", 2)
            if len(parts) < 3:
                continue
            content = parts[2].strip()
            # Skip definitions
            if any(re.match(p, content) for p in def_patterns):
                continue
            # Skip comment-only lines
            stripped = content.lstrip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            matches.append(f"{parts[0]}:{parts[1]}: {content}")

        truncated = len(matches) >= max_results
        return ReferenceSearchResult(
            query=name,
            matches=matches[:max_results],
            total_matches=len(matches),
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _iter_source_files(self, root: Path, max_files: int = 500):
        """Yield source files under *root*, skipping non-source and common build dirs."""
        count = 0
        for fpath in sorted(root.rglob("*")):
            if count >= max_files:
                break
            if not fpath.is_file():
                continue
            if _detect_lang(fpath) == "unknown":
                continue
            # Skip common non-source dirs
            parts = fpath.parts
            if any(
                p.startswith(".")
                or p in ("node_modules", "__pycache__", "venv", ".venv", "build", "dist")
                for p in parts
            ):
                continue
            yield fpath
            count += 1

    def _resolve(self, path: str) -> Path:
        """Resolve a path relative to the repo root."""
        p = Path(path)
        return p if p.is_absolute() else self._root / p
