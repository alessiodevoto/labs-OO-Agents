"""WebFrontend — Frontend protocol implementation for the browser/WebSocket layer.

Each WebSocket connection gets its own ``WebFrontend`` instance.  All
``Output`` objects are serialised to JSON via ``output.to_json()`` and pushed
over the socket.

Pure rendering — no event subscription, no streaming state, no behavioral
decisions.  All behavior lives in ``Session``.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..tui.output import Output


class WebFrontend:
    """Frontend backed by a FastAPI WebSocket connection.

    Args:
        websocket: A ``fastapi.WebSocket`` instance (duck-typed so we avoid
                   a hard FastAPI import here).
    """

    def __init__(self, websocket: Any, *, completer: Any | None = None) -> None:
        self._ws = websocket
        self._disconnected: bool = False
        self._send_lock: asyncio.Lock = asyncio.Lock()
        self._completer = completer  # shared Completer instance (optional)

    # ------------------------------------------------------------------
    # Frontend protocol
    # ------------------------------------------------------------------

    async def render(self, output: "Output") -> None:
        """Serialise *output* to JSON and push to the browser."""
        payload = self._serialise(output)
        if payload is not None:
            await self._send(payload)

    async def get_input(self, prompt: str, completions: list[str] | None = None) -> str:
        """Request input from the browser and await the response."""
        from starlette.websockets import WebSocketDisconnect

        await self._send(
            {
                "type": "prompt_request",
                "text": prompt,
                "completions": completions or [],
            }
        )
        while True:
            try:
                raw = await self._ws.receive_text()
            except WebSocketDisconnect:
                self._disconnected = True
                raise EOFError("WebSocket disconnected") from None
            # Successful read proves the connection is alive — clear any
            # transient send-error flag so the session doesn't get killed.
            self._disconnected = False
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                # treat bare text as a user message
                return raw.strip()
            if msg.get("type") == "user_input":
                return (msg.get("text") or "").strip()
            # Handle completion requests inline
            if msg.get("type") == "completion_request" and self._completer:
                text = msg.get("text", "")
                try:
                    items = self._completer.complete(text)
                except Exception:
                    items = []
                await self._send(
                    {
                        "type": "completion_response",
                        "items": [
                            {"text": it.text, "display": it.display, "description": it.description}
                            for it in items
                        ],
                    }
                )
                continue
            # ignore other frame types (e.g. ping) while waiting

    async def start_thinking(self, message: str = "NeMo OO Agents is thinking...") -> None:
        await self._send({"type": "thinking_start", "message": message})

    async def stop_thinking(self) -> None:
        await self._send({"type": "thinking_stop"})

    async def open_editor(
        self, filename: str, content: str, language: str = "plaintext"
    ) -> str | None:
        """Send an editor_open frame, await editor_save or editor_cancel."""
        from starlette.websockets import WebSocketDisconnect

        await self._send(
            {
                "type": "editor_open",
                "filename": filename,
                "content": content,
                "language": language,
            }
        )
        while True:
            try:
                raw = await self._ws.receive_text()
            except WebSocketDisconnect:
                self._disconnected = True
                return None
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "editor_save":
                return msg.get("content", "")
            if msg.get("type") == "editor_cancel":
                return None
            # ignore other frames (ping, etc.) while editor is open

    @property
    def is_connected(self) -> bool:
        """Whether the WebSocket is still alive."""
        return not self._disconnected

    def close(self) -> None:
        """No-op — WebSocket cleanup handled by the server."""
        pass

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise(output: "Output") -> dict:
        """Delegate to the Output's own ``to_json()`` — single source of truth."""
        return output.to_json()

    # ------------------------------------------------------------------
    # Low-level send
    # ------------------------------------------------------------------

    async def _send(self, payload: dict) -> None:
        async with self._send_lock:
            try:
                await self._ws.send_text(json.dumps(payload))
            except Exception as exc:
                import logging

                logging.getLogger("nemo_oo_agents.web").debug(
                    "WebSocket send failed (%s): %s", type(exc).__name__, exc
                )
                self._disconnected = True
