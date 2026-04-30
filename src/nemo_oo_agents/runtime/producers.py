# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Async producer helpers for QueueManager.spawn().

Each function returns a coroutine or async generator suitable for
``qm.spawn(producer, channel="name")``.

Usage::

    from nemo_oo_agents.runtime.producers import monitor, after, cron

    qm.spawn(monitor("make test"), channel="ci", buffer=100)
    qm.spawn(after(300), channel="wakeup")
    qm.spawn(cron(60), channel="ticks")
"""


import asyncio


async def after(delay: float) -> None:
    """One-shot timer: sleep *delay* seconds, then return None."""
    await asyncio.sleep(delay)
    return None


async def cron(interval_seconds: float):
    """Yield a tick counter at fixed intervals.

    Yields an incrementing integer every *interval_seconds*.
    """
    tick = 0
    while True:
        await asyncio.sleep(interval_seconds)
        tick += 1
        yield tick


async def tail(path: str, *, poll_interval: float = 0.5):
    """Tail a file, yielding new lines as they appear.

    Starts from the current end of file. Polls every
    *poll_interval* seconds.
    """
    fh = open(path)
    try:
        fh.seek(0, 2)
        while True:
            line = fh.readline()
            if line:
                yield line.rstrip("\n")
            else:
                await asyncio.sleep(poll_interval)
    finally:
        fh.close()


async def run_job(coro, job_id: str):
    """Wrap a coroutine result with a job_id tag.

    Returns ``{"job_id": job_id, "result": <awaited value>}``.
    """
    result = await coro
    return {"job_id": job_id, "result": result}


async def monitor(cmd: str):
    """Stream stdout lines from a shell command as they appear.

    Yields each line (stripped) as it's written to stdout.
    stderr is merged into stdout. Kills the subprocess on
    cancellation/close to prevent orphans.
    """
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async for line in proc.stdout:
            yield line.decode().rstrip("\n")
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
