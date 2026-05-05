# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Persistent bash session for SWE tools.

Maintains a long-running bash subprocess with sentinel-based output capture.
``cd``, ``export``, ``source``, and other stateful commands persist across calls.

Reliability notes:
- create_subprocess_exec (no sh wrapper) so proc.pid IS bash directly
- start_new_session=True for process-group isolation
- On timeout: kill child processes via pgrep -P, bash survives and emits sentinels
- Env vars, cwd, aliases preserved across timeouts; falls back to reset if recovery fails
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

        self._process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "--norc",
            "--noprofile",
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

    async def run(self, command: str, timeout: float = 30.0) -> tuple[str, str, int]:
        """Run a command and return (stdout, stderr, exit_code).

        The session persists state: ``cd``, ``export``, etc. carry over.
        After each command the session's ``.cwd`` is updated automatically.

        Args:
            command: Shell command to execute.
            timeout: Maximum seconds to wait (default 30s).

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

        On timeout: kills child processes via pgrep -P and waits for bash to
        emit sentinels.  Env vars, aliases, and cwd are preserved.  Falls back
        to full session reset only if sentinel recovery fails.
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
            # Kill only child processes — bash itself survives and emits sentinels.
            recovered = await self._interrupt_and_drain(proc, sentinel, timeout)
            if not recovered:
                logger.warning(
                    "Command timed out after %.1fs and recovery failed — resetting session",
                    timeout,
                )
                await self.reset()

        return out.strip(), err.strip(), timed_out

    async def _interrupt_and_drain(
        self,
        proc: asyncio.subprocess.Process,
        sentinel: str,
        original_timeout: float,
    ) -> bool:
        """Kill child processes and drain sentinels. Returns True if session recovered.

        With create_subprocess_exec, proc.pid IS bash directly.
        pgrep -P finds the actual command. After the command dies,
        bash continues and emits our sentinels — session preserved.

        Graduated escalation: SIGTERM children → 5s → SIGINT children → 2s.
        """
        assert proc.stdout is not None and proc.stderr is not None

        async def try_drain(grace: float) -> bool:
            """Attempt to read both sentinels within *grace* seconds."""
            found = [False, False]

            async def drain_one(stream: asyncio.StreamReader, idx: int) -> None:
                while True:
                    try:
                        raw = await asyncio.wait_for(stream.readline(), timeout=grace)
                    except TimeoutError:
                        return
                    if not raw:
                        return
                    line = raw.decode("utf-8", errors="replace")
                    if sentinel in line:
                        found[idx] = True
                        return

            await asyncio.gather(drain_one(proc.stdout, 0), drain_one(proc.stderr, 1))
            return all(found)

        async def kill_children(sig: int) -> None:
            """Send signal to child processes of bash via pgrep."""
            try:
                pgrep = await asyncio.create_subprocess_exec(
                    "pgrep",
                    "-P",
                    str(proc.pid),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(pgrep.communicate(), timeout=2.0)
                if stdout:
                    for pid_str in stdout.decode().split():
                        if pid_str.strip():
                            try:
                                os.kill(int(pid_str), sig)
                            except (ProcessLookupError, OSError):
                                pass
            except (TimeoutError, OSError):
                pass

        # First attempt: SIGTERM children → wait for sentinels
        await kill_children(signal.SIGTERM)
        if await try_drain(5.0):
            logger.info(
                "Command timed out after %.1fs — killed child, session preserved",
                original_timeout,
            )
            return True

        # Second attempt: SIGKILL children → wait for sentinels
        await kill_children(signal.SIGKILL)
        if await try_drain(2.0):
            logger.info(
                "Command timed out after %.1fs — force-killed child, session preserved",
                original_timeout,
            )
            return True

        return False

    async def reset(self) -> None:
        """Kill the current session and start a fresh one, preserving cwd."""
        cwd = self._cwd
        await self.close()
        self._cwd = cwd
        await self.start()

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
