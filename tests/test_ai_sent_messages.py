import asyncio

import pytest
from test_ai_plugins import deliver, event, summarize
from test_ai_plugins import setup_ai as setup_ai

from accbot import MessageBuilder, MessageSent


@pytest.mark.parametrize("direct", [False, True])
def test_external_send_is_observed_without_starting_session(setup_ai, direct):
    async def run():
        runtime, plugin, model = await setup_ai(owner={"connection_id": "main", "user_id": "200"})
        incoming = event(direct=direct)
        messages = runtime.plugins.instances["ai.tools"][0].ctx.messages
        notices = []
        runtime.events.subscribe(MessageSent, notices.append)
        try:
            chain = MessageBuilder().text("外部通知").mention(incoming.connection.bot).build()
            await messages.send(incoming.message.conversation, chain)
            await runtime.events.drain()
            channel = next(iter(plugin.sessions.channels.values()))
            assert channel.session is None and not channel.records and not channel.queue
            assert channel.idle_timer is None and channel.worker_id is None
            assert not channel.store.pending_path.exists() and not model.requests
            assert len(channel.recent) == 1 and channel.recent[0].message.is_self
            assert notices[0].source_plugin_id == "ai.tools"

            await deliver(runtime, event("wake", text="解释这条通知", direct=direct))
            history = "".join(p.text for i in model.requests[0][0] if i.role == "user" for p in i.content)
            assert history.count("外部通知") == 1 and "用户 ID：999" in history
            assert not channel.session.record.waiting  # The chat plugin's own reply is not echoed back.
            assert notices[-1].source_plugin_id == "ai.chat"
            assert [e.message.content.plain_text for e in channel.recent] == ["外部通知", "解释这条通知"]
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("finish", ["wake", "summarize"])
def test_external_send_is_journaled_without_wake_or_timer_reset(setup_ai, finish):
    async def run():
        runtime, plugin, model = await setup_ai(active_probability=1, active_busy_probability=1)
        messages = runtime.plugins.instances["ai.tools"][0].ctx.messages
        notices = []
        runtime.events.subscribe(MessageSent, notices.append)
        try:
            await deliver(runtime, event("first"))
            channel = next(iter(plugin.sessions.channels.values()))
            session = channel.session
            wake, timer, ticket = session.last_wake, channel.idle_timer, channel.idle_ticket
            turns = [list(turn) for turn in session.record.turns]
            await messages.send(channel.conversation, "定时插件已完成任务")
            await runtime.events.drain()
            assert len(model.requests) == 1 and channel.worker_id is None and not channel.queue
            assert channel.session is session and session.record.turns == turns
            assert (session.last_wake, channel.idle_timer, channel.idle_ticket) == (wake, timer, ticket)
            item = session.record.waiting[0]
            assert item.role == "user" and item.sender.ref.id == "999"
            assert item.content[0].text == "定时插件已完成任务"
            assert "定时插件已完成任务" in channel.store.pending_path.read_text("utf-8")
            # Repeated notification does not append the same platform message twice.
            assert not await (await runtime.events.publish(notices[-1])).wait()
            assert len(session.record.waiting) == 1
            if finish == "wake":
                await deliver(runtime, event("next"))
                assert channel.session is session
            else:
                plugin.sessions.clock = lambda: wake + plugin.config.idle_seconds + 1
                await summarize(runtime, plugin, channel)
                await runtime.jobs.drain()
                assert channel.session is None
            assert len(model.requests) == 2
            history = "".join(p.text for i in model.requests[-1][0] if i.role == "user" for p in i.content)
            assert history.count("定时插件已完成任务") == 1
        finally:
            await runtime.close()

    asyncio.run(run())


def test_external_send_respects_conversation_rules_and_recent_window(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        messages = runtime.plugins.instances["ai.tools"][0].ctx.messages
        try:
            for incoming in (event(group="other"), event(connection="second"), event(direct=True)):
                await messages.send(incoming.message.conversation, "不应记录")
            await runtime.events.drain()
            assert not plugin.sessions.channels
            for number in range(17):
                await messages.send(event().message.conversation, f"notice-{number}")
            await runtime.events.drain()
            channel = next(iter(plugin.sessions.channels.values()))
            assert [e.message.content.plain_text for e in channel.recent] == [
                f"notice-{number}" for number in range(2, 17)
            ]
            assert channel.session is None and not model.requests and channel.idle_timer is None
        finally:
            await runtime.close()

    asyncio.run(run())
