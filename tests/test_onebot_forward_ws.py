import asyncio
import json
from http import HTTPStatus

import httpx
import pytest
from pydantic import ValidationError
from websockets.asyncio.server import serve

from accbot import FrameworkError, MessageReceived
from accbot.adapters import onebot_ws
from accbot.config import ConfigStore, OneBotConfig, Settings
from accbot.runtime import Runtime
from accbot.web import create_app
from accbot.web.schemas import OneBotDraft


def incoming(number=1):
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 100,
        "user_id": 200,
        "group_id": 300,
        "message_id": number,
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }


def endpoint(server):
    return f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://localhost:3001",
        "ws:///",
        "ws://host:invalid",
        "ws://user:secret@host/",
        "ws://host/?access_token=secret",
        "ws://host/#fragment",
        "ws://bad host/",
    ],
)
def test_forward_url_rejects_missing_address_and_embedded_credentials(url):
    for cls in (OneBotConfig, OneBotDraft):
        with pytest.raises(ValidationError):
            cls(id="main", self_id="100", mode="forward_ws", ws_url=url)


def test_forward_management_events_echo_and_backpressure(workspace, caplog):
    async def run():
        connections = asyncio.Queue()
        replied = asyncio.Event()
        ConfigStore(workspace[1]).save(Settings(plugin_directory=str(workspace[0]), event_capacity=1))
        runtime = Runtime(workspace[1])
        # A handler waiting for an action must not block reception of its echo.

        async def protocol(socket):
            await connections.put(socket)
            for frame in (
                "invalid JSON",
                "[]",
                '{"post_type":"message"}',
                json.dumps({**incoming(), "self_id": 101, "message": "do-not-log-this-message"}),
                json.dumps(incoming()),
            ):
                await socket.send(frame)
            async for frame in socket:
                action = json.loads(frame)
                assert action["action"] == "send_group_msg"
                await socket.send(json.dumps(incoming(2)))
                await socket.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "echo": action["echo"],
                            "data": {"message_id": 500},
                        }
                    )
                )

        async def handler(event):
            if event.message.ref.id == "1":
                receipt = await runtime.messages.send(event.message.conversation, "reply")
                assert receipt.messages[0].id == "500"
                replied.set()

        runtime.events.subscribe(MessageReceived, handler)
        await runtime.start()
        try:
            async with serve(protocol, "127.0.0.1", 0) as server:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://test"
                ) as client:
                    draft = {
                        "id": "main",
                        "self_id": "100",
                        "mode": "forward_ws",
                        "ws_url": endpoint(server),
                        "token": "test-ws-secret",
                    }
                    saved = await client.post("/api/adapters/onebot", json=draft)
                    assert saved.status_code == 201 and saved.json()["status"] == "inactive"
                    assert "test-ws-secret" not in saved.text
                    assert not runtime.messages.adapters and connections.empty()
                    assert ConfigStore(workspace[1]).load().onebot[0].ws_url == endpoint(server)
                    invalid = await client.post("/api/adapters/onebot", json={**draft, "ws_url": None})
                    assert invalid.status_code == 422 and "test-ws-secret" not in invalid.text
                    activated = await client.post("/api/adapters/onebot/main/activate")
                    assert activated.json()["status"] == "connecting"
                    socket = await asyncio.wait_for(connections.get(), 3)
                    assert socket.request.headers["Authorization"] == "Bearer test-ws-secret"
                    await asyncio.wait_for(replied.wait(), 3)
                    assert "OneBot self_id does not match connection" in caplog.text
                    assert "do-not-log-this-message" not in caplog.text
                    assert "test-ws-secret" not in caplog.text
                    assert (await client.get("/api/adapters/onebot")).json()[0]["status"] == "connected"
                    live = (await client.get("/api/status")).json()["connections"][0]
                    assert live["status"] == "connected" and live["websocket"]
                    assert (await client.post("/onebot/main/events", json=incoming())).status_code == 404
                    adapter = runtime.messages.adapters["main"]
                    await client.post("/api/adapters/onebot", json={"id": "other", "self_id": "101"})
                    assert runtime.messages.adapters["main"] is adapter
                    stopped = await client.post("/api/adapters/onebot/main/deactivate")
                    assert stopped.json()["status"] == "inactive"
                    await asyncio.wait_for(socket.wait_closed(), 3)
                    assert adapter.socket is None and adapter._runner is None
        finally:
            await runtime.close()

    asyncio.run(run())


def test_forward_disconnect_reconnects_without_replaying_actions(workspace, monkeypatch):
    monkeypatch.setattr(onebot_ws, "RECONNECT_DELAY", 0.01)

    async def run():
        connections, actions = asyncio.Queue(), []
        ready = asyncio.Event()

        async def protocol(socket):
            await connections.put(socket)
            async for frame in socket:
                action = json.loads(frame)
                actions.append(action["echo"])
                if action["echo"] == "lost":
                    await socket.close()
                else:
                    await socket.send(
                        json.dumps({"status": "ok", "echo": action["echo"], "data": {"ok": True}})
                    )

        async with serve(protocol, "127.0.0.1", 0) as server:
            config = OneBotConfig(id="main", self_id="100", mode="forward_ws", ws_url=endpoint(server))
            ConfigStore(workspace[1]).save(Settings(plugin_directory=str(workspace[0]), onebot=(config,)))
            runtime = Runtime(workspace[1])
            adapter = runtime.messages.adapters["main"]
            assert adapter._runner is None  # Constructing a runtime must not open sockets.
            runtime.events.subscribe(MessageReceived, lambda _: ready.set())
            await runtime.start()
            try:
                # A protocol event establishes that the client receive loop is attached.
                first = await asyncio.wait_for(connections.get(), 3)
                await first.send(json.dumps(incoming()))
                await asyncio.wait_for(ready.wait(), 3)
                ready.clear()
                with pytest.raises(FrameworkError) as failure:
                    await asyncio.wait_for(adapter.call("get_status", {}, "lost"), 3)
                assert failure.value.info.outcome == "unknown"
                second = await asyncio.wait_for(connections.get(), 3)
                await second.send(json.dumps(incoming(2)))
                await asyncio.wait_for(ready.wait(), 3)
                assert await adapter.call("get_status", {}, "next") == {"ok": True}
                assert actions == ["lost", "next"] and not adapter._pending
                await runtime.replace_connections([config.model_copy(update={"enabled": False})])
                await asyncio.wait_for(second.wait_closed(), 3)
                await asyncio.sleep(0.03)
                assert connections.empty() and adapter._runner is None
            finally:
                await runtime.close()

    asyncio.run(run())


def test_forward_auth_retry_reads_updated_secret_and_edit_replaces_connection(workspace, monkeypatch):
    monkeypatch.setattr(onebot_ws, "RECONNECT_DELAY", 0.01)
    monkeypatch.setattr(onebot_ws, "MAX_RECONNECT_DELAY", 0.02)

    async def run():
        rejected = asyncio.Event()
        connections = asyncio.Queue()

        def authenticate(connection, request):
            if request.headers.get("Authorization") != "Bearer new-test-token":
                rejected.set()
                return connection.respond(HTTPStatus.UNAUTHORIZED, "invalid token")

        async def protocol(socket):
            await connections.put(socket)
            await socket.wait_closed()

        async with serve(protocol, "127.0.0.1", 0, process_request=authenticate) as server:
            async with serve(protocol, "127.0.0.1", 0, process_request=authenticate) as replacement:
                config = OneBotConfig(
                    id="main",
                    self_id="100",
                    mode="forward_ws",
                    ws_url=endpoint(server),
                    token_env="ONEBOT_TEST",
                )
                store = ConfigStore(workspace[1])
                store.set_secret("ONEBOT_TEST", "wrong-test-token")
                store.save(Settings(plugin_directory=str(workspace[0]), onebot=(config,)))
                runtime = Runtime(workspace[1])
                await runtime.start()
                original = runtime.messages.adapters["main"]
                try:
                    await asyncio.wait_for(rejected.wait(), 3)
                    runtime.store.set_secret("ONEBOT_TEST", "new-test-token")
                    socket = await asyncio.wait_for(connections.get(), 3)
                    await runtime.replace_connections(
                        [config.model_copy(update={"ws_url": endpoint(replacement)})]
                    )
                    await asyncio.wait_for(socket.wait_closed(), 3)
                    assert original._runner is None and original.socket is None
                    new_socket = await asyncio.wait_for(connections.get(), 3)
                    assert new_socket.request.headers["Authorization"] == "Bearer new-test-token"
                finally:
                    await runtime.close()

    asyncio.run(run())


def test_shutdown_cancels_incomplete_handshake_without_waiting_for_timeout(workspace, monkeypatch):
    # OneBot endpoints are dialed directly, independently of model/HTTP proxy settings.
    monkeypatch.setenv("ws_proxy", "http://127.0.0.1:1")

    async def run():
        started, closed = asyncio.Event(), asyncio.Event()

        async def stalled(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                started.set()
                await reader.read()  # Never send the WebSocket handshake response.
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()

        async with await asyncio.start_server(stalled, "127.0.0.1", 0) as server:
            config = OneBotConfig(
                id="main", self_id="100", mode="forward_ws", ws_url=endpoint(server), timeout=30
            )
            ConfigStore(workspace[1]).save(Settings(plugin_directory=str(workspace[0]), onebot=(config,)))
            runtime = Runtime(workspace[1])
            await runtime.start()
            try:
                await asyncio.wait_for(started.wait(), 3)
            finally:
                await asyncio.wait_for(runtime.close(), 2)
            await asyncio.wait_for(closed.wait(), 2)
            assert runtime.messages.adapters["main"]._runner is None

    asyncio.run(run())
