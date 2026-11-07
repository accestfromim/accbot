"""Shared OneBot WebSocket ingress and the outgoing connection loop."""

import asyncio
import json
import logging

from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from ..errors import FrameworkError

log = logging.getLogger(__name__)
RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0


class ClientSocket:
    """Use the same JSON interface for client and ASGI WebSockets."""

    def __init__(self, socket):
        self.socket = socket

    async def send_json(self, value):
        await self.socket.send(json.dumps(value, ensure_ascii=False))

    async def receive_json(self):
        return json.loads(await self.socket.recv())

    async def close(self, code=1000):
        await self.socket.close(code=code)


async def serve_websocket(adapter, socket):
    adapter.attach(socket)
    incoming = asyncio.Queue(maxsize=128)

    async def forward_events():
        while True:
            raw = await incoming.get()
            try:
                await adapter.ingest(raw)
            except FrameworkError as exc:
                log.warning(
                    "OneBot event rejected: %s (%s): %s",
                    adapter.config.id,
                    exc.info.code,
                    exc.info.message,
                )
            except Exception as exc:
                log.warning("Invalid OneBot event: %s (%s)", adapter.config.id, type(exc).__name__)
            finally:
                incoming.task_done()

    forwarder = asyncio.create_task(forward_events())
    try:
        while True:
            try:
                raw = await socket.receive_json()
            except (ValueError, UnicodeError):
                log.warning("Invalid OneBot JSON: %s", adapter.config.id)
                continue
            if not isinstance(raw, dict):
                log.warning("OneBot frame is not an object: %s", adapter.config.id)
                continue
            if "echo" in raw and "post_type" not in raw:
                # A blocked event handler may itself be waiting for this response.
                await adapter.ingest(raw)
            else:
                try:
                    incoming.put_nowait(raw)
                except asyncio.QueueFull:
                    log.error("OneBot ingress capacity exceeded: %s", adapter.config.id)
                    await socket.close(code=1013)
                    break
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    finally:
        adapter.detach(socket)
        forwarder.cancel()
        await asyncio.gather(forwarder, return_exceptions=True)


async def run_forward(adapter):
    delay = RECONNECT_DELAY
    while True:
        try:
            headers = {}
            if adapter.config.token_env:
                headers["Authorization"] = "Bearer " + adapter.secret_resolver(adapter.config.token_env)
            async with connect(
                adapter.config.ws_url,
                additional_headers=headers,
                proxy=None,
                open_timeout=adapter.config.timeout,
                close_timeout=3,
                max_size=8 * 1024 * 1024,
            ) as socket:
                delay = RECONNECT_DELAY
                await serve_websocket(adapter, ClientSocket(socket))
        except Exception as exc:
            # Handshake responses and exception text may contain credentials; don't log them.
            log.warning("OneBot connection failed: %s (%s)", adapter.config.id, type(exc).__name__)
        adapter.retrying = True
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_RECONNECT_DELAY)
