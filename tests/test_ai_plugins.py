import asyncio
import importlib
import io
import json
import shutil
from pathlib import Path

import httpx
import pytest
from PIL import Image
from websockets.asyncio.server import serve

from accbot import (
    AssetRef,
    ChatMessage,
    ConnectionRef,
    ConversationRef,
    GenerationResult,
    GroupMessageReceived,
    MessageBuilder,
    MessageQuote,
    MessageReceived,
    MessageRef,
    ModelItem,
    Tool,
    ToolCall,
    ToolContext,
    User,
    UserRef,
)
from accbot.config import ModelCard, OneBotConfig
from accbot.models import render_items
from accbot.runtime import Runtime
from accbot.types import SendReceipt

PLUGINS = ["ai.tools", "ai.chat"]
MEMORY = {"version": 1, "summary": "群友喜欢猫", "members": [], "facts": [], "open_tasks": []}


class Model:
    card = ModelCard(id="test", endpoint="https://unused.test/v1", model="test")

    def __init__(self, responder=None, prompts=None):
        self.prompts = prompts
        self.requests = []
        self.responder = responder
        self.summary_failures = 0
        self.summary_responder = None

    async def generate(self, items, tools=(), *, system_prompt_id=None, persona_id=None, **options):
        if system_prompt_id is not None:
            content = self.prompts.render(system_prompt_id, persona_id=persona_id, tools=tools)
            items = [ModelItem.text("system", content), *items]
        items = render_items(items)
        self.requests.append((list(items), tools, options))
        if options.get("tool_choice") == "auto":
            if self.summary_responder:
                return await self.summary_responder(items, tools, options)
            if self.summary_failures:
                self.summary_failures -= 1
                return GenerationResult(items=(ModelItem.text("assistant", "invalid memory"),))
            return GenerationResult(items=(ModelItem.text("assistant", json.dumps(MEMORY)),))
        if self.responder:
            return await self.responder(items, tools, options)
        return GenerationResult(items=(ModelItem.text("assistant", "收到喵"),))


class Adapter:
    def __init__(self, connection):
        self.connection = connection
        self.sent = []

    async def send(self, target, content, *, reply_to, request_id):
        assert reply_to is None
        self.sent.append((target, content))
        return SendReceipt(
            request_id=request_id, messages=(MessageRef(conversation=target, id=f"sent-{len(self.sent)}"),)
        )

    async def send_file(self, target, asset, *, request_id):
        self.sent.append((target, asset))
        return SendReceipt(request_id=request_id, messages=())

    async def close(self):
        pass


def event(
    number="1",
    *,
    group="100",
    connection="main",
    user="200",
    direct=False,
    mention=True,
    text=None,
    image=None,
    reply=None,
    bot_id="999",
):
    bot = UserRef(platform="qq", scope="scope", id=bot_id)
    source = ConnectionRef(id=connection, platform="qq", scope="scope", bot=bot)
    conversation = ConversationRef(
        connection_id=connection,
        platform="qq",
        scope="scope",
        id=user if direct else group,
        kind="direct" if direct else "group",
    )
    chain = MessageBuilder().text(text or number)
    if mention:
        chain.mention(bot)
    if image:
        chain.media("image", image)
    message = ChatMessage(
        ref=MessageRef(conversation=conversation, id=number),
        sender=User(ref=UserRef(platform="qq", scope="scope", id=user), name="群友"),
        content=chain.build(),
        reply_to=reply,
    )
    return (MessageReceived if direct else GroupMessageReceived)(connection=source, message=message)


@pytest.fixture
def setup_ai(workspace):
    shutil.copytree(Path(__file__).parents[1] / "plugins/ai", workspace[0] / "ai")

    async def setup(responder=None, **options):
        runtime = Runtime(workspace[1])
        await runtime.start()
        await runtime.replace_models([ModelCard(id="test", endpoint="https://unused.test/v1", model="test")])
        model = Model(responder, runtime.prompts)
        runtime.models._clients["test"] = model
        for connection in ("main", "second"):
            runtime.messages.add_adapter(Adapter(event(connection=connection).connection))
        await runtime.set_active(PLUGINS)
        plugin = runtime.plugins.instances["ai.chat"][0]
        config = {
            **plugin.config.model_dump(),
            "default_model": "test",
            "groups": [{"connection_id": "main", "group_id": "100"}],
            "initial_probability": 0,
            "busy_probability": 0,
            **options,
        }
        # Persist through the real plugin-owned writer so reload sees the same configuration.
        settings_module = importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + ".settings")
        from accbot import SettingsContext

        settings = settings_module.Settings(
            SettingsContext("ai.chat", plugin.ctx.data_dir, runtime.models, runtime.personas, runtime.prompts)
        )
        saved = await settings.write(config)
        await plugin.on_settings_changed(saved)
        return runtime, plugin, model

    return setup


async def deliver(runtime, value, *, drain=True):
    receipt = await runtime.events.publish(value)
    assert not await receipt.wait()
    if drain:
        await runtime.jobs.drain()
        await runtime.events.drain()


async def summarize(runtime, plugin, channel=None, *, reason="idle", ticket=None):
    module = importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + ".events")
    for item in [channel] if channel else tuple(plugin.sessions.channels.values()):
        current = ticket or getattr(item, f"{reason}_ticket")
        if current:
            receipt = await plugin.ctx.publish(
                module.SummarizeMemory(
                    memory_key=item.store.key,
                    ticket=current,
                    reason=reason,
                )
            )
            assert not await receipt.wait()
    await asyncio.sleep(0)
    await runtime.events.drain()


def test_chat_identity_comes_from_each_inbound_connection(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai(
            groups=[{"connection_id": name, "group_id": "100"} for name in ("main", "second")]
        )
        try:
            for connection, bot_id in (("main", "999"), ("second", "888")):
                incoming = event(connection, connection=connection, bot_id=bot_id)
                runtime.messages.adapters[connection].connection = incoming.connection
                await deliver(runtime, incoming)
                items = model.requests[-1][0]
                identity = [
                    i.content[0].text
                    for i in items
                    if i.role == "system" and i.content[0].text.startswith("机器人自身身份")
                ]
                assert len(identity) == 1
                assert f"你的QQ号：{bot_id}" in identity[0]
                assert "平台：qq" in identity[0]
                # The sender's ID remains separate from the bot's connection identity.
                assert any(i.role == "user" and "用户 ID：200" in i.content[0].text for i in items)
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("message_format", ["array", "string"])
def test_forward_ws_wakes_real_chat_plugin(setup_ai, message_format, caplog):
    trace_loggers = ("accbot.adapters.onebot", "accbot.events.bus", "accbot.ai.chat.events")
    for logger in trace_loggers:
        caplog.set_level("DEBUG", logger=logger)

    async def run():
        runtime, plugin, model = await setup_ai()
        connected = asyncio.Queue()
        observed = asyncio.Event()
        actions = []
        runtime.events.subscribe(MessageReceived, lambda _: observed.set())

        async def protocol(socket):
            await connected.put(socket)
            async for frame in socket:
                action = json.loads(frame)
                actions.append(action)
                await socket.send(
                    json.dumps(
                        {"status": "ok", "retcode": 0, "echo": action["echo"], "data": {"message_id": 500}}
                    )
                )

        async def receive(socket, number, *, mention=True, group=100):
            text = "你好 & [群聊]"
            message = [{"type": "text", "data": {"text": text}}]
            if mention:
                message.append({"type": "at", "data": {"qq": "999"}})
            if message_format == "string":
                message = "你好 &amp; &#91;群聊&#93;" + ("[CQ:at,qq=999]" if mention else "")
            observed.clear()
            await socket.send(
                json.dumps(
                    {
                        "post_type": "message",
                        "message_type": "group",
                        "self_id": 999,
                        "group_id": group,
                        "user_id": 200,
                        "message_id": number,
                        "time": 1700000000,
                        "sender": {"nickname": "群友", "card": "群名片"},
                        "message": message,
                    }
                )
            )
            await asyncio.wait_for(observed.wait(), 3)
            await asyncio.wait_for(runtime.events.drain(), 3)
            await asyncio.wait_for(runtime.jobs.drain(), 3)
            await asyncio.wait_for(runtime.events.drain(), 3)

        try:
            async with serve(protocol, "127.0.0.1", 0) as server:
                # Replace the fixture's fake senders with the actual production adapter.
                runtime.messages.adapters.clear()
                await runtime.replace_connections(
                    [
                        OneBotConfig(
                            id="main",
                            self_id="999",
                            mode="forward_ws",
                            ws_url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/",
                        )
                    ]
                )
                try:
                    socket = await asyncio.wait_for(connected.get(), 3)
                    # A different group is rejected by the decorator, before session observation.
                    await receive(socket, 1, group=101)
                    assert not plugin.sessions.channels and not model.requests
                    # Unmentioned messages are observed, but do not wake at probability zero.
                    await receive(socket, 2, mention=False)
                    channel = next(iter(plugin.sessions.channels.values()))
                    assert channel.session is None and not model.requests
                    await receive(socket, 3)
                    assert channel.session is not None
                    assert len(model.requests) == len(actions) == 1
                    items, tools, _ = model.requests[0]
                    assert any(i.role == "user" and "你好 & [群聊]" in i.content[0].text for i in items)
                    assert all(t.name != "execute_shell" for t in tools)
                    assert actions[0]["action"] == "send_group_msg"
                    assert str(actions[0]["params"]["group_id"]) == "100"
                    assert channel.idle_timer is not None
                    # Adapter deduplication must prevent a second model call on a repeated frame.
                    receipt = await runtime.messages.adapters["main"].ingest(
                        {
                            "post_type": "message",
                            "message_type": "group",
                            "self_id": 999,
                            "group_id": 100,
                            "user_id": 200,
                            "message_id": 3,
                            "message": [],
                        }
                    )
                    assert receipt is None and len(model.requests) == 1
                    trace = "\n".join(r.getMessage() for r in caplog.records if r.name in trace_loggers)
                    assert "handler=Plugin.receive_group_message" in trace
                    assert "awake=True" in trace and "awake=False" in trace
                    assert "reason=duplicate" in trace
                    assert "你好" not in trace and "收到喵" not in trace and "群名片" not in trace
                finally:
                    await runtime.replace_connections([])
        finally:
            await runtime.close()

    asyncio.run(run())


def test_group_gates_dedup_quote_memory_and_restart(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        adapter = runtime.messages.adapters["main"]
        try:
            await deliver(runtime, event("disabled", group="other"))
            await deliver(runtime, event("connection", connection="second"))
            assert not plugin.sessions.channels
            original = event("old", mention=False, text="引用原文").message
            quoted = event(reply=MessageQuote.from_message(original))
            await deliver(runtime, quoted)
            await deliver(runtime, quoted)
            assert len(adapter.sent) == 1
            assert "引用原文" in str(model.requests[0][0])
            channel = next(iter(plugin.sessions.channels.values()))
            assert len(channel.recent) == 1
            assert channel.store.pending_path.exists()
            # A late message during a session is retained, even without a wake.
            plugin.sessions.dice = lambda: 1
            await deliver(runtime, event("2", mention=False, text="我喜欢猫"))
            assert len(adapter.sent) == 1
            expired = channel.session.last_wake + 181
            plugin.sessions.clock = lambda: expired
            await summarize(runtime, plugin)
            await runtime.jobs.drain()
            assert channel.session is None
            assert channel.store.load().summary == "群友喜欢猫"
            assert not channel.store.pending_path.exists()
            assert "我喜欢猫" in str(model.requests[-1][0])
            assert model.requests[-1][2]["tool_choice"] == "auto"
            assert {t.id for t in model.requests[-1][1]} == {"ai.chat.update_user_profile"}
            reloaded = await runtime.reload("ai.tools")
            assert not reloaded["failed"]
            restored = runtime.plugins.instances["ai.chat"][0]
            assert restored.config.default_model == "test"
            await deliver(runtime, event("3"))
            assert "群友喜欢猫" in str(model.requests[-1][0])
            await runtime.unload("ai.tools")
            assert runtime.plugins.active == set()
            assert not runtime.tools.list()
        finally:
            await runtime.close()

    asyncio.run(run())


def test_wake_probabilities_cooldown_and_busy_sixteenth_message(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai(
            initial_probability=0, busy_probability=1, active_probability=1, active_busy_probability=1
        )
        now = [100.0]
        plugin.sessions.clock = lambda: now[0]
        try:
            for i in range(15):
                await deliver(runtime, event(str(i), mention=False))
            assert not model.requests
            await deliver(runtime, event("16", mention=False))
            assert len(model.requests) == 1
            channel = next(iter(plugin.sessions.channels.values()))
            assert len(channel.recent) == 15
            await deliver(runtime, event("17", mention=False))
            assert len(model.requests) == 1 and channel.session.last_wake == 100
            now[0] = 101
            await deliver(runtime, event("18"))
            assert len(model.requests) == 2 and channel.session.last_wake == 101
            now[0] = 107
            await deliver(runtime, event("19", mention=False))
            assert len(model.requests) == 3
            assert "17" in str(model.requests[1][0])
        finally:
            await runtime.close()

    asyncio.run(run())


def test_inflight_fifo_expiry_and_cross_group_progress(setup_ai):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def respond(items, tools, options):
            text = str(items)
            calls.append(text)
            if "FIRST" in text and len(calls) == 1:
                entered.set()
                await release.wait()
            return GenerationResult(items=(ModelItem.text("assistant", "done"),))

        runtime, plugin, model = await setup_ai(
            respond,
            groups=[
                {"connection_id": "main", "group_id": "100"},
                {"connection_id": "main", "group_id": "101"},
            ],
        )
        now = [10.0]
        plugin.sessions.clock = lambda: now[0]
        try:
            await deliver(runtime, event("1", text="FIRST"), drain=False)
            await entered.wait()
            await deliver(runtime, event("2", text="SECOND"), drain=False)
            now[0] = 191
            await summarize(runtime, plugin)
            await deliver(runtime, event("3", group="101", text="OTHER"), drain=False)
            for _ in range(30):
                if len(calls) > 1:
                    break
                await asyncio.sleep(0.01)
            assert len(calls) == 2 and "OTHER" in calls[1]
            release.set()
            await runtime.jobs.drain()
            assert "SECOND" in calls[2]
            assert sum(bool(options.get("tool_choice")) for _, _, options in model.requests) == 1
            assert len(runtime.messages.adapters["main"].sent) == 3
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


def test_parallel_tools_silence_and_allowlist(setup_ai, monkeypatch):
    async def run():
        step = 0

        async def respond(items, tools, options):
            nonlocal step
            step += 1
            if step == 1:
                calls = (
                    ToolCall(id="a", name="ai.tools.send_text", arguments={"text": "进度"}),
                    ToolCall(id="b", name="ai.chat.read_file", arguments={"path": "/etc/passwd"}),
                    ToolCall(id="c", name="ai.chat.stay_silent"),
                )
                return GenerationResult(items=(ModelItem(role="assistant", calls=calls),))
            outputs = [i.output for i in items if i.kind == "tool_result"]
            assert outputs[0]["ok"] and not outputs[1]["ok"] and not outputs[2]["ok"]
            return GenerationResult(
                items=(ModelItem(role="assistant", calls=(ToolCall(id="d", name="ai.chat.stay_silent"),)),)
            )

        runtime, plugin, model = await setup_ai(respond)
        executed = []
        execute = runtime.tools.execute

        async def record_execution(call, context):
            executed.append(call.name)
            return await execute(call, context)

        monkeypatch.setattr(runtime.tools, "execute", record_execution)
        try:
            await deliver(runtime, event())
            assert len(runtime.messages.adapters["main"].sent) == 1
            assert runtime.messages.adapters["main"].sent[0][1].plain_text == "进度"
            record = next(iter(plugin.sessions.channels.values())).records[0]
            assert len([i for i in record.turns[0] if i.kind == "tool_result"]) == 4
            assert executed == ["ai.tools.send_text", "ai.chat.stay_silent"]
            assert all("jm" not in t.name for t in runtime.tools.list())
        finally:
            await runtime.close()

    asyncio.run(run())


def test_same_name_and_content_keep_distinct_platform_message_ids(setup_ai):
    async def run():
        runtime, plugin, _ = await setup_ai()
        try:
            first = event("message-a", user="200", text="相同内容", mention=False)
            second = event("message-b", user="201", text="相同内容", mention=False)
            await deliver(runtime, first)
            await deliver(runtime, second)
            channel = next(iter(plugin.sessions.channels.values()))
            a, b = channel.recent
            assert a.message.sender.name == b.message.sender.name and a.message.content == b.message.content
            assert a.message.ref.id == "message-a" and b.message.ref.id == "message-b"
            quoted = event("reply", reply=MessageQuote.from_message(first.message))
            rendered = ModelItem.from_message(quoted.message)
            assert rendered.message_id == "reply" and rendered.reply_to.message_id is None
            assert rendered.reply_to.sender.ref == first.message.sender.ref
        finally:
            await runtime.close()

    asyncio.run(run())


def test_vision_is_lazy_bounded_and_not_in_history(setup_ai):
    async def run():
        step = 0

        async def respond(items, tools, options):
            nonlocal step
            step += 1
            if step == 1:
                assert not any(p.type == "image" for i in items for p in i.content)
                text = next(p.text for i in items if i.role == "user" for p in i.content)
                message_id = text.split("消息id: ")[1].splitlines()[0]
                return GenerationResult(
                    items=(
                        ModelItem(
                            role="assistant",
                            calls=(
                                ToolCall(
                                    name="ai.chat.view_message_images", arguments={"message_id": message_id}
                                ),
                            ),
                        ),
                    )
                )
            if step == 2:
                assert any(p.type == "image" for i in items for p in i.content)
            if step == 3:
                assert not any(p.type == "image" for i in items for p in i.content)
            return GenerationResult(items=(ModelItem.text("assistant", "一张图片"),))

        runtime, plugin, _ = await setup_ai(respond)
        network = runtime.plugins.instances["ai.tools"][0]
        requests = []
        raw = io.BytesIO()
        Image.new("RGBA", (1600, 800), (255, 0, 0, 128)).save(raw, "PNG")

        async def fetch(url, **kwargs):
            requests.append(url)
            return raw.getvalue(), "image/png", url

        network.http.get = fetch
        try:
            await deliver(runtime, event(image=AssetRef(url="https://images.test/cat")))
            assert len(requests) == 1
            channel = next(iter(plugin.sessions.channels.values()))
            assert "base64" not in channel.store.pending_path.read_text()
            await deliver(runtime, event("2"))
            assert len(requests) == 1
            # A caller cannot request arbitrary URLs through the image tool.
            result = await runtime.tools.execute(
                ToolCall(name="ai.chat.view_message_images", arguments={"message_id": "outside"}),
                ToolContext(conversation=event().message.conversation),
            )
            assert not result.ok
            with pytest.raises(ValueError):
                await network.images([AssetRef(url="https://images.test/cat")] * 17)
        finally:
            await runtime.close()

    asyncio.run(run())


def test_failed_memory_retry_recovery_and_commit_marker(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            model.summary_failures = 1
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            assert channel.store.pending_path.exists()
            assert channel.store.load().summary == ""
            assert channel.retry_delay == 120
            # Reload recovers pending work before the next conversation turn.
            assert not (await runtime.reload("ai.chat"))["failed"]
            plugin = runtime.plugins.instances["ai.chat"][0]
            channel = next(iter(plugin.sessions.channels.values()))
            await summarize(runtime, plugin, reason="retry")
            await runtime.jobs.drain()
            assert channel.store.load().summary == MEMORY["summary"]
            assert not channel.records
            # Simulate a crash between the atomic memory commit and journal cleanup.
            memory_module = importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + ".memory")
            record = memory_module.Record(
                id="already-committed",
                model="test",
                owner=False,
                turns=[[ModelItem.text("user", "x")]],
                sealed=True,
            )
            channel.store.save(channel.store.load(), record.id)
            channel.records.append(record)
            channel.store.journal(channel.records)
            previous = len(model.requests)
            await plugin.sessions.flush(channel)
            assert len(model.requests) == previous and not channel.records
            channel.store.memory_path.write_text("invalid", encoding="utf-8")
            with pytest.raises(ValueError):
                channel.store.load()
        finally:
            await runtime.close()

    asyncio.run(run())


def test_pending_memory_roundtrip_preserves_typed_turns_and_rejects_bad_fields(setup_ai, tmp_path):
    async def run():
        runtime, plugin, _ = await setup_ai()
        memory = importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + ".memory")
        store = memory.Store(tmp_path / "record-roundtrip", event().message.conversation)
        first, waiting = ModelItem.text("user", "已接收"), ModelItem.text("user", "尚未处理")
        call = ModelItem(role="assistant", calls=(ToolCall(id="unfinished", name="example.action"),))
        record = memory.Record(
            id="record", model="test", owner=False, turns=[[first, call]], waiting=[waiting]
        )
        try:
            store.journal([record])
            saved = store.pending_path.read_text("utf-8")
            restored = store.recover()[0]
            assert restored.id == record.id and restored.sealed and not restored.waiting
            assert restored.turns[0][:2] == [first, call]
            assert restored.turns[0][2].call_id == "unfinished" and not restored.turns[0][2].output["ok"]
            assert restored.turns[1] == [waiting]
            assert store.pending_path.read_text("utf-8") == saved
            for invalid in ({"owner": "false"}, {"turns": [["not a ModelItem"]]}):
                data = json.loads(saved)
                data["records"][0].update(invalid)
                malformed = json.dumps(data, ensure_ascii=False)
                store.pending_path.write_text(malformed, encoding="utf-8")
                with pytest.raises(ValueError):
                    store.recover()
                assert store.pending_path.read_text("utf-8") == malformed
        finally:
            await runtime.close()

    asyncio.run(run())


def test_owner_identity_controls_and_stateless_private(setup_ai, tmp_path):
    async def run():
        runtime, plugin, model = await setup_ai(
            owner={"connection_id": "main", "user_id": "700"},
            owner_controls=True,
            private_users=[{"connection_id": "main", "user_id": "800"}],
        )
        try:
            owner_event = event(user="700", direct=True, mention=False)
            await deliver(runtime, owner_event)
            owner_names = {t.name for t in model.requests[-1][1]}
            assert {"read_file", "write_file", "execute_shell"} <= owner_names
            assert owner_names == {t.name for t in runtime.tools.list()}
            assert "execute_shell" not in {t.name for t in plugin.tool_list()}
            target = tmp_path / "owner.txt"
            context_module = importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + ".context")
            context = context_module.ChatContext(
                conversation=owner_event.message.conversation, message=owner_event.message
            )
            result = await runtime.tools.execute(
                ToolCall(name="ai.chat.write_file", arguments={"path": str(target), "content": "猫abc"}),
                context,
            )
            assert result.ok and target.read_text() == "猫abc"
            result = await runtime.tools.execute(
                ToolCall(name="ai.chat.read_file", arguments={"path": str(target), "max_bytes": 4}), context
            )
            assert result.ok and result.value["content"] == "猫a" and result.value["next_offset"] == 4
            result = await runtime.tools.execute(
                ToolCall(name="ai.chat.execute_shell", arguments={"command": "printf test"}), context
            )
            assert result.ok and result.value["output"] == "test" and result.value["exit_code"] == 0
            forged = event(user="700", direct=False)
            result = await runtime.tools.execute(
                ToolCall(name="ai.chat.read_file", arguments={"path": str(target)}),
                context_module.ChatContext(conversation=forged.message.conversation, message=forged.message),
            )
            assert not result.ok
            previous = len(model.requests)
            await deliver(runtime, event("private-no-at", user="800", direct=True, mention=False))
            assert len(model.requests) == previous
            await deliver(runtime, event("p1", user="800", direct=True, text="PRIVATE_FIRST"))
            await deliver(runtime, event("p2", user="800", direct=True, text="PRIVATE_SECOND"))
            assert "PRIVATE_FIRST" not in str(model.requests[-1][0])
            channel = plugin.sessions.channel(event(user="800", direct=True).message.conversation)
            assert not channel.records and not channel.store.pending_path.exists()
        finally:
            await runtime.close()

    asyncio.run(run())


def test_configuration_change_keeps_event_subscriptions_live(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        try:
            old = plugin.sessions
            await plugin.on_settings_changed(plugin.config.model_dump())
            assert old.closed
            await deliver(runtime, event())
            assert model.requests and plugin.sessions.channels and not old.channels
            with pytest.raises(ValueError):
                type(plugin.config).model_validate(
                    {"groups": [{"connection_id": "main", "group_id": "100", "enabled": "true"}]}
                )
        finally:
            await runtime.close()

    asyncio.run(run())


def test_independent_tool_calls_execute_in_parallel(setup_ai):
    async def run():
        round_number, started = 0, []
        both = asyncio.Event()

        async def respond(items, tools, options):
            nonlocal round_number
            round_number += 1
            if round_number == 1:
                return GenerationResult(
                    items=(
                        ModelItem(
                            role="assistant",
                            calls=tuple(
                                ToolCall(id=str(n), name="ai.tools.parallel", arguments={"n": n})
                                for n in range(2)
                            ),
                        ),
                    )
                )
            return GenerationResult(items=(ModelItem.text("assistant", "parallel done"),))

        runtime, plugin, model = await setup_ai(respond)

        @Tool
        async def parallel(n: int):
            started.append(n)
            if len(started) == 2:
                both.set()
            await both.wait()
            return n

        runtime.tools.register(runtime.plugins.instances["ai.tools"][1], parallel)
        try:
            async with asyncio.timeout(2):
                await deliver(runtime, event())
            assert started == [0, 1]
            assert len([i for i in model.requests[-1][0] if i.kind == "tool_result"]) == 2
        finally:
            await runtime.close()

    asyncio.run(run())


def test_reload_drains_old_generation_and_never_replays_side_effects(setup_ai):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        step = 0

        async def respond(items, tools, options):
            nonlocal step
            step += 1
            if step == 1:
                return GenerationResult(
                    items=(
                        ModelItem(
                            role="assistant",
                            calls=(
                                ToolCall(
                                    id="progress", name="ai.tools.send_text", arguments={"text": "progress"}
                                ),
                            ),
                        ),
                    )
                )
            entered.set()
            await release.wait()
            return GenerationResult(items=(ModelItem.text("assistant", "old answer"),))

        runtime, plugin, model = await setup_ai(respond)
        try:
            await deliver(runtime, event(), drain=False)
            await entered.wait()
            reload = asyncio.create_task(runtime.reload("ai.chat"))
            for _ in range(50):
                if plugin.sessions.closed:
                    break
                await asyncio.sleep(0.01)
            assert plugin.sessions.closed
            release.set()
            assert not (await reload)["failed"]
            restored = runtime.plugins.instances["ai.chat"][0]
            assert restored is not plugin
            await summarize(runtime, restored, reason="retry")
            await runtime.jobs.drain()
            assert len(runtime.messages.adapters["main"].sent) == 1
            assert runtime.messages.adapters["main"].sent[0][1].plain_text == "progress"
            assert step == 2
            assert any(o.get("tool_choice") == "auto" for _, _, o in model.requests)
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


def test_download_and_send_tools_use_only_managed_files(setup_ai, tmp_path):
    async def run():
        runtime, _, _ = await setup_ai()
        plugin = runtime.plugins.instances["ai.tools"][0]

        async def fetch(url, **kwargs):
            return b"document", "text/plain", url

        plugin.http.get = fetch
        context = ToolContext(conversation=event().message.conversation)
        try:
            result = await runtime.tools.execute(
                ToolCall(name="ai.tools.download_url", arguments={"url": "https://download.test/report.txt"})
            )
            assert result.ok
            path = result.value["local_path"]
            assert Path(path).read_bytes() == b"document"
            result = await runtime.tools.execute(
                ToolCall(name="ai.tools.send_file", arguments={"path": path}), context
            )
            assert result.ok
            result = await runtime.tools.execute(
                ToolCall(
                    name="ai.tools.download_url",
                    arguments={"url": "https://download.test/file", "filename": "../escape"},
                )
            )
            assert not result.ok and not (plugin.ctx.data_dir / "escape").exists()
            private = ToolContext(conversation=event(direct=True).message.conversation)
            assert not (
                await runtime.tools.execute(
                    ToolCall(name="ai.tools.send_file", arguments={"path": path}), private
                )
            ).ok
            outside = tmp_path / "outside.txt"
            outside.write_text("private")
            (plugin.downloads / "escape.txt").symlink_to(outside)
            for name in (str(outside), str(plugin.downloads / "escape.txt")):
                assert not (
                    await runtime.tools.execute(
                        ToolCall(name="ai.tools.send_file", arguments={"path": name}), context
                    )
                ).ok
            png = plugin.downloads / "cat.png"
            Image.new("RGB", (2, 2)).save(png)
            result = await runtime.tools.execute(
                ToolCall(name="ai.tools.send_image", arguments={"path": str(png)}), context
            )
            assert result.ok
            assert runtime.messages.adapters["main"].sent[-1][1].segments[0].asset.data.startswith("data:")
            assert not (
                await runtime.tools.execute(
                    ToolCall(name="ai.tools.send_file", arguments={"path": str(png)}), context
                )
            ).ok
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_real_model_codecs_with_tool_loop_and_memory(setup_ai, protocol):
    async def run():
        from accbot.models.client import ModelClient

        runtime, plugin, _ = await setup_ai()
        requests = []

        def provider(request):
            body = json.loads(request.content)
            requests.append(body)
            if body.get("tool_choice") == "auto":
                assert (body.get("response_format") if protocol == "chat" else body["text"]["format"]) == {
                    "type": "json_object"
                }
                answer = json.dumps(MEMORY)
            elif len(requests) == 1:
                definitions = body["tools"]
                tool = next(t for t in plugin.tool_list() if t.name == "send_text")
                assert tool.wire_name in str(definitions)
                if protocol == "chat":
                    return httpx.Response(
                        200,
                        json={
                            "choices": [
                                {
                                    "finish_reason": "tool_calls",
                                    "message": {
                                        "role": "assistant",
                                        "content": None,
                                        "reasoning_content": "preserved reasoning",
                                        "tool_calls": [
                                            {
                                                "id": "call",
                                                "type": "function",
                                                "function": {
                                                    "name": tool.wire_name,
                                                    "arguments": '{"text":"progress"}',
                                                },
                                            }
                                        ],
                                    },
                                }
                            ]
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output": [
                            {"type": "reasoning", "id": "reason", "summary": []},
                            {
                                "type": "function_call",
                                "call_id": "call",
                                "name": tool.wire_name,
                                "arguments": '{"text":"progress"}',
                            },
                        ],
                    },
                )
            else:
                context = body["messages" if protocol == "chat" else "input"]
                assert any(i.get("tool_call_id", i.get("call_id")) == "call" for i in context)
                if protocol == "chat":
                    assert any(i.get("reasoning_content") == "preserved reasoning" for i in context)
                else:
                    assert any(i.get("type") == "reasoning" for i in context)
                answer = "final reply"
            if protocol == "chat":
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"finish_reason": "stop", "message": {"role": "assistant", "content": answer}}
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": answer}],
                        }
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            runtime.models._clients["test"] = ModelClient(
                ModelCard(id="test", endpoint="https://model.test/v1", model="model", protocol=protocol),
                http,
                lambda _: "",
                prompts=runtime.prompts,
            )
            try:
                plugin.sessions.dice = lambda: 1
                await deliver(runtime, event("first", user="201", mention=False, text="你今天去吗？"))
                await deliver(runtime, event("second", user="202", mention=False, text="我今天去。"))
                await deliver(runtime, event())
                key = "messages" if protocol == "chat" else "input"
                initial, continuation = requests[0][key], requests[1][key]
                assert len(initial) == 4  # Template, bot identity, memory, one merged group-history message.
                assert "下面是群聊历史" in str(initial[-1])
                assert "201" in str(initial[-1]) and "202" in str(initial[-1])
                assert continuation[: len(initial)] == initial
                assert [m.plain_text for _, m in runtime.messages.adapters["main"].sent] == [
                    "progress",
                    "final reply",
                ]
                await deliver(runtime, event("later", user="203", text="还有人去吗？"))
                assert requests[2][key][: len(continuation)] == continuation
                channel = next(iter(plugin.sessions.channels.values()))
                plugin.sessions.seal(channel)
                await runtime.jobs.drain()
                assert channel.store.load().summary == MEMORY["summary"]
                assert not channel.store.pending_path.exists()
            finally:
                await runtime.close()

    asyncio.run(run())


def test_wake_reschedules_only_its_group_and_stale_events_do_nothing(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai(
            groups=[
                {"connection_id": "main", "group_id": "100"},
                {"connection_id": "main", "group_id": "101"},
            ]
        )
        now = [100.0]
        plugin.sessions.clock = lambda: now[0]
        plugin.sessions.dice = lambda: 1
        try:
            assert not runtime.timers.tasks  # No polling or timers for configured-but-idle groups.
            await deliver(runtime, event("a"))
            a = plugin.sessions.channel(event().message.conversation)
            old_timer, old_ticket = a.idle_timer, a.idle_ticket
            await deliver(runtime, event("b", group="101"))
            b = plugin.sessions.channel(event(group="101").message.conversation)
            b_timer, b_ticket = b.idle_timer, b.idle_ticket
            now[0] = 110
            await deliver(runtime, event("a2"))
            assert a.idle_timer != old_timer and old_timer not in runtime.timers.tasks
            assert (b.idle_timer, b.idle_ticket) == (b_timer, b_ticket)
            new_timer = a.idle_timer
            await deliver(runtime, event("passive", mention=False))
            assert a.idle_timer == new_timer
            # The old event may already have been queued before cancellation.
            now[0] = 281
            await summarize(runtime, plugin, a, ticket=old_ticket)
            assert a.session and b.session
            await summarize(runtime, plugin, b)
            await runtime.jobs.drain()
            assert b.session is None and a.session is not None
            now[0] = 291
            await summarize(runtime, plugin, a)
            await runtime.jobs.drain()
            assert a.session is None and not a.records and not b.records
            assert sum(o.get("tool_choice") == "auto" for _, _, o in model.requests) == 2
        finally:
            await runtime.close()

    asyncio.run(run())


def test_idle_timer_really_publishes_summary_event_without_polling(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai(idle_seconds=1)
        done = asyncio.Event()
        module = importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + ".events")
        runtime.events.subscribe(module.SummarizeMemory, lambda _: done.set())
        try:
            await deliver(runtime, event())
            async with asyncio.timeout(3):
                await done.wait()
                await runtime.events.drain()
                await runtime.jobs.drain()
            channel = next(iter(plugin.sessions.channels.values()))
            assert channel.session is None and channel.store.load().summary == MEMORY["summary"]
            assert "ai.chat.tick" not in runtime.events._types
        finally:
            await runtime.close()

    asyncio.run(run())


def test_memory_survives_new_connection_scope_and_legacy_file_migration(setup_ai):
    async def run():
        import hashlib

        runtime, plugin, model = await setup_ai()
        try:
            await deliver(runtime, event())
            old = next(iter(plugin.sessions.channels.values()))
            plugin.sessions.seal(old)
            await runtime.jobs.drain()
            data_dir = plugin.ctx.data_dir
            await runtime.unload("ai.chat")
            # Recreate the previous on-disk format, including an uncommitted record.
            original = old.store.conversation
            legacy = data_dir / "memory" / hashlib.sha256(json.dumps(original.key).encode()).hexdigest()[:24]
            legacy.mkdir()
            state = json.loads(old.store.memory_path.read_text())
            state.pop("memory_key")
            (legacy / "memory.json").write_text(json.dumps(state))
            (legacy / "pending.json").write_text(
                json.dumps(
                    {
                        "conversation": original.model_dump(),
                        "records": [
                            {
                                "id": "unfinished",
                                "model": "test",
                                "owner": False,
                                "sealed": False,
                                "turns": [[ModelItem.text("user", "旧连接尚未整理的消息").model_dump()]],
                            }
                        ],
                    }
                )
            )
            shutil.rmtree(old.store.directory)
            config_path = data_dir / "config/settings.json"
            values = json.loads(config_path.read_text())
            values["groups"] = [{"connection_id": "second", "group_id": "100"}]
            config_path.write_text(json.dumps(values))
            connection = runtime.messages.adapters["second"].connection
            runtime.messages.adapters["second"].connection = connection.model_copy(
                update={"scope": "new-scope"}
            )
            await runtime.set_active(PLUGINS)
            restored = runtime.plugins.instances["ai.chat"][0]
            channel = next(iter(restored.sessions.channels.values()))
            assert channel.store.key.model_dump() == {
                "platform": "qq",
                "scope": "",
                "kind": "group",
                "id": "100",
            }
            assert channel.store.directory == old.store.directory
            assert (
                channel.conversation.connection_id == "second" and channel.conversation.scope == "new-scope"
            )
            await summarize(runtime, restored, reason="retry")
            await runtime.jobs.drain()
            assert "旧连接尚未整理的消息" in str(model.requests[-1][0])
            assert "群友喜欢猫" in str(model.requests[-1][0])
            assert not legacy.exists() and (data_dir / "memory-legacy" / legacy.name).is_dir()
            await deliver(runtime, event("new", connection="second"))
            assert len(restored.sessions.channels) == 1
            assert "群友喜欢猫" in str(model.requests[-1][0])
        finally:
            await runtime.close()

    asyncio.run(run())


def test_conflicting_legacy_group_memories_are_preserved(setup_ai):
    async def run():
        runtime, plugin, _ = await setup_ai()
        module = importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + ".memory")
        root = plugin.ctx.data_dir
        conversation = event().message.conversation
        try:
            for name, summary in (("old-a", "A"), ("old-b", "B")):
                path = root / "memory" / name
                path.mkdir(parents=True)
                (path / "memory.json").write_text(
                    json.dumps(
                        {
                            "conversation": conversation.model_dump(),
                            "memory": {**MEMORY, "summary": summary},
                            "last_record": name,
                        }
                    )
                )
            with pytest.raises(ValueError, match="多份不同"):
                module.Store(root, conversation)
            assert {p.name for p in (root / "memory").iterdir()} == {"old-a", "old-b"}
            assert not (root / "memory-legacy").exists()
        finally:
            await runtime.close()

    asyncio.run(run())


def test_group_memory_survives_full_runtime_restart(setup_ai):
    async def run():
        runtime, plugin, _ = await setup_ai()
        restarted = None
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            directory = channel.store.directory
            await runtime.close()
            restarted = Runtime(runtime.store.directory)
            model = Model(prompts=restarted.prompts)
            restarted.models._clients["test"] = model
            restarted.messages.add_adapter(Adapter(event().connection))
            await restarted.start()
            await deliver(restarted, event("after-restart"))
            fresh = restarted.plugins.instances["ai.chat"][0]
            restored = next(iter(fresh.sessions.channels.values()))
            assert restored.store.directory == directory
            assert "群友喜欢猫" in str(model.requests[-1][0])
            key_type = type(restored.store.key)
            other = event().message.conversation.model_copy(update={"platform": "feishu"})
            assert key_type.of(other) != restored.store.key
            assert key_type.of(other.model_copy(update={"scope": "other-tenant"})) != key_type.of(other)
        finally:
            await (restarted or runtime).close()

    asyncio.run(run())


def test_persona_edits_apply_to_next_call_and_owner_filters_track_configuration(setup_ai):
    from accbot import PersonaCard

    async def run():
        runtime, plugin, model = await setup_ai(
            owner={"connection_id": "main", "user_id": "700"},
            owner_controls=True,
            private_users=[{"connection_id": "main", "user_id": "700"}],
        )
        try:
            await runtime.replace_personas(
                [PersonaCard(id="default", name="新人格", system_prompt="精确的机器人")]
            )
            await deliver(runtime, event("owner", user="700", direct=True, mention=False))
            assert len(model.requests) == 1  # Owner also in private allowlist must not receive twice.
            assert "精确的机器人" in str(model.requests[-1][0])
            assert {t.id for t in model.requests[-1][1]} == {t.id for t in runtime.tools.list()}
            config = plugin.config.model_dump()
            config.update(owner={"connection_id": "main", "user_id": "701"}, private_users=[])
            await plugin.on_settings_changed(config)
            previous = len(model.requests)
            await deliver(runtime, event("old-owner", user="700", direct=True))
            assert len(model.requests) == previous
            await deliver(runtime, event("new-owner", user="701", direct=True, mention=False))
            assert len(model.requests) == previous + 1
            await deliver(runtime, event("group", user="701"))
            assert "ai.chat.execute_shell" not in {t.id for t in model.requests[-1][1]}
        finally:
            await runtime.close()

    asyncio.run(run())


def test_ai_uses_selected_prompt_cards_without_group_control_tool_instructions(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai(
            owner={"connection_id": "main", "user_id": "700"},
            owner_controls=True,
        )
        try:
            await deliver(runtime, event("group-prompt"))
            group_system = model.requests[-1][0][0].content[0].text
            assert "### 插件：ai.chat" in group_system and "### 插件：ai.tools" in group_system
            assert "Shell" not in group_system and "Bash" not in group_system
            await deliver(runtime, event("owner-prompt", user="700", direct=True, mention=False))
            owner_system = model.requests[-1][0][0].content[0].text
            assert "Bash" in owner_system
            assert (
                next(t.wire_name for t in runtime.tools.list() if t.name == "execute_shell") in owner_system
            )
            channel = plugin.sessions.channel(event().message.conversation)
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            memory_system = model.requests[-1][0][0].content[0].text
            assert '"open_tasks"' in memory_system
            assert "### 插件：" not in memory_system and "Bash" not in memory_system
        finally:
            await runtime.close()

    asyncio.run(run())


def test_expanded_system_prompt_counts_toward_chat_budget(setup_ai):
    from accbot import SystemPromptCard

    async def run():
        runtime, _, model = await setup_ai(context_budget=4096, output_reserve=512)
        try:
            original = runtime.prompts.get("chat")
            large = SystemPromptCard(id="chat", name=original.name, content="x" * 6000)
            await runtime.replace_prompts([large if c.id == "chat" else c for c in runtime.prompts.list()])
            await deliver(runtime, event())
            assert not model.requests  # Rejected locally rather than silently dropping the system prompt.
            assert "处理失败" in runtime.messages.adapters["main"].sent[-1][1].plain_text
        finally:
            await runtime.close()

    asyncio.run(run())
