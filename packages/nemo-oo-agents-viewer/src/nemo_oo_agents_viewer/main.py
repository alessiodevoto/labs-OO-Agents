"""Combined Trace + Evaluation Viewer backend.

Single FastAPI app serving both trace viewer and evaluation viewer APIs.
Serves a React SPA from FRONTEND_DIR (Vite build output) with a catch-all
for client-side routing.
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import ClientDisconnect
from starlette.staticfiles import StaticFiles

load_dotenv()

from . import FRONTEND_DIR, otlp_store  # noqa: E402
from .annotation_routes import router as annotation_router  # noqa: E402
from .eval_routes import router as eval_router  # noqa: E402
from .trace_routes import router as trace_router  # noqa: E402

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Write queue — decouple HTTP ingest latency from SQLite write latency.
#
# Under parallel eval runs, many subprocesses POST spans simultaneously.
# Calling otlp_store.ingest() directly in the async handler blocks the event
# loop on SQLite I/O, causing export timeouts and dropped spans.
#
# Instead: accept the POST into an asyncio.Queue and return 200 immediately.
# A single background task drains the queue serially — SQLite gets one writer
# at a time, the event loop is never blocked, and HTTP latency is near-zero.
# ---------------------------------------------------------------------------

_ingest_queue: asyncio.Queue[bytes] = asyncio.Queue()
_QUEUE_WARN_THRESHOLD = 500  # log a warning if the backlog grows this large

# Single-writer thread pool: exactly one thread owns the write connection.
# Using max_workers=1 ensures serial SQLite writes with no concurrent access.
# The write thread uses otlp_store._get_write_db() (a thread-local connection)
# so it never shares a connection object with the event-loop read path.
_write_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite-writer")


_INGEST_MAX_BATCH = 32  # max payloads to commit in one SQLite transaction


async def _ingest_worker() -> None:
    """Drain _ingest_queue, writing batches to SQLite in a dedicated writer thread.

    Design:
    - Awaits the first queued item (yields to event loop between batches).
    - Greedily drains up to _INGEST_MAX_BATCH additional items already in the
      queue — all committed in a single SQLite transaction, amortising WAL sync
      (db.commit()) across the batch.
    - Runs ingest_batch_write() in a single-thread executor so SQLite I/O
      never blocks the event loop.  Parallel BSP exporters can always deliver
      spans without HTTP timeouts.
    - The writer thread uses _get_write_db() (thread-local connection), separate
      from the event-loop read connection, preventing the concurrent-access
      corruption that a default thread-pool executor caused.
    """
    loop = asyncio.get_running_loop()
    while True:
        # Wait for first item — yields control to event loop
        batch = [await _ingest_queue.get()]
        # Drain additional items already waiting (non-blocking)
        while len(batch) < _INGEST_MAX_BATCH:
            try:
                batch.append(_ingest_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        remaining = _ingest_queue.qsize()
        t0 = time.monotonic()
        try:
            await loop.run_in_executor(_write_executor, otlp_store.ingest_batch_write_bytes, batch)
        except Exception:
            log.exception("[ingest_worker] Failed to write batch of %d to SQLite", len(batch))
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if remaining > 0 or elapsed_ms > 500:
                log.info(
                    "[ingest_worker] batch=%d  queued=%d  write=%.0fms",
                    len(batch),
                    remaining,
                    elapsed_ms,
                )
            for _ in batch:
                _ingest_queue.task_done()


# Suppress all successful (2xx/3xx) access logs — they're noise during eval runs.
# Errors (4xx/5xx) still appear.  Our own diagnostic log.info/warning messages
# go through the "nemo_oo_agents_viewer" logger, not "uvicorn.access", so they're unaffected.


class _QuietAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Keep 4xx/5xx responses visible
        return '" 4' in msg or '" 5' in msg


logging.getLogger("uvicorn.access").addFilter(_QuietAccessFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Frontend: %s", FRONTEND_DIR)
    log.info("Initializing SQLite trace store...")
    count = otlp_store.init_db()
    log.info("Database ready: %d sessions in %s", count, otlp_store.DB_PATH)

    worker = asyncio.create_task(_ingest_worker())
    try:
        yield
    finally:
        # Drain the queue before shutdown so in-flight spans aren't lost.
        if not _ingest_queue.empty():
            log.info("Flushing %d pending ingest(s)…", _ingest_queue.qsize())
            await _ingest_queue.join()
        worker.cancel()
        _write_executor.shutdown(wait=True)
        log.info("Shutdown complete")


app = FastAPI(title="NeMo OO Agents Viewer", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trace_router)
app.include_router(eval_router)
app.include_router(annotation_router)


# ============================================================================
# OTLP ingest endpoint
# ============================================================================


@app.post("/v1/traces")
async def otlp_ingest(request: Request):
    """Accept OTLP JSON ExportTraceServiceRequest and queue for async SQLite write.

    Returns 200 immediately — the actual SQLite write happens in a dedicated
    writer thread.  This prevents parallel eval runs from blocking the event
    loop and causing BSP export timeouts or ClientDisconnect errors.

    JSON parsing is offloaded to a thread executor so large Opus traces
    (3-5 MB payloads) don't block the event loop while parsing.
    """
    try:
        body_bytes = await request.body()
    except ClientDisconnect:
        # BSP exporter timed out and closed the connection before we read the
        # body — log at WARNING (not ERROR) since this is a transient backpressure
        # signal, not a bug.  The BSP will retry on the next export cycle.
        log.warning("[otlp_ingest] Client disconnected before body was read — BSP may retry")
        return JSONResponse(status_code=499, content={"error": "client disconnected"})
    qsize = _ingest_queue.qsize()
    if qsize >= _QUEUE_WARN_THRESHOLD:
        log.warning(
            "[otlp_ingest] Write queue backlog: %d pending — "
            "SQLite may not be keeping up with ingest rate.",
            qsize,
        )
    await _ingest_queue.put(body_bytes)
    return JSONResponse(content={"queued": True})


# ============================================================================
# Message journal endpoints
# ============================================================================


@app.post("/v1/journal/messages")
async def journal_messages_ingest(request: Request):
    """Accept a batch of content-addressed message records.

    Body: list of ``{"h": "<hash>", "msg": {<message dict>}}`` objects.
    Already-stored hashes are silently skipped.

    Offloads the SQLite write to the single-writer executor so the event
    loop is never blocked — same pattern as /v1/traces ingest.
    """
    body = await request.json()
    if not isinstance(body, list):
        return JSONResponse(
            status_code=400,
            content={"error": "Body must be a list of message objects"},
        )
    n_items = len(body)
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    result = await loop.run_in_executor(_write_executor, otlp_store.ingest_journal_messages, body)
    elapsed_ms = (time.monotonic() - t0) * 1000
    if elapsed_ms > 500:
        log.warning(
            "[journal/messages] slow write: %.0fms  items=%d  (executor backlog likely)",
            elapsed_ms,
            n_items,
        )
    return JSONResponse(content=result)


@app.post("/v1/journal/calls")
async def journal_call_ingest(request: Request):
    """Accept a single LLM call record with input/output hash lists.

    Offloads the SQLite write to the single-writer executor.
    """
    body = await request.json()
    if not isinstance(body, dict) or not body.get("call_id") or not body.get("session_id"):
        return JSONResponse(
            status_code=400,
            content={"error": "call_id and session_id are required"},
        )
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    try:
        result = await loop.run_in_executor(_write_executor, otlp_store.ingest_journal_call, body)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    elapsed_ms = (time.monotonic() - t0) * 1000
    if elapsed_ms > 500:
        log.warning(
            "[journal/calls] slow write: %.0fms  call_id=%s  (executor backlog likely)",
            elapsed_ms,
            body.get("call_id", "?"),
        )
    return JSONResponse(content=result)


@app.get("/api/traces/{session_id:path}/calls")
def get_session_calls(session_id: str):
    """Return all LLM calls for a session with fully reconstructed messages."""
    if not otlp_store.session_exists(session_id):
        return JSONResponse(status_code=404, content={"error": f"Session not found: {session_id}"})
    return JSONResponse(content=otlp_store.get_session_calls(session_id))


# ============================================================================
# Unified refresh endpoint
# ============================================================================


@app.post("/api/refresh")
def refresh_all():
    """Return current store stats."""
    stats = otlp_store.get_stats()
    return {
        "status": "ok",
        "sessions_found": stats["sessions"],
        "experiments_found": stats["experiments"],
    }


# ============================================================================
# Frontend serving — React SPA
# NOTE: The catch-all GET route MUST be registered AFTER all API GET routes.
# ============================================================================

app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")


@app.get("/{path:path}")
def spa_catchall(request: Request, path: str):
    """Serve static files if they exist, otherwise index.html for client-side routing."""
    file_path = (FRONTEND_DIR / path).resolve()
    if path and file_path.is_file() and file_path.is_relative_to(FRONTEND_DIR.resolve()):
        return FileResponse(file_path)
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("VIEWER_PORT", os.environ.get("TRACE_VIEWER_PORT", "5001")))
    log.info("Starting viewer on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
