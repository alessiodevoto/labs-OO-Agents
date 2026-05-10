# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Persistent bash session for SWE tools.

Maintains a long-running bash subprocess with sentinel-based output capture.
Uses a dedicated control file descriptor (fd 3) for sentinels so that
stdout/stderr are 100% user-owned.

Architecture:
  stdin  -> bash (commands only)
  stdout <- pure command output (no sentinel parsing)
  stderr <- pure command stderr (no sentinel parsing)
  fd 3   <- exit code + cwd + sentinel (control channel)
"""

import asyncio
import logging
import os
import secrets
import signal
import time
from collections.abc import AsyncIterator
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 30_000
_DRAIN_TIMEOUT = 0.05  # Seconds to wait for remaining output after sentinel
_ACCUMULATE_POLL = 0.1  # Seconds between stream read attempts during execution
_SIGTERM_GRACE = 5.0  # Seconds to wait for sentinel after SIGTERM
_SIGKILL_GRACE = 2.0  # Seconds to wait for sentinel after SIGKILL


class BashSession:
    """A persistent bash shell session with dedicated control channel.

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
        self._control_reader: asyncio.StreamReader | None = None
        self._control_transport: asyncio.BaseTransport | None = None
        self._started = False
        self._last_successful_command: float = 0.0
        self._last_command: str = ""
        self._start_count: int = 0

    @property
    def cwd(self) -> Path:
        """Current working directory of the session."""
        return self._cwd

    def _diagnose_death(self, context: str) -> str:
        """Capture diagnostic info about why bash died. Logs at ERROR level."""
        proc = self._process
        parts = [f"[BASH_DEATH] context={context}"]
        parts.append(f"  last_successful_cmd_ago={time.time() - self._last_successful_command:.1f}s")
        parts.append(f"  last_command={self._last_command[:200]!r}")
        parts.append(f"  start_count={self._start_count}")
        parts.append(f"  cwd={self._cwd}")
        if proc is None:
            parts.append("  proc=None")
        else:
            parts.append(f"  proc.pid={proc.pid}")
            parts.append(f"  proc.returncode={proc.returncode}")
            if proc.returncode is not None and proc.returncode < 0:
                sig_num = -proc.returncode
                try:
                    sig_name = signal.Signals(sig_num).name
                except (ValueError, AttributeError):
                    sig_name = f"signal {sig_num}"
                parts.append(f"  killed_by={sig_name}")
            # Try to read /proc/<pid>/status before it disappears
            try:
                with open(f"/proc/{proc.pid}/status") as f:
                    for line in f:
                        if any(k in line for k in ("State:", "SigPnd:", "SigCgt:")):
                            parts.append(f"  /proc/status: {line.strip()}")
            except (FileNotFoundError, PermissionError, OSError):
                parts.append("  /proc/status: unavailable (process reaped)")
        # Check cwd accessibility (detects virtiofs / mount failures)
        try:
            os.stat(str(self._cwd))
            parts.append("  cwd_stat=OK")
        except OSError as e:
            parts.append(f"  cwd_stat=FAILED: {e}")
        # FD count of parent process
        try:
            fd_count = len(os.listdir("/proc/self/fd"))
            parts.append(f"  parent_fd_count={fd_count}")
        except OSError:
            pass
        diag = "\n".join(parts)
        logger.error(diag)
        return diag

    async def start(self) -> None:
        """Start the bash subprocess with a dedicated control fd."""
        if self._started:
            return

        self._start_count += 1
        env = os.environ.copy()
        env["PS1"] = ""
        env["TERM"] = "dumb"

        # Create pipe for control channel (fd 3 inside bash).
        ctrl_r, ctrl_w = os.pipe()
        try:
            self._process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "--norc",
                "--noprofile",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._cwd),
                env=env,
                start_new_session=True,
                pass_fds=(ctrl_w,),
            )
        except Exception:
            os.close(ctrl_r)
            os.close(ctrl_w)
            raise

        # Dup the write end to fd 3 inside bash, then close the original.
        assert self._process.stdin is not None
        self._process.stdin.write(f"exec 3>&{ctrl_w} {ctrl_w}>&-\n".encode())
        await self._process.stdin.drain()
        os.close(ctrl_w)

        # Wrap the read end in an asyncio StreamReader.
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=2**20)
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            os.fdopen(ctrl_r, "rb", 0),
        )
        self._control_reader = reader
        self._control_transport = transport
        self._started = True

        # Drain startup — send a no-op through the control channel.
        sentinel = f"__CTRL_{secrets.token_hex(8)}__"
        self._process.stdin.write(f"echo {sentinel} >&3\n".encode())
        await self._process.stdin.drain()
        await self._read_control_until(sentinel, timeout=5.0)

    async def run(self, command: str, timeout: float = 30.0) -> tuple[str, str, int]:
        """Run a command and return (stdout, stderr, exit_code).

        The session persists state: cd, export, etc. carry over.
        """
        if not self._started:
            await self.start()

        self._last_command = command
        sentinel = f"__CTRL_{secrets.token_hex(8)}__"

        # Command runs normally; exit code + cwd + sentinel go to fd 3.
        script = f"{command}\n_nemo_ec=$?\necho $_nemo_ec >&3\npwd >&3\necho {sentinel} >&3\n"

        ctrl_lines, stdout, stderr, timed_out = await self._send_and_wait(script, sentinel, timeout)

        # Parse control channel: [exit_code, cwd]
        # Empty ctrl_lines means bash died (EOF on control fd) → non-zero exit.
        exit_code = -1 if not ctrl_lines else 0
        if ctrl_lines:
            try:
                exit_code = int(ctrl_lines[0].strip())
            except (ValueError, IndexError):
                pass
            if len(ctrl_lines) >= 2:
                candidate = ctrl_lines[1].strip()
                if candidate.startswith("/"):
                    self._cwd = Path(candidate)

        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
        if len(stderr) > MAX_OUTPUT_CHARS:
            stderr = stderr[:MAX_OUTPUT_CHARS] + "\n... (stderr truncated)"

        if timed_out:
            exit_code = 124
        else:
            self._last_successful_command = time.time()

        return stdout.strip(), stderr.strip(), exit_code

    async def run_stream(
        self, command: str, timeout: float = 30.0
    ) -> AsyncIterator[tuple[str, str]]:
        """Run a command and yield (stream_name, chunk) pairs as output arrives.

        stream_name is 'stdout' or 'stderr'. After the command finishes,
        yields ('__done__', exit_code_str). Caller must interpret that sentinel.
        """
        if not self._started:
            await self.start()

        sentinel = f"__CTRL_{secrets.token_hex(8)}__"
        script = f"{command}\n_nemo_ec=$?\necho $_nemo_ec >&3\npwd >&3\necho {sentinel} >&3\n"

        proc = self._process
        ctrl = self._control_reader
        if proc is None or proc.stdin is None or ctrl is None or proc.returncode is not None:
            await self.reset()
            proc = self._process
            ctrl = self._control_reader
            if proc is None or proc.stdin is None or ctrl is None:
                raise RuntimeError("Bash session failed to restart")

        try:
            proc.stdin.write(script.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            await self.reset()
            proc = self._process
            ctrl = self._control_reader
            if proc is None or proc.stdin is None or ctrl is None:
                raise RuntimeError("Bash session failed to restart") from None
            proc.stdin.write(script.encode())
            await proc.stdin.drain()

        assert proc.stdout is not None and proc.stderr is not None

        # Read stdout/stderr concurrently, yielding chunks as they arrive,
        # while watching the control fd for the sentinel.
        stdout_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        stderr_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

        async def _read_stream(stream, name, queue):
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(stream.read(4096), timeout=_ACCUMULATE_POLL)
                    except TimeoutError:
                        continue
                    if not chunk:
                        break
                    queue.put_nowait((name, chunk.decode("utf-8", errors="replace")))
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            finally:
                queue.put_nowait(None)

        stdout_task = asyncio.create_task(_read_stream(proc.stdout, "stdout", stdout_queue))
        stderr_task = asyncio.create_task(_read_stream(proc.stderr, "stderr", stderr_queue))

        ctrl_lines, timed_out = await self._read_control_until(sentinel, timeout)

        # Sentinel received — cancel readers and drain remaining.
        stdout_task.cancel()
        stderr_task.cancel()
        for task in (stdout_task, stderr_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Drain queues
        for q in (stdout_queue, stderr_queue):
            while not q.empty():
                item = q.get_nowait()
                if item is not None:
                    yield item

        # Greedy-drain remaining pipe data
        for stream, name in [(proc.stdout, "stdout"), (proc.stderr, "stderr")]:
            while True:
                try:
                    chunk = await asyncio.wait_for(stream.read(4096), timeout=_DRAIN_TIMEOUT)
                    if not chunk:
                        break
                    yield (name, chunk.decode("utf-8", errors="replace"))
                except (TimeoutError, Exception):
                    break

        # Parse exit code
        exit_code = -1 if not ctrl_lines else 0
        if ctrl_lines:
            try:
                exit_code = int(ctrl_lines[0].strip())
            except (ValueError, IndexError):
                pass
            if len(ctrl_lines) >= 2:
                candidate = ctrl_lines[1].strip()
                if candidate.startswith("/"):
                    self._cwd = Path(candidate)

        if timed_out:
            exit_code = 124

        yield ("__done__", f"{exit_code},{1 if timed_out else 0}")

    async def _send_and_wait(
        self, script: str, sentinel: str, timeout: float
    ) -> tuple[list[str], str, str, bool]:
        """Write script to stdin; drain stdout/stderr while waiting for sentinel.

        Drains stdout and stderr concurrently with reading the control fd to
        prevent pipe deadlock on commands producing large output (>64KB).

        Returns (control_lines, stdout, stderr, timed_out).
        Auto-resets on dead process or broken pipe.
        """
        proc = self._process
        ctrl = self._control_reader
        if proc is None or proc.stdin is None or ctrl is None or proc.returncode is not None:
            self._diagnose_death("send_and_wait_pre_check")
            logger.warning("Bash process dead or missing — resetting session")
            await self.reset()
            proc = self._process
            ctrl = self._control_reader
            if proc is None or proc.stdin is None or ctrl is None:
                raise RuntimeError("Bash session failed to restart")

        try:
            proc.stdin.write(script.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            self._diagnose_death(f"send_and_wait_write: {e}")
            logger.warning("Pipe error writing to bash (%s) — resetting session", e)
            await self.reset()
            proc = self._process
            ctrl = self._control_reader
            if proc is None or proc.stdin is None or ctrl is None:
                raise RuntimeError("Bash session failed to restart") from e
            try:
                proc.stdin.write(script.encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as e2:
                diag = self._diagnose_death(f"send_and_wait_retry: {e2}")
                raise RuntimeError(
                    f"Bash session recovery failed — process died twice.\n{diag}"
                ) from e2

        # Drain stdout/stderr concurrently with control fd to prevent deadlock.
        assert proc.stdout is not None and proc.stderr is not None
        stdout_buf: list[bytes] = []
        stderr_buf: list[bytes] = []

        async def accumulate(stream: asyncio.StreamReader, buf: list[bytes]) -> None:
            """Read from stream until EOF or external cancellation."""
            while True:
                try:
                    chunk = await asyncio.wait_for(stream.read(65536), timeout=_ACCUMULATE_POLL)
                    if not chunk:
                        return
                    buf.append(chunk)
                except TimeoutError:
                    continue
                except asyncio.CancelledError:
                    return
                except Exception:
                    return

        stdout_task = asyncio.create_task(accumulate(proc.stdout, stdout_buf))
        stderr_task = asyncio.create_task(accumulate(proc.stderr, stderr_buf))

        ctrl_lines, timed_out = await self._read_control_until(sentinel, timeout)

        # Cancel accumulators FIRST to avoid concurrent StreamReader access.
        # StreamReader does not support multiple concurrent readers.
        stdout_task.cancel()
        stderr_task.cancel()
        for task in (stdout_task, stderr_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Now greedy-drain remaining output (sole reader per stream, safe).
        for stream, buf in [(proc.stdout, stdout_buf), (proc.stderr, stderr_buf)]:
            while True:
                try:
                    chunk = await asyncio.wait_for(stream.read(65536), timeout=_DRAIN_TIMEOUT)
                    if not chunk:
                        break
                    buf.append(chunk)
                except (TimeoutError, Exception):
                    break

        stdout = b"".join(stdout_buf).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_buf).decode("utf-8", errors="replace")
        return ctrl_lines, stdout, stderr, timed_out

    async def _read_control_until(self, sentinel: str, timeout: float) -> tuple[list[str], bool]:
        """Read lines from control fd until sentinel. Returns (lines, timed_out)."""
        ctrl = self._control_reader
        assert ctrl is not None
        lines: list[str] = []
        timed_out = False
        while True:
            try:
                raw = await asyncio.wait_for(ctrl.readline(), timeout=timeout)
            except TimeoutError:
                timed_out = True
                break
            if not raw:
                self._diagnose_death("control_fd_eof")
                break  # EOF — bash died
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if sentinel in line:
                break
            lines.append(line)

        if timed_out:
            proc = self._process
            if proc is not None:
                recovered = await self._interrupt_and_recover(proc, sentinel, timeout)
                if not recovered:
                    self._diagnose_death("timeout_recovery_failed")
                    logger.warning("Timeout recovery failed — resetting session")
                    await self.reset()

        return lines, timed_out

    async def _interrupt_and_recover(
        self,
        proc: asyncio.subprocess.Process,
        sentinel: str,
        original_timeout: float,
    ) -> bool:
        """Kill child processes and wait for sentinel on control fd.

        Graduated: SIGTERM children -> 5s -> SIGINT bash -> 2s.
        """
        ctrl = self._control_reader
        assert ctrl is not None

        async def try_drain(grace: float) -> bool:
            while True:
                try:
                    raw = await asyncio.wait_for(ctrl.readline(), timeout=grace)
                except TimeoutError:
                    return False
                if not raw:
                    return False
                if sentinel in raw.decode("utf-8", errors="replace"):
                    return True

        async def kill_children(sig: int) -> None:
            killed_any = False
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
                                killed_any = True
                            except (ProcessLookupError, OSError):
                                pass
            except (TimeoutError, OSError, FileNotFoundError):
                pass
            if not killed_any:
                # SIGINT to bash (like Ctrl-C) to break pending reads.
                try:
                    os.kill(proc.pid, signal.SIGINT)
                except (ProcessLookupError, OSError):
                    pass

        await kill_children(signal.SIGTERM)
        if await try_drain(_SIGTERM_GRACE):
            return True
        await kill_children(signal.SIGKILL)
        if await try_drain(_SIGKILL_GRACE):
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
        if self._control_transport is not None:
            self._control_transport.close()
            self._control_transport = None
        self._control_reader = None

        if self._process is not None and self._process.returncode is None:
            try:
                pgid = os.getpgid(self._process.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
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
