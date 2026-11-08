import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from accbot import (
    FrameworkError,
    GroupMessageReceived,
    MessageBuilder,
    MessageQuote,
    MessageReceived,
    MessageRef,
    ModelItem,
)
from accbot.adapters.onebot import OneBotAdapter
from accbot.config import ConfigStore, ModelCard, OneBotConfig, Settings
from accbot.events import CustomEvent, EventBus, FileReceived, FileSent, MemberLeft, MessageSent
from accbot.models import render_items
from accbot.runtime import Runtime
from accbot.web import create_app


def incoming(**changes):
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 100,
        "user_id": 200,
        "group_id": 300,
        "message_id": 400,
        "time": 1700000000,
        "sender": {"nickname": "member", "card": "group name", "role": "member"},
        "message": [
            {"type": "text", "data": {"text": "hi"}},
            {"type": "reply", "data": {"id": 399}},
            {"type": "at", "data": {"qq": "100"}},
            {"type": "image", "data": {"file": "image-id", "url": "https://media.test/image"}},
        ],
        **changes,
    }


def test_mapping_scope_notice_file_and_custom():
    async def run():
        adapter = OneBotAdapter(OneBotConfig(id="main", self_id="100"), EventBus(), None, lambda _: "")
        event = await adapter.decode(incoming())
        assert isinstance(event, GroupMessageReceived)
        assert event.message.ref.id == "400" and event.message.reply_to == MessageQuote()
        assert event.message.member.nickname == "group name"
        assert [s.type for s in event.message.content] == ["text", "mention", "image"]
        other = OneBotAdapter(OneBotConfig(id="other", self_id="100"), EventBus(), None, lambda _: "")
        assert (await other.decode(incoming())).message.sender.ref != event.message.sender.ref
        left = await adapter.decode(
            {
                "post_type": "notice",
                "notice_type": "group_decrease",
                "sub_type": "kick",
                "group_id": 300,
                "user_id": 200,
                "operator_id": 201,
            }
        )
        assert isinstance(left, MemberLeft) and left.operator.ref.id == "201" and left.user.ref.id == "200"
        file = await adapter.decode(
            {
                "post_type": "notice",
                "notice_type": "group_upload",
                "group_id": 300,
                "user_id": 200,
                "file": {"id": "f", "name": "report", "size": 12},
            }
        )
        assert isinstance(file, FileReceived) and file.asset.size == 12
        assert isinstance(
            (await adapter.decode({"post_type": "notice", "notice_type": "notify", "x": 1})), CustomEvent
        )
        with pytest.raises(FrameworkError) as invalid:
            (await adapter.decode({"post_type": "message"}))
        assert invalid.value.info.code == "invalid_argument"

    asyncio.run(run())


def test_cq_and_array_messages_have_identical_internal_content():
    async def run():
        adapter = OneBotAdapter(OneBotConfig(id="main", self_id="100"), EventBus(), None, lambda _: "")
        cq = "hi[CQ:reply,id=399][CQ:at,qq=100][CQ:image,file=image-id,url=https://media.test/image]"
        # Asset handles are newly allocated for each decode; their platform references must match.
        exclude = {"content": {"segments": {"__all__": {"asset": {"id"}}}}}
        actual = (await adapter.decode(incoming(message=cq))).message.model_dump(exclude=exclude)
        assert actual == (await adapter.decode(incoming())).message.model_dump(exclude=exclude)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("你好，群友", "你好，群友"),
        ("&#91;CQ:at,qq=100&#93;", "[CQ:at,qq=100]"),
        ("&amp;#91; &amp;#93; &amp;amp; &#44;", "&#91; &#93; &amp; &#44;"),
        ("[CQ:at,qq]", "[CQ:at,qq]"),
        ("[CQ:at,qq=100", "[CQ:at,qq=100"),
    ],
)
def test_cq_text_escapes_and_malformed_codes_remain_text(source, expected):
    async def run():
        adapter = OneBotAdapter(OneBotConfig(id="main", self_id="100"), EventBus(), None, lambda _: "")
        content = (await adapter.decode(incoming(message=source))).message.content
        assert content == MessageBuilder().text(expected).build()

    asyncio.run(run())


def test_cq_unknown_segments_parameters_and_empty_message():
    async def run():
        adapter = OneBotAdapter(OneBotConfig(id="main", self_id="100"), EventBus(), None, lambda _: "")
        content = (
            await adapter.decode(
                incoming(
                    message="[CQ:shake][CQ:share,title=a=b&#44;\n&#91;x&#93;&amp;#44;,url=https://test/?a=b]"
                )
            )
        ).message.content
        shake, share = content
        assert shake.type == "custom" and shake.name == "onebot.shake" and shake.data == {}
        assert share.name == "onebot.share"
        assert share.data == {"title": "a=b,\n[x]&#44;", "url": "https://test/?a=b"}
        assert (await adapter.decode(incoming(message=""))).message.content.segments == ()

    asyncio.run(run())


def test_http_send_roundtrip_dedup_and_self_echo(workspace):
    requests = []

    def endpoint(request):
        if request.url.path == "/get_msg":
            return httpx.Response(200, json={"status": "failed", "retcode": 1})
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 500}})

    async def run():
        store = ConfigStore(workspace[1])
        store.save(
            Settings(
                plugin_directory=str(workspace[0]),
                onebot=(OneBotConfig(id="main", self_id="100", http_url="https://qq.test"),),
            )
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as http:
            runtime = Runtime(workspace[1], http=http)
            await runtime.start()
            inbound, sent = [], []
            runtime.events.subscribe(MessageReceived, inbound.append)
            runtime.events.subscribe(MessageSent, sent.append)
            adapter = runtime.messages.adapters["main"]
            await adapter.ingest(incoming())
            await adapter.ingest(incoming())
            source = (await adapter.decode(incoming())).message
            receipt = await runtime.messages.send(
                source.conversation, MessageBuilder().text("reply").build(), reply_to=source.ref
            )
            await adapter.ingest(incoming(message_id=500, user_id=100))
            await runtime.events.drain()
            assert len(inbound) == 1 and len(sent) == 1
            assert receipt.messages[0].id == "500"
            assert requests[0]["message"][0]["type"] == "reply"
            await runtime.close()

    asyncio.run(run())


def test_message_lookup_maps_quotes_and_rejects_wrong_conversations():
    async def run():
        from accbot.messages import MessageSender

        reply = {
            "message_type": "group",
            "message_id": 399,
            "time": 1700000000,
            "sender": {"user_id": 200, "nickname": "quoted user"},
            "message": [{"type": "text", "data": {"text": "original"}}],
        }

        def endpoint(request):
            assert request.url.path == "/get_msg"
            assert json.loads(request.content) == {"message_id": "399"}
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": reply})

        bus = EventBus()
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as http:
            adapter = OneBotAdapter(
                OneBotConfig(id="main", self_id="100", http_url="https://qq.test"), bus, http, lambda _: ""
            )
            sender = MessageSender(bus)
            sender.add_adapter(adapter)
            ref = MessageRef(conversation=adapter.conversation("300"), id="399")
            message = await sender.fetch(ref)
            assert message.ref == ref and message.content.plain_text == "original"
            reply["group_id"] = 900
            with pytest.raises(FrameworkError):
                await sender.fetch(ref)
            reply.pop("group_id")
            reply["message_type"] = "private"
            with pytest.raises(FrameworkError):
                await sender.fetch(ref)
            reply["sender"]["user_id"] = 100
            direct = ref.model_copy(update={"conversation": adapter.conversation("200", "direct")})
            message = await sender.fetch(direct)
            assert message.is_self and message.conversation.id == "200"
        await bus.close()

    asyncio.run(run())


@pytest.mark.parametrize("cq", [False, True])
@pytest.mark.parametrize("private", [False, True])
def test_ingress_delivers_single_level_quote_snapshot(cq, private):
    async def run():
        calls, received = [], []
        original = {
            "message_type": "private" if private else "group",
            "message_id": 399,
            "time": 1700000000,
            "sender": {"user_id": 100, "nickname": "[机器人名称]", "card": "群名片"},
            "message": [
                {"type": "text", "data": {"text": "原始正文"}},
                {"type": "image", "data": {"file": "image-id"}},
                {"type": "reply", "data": {"id": 398}},
            ],
        }

        def endpoint(request):
            calls.append(json.loads(request.content))
            assert request.url.path == "/get_msg"
            return httpx.Response(200, json={"status": "ok", "data": original})

        bus = EventBus()
        bus.subscribe(MessageReceived, received.append)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as http:
                adapter = OneBotAdapter(
                    OneBotConfig(id="main", self_id="100", http_url="https://qq.test"),
                    bus,
                    http,
                    lambda _: "",
                )
                raw = incoming(message_type=original["message_type"])
                if cq:
                    raw["message"] = "hi[CQ:reply,id=399]"
                await adapter.ingest(raw)
                await bus.drain()
                message = received[0].message
                assert message.conversation.id == ("200" if private else "300")
                quote = message.reply_to
                assert quote.sender.ref == adapter.connection.bot
                assert quote.sender.name == ("[机器人名称]" if private else "群名片")
                assert quote.content.display_text == "原始正文[图片]" and quote.sent_at is not None
                assert bus.decode(received[0].to_dict()).message == message
                assert calls == [{"message_id": "399"}]  # No nested lookup or asset download.
                original["message"][0]["data"]["text"] = "后来的修改"
                assert message.reply_to.content.plain_text == "原始正文"
        finally:
            await bus.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["missing", "timeout", "wrong_group", "malformed"])
def test_quote_lookup_failure_preserves_incoming_body(failure):
    async def run():
        received = []

        def endpoint(request):
            if failure == "timeout":
                raise httpx.ReadTimeout("expired")
            data = {"message_id": 399, "message_type": "group", "group_id": 999}
            if failure == "malformed":
                data = {"message_id": 399, "message_type": "group", "sender": "invalid sender"}
            return httpx.Response(
                200, json={"status": "failed" if failure == "missing" else "ok", "data": data}
            )

        bus = EventBus()
        bus.subscribe(MessageReceived, received.append)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as http:
                adapter = OneBotAdapter(
                    OneBotConfig(id="main", self_id="100", http_url="https://qq.test"),
                    bus,
                    http,
                    lambda _: "",
                )
                await adapter.ingest(incoming())
                await bus.drain()
                message = received[0].message
                assert message.content.plain_text == "hi" and message.reply_to == MessageQuote()
                rendered = render_items([ModelItem.from_message(message)])[0].content[0].text
                assert "引用原文不可用" in rendered and "399" not in rendered
        finally:
            await bus.close()

    asyncio.run(run())


def test_management_validation_secrets_and_websocket_echo(workspace):
    store = ConfigStore(workspace[1])
    store.set_secret("ADMIN", "admin-secret")
    store.set_secret("QQ", "qq-secret")
    store.save(
        Settings(
            plugin_directory=str(workspace[0]),
            management_token_env="ADMIN",
            onebot=(OneBotConfig(id="main", self_id="100", token_env="QQ"),),
        )
    )
    runtime = Runtime(workspace[1])
    headers = {"X-Accbot-Token": "admin-secret"}
    with TestClient(create_app(runtime)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/settings").status_code == 401
        assert (
            client.put("/api/secrets/KEY", headers=headers, json={"value": "model-secret"}).status_code == 200
        )
        response = client.get("/api/secrets", headers=headers)
        assert response.status_code == 200 and "model-secret" not in response.text
        assert "admin-secret" not in client.get("/api/settings", headers=headers).text
        assert (
            client.put("/api/plugins/active", headers=headers, json={"selected": ["missing"]}).status_code
            == 400
        )
        assert client.post("/onebot/main/events", json=incoming()).status_code == 401
        with client.websocket_connect("/onebot/main/ws", headers={"Authorization": "Bearer qq-secret"}) as ws:

            async def send():
                adapter = runtime.messages.adapters["main"]
                return await runtime.messages.send(adapter.conversation("300"), "hello")

            future = client.portal.start_task_soon(send)
            action = ws.receive_json()
            assert action["action"] == "send_group_msg"
            ws.send_json({"echo": action["echo"], "status": "ok", "retcode": 0, "data": {"message_id": 888}})
            assert future.result(timeout=3).messages[0].id == "888"
    reloaded = ConfigStore(workspace[1])
    assert reloaded.get_secret("KEY") == "model-secret"


def test_connection_identity_is_checked_before_send(workspace):
    async def run():
        store = ConfigStore(workspace[1])
        store.save(
            Settings(plugin_directory=str(workspace[0]), onebot=(OneBotConfig(id="main", self_id="100"),))
        )
        runtime = Runtime(workspace[1])
        adapter = runtime.messages.adapters["main"]
        wrong = adapter.conversation("300").model_copy(update={"scope": "different"})
        with pytest.raises(FrameworkError, match="identity"):
            await runtime.messages.send(wrong, "x")
        await runtime.close()

    asyncio.run(run())


def test_model_card_configuration_survives_restart(workspace):
    store = ConfigStore(workspace[1])
    card = ModelCard(id="chat", endpoint="https://test/v1", model="test", api_key_env="KEY")
    store.save(Settings(models=(card,), plugin_directory=str(workspace[0])))
    assert ConfigStore(workspace[1]).load().models == (card,)


def test_file_upload_has_its_own_receipt_event(workspace):
    from accbot import AssetRef

    requests = []

    def endpoint(request):
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": None})

    async def run():
        ConfigStore(workspace[1]).save(
            Settings(
                plugin_directory=str(workspace[0]),
                onebot=(OneBotConfig(id="main", self_id="100", http_url="https://qq.test"),),
            )
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as http:
            runtime = Runtime(workspace[1], http=http)
            await runtime.start()
            files = []
            runtime.events.subscribe(FileSent, files.append)
            adapter = runtime.messages.adapters["main"]
            receipt = await runtime.messages.send_file(
                adapter.conversation("300"), AssetRef(name="report.txt", url="https://files.test/report.txt")
            )
            await runtime.events.drain()
            assert not receipt.messages and files[0].receipt == receipt
            assert requests[0][0] == "/upload_group_file"
            assert requests[0][1]["name"] == "report.txt"
            await runtime.close()

    asyncio.run(run())


def test_websocket_echo_progresses_when_event_bus_is_full(workspace):
    ConfigStore(workspace[1]).save(
        Settings(
            plugin_directory=str(workspace[0]),
            event_capacity=1,
            onebot=(OneBotConfig(id="main", self_id="100"),),
        )
    )
    runtime = Runtime(workspace[1])
    received = []

    async def handler(event):
        if event.message.ref.id == "400":
            assert event.message.reply_to.content.plain_text == "original"
            receipt = await runtime.messages.send(event.message.conversation, "reply")
            received.append(receipt.messages[0].id)

    with TestClient(create_app(runtime)) as client:
        runtime.events.subscribe(MessageReceived, handler)
        with client.websocket_connect("/onebot/main/ws") as ws:
            ws.send_json(incoming())
            lookup = ws.receive_json()
            assert lookup["action"] == "get_msg" and lookup["params"] == {"message_id": "399"}
            ws.send_json(incoming(message_id=401, message="another"))
            ws.send_json(
                {
                    "echo": lookup["echo"],
                    "status": "ok",
                    "data": {
                        "message_id": 399,
                        "message_type": "group",
                        "message": "original",
                        "sender": {"user_id": 201},
                    },
                }
            )
            action = ws.receive_json()
            assert action["action"] == "send_group_msg"
            ws.send_json({"echo": action["echo"], "status": "ok", "retcode": 0, "data": {"message_id": 999}})
            client.portal.call(runtime.events.drain)
            assert received == ["999"]


def test_file_notification_resolves_url_only_when_read(workspace):
    requests = []

    def endpoint(request):
        requests.append(request.url.path)
        if request.url.path == "/get_group_file_url":
            return httpx.Response(
                200, json={"status": "ok", "retcode": 0, "data": {"url": "https://files.test/content"}}
            )
        return httpx.Response(200, content=b"file contents")

    async def run():
        ConfigStore(workspace[1]).save(
            Settings(
                plugin_directory=str(workspace[0]),
                onebot=(OneBotConfig(id="main", self_id="100", http_url="https://qq.test"),),
            )
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as http:
            runtime = Runtime(workspace[1], http=http)
            event = await runtime.messages.adapters["main"].decode(
                {
                    "post_type": "notice",
                    "notice_type": "group_upload",
                    "group_id": 300,
                    "user_id": 200,
                    "file": {"id": "file1", "name": "report", "size": 13, "busid": 102},
                }
            )
            assert not requests
            assert await runtime.assets.read(event.asset) == b"file contents"
            assert requests == ["/get_group_file_url", "/content"]
            await runtime.close()

    asyncio.run(run())


def test_disconnect_releases_pending_action(workspace):
    ConfigStore(workspace[1]).save(
        Settings(plugin_directory=str(workspace[0]), onebot=(OneBotConfig(id="main", self_id="100"),))
    )
    runtime = Runtime(workspace[1])
    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/onebot/main/ws") as ws:

            async def send():
                adapter = runtime.messages.adapters["main"]
                return await runtime.messages.send(adapter.conversation("300"), "hello")

            future = client.portal.start_task_soon(send)
            ws.receive_json()
        with pytest.raises(FrameworkError, match="disconnected") as error:
            future.result(timeout=3)
        assert error.value.info.outcome == "unknown"
