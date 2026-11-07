import secrets

from fastapi import APIRouter, HTTPException, Request, WebSocket

from ...adapters.onebot_ws import serve_websocket


def create_router(runtime):
    router = APIRouter()

    def adapter_for(connection_id):
        adapter = runtime.messages.adapters.get(connection_id)

        if adapter is None or adapter.config.mode == "forward_ws":
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

        if (
            adapter is None
            or adapter.config.mode != "reverse_ws"
            or not authorized_adapter(adapter, socket.headers.get("authorization"))
        ):
            await socket.close(code=1008)

            return

        if socket.headers.get("x-self-id", adapter.config.self_id) != adapter.config.self_id:
            await socket.close(code=1008)

            return

        if adapter.socket is not None:
            await socket.close(code=1008)

            return

        await socket.accept()

        await serve_websocket(adapter, socket)

    return router
