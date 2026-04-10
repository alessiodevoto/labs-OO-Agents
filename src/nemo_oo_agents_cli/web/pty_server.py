"""PTY-based web terminal server for NeMo OO Agents.

Serves an xterm.js browser terminal that connects to a real PTY running the
NeMo OO Agents TUI, with a rich content side-channel for plots/HTML.

Endpoints:
  GET  /        — xterm.js UI (HTML embedded)
  WS   /ws/pty  — PTY I/O bridge (base64-framed JSON)
  WS   /ws/rich — rich content push (agent → browser)
  POST /rich    — HTTP endpoint the agent POSTs rich payloads to
"""


import asyncio
import base64
import json
import logging
import os
import threading
from typing import Any

_log = logging.getLogger("nemo_oo_agents.pty_server")

# ---------------------------------------------------------------------------
# Embedded HTML UI
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>NeMo OO Agents — Terminal</title>
<!-- xterm.js -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css"/>
<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
<!-- Plotly for rich output -->
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<!-- marked.js for markdown -->
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:      #1e1e2e;
    --surface: #181825;
    --overlay: #313244;
    --text:    #cdd6f4;
    --subtext: #a6adc8;
    --blue:    #89b4fa;
    --green:   #a6e3a1;
    --red:     #f38ba8;
    --yellow:  #f9e2af;
    --mauve:   #cba6f7;
  }

  html, body {
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    overflow: hidden;
  }

  #layout {
    display: flex;
    height: 100vh;
    gap: 0;
  }

  /* ---- Terminal pane ---- */
  #term-pane {
    flex: 1 1 0;
    display: flex;
    flex-direction: column;
    min-width: 0;
    background: var(--bg);
  }

  #term-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: var(--surface);
    border-bottom: 1px solid var(--overlay);
    font-size: 12px;
    color: var(--subtext);
    flex-shrink: 0;
  }

  #term-header .dot { width: 10px; height: 10px; border-radius: 50%; }
  .dot-red    { background: var(--red); }
  .dot-yellow { background: var(--yellow); }
  .dot-green  { background: var(--green); }
  #term-title { flex: 1; text-align: center; }

  #terminal {
    flex: 1 1 0;
    min-height: 0;
    padding: 4px;
  }

  /* status bar at top-right */
  #status-bar {
    position: fixed;
    top: 8px;
    right: 8px;
    font-size: 11px;
    color: var(--subtext);
    pointer-events: none;
    z-index: 100;
  }
</style>
</head>
<body>
<div id="layout">
  <div id="term-pane">
    <div id="term-header">
      <span class="dot dot-red"></span>
      <span class="dot dot-yellow"></span>
      <span class="dot dot-green"></span>
      <span id="term-title">NeMo OO Agents</span>
    </div>
    <div id="terminal"></div>
  </div>
</div>
<div id="status-bar"></div>

<script>
// ---- Catppuccin Mocha xterm theme ----
const MOCHA = {
  background:          '#1e1e2e',
  foreground:          '#cdd6f4',
  cursor:              '#f5e0dc',
  cursorAccent:        '#1e1e2e',
  selectionBackground: '#585b7066',
  black:               '#45475a',
  red:                 '#f38ba8',
  green:               '#a6e3a1',
  yellow:              '#f9e2af',
  blue:                '#89b4fa',
  magenta:             '#f5c2e7',
  cyan:                '#94e2d5',
  white:               '#bac2de',
  brightBlack:         '#585b70',
  brightRed:           '#f38ba8',
  brightGreen:         '#a6e3a1',
  brightYellow:        '#f9e2af',
  brightBlue:          '#89b4fa',
  brightMagenta:       '#f5c2e7',
  brightCyan:          '#94e2d5',
  brightWhite:         '#a6adc8',
};

// ---- xterm init ----
const term = new Terminal({
  theme: MOCHA,
  fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
  fontSize: 14,
  lineHeight: 1.2,
  cursorBlink: true,
  allowProposedApi: true,
  scrollback: 10000,
});
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.open(document.getElementById('terminal'));
fitAddon.fit();

window.addEventListener('resize', () => fitAddon.fit());

// ---- PTY WebSocket ----
const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
const ptyWs  = new WebSocket(`${proto}//${location.host}/ws/pty`);
const richWs = new WebSocket(`${proto}//${location.host}/ws/rich`);

ptyWs.binaryType = 'arraybuffer';

const statusEl = document.getElementById('status-bar');
function setStatus(msg, color) {
  statusEl.textContent = msg;
  statusEl.style.color = color || 'var(--subtext)';
}

ptyWs.onopen = () => {
  setStatus('connected', 'var(--green)');
  setTimeout(() => { statusEl.textContent = ''; }, 2000);
  // Send initial size
  sendResize();
};

ptyWs.onclose = () => setStatus('disconnected — refresh to reconnect', 'var(--red)');
ptyWs.onerror = () => setStatus('connection error', 'var(--red)');

ptyWs.onmessage = (ev) => {
  let msg;
  try { msg = JSON.parse(ev.data); } catch { return; }
  if (msg.t === 'o') {
    term.write(Uint8Array.from(atob(msg.d), c => c.charCodeAt(0)));
  }
};

// Keyboard input → PTY
term.onData(data => {
  if (ptyWs.readyState !== WebSocket.OPEN) return;
  const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(data)));
  ptyWs.send(JSON.stringify({t: 'i', d: b64}));
});

// Resize → PTY
function sendResize() {
  if (ptyWs.readyState !== WebSocket.OPEN) return;
  ptyWs.send(JSON.stringify({t: 'r', cols: term.cols, rows: term.rows}));
}
term.onResize(({cols, rows}) => {
  ptyWs.send(JSON.stringify({t: 'r', cols, rows}));
});

// ---- Rich WebSocket ----
richWs.onmessage = (ev) => {
  let payload;
  try { payload = JSON.parse(ev.data); } catch { return; }
  handleRich(payload);
};

// ---- Inline rendering via xterm.js Decoration API ----
// registerMarker(0) bookmarks the current cursor row in the scroll buffer.
// We write blank rows to reserve space, then registerDecoration overlays an
// HTML element at that marker — it scrolls with the terminal content.

function estimateRichRows(payload) {
  switch (payload.kind) {
    case 'plotly':   return 30;
    case 'html':     return 20;
    case 'image':    return 18;
    case 'markdown': return 10;
    case 'json':     return 10;
    default:         return 8;
  }
}

function renderPayloadInto(payload, el) {
  switch (payload.kind) {
    case 'plotly': {
      const div = document.createElement('div');
      div.style.cssText = 'width:100%;height:100%;';
      el.appendChild(div);
      setTimeout(() => {
        try {
          const fig = JSON.parse(payload.figure_json);
          Plotly.newPlot(div, fig.data, Object.assign({}, fig.layout, {
            paper_bgcolor: 'transparent',
            plot_bgcolor:  'transparent',
            font: { color: '#cdd6f4' },
            margin: { t: 40, b: 20, l: 40, r: 10 },
          }), { responsive: true, displayModeBar: false });
        } catch(e) { el.textContent = `Plotly error: ${e}`; }
      }, 0);
      break;
    }
    case 'html': {
      const isFullPage = /^\s*(<html|<!doctype)/i.test(payload.html);
      if (isFullPage) {
        const iframe = document.createElement('iframe');
        iframe.style.cssText = 'width:100%;height:100%;border:none;';
        iframe.sandbox = 'allow-scripts allow-same-origin';
        iframe.srcdoc = payload.html;
        el.appendChild(iframe);
      } else {
        const shadow = el.attachShadow({ mode: 'open' });
        const style = document.createElement('style');
        style.textContent = ':host{display:block;padding:8px;color:#cdd6f4;font-size:13px;font-family:monospace}';
        shadow.appendChild(style);
        const div = document.createElement('div');
        div.innerHTML = payload.html;
        shadow.appendChild(div);
      }
      break;
    }
    case 'image': {
      const img = document.createElement('img');
      img.src = payload.src;
      img.alt = payload.alt || '';
      img.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;display:block;margin:4px auto;';
      el.appendChild(img);
      break;
    }
    case 'markdown': {
      const div = document.createElement('div');
      div.className = 'md-render';
      div.style.cssText = 'padding:8px;font-size:13px;line-height:1.5;color:#cdd6f4;';
      div.innerHTML = marked.parse(payload.text || '');
      el.appendChild(div);
      break;
    }
    case 'json': {
      const pre = document.createElement('pre');
      pre.style.cssText = 'padding:8px;font-size:12px;color:#cdd6f4;overflow:auto;margin:0;';
      try { pre.textContent = JSON.stringify(payload.data, null, 2); }
      catch { pre.textContent = String(payload.data); }
      el.appendChild(pre);
      break;
    }
  }
}

function renderRichInline(payload) {
  if (payload.kind === 'clear') return;
  const numRows = estimateRichRows(payload);
  // Bookmark the current cursor row before writing blank lines
  const marker = term.registerMarker(0);
  if (!marker) return;
  // Reserve space — write numRows+1 blank rows: numRows for the decoration,
  // +1 buffer row so the decoration bottom never abuts terminal text directly.
  term.write('\r\n'.repeat(numRows + 1));
  // Overlay an HTML element at the marker, spanning the reserved rows
  const dec = term.registerDecoration({ marker, height: numRows, width: term.cols, layer: 'top' });
  if (!dec) { marker.dispose(); return; }
  dec.onRender((el) => {
    if (el.hasChildNodes()) return; // already populated (called on each repaint)
    el.style.cssText = 'overflow:hidden;background:var(--surface);border:1px solid var(--overlay);border-radius:4px;box-sizing:border-box;';
    renderPayloadInto(payload, el);
  });
}

function handleRich(payload) {
  renderRichInline(payload);
}

// Focus terminal on click
document.getElementById('terminal').addEventListener('click', () => term.focus());
term.focus();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------


def create_pty_app(
    tui_argv: list[str], env_extra: dict[str, str] | None = None
) -> "tuple[Any, Any]":
    """Create the FastAPI PTY terminal application.

    Returns:
        A ``(app, kill_all)`` tuple.  ``kill_all()`` is a synchronous callable
        that terminates every active PTY process — call it from a SIGINT handler
        so connections drain before uvicorn's graceful shutdown timer expires.

    Args:
        tui_argv: Command + arguments to spawn in the PTY (e.g. ``["python", "-m", ...]``).
        env_extra: Extra environment variables to inject into the PTY process.
    """
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import HTMLResponse, JSONResponse
        from fastapi.websockets import WebSocket, WebSocketDisconnect  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "Web terminal requires FastAPI. Install with: uv add fastapi uvicorn[standard]"
        ) from e

    try:
        import ptyprocess  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "Web terminal requires ptyprocess. Install with: uv add ptyprocess"
        ) from e

    from contextlib import asynccontextmanager

    # Track all live PTY processes so the lifespan handler can kill them on shutdown.
    _active_procs: set = set()
    _procs_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        # Shutdown: kill all PTY subprocesses so they don't linger as orphans.
        with _procs_lock:
            procs = list(_active_procs)
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass

    app = FastAPI(title="NeMo OO Agents Terminal", docs_url=None, redoc_url=None, lifespan=lifespan)

    # Rich content state shared across all browser connections to this server.
    # _rich_history: ordered list of payloads (up to _RICH_HISTORY_LIMIT).
    #   - populated from live POSTs (including replayed events on resume).
    #   - replayed to each new browser that connects to /ws/rich (handles
    #     page reload without restarting the TUI).
    _RICH_HISTORY_LIMIT = 200
    _rich_history: list[dict] = []
    _rich_subscribers: set[asyncio.Queue] = set()
    _rich_lock = threading.Lock()

    def _add_rich_subscriber(q: asyncio.Queue) -> None:
        with _rich_lock:
            _rich_subscribers.add(q)

    def _remove_rich_subscriber(q: asyncio.Queue) -> None:
        with _rich_lock:
            _rich_subscribers.discard(q)

    async def _broadcast_rich(payload: dict) -> None:
        with _rich_lock:
            subs = list(_rich_subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # Slow consumer; drop rather than block

    # -----------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(_HTML)

    # -----------------------------------------------------------------------
    @app.post("/rich")
    async def rich_post(request: Request):
        """Agent POSTs rich payloads here; forwarded to all /ws/rich subscribers."""
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        # Store in history for page-reload replay (skip "clear" — nothing to replay)
        if payload.get("kind") != "clear":
            with _rich_lock:
                _rich_history.append(payload)
                if len(_rich_history) > _RICH_HISTORY_LIMIT:
                    del _rich_history[0]
        else:
            with _rich_lock:
                _rich_history.clear()
        await _broadcast_rich(payload)
        return JSONResponse({"ok": True})

    # -----------------------------------------------------------------------
    @app.websocket("/ws/rich")
    async def rich_ws(websocket: WebSocket):
        await websocket.accept()
        # Replay stored history before subscribing to live updates so a
        # page reload restores the rich panel without restarting the TUI.
        with _rich_lock:
            history_snapshot = list(_rich_history)
        for payload in history_snapshot:
            try:
                await websocket.send_json(payload)
            except Exception:
                return
        q: asyncio.Queue = asyncio.Queue(maxsize=128)
        _add_rich_subscriber(q)
        try:
            while True:
                payload = await q.get()
                await websocket.send_json(payload)
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            _remove_rich_subscriber(q)
            try:
                await websocket.close()
            except BaseException:
                pass

    # -----------------------------------------------------------------------
    @app.websocket("/ws/pty")
    async def pty_ws(websocket: WebSocket):
        """Bridge the browser xterm.js ↔ a real PTY process."""
        import ptyprocess

        await websocket.accept()

        loop = asyncio.get_running_loop()

        # Queue of bytes chunks from the PTY → browser
        pty_out_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)

        # Merge base env with extras
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        # Spawn PTY
        try:
            proc = ptyprocess.PtyProcess.spawn(
                tui_argv,
                env=env,
                dimensions=(24, 80),
            )
        except Exception as exc:
            _log.error("Failed to spawn PTY process %s: %s", tui_argv, exc)
            try:
                msg = f"\r\n\x1b[31mFailed to start terminal: {exc}\x1b[0m\r\n"
                await websocket.send_json({"t": "o", "d": _b64(msg.encode())})
            except Exception:
                pass
            await websocket.close()
            return

        # Background thread: read PTY output → asyncio queue
        def _read_loop():
            while True:
                try:
                    data = proc.read(4096)
                    if data:
                        loop.call_soon_threadsafe(pty_out_q.put_nowait, data)
                except EOFError:
                    break
                except Exception as exc:
                    _log.debug("PTY read: %s", exc)
                    break
            loop.call_soon_threadsafe(pty_out_q.put_nowait, None)  # sentinel

        threading.Thread(target=_read_loop, daemon=True).start()
        with _procs_lock:
            _active_procs.add(proc)

        # Forward PTY output → WebSocket
        async def _forward_output():
            while True:
                chunk = await pty_out_q.get()
                if chunk is None:
                    break  # PTY EOF
                try:
                    await websocket.send_json({"t": "o", "d": _b64(chunk)})
                except Exception:
                    break

        # Receive WebSocket messages → PTY input / resize
        async def _handle_input():
            try:
                while True:
                    raw = await websocket.receive_text()
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    t = msg.get("t")
                    if t == "i":
                        data = base64.b64decode(msg["d"])
                        try:
                            proc.write(data)
                        except Exception:
                            return
                    elif t == "r":
                        cols = int(msg.get("cols", 80))
                        rows = int(msg.get("rows", 24))
                        try:
                            proc.setwinsize(rows, cols)
                        except Exception:
                            pass
            except Exception:
                pass

        # Run both sides; cancel the other when either finishes
        out_task = asyncio.create_task(_forward_output())
        inp_task = asyncio.create_task(_handle_input())
        try:
            _done, pending = await asyncio.wait(
                [out_task, inp_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            with _procs_lock:
                _active_procs.discard(proc)
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass

    def kill_all() -> None:
        """Terminate every active PTY subprocess (call on SIGINT/SIGTERM)."""
        with _procs_lock:
            procs = list(_active_procs)
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass

    return app, kill_all


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
