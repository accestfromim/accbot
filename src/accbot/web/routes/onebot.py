import asyncio
import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


def create_router(runtime):
    router = APIRouter()

    def adapter_for(connection_id):
        adapter = runtime.messages.adapters.get(connection_id)

        if adapter is None:
            raise HTTPException(404, "connection not found")

        return adapter

    def authorized_adapter(adapter, authorization):
        name = adapter.config.token_env

        return not name or secrets.compare_digest(
            "Bearer " + runtime.store.get_secret(name), authorization or ""
        )

    @router.post("/onebot/{connection_id}/events")
    async def onebot_event(connection_id: str, request: Request):
        adapter = adapter_for(connection_id)

        if not authorized_adapter(adapter, request.headers.get("authorization")):
            raise HTTPException(401, "invalid OneBot token")

        receipt = await adapter.ingest(await request.json())

        return {"accepted": True, "event_id": receipt.event_id if receipt else None}

    @router.websocket("/onebot/{connection_id}/ws")
    async def onebot_socket(connection_id: str, socket: WebSocket):
        adapter = runtime.messages.adapters.get(connection_id)

        if adapter is None or not authorized_adapter(adapter, socket.headers.get("authorization")):
            await socket.close(code=1008)

            return

        if socket.headers.get("x-self-id", adapter.config.self_id) != adapter.config.self_id:
            await socket.close(code=1008)

            return

        if adapter.socket is not None:
            await socket.close(code=1008)

            return

        await socket.accept()

        adapter.attach(socket)

        incoming = asyncio.Queue(maxsize=128)

        async def forward_events():
            while True:
                raw = await incoming.get()

                try:
                    await adapter.ingest(raw)

                except Exception:
                    log.exception("invalid OneBot event: %s", connection_id)

                finally:
                    incoming.task_done()

        forwarder = asyncio.create_task(forward_events())

        try:
            while True:
                raw = await socket.receive_json()

                if "echo" in raw and "post_type" not in raw:
                    await adapter.ingest(raw)

                else:
                    try:
                        incoming.put_nowait(raw)

                    except asyncio.QueueFull:
                        log.error("OneBot ingress capacity exceeded: %s", connection_id)

                        await socket.close(code=1013)

                        break

        except WebSocketDisconnect:
            pass

        finally:
            adapter.detach(socket)

            forwarder.cancel()

            await asyncio.gather(forwarder, return_exceptions=True)

    return router
