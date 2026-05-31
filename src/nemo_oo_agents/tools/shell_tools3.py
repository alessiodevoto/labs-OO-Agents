# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ShellTools3 — the "lean into Python" bake-off shell.

Same anti-quoting-hell base as ShellTools2 (run(stdin=), write_file, read), but
the search + edit surface is Python-native:

* pyp is folded directly onto the shell: ``shell.rg(...)``, ``shell.cat(...)``,
  ``shell.find(...)``, ``shell.run_pipe(...)`` return chainable Streams. Search
  is a first-class Python value, not text grepped out of ``run()``.

* A new ``.matches()`` sink turns an ``rg`` Stream into structured ``Match``
  objects (exact path + byte span + line region). ``replace(match, new)`` edits
  that anchor directly — it never re-searches, so an ambiguous "matched two
  places" can't happen. ``replace(span, new)`` does the surgical byte-span case;
  ``replace(path, old, new)`` remains as a unique-or-error escape hatch.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Annotated

from nemo_oo_agents_cli.tools.pyp import sources as _pyp

from nemo_oo_agents.agentdoc import spec
from nemo_oo_agents.skill import Skill
from nemo_oo_agents.tools._bash_session import BashSession
from nemo_oo_agents.tools._match import Match, Span
from nemo_oo_agents.tools._results2 import FileWrite, ShellResult, _AwaitableResult
from nemo_oo_agents.tools.shell_tools import _strip_line_number_prefixes, _unified_diff


class ShellTools3(Skill):
    """Persistent shell + Python-native search/edit. The "lean in" bake-off variant.

    METHOD INDEX (await every call; rg/cat/find/run_pipe are sync builders):
        run(cmd, stdin=, timeout=)   -> ShellResult (str-like; forgetting await errors loudly)
        write_file(path, content)    -> FileWrite
        read(path, lines=, numbers=) -> str  (numbers=False = raw text, no gutter — safe for replace)
        lines(path, start, end)      -> Match  (line-range locator; feed to replace/pipe)
        replace(target, old, new)    -> FileWrite  (target = Match | Match.span | path)
        rg(pattern, path, **filters) -> Stream  (.matches() -> list[Match])
        cat(*paths) / find(root,**kw)/ run_pipe(cmd) -> Stream  (awaitable -> list)

    Run/file:
        run(command, stdin=, timeout=)  — run a shell command (str-like result)
        write_file(path, content)        — create/overwrite a file (no quoting)
        read(path, lines=, numbers=)     — VIEW a window (numbers=False = raw text)
        lines(path, s, e)                — LOCATE a line range -> Match (edit/pipe it)

    Search (pyp streams, chainable: .grep/.head/.tail/.map/.filter/.cut/...):
        rg(pattern, path, include="*.py" | type_filter="py" | files_only=True | ...)
                                         — ripgrep -> Stream  (NO glob=; use include=)
        cat(*paths)                      — file lines -> Stream
        find(root, name=, ...)           — file paths -> Stream
        run_pipe(cmd)                    — command stdout -> Stream
    (head/tail/skip are Stream transforms: await shell.cat("f").head(20).collect())

    Structured edit (no ambiguous matches) — locate, then replace/pipe:
        matches = await shell.rg("foo", "x.py").matches()   # list[Match] (by pattern)
        region  = await shell.lines("x.py", 5, 7)           # Match (by line range)
        await shell.replace(matches[0], "bar")              # replace the line region
        await shell.replace(region, "bar")                  # replace lines 5-7 (no off-by-one)
        await shell.replace(matches[0].span, "bar")         # replace the byte span
        await shell.replace("x.py", old, new)               # unique-or-error escape hatch
        region.text / region.numbered                       # render without / with gutter
        await region.pipe().grep("x").collect()             # stream the region's lines

    Streams are awaitable directly (no terminal verb needed for the common case):
        await shell.find("src", name="*.py")    # ≡ .collect() -> list[str]
        await shell.cat("a.py")                 # ≡ .collect() -> list[str]
        await shell.rg("foo", "x.py")           # ≡ .matches() -> list[Match]

    A finished run() result pipes too, via ``|`` (pipe-over-buffered-output):
        await (await shell.run("ps aux") | "python").collect()   # | str  => grep
        await (await shell.run("make test") | transform).collect()  # | fn => pyp transform
    (For live/unbounded output use run_pipe(), which streams as it arrives.)
    """

    __nosnapshot__ = True

    def __init__(self, cwd: str | Path = ".", *, max_anchor_mb: float = 10.0) -> None:
        self._session = BashSession(cwd=cwd)
        # Files larger than this are not loaded whole into a Match (memory + repr
        # blow-up). Default 10 MB ≈ 200k lines of source — trips only on
        # data/log/generated files. Raise it for workloads that edit big files.
        self._max_anchor_bytes = int(max_anchor_mb * 1_000_000)

    @property
    def cwd(self) -> Path:
        """Current working directory of the shell session."""
        return self._session.cwd

    def __repr__(self) -> str:
        return f"ShellTools3(cwd={str(self._session.cwd)!r})"

    # ------------------------------------------------------------------ run/file
    def run(
        self,
        command: Annotated[str, spec(description="Shell command to execute")],
        *,
        stdin: Annotated[
            str | None,
            spec(description="Text piped to the command's stdin (replaces heredocs; no quoting)."),
        ] = None,
        timeout: Annotated[float, spec(description="Max seconds before timeout")] = 30.0,
    ) -> ShellResult:
        """Run a shell command in the persistent bash session (state persists).

        Pass a script/payload as ``stdin=`` instead of a heredoc. ``await`` it —
        the result is a ``str`` subclass with ``.stdout`` / ``.stderr`` /
        ``.returncode`` / ``.success``. (Forgetting ``await`` and then touching
        ``.stdout`` raises a clear "did you forget await?" error.) For
        search/inspection prefer ``rg``/``cat``/``find``.
        """
        return _AwaitableResult(self._run_impl(command, stdin=stdin, timeout=timeout))

    async def _run_impl(self, command, *, stdin=None, timeout=30.0) -> ShellResult:
        if stdin is not None:
            b64 = base64.b64encode(stdin.encode()).decode()
            command = (
                f"__nemo_in=$(mktemp); base64 -d <<<{b64} > $__nemo_in; "
                f"({command}) < $__nemo_in; __nemo_rc=$?; rm -f $__nemo_in; "
                f"( exit $__nemo_rc )"
            )
        stdout, stderr, code, timed_out = await self._session.run_with_timeout_flag(
            command, timeout=timeout
        )
        return ShellResult(stdout=stdout, stderr=stderr, returncode=code, timed_out=timed_out)

    async def write_file(
        self,
        path: Annotated[str, spec(description="File path (relative to cwd)")],
        content: Annotated[str, spec(description="Full file content")],
    ) -> FileWrite:
        """Create or overwrite a file with ``content`` (plain string, no shell/heredoc)."""
        resolved = self._resolve(path)
        existed = resolved.exists()
        old = resolved.read_text(errors="replace") if existed else ""
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        diff = _unified_diff(path, old, content) if existed else ""
        return FileWrite(
            path=path,
            created=not existed,
            lines=content.count("\n") + (1 if content else 0),
            diff=diff,
        )

    async def read(
        self,
        path: Annotated[str, spec(description="File path (relative to cwd)")],
        lines: Annotated[
            tuple[int, int] | None,
            spec(description="(start, end) line range, 1-indexed inclusive. None = whole file."),
        ] = None,
        *,
        numbers: Annotated[
            bool,
            spec(
                description="Include the ``N| `` line-number gutter + header (True, default), "
                "or return the raw file text with no gutter (False)."
            ),
        ] = True,
    ) -> str:
        """Read a file (or a line window).

        ``numbers=True`` (default) returns a ``N| `` line-number gutter for
        *viewing*::

            await shell.read("big.py", lines=(555, 640))

        ``numbers=False`` returns the **raw text** of that window, with NO gutter
        and NO header — safe to copy into ``replace(path, old, new)`` or compare
        against file content (the gutter would otherwise corrupt the match)::

            snippet = await shell.read("big.py", lines=(555, 560), numbers=False)
            await shell.replace("big.py", snippet, new_snippet)

        For *editing* a line range, prefer ``lines(path, s, e)`` → a ``Match``
        you can ``replace()`` directly (no copy-paste, no off-by-one). Use
        ``cat()`` only to *stream/transform* lines, not to view them.
        """
        resolved = self._resolve(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        if lines is not None:
            # Windowed read — stream only the requested lines (cheap on huge files,
            # no whole-file load, no size guard needed).
            import itertools

            start = max(1, lines[0])
            with resolved.open("r", errors="replace") as fh:
                # splitlines()-consistent: strip \r\n / \n / \r like the whole-file
                # path and lines()/Match.text, so a windowed snippet matches what
                # replace() sees.
                window = [
                    ln.splitlines()[0] if ln.splitlines() else ""
                    for ln in itertools.islice(fh, start - 1, lines[1])
                ]
            end = start + len(window) - 1
            if not numbers:
                return "\n".join(window)
            width = len(str(end))
            out = [f"[{path}] lines {start}-{end}"]
            for off, ln in enumerate(window):
                out.append(f"{start + off:>{width}}| {ln}")
            return "\n".join(out)
        # Whole-file read — guarded against giant files.
        all_lines = self._read_file_lines(resolved)
        total = len(all_lines)
        if not numbers:
            return "\n".join(all_lines)
        width = len(str(total))
        out = [f"[{path}] lines 1-{total} of {total}"]
        for i in range(1, total + 1):
            out.append(f"{i:>{width}}| {all_lines[i - 1]}")
        return "\n".join(out)

    async def lines(
        self,
        path: Annotated[str, spec(description="File path (relative to cwd)")],
        start: Annotated[int, spec(description="First line, 1-indexed inclusive")],
        end: Annotated[int, spec(description="Last line, 1-indexed inclusive")],
    ) -> Match:
        """Locate a line range as a structured ``Match`` — the bridge to replace/pipe.

        Symmetric with ``rg(...).matches()``, but for a line *range* instead of a
        pattern. The returned ``Match`` carries an exact anchor, so:

            region = await shell.lines("f.py", 5, 7)
            await shell.replace(region, new_text)        # edit — no gutter, no off-by-one
            region.text       # the 3 raw lines (no gutter)
            region.numbered   # the 3 lines WITH gutter
            await region.pipe().grep("x").collect()      # stream the region's lines

        Use ``read(lines=)`` to *view* a window; use ``lines()`` when you want to
        *operate* on it (replace/pipe). Both share the ``Match`` rendering.

        Note: the byte span assumes ``\n`` line endings; on ``\r\n`` (Windows)
        files ``replace(region.span, ...)`` offsets may be off. Line-region
        ``replace(region, ...)`` is ending-agnostic — prefer it for CRLF files.
        """
        resolved = self._resolve(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        file_lines = self._read_file_lines(resolved)
        total = len(file_lines)
        s = max(1, start)
        e = min(total, end)
        # Byte span of the region (start of line s .. end of line e), for replace(region.span, ...).
        byte_start = sum(len(line.encode()) + 1 for line in file_lines[: s - 1])
        region_text = "\n".join(file_lines[s - 1 : e])
        return Match(
            path=path,
            line=s,
            end_line=e,
            col=1,
            byte_start=byte_start,
            byte_end=byte_start + len(region_text.encode()),
            matched=region_text,
            _file_lines=file_lines,
        )

    # ------------------------------------------------------------------ pyp search
    # pyp sources run in their own BashSession (default cwd "."), so paths are
    # resolved to absolute against this shell's session cwd before handoff.
    def rg(self, pattern: str, path: str = ".", **kw) -> _MatchStream:
        """Ripgrep search -> chainable Stream. Add ``.matches()`` for structured Match objects.

        Filter kwargs (forwarded to ripgrep) — there is NO ``glob=``/``-g``; use:
            include="*.py"          glob filter for files to search
            type_filter="py"        ripgrep language type (py/js/rs/go/...)
            exclude=["*_test.py"]   glob(s) to skip
            files_only=True         yield matching file paths only (-l)
            ignore_case=True        case-insensitive (-i)
            fixed=True              literal string, not regex (-F)
            context=3               N lines around each match (-C)
            max_count=N, hidden=True, no_ignore=True

        Examples::

            await shell.rg("TODO", "src", include="*.py").collect()
            await shell.rg("def parse", "src", type_filter="py").matches()
            await shell.rg("FIXME", ".", files_only=True).collect()

        Chain transforms (.grep/.head/.cut/...) then a sink (.collect/.count),
        or call .matches() for structured Match objects to edit with replace().
        """
        apath = str(self._resolve(path))
        return _MatchStream(_pyp.rg(pattern, apath, **kw), self, pattern, apath, kw)

    def cat(self, *paths: str):
        """Stream a file's lines as a chainable pipeline — prefer over ``run("cat ...")``.

        Lines flow through transforms (``.grep/.map/.filter/...``) lazily, so
        you can slice huge files without loading them whole::

            await shell.cat("app.log").grep("ERROR").head(20).collect()
        """
        return _pyp.cat(*(str(self._resolve(p)) for p in paths))

    def find(self, root: str = ".", **kw):
        """Stream matching file paths — prefer over ``run("find ...")``.

        Gitignore-aware via ripgrep. Filter with ``name=`` / ``type=``::

            await shell.find("src", name="*.py").collect()
        """
        return _pyp.find(str(self._resolve(root)), **kw)

    def run_pipe(self, cmd: str, **kw):
        """Stream a command's stdout as a chainable pipeline (true streaming).

        Use when you want to process output line-by-line as it arrives — e.g.
        watching a long build for failures — rather than ``run()`` which
        buffers the whole result::

            await shell.run_pipe("make test").grep("FAIL").collect()
        """
        kw.setdefault("cwd", str(self._session.cwd))
        return _pyp.run(cmd, **kw)

    # ------------------------------------------------------------------ structured edit
    async def replace(
        self,
        target: Annotated[
            object,
            spec(
                description="A Match (replaces its line region), a Match.span (byte span), "
                "or a file path string (then pass old, new — unique-or-error)."
            ),
        ],
        old_or_new: Annotated[
            str,
            spec(
                description="For a Match/Span: the replacement text. For a path: the text to "
                "replace (must be unique)."
            ),
        ] = "",
        new: Annotated[
            str | None,
            spec(
                description="Only with a path target: the replacement text (use '' to delete). "
                "Leave unset for a Match/Span target."
            ),
        ] = None,
    ) -> FileWrite:
        """Replace at an unambiguous location — conventional ``(old, new)`` order.

        Three forms:

        * ``replace(match, new)``      — replace the Match's line region.
        * ``replace(match.span, new)`` — replace just the matched byte span.
        * ``replace(path, old, new)``  — escape hatch; ``old`` must match exactly
          one place or this errors (no fuzzy guessing). ``new=""`` deletes.

        A Match/Span carries an exact location, so there is no re-search and no
        "matched two places" ambiguity.
        """
        if isinstance(target, Match):
            if new is not None:
                return FileWrite(
                    path=target.path,
                    success=False,
                    error="Match form takes one replacement arg: replace(match, new). "
                    "Drop the third argument.",
                )
            return self._replace_region(target.path, target.line, target.end_line, old_or_new)
        if isinstance(target, Span):
            if new is not None:
                return FileWrite(
                    path=target.path,
                    success=False,
                    error="Span form takes one replacement arg: replace(match.span, new). "
                    "Drop the third argument.",
                )
            return self._replace_bytes(target.path, target.byte_start, target.byte_end, old_or_new)
        if isinstance(target, str):
            if new is None:
                return FileWrite(
                    path=target,
                    success=False,
                    error="path form is replace(path, old, new) — the text to replace "
                    "and its replacement.",
                )
            return self._replace_unique(target, old_or_new, new)
        return FileWrite(
            path=str(target),
            success=False,
            error=f"replace target must be Match, Span, or path str, got {type(target).__name__}",
        )

    def _replace_region(self, path: str, line: int, end_line: int, new: str) -> FileWrite:
        resolved = self._resolve(path)
        if not resolved.is_file():
            return FileWrite(path=path, success=False, error=f"File not found: {path}")
        content = resolved.read_text(errors="replace")
        had_final_nl = content.endswith("\n")
        lines = content.split("\n")
        if had_final_nl:
            lines = lines[:-1]
        if not (1 <= line <= end_line <= len(lines)):
            return FileWrite(
                path=path,
                success=False,
                error=f"line region {line}-{end_line} out of range (file has {len(lines)} lines)",
            )
        new_lines = lines[: line - 1] + (new.split("\n") if new != "" else []) + lines[end_line:]
        new_content = "\n".join(new_lines) + ("\n" if had_final_nl else "")
        resolved.write_text(new_content)
        return FileWrite(
            path=path,
            lines=new_content.count("\n") + 1,
            diff=_unified_diff(path, content, new_content),
        )

    def _replace_bytes(self, path: str, start: int, end: int, new: str) -> FileWrite:
        resolved = self._resolve(path)
        if not resolved.is_file():
            return FileWrite(path=path, success=False, error=f"File not found: {path}")
        raw = resolved.read_bytes()
        new_raw = raw[:start] + new.encode() + raw[end:]
        old_text = raw.decode(errors="replace")
        new_text = new_raw.decode(errors="replace")
        resolved.write_bytes(new_raw)
        return FileWrite(
            path=path, lines=new_text.count("\n") + 1, diff=_unified_diff(path, old_text, new_text)
        )

    def _replace_unique(self, path: str, old: str, new: str) -> FileWrite:
        resolved = self._resolve(path)
        if not resolved.is_file():
            return FileWrite(path=path, success=False, error=f"File not found: {path}")
        content = resolved.read_text(errors="replace")
        old = _strip_line_number_prefixes(old)
        count = content.count(old)
        if count == 0:
            return FileWrite(
                path=path,
                success=False,
                error=f"`old` not found in {path}. Use rg(...).matches() to locate it exactly.",
            )
        if count > 1:
            return FileWrite(
                path=path,
                success=False,
                error=f"`old` matched {count} places — ambiguous. Use rg(...).matches() "
                "and replace(match, new) to pick one.",
            )
        new_content = content.replace(old, new, 1)
        resolved.write_text(new_content)
        return FileWrite(
            path=path,
            lines=new_content.count("\n") + 1,
            diff=_unified_diff(path, content, new_content),
        )

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self._session.cwd / p)

    def _read_file_lines(self, resolved: Path) -> tuple[str, ...]:
        """Load a whole file as lines for anchoring — with a large-file guard.

        Files over ``_MAX_ANCHOR_BYTES`` are refused (loading them whole into a
        Match would blow up memory). The error names the streaming escape hatch.
        """
        size = resolved.stat().st_size
        if size > self._max_anchor_bytes:
            mb = size // 1_000_000
            limit = self._max_anchor_bytes // 1_000_000
            raise ValueError(
                f"{resolved.name} is ~{mb} MB — too large to anchor as a Match "
                f"(limit {limit} MB). For big files: search with rg(...).collect() "
                f"(streams, no whole-file load), or read a bounded window with "
                f"read(path, lines=(a, b))."
            )
        return tuple(resolved.read_text(errors="replace").splitlines())


class _MatchStream:
    """Wraps a pyp rg Stream. Delegates transforms/sinks, adds ``.matches()``."""

    def __init__(self, stream, shell: ShellTools3, pattern: str, path: str, kw: dict):
        self._stream = stream
        self._shell = shell
        self._pattern = pattern
        self._path = path
        self._kw = kw
        self._matches_cache: list[Match] | None = None

    def __getattr__(self, name):
        # Delegate any pyp transform/sink (.grep/.head/.collect/.count/...).
        return getattr(self._stream, name)

    def __aiter__(self):
        return self._stream.__aiter__()

    def __await__(self):
        """Awaiting an rg stream returns structured Matches — ``await shell.rg(...)``
        ≡ ``await shell.rg(...).matches()``. Use ``.collect()`` for raw text lines."""
        return self.matches().__await__()

    async def matches(self) -> list[Match]:
        """Run the rg query with ``--json`` and return structured Match objects.

        Each Match anchors an exact (path, line, byte span). Inspect ``.text`` /
        ``.numbered``, widen with ``.context()``, then ``replace(match, new)``.

        Note: ``.matches()`` issues its OWN ``rg --json`` invocation — it does not
        reuse a plain stream you may have already collected. So call EITHER a text
        sink (``.collect()``/``.count()``) OR ``.matches()`` on a query, not both;
        mixing them shells rg twice and opens a TOCTOU window. Repeated
        ``.matches()`` calls on the same stream are memoized (rg runs once).
        """
        if self._matches_cache is not None:
            return self._matches_cache
        import shlex as _shlex

        args = ["rg", "--json", "-n"]
        if self._kw.get("ignore_case"):
            args.append("-i")
        if self._kw.get("fixed"):
            args.append("-F")
        if self._kw.get("hidden"):
            args.append("--hidden")
        if self._kw.get("no_ignore"):
            args.append("--no-ignore")
        if self._kw.get("type_filter"):
            args.append(f"-t{self._kw['type_filter']}")
        if self._kw.get("include"):
            args.append(f"-g{self._kw['include']}")
        for pat in self._kw.get("exclude", []) or []:
            args.append(f"-g!{pat}")
        if self._kw.get("max_count") is not None:
            args.append(f"-m{self._kw['max_count']}")
        # context= and files_only= are incompatible with structured matches: -C emits
        # non-match context lines and -l emits only paths, so neither yields the
        # per-match (line, span) --json records Match needs. Reject loudly rather
        # than silently dropping them.
        if self._kw.get("context"):
            raise ValueError(
                "rg(context=...).matches() is unsupported — context lines have no "
                "match anchor. Use .collect() for context, or .matches() without context=."
            )
        if self._kw.get("files_only"):
            raise ValueError(
                "rg(files_only=True).matches() is unsupported — files_only yields paths, "
                "not match anchors. Use .collect() for paths, or .matches() without files_only."
            )
        args += ["--", self._pattern, self._path]
        cmd = " ".join(_shlex.quote(a) for a in args)

        result = await self._shell.run(cmd)
        out: list[Match] = []
        file_cache: dict[str, tuple[str, ...]] = {}
        for raw_line in result.stdout.splitlines():
            if not raw_line.strip():
                continue
            try:
                d = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "match":
                continue
            data = d["data"]
            mpath = data["path"]["text"]
            line_no = data["line_number"]
            abs_off = data["absolute_offset"]
            if mpath not in file_cache:
                resolved = self._shell._resolve(mpath)
                file_cache[mpath] = self._shell._read_file_lines(resolved)
            for sm in data.get("submatches", []):
                col = sm["start"] + 1
                out.append(
                    Match(
                        path=mpath,
                        line=line_no,
                        end_line=line_no,
                        col=col,
                        byte_start=abs_off + sm["start"],
                        byte_end=abs_off + sm["end"],
                        matched=sm["match"]["text"],
                        _file_lines=file_cache[mpath],
                    )
                )
        self._matches_cache = out
        return out
