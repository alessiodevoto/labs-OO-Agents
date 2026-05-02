# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Persistent bash session for SWE tools.

Maintains a long-running bash subprocess with sentinel-based output capture.
``cd``, ``export``, ``source``, and other stateful commands persist across calls.

Reliability notes (lessons from the main BashTool):
- start_new_session=True for process-group isolation
- On timeout: SIGINT to foreground process group (handles vi/less/repl hangs)
- Recovery sentinel after interrupt so session stays usable
- Process-group SIGTERM/SIGKILL on close()
"""

import asyncio
import logging
import os
import signal
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 30_000


class BashSession:
    """A persistent bash shell session.

    Usage::

        session = BashSession(cwd="/my/project")
        await session.start()
        stdout, stderr, code = await session.run("ls -la")
        stdout, stderr, code = await session.run("cd src && pwd")  # cd persists!
        await session.close()
    """

    def __init__(self, cwd: str | Path = ".") -> None:
        self._cwd = Path(cwd).resolve()
        self._process: asyncio.subprocess.Process | None = None
        self._started = False

    @property
    def cwd(self) -> Path:
        """Current working directory of the session."""
        return self._cwd

    async def start(self) -> None:
        """Start the bash subprocess."""
        if self._started:
            return

        env = os.environ.copy()
        env["PS1"] = ""
        env["TERM"] = "dumb"

        self._process = await asyncio.create_subprocess_shell(
            "/bin/bash --norc --noprofile",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._cwd),
            env=env,
            start_new_session=True,  # own process group for clean kill
        )
        self._started = True

        # Drain any shell startup output
        await self._run_raw("true", timeout=5.0)

    async def run(self, command: str, timeout: float = 120.0) -> tuple[str, str, int]:
        """Run a command and return (stdout, stderr, exit_code).

        The session persists state: ``cd``, ``export``, etc. carry over.
        After each command the session's ``.cwd`` is updated automatically.

        Args:
            command: Shell command to execute.
            timeout: Maximum seconds to wait (default 120s).

        Returns:
            Tuple of (stdout, stderr, exit_code).
        """
        if not self._started:
            await self.start()

        # Unique sentinel for this invocation
        tag = f"_ST_{id(command) & 0xFFFFFF}_"

        # Run command, capture exit code on stdout, capture pwd on stderr
        script = (
            f"{command}\n"
            f"__ec=$?\n"
            f"echo {tag} $__ec\n"  # sentinel + exit code -> stdout
            f"pwd 1>&2\n"  # cwd -> stderr
            f"echo {tag} 1>&2\n"  # sentinel -> stderr
        )

        raw_out, raw_err, timed_out = await self._run_raw(script, timeout=timeout)

        # Parse exit code from stdout
        exit_code = 0
        out_lines: list[str] = []
        for line in raw_out.split("\n"):
            if tag in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        exit_code = int(parts[-1])
                    except ValueError:
                        pass
            else:
                out_lines.append(line)
        stdout = "\n".join(out_lines).strip()

        # Parse cwd from stderr (last non-sentinel, non-empty line)
        err_lines: list[str] = []
        for line in raw_err.split("\n"):
            if tag in line:
                continue
            err_lines.append(line)

        # Last line of stderr should be the pwd output
        if err_lines:
            candidate = err_lines[-1].strip()
            if candidate and candidate.startswith("/"):
                self._cwd = Path(candidate)
                err_lines = err_lines[:-1]
        stderr = "\n".join(err_lines).strip()

        # Truncate if too long
        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
        if len(stderr) > MAX_OUTPUT_CHARS:
            stderr = stderr[:MAX_OUTPUT_CHARS] + "\n... (stderr truncated)"

        if timed_out:
            exit_code = exit_code or 124

        return stdout, stderr, exit_code

    async def _run_raw(self, script: str, timeout: float = 30.0) -> tuple[str, str, bool]:
        """Send raw script and read until sentinel. Returns (stdout, stderr, timed_out).

        On timeout (e.g. vi, less, python repl), sends SIGINT to the session's
        process group then drains recovery sentinels so the session stays usable.
        """
        proc = self._process
        if proc is None or proc.stdin is None:
            raise RuntimeError("Bash session not started")

        sentinel = f"__DONE_{id(script) & 0xFFFFFF}__"
        full = f"{script}\necho {sentinel}\necho {sentinel} 1>&2\n"
        proc.stdin.write(full.encode())
        await proc.stdin.drain()

        timed_out = False

        async def read_until_sentinel(stream: asyncio.StreamReader) -> str:
            parts: list[str] = []
            while True:
                try:
                    raw = await asyncio.wait_for(stream.readline(), timeout=timeout)
                except TimeoutError:
                    nonlocal timed_out
                    timed_out = True
                    return "".join(parts)
                if not raw:
                    return "".join(parts)  # EOF
                line = raw.decode("utf-8", errors="replace")
                if sentinel in line:
                    return "".join(parts)
                parts.append(line)

        assert proc.stdout is not None and proc.stderr is not None
        out, err = await asyncio.gather(
            read_until_sentinel(proc.stdout),
            read_until_sentinel(proc.stderr),
        )

        if timed_out:
            # Command may still be running (vi, less, python repl, etc.).
            # Send SIGINT to the session's process group to interrupt
            # the foreground job, then re-send sentinel for recovery.
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGINT)
            except (ProcessLookupError, OSError):
                pass

            await asyncio.sleep(0.2)

            # Re-send sentinel so session recovers for next run()
            recovery = f"\necho {sentinel}\necho {sentinel} 1>&2\n"
            try:
                proc.stdin.write(recovery.encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                self._started = False  # session died

            # Drain recovery sentinels (best-effort, short timeout)
            async def _drain(stream: asyncio.StreamReader) -> None:
                try:
                    while True:
                        raw = await asyncio.wait_for(stream.readline(), timeout=1.0)
                        if not raw or sentinel in raw.decode("utf-8", errors="replace"):
                            return
                except TimeoutError:
                    return

            if self._started:
                await asyncio.gather(_drain(proc.stdout), _drain(proc.stderr))

        return out.strip(), err.strip(), timed_out

    async def close(self) -> None:
        """Terminate the bash session cleanly."""
        if self._process is not None and self._process.returncode is None:
            # Kill the entire process group so child processes die too
            try:
                pgid = os.getpgid(self._process.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                # Fallback: start_new_session may not have taken
                try:
                    self._process.kill()
                except Exception:
                    pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
            except TimeoutError:
                try:
                    pgid = os.getpgid(self._process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
        self._process = None
        self._started = False
