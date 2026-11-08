import asyncio
import importlib

import pytest
from test_ai_plugins import MEMORY, deliver, event
from test_ai_plugins import setup_ai as setup_ai

from accbot import GenerationResult, ModelItem


def module(plugin, name):
    return importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + "." + name)


def fill_history(plugin, channel, usage):
    record = channel.session.record
    items = plugin.agent.context(channel.store.load(), False, event().connection.bot, record.turns, [])
    overhead = plugin.config.context_budget - plugin.agent.budget(
        plugin.config, plugin.config.chat_prompt_id, plugin.config.persona_id, plugin.tool_list()
    )
    padding = usage - overhead - module(plugin, "context").cost(items)
    assert padding > 0
    last = record.turns[-1][-1]
    record.turns[-1][-1] = ModelItem.text("assistant", last.content[0].text + "x" * padding)
    channel.store.journal(channel.records)


def test_budget_event_compacts_before_next_turn_without_ending_session(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        events = []
        runtime.events.subscribe(module(plugin, "events").SummarizeMemory, events.append)

        def failing_observer(event):
            raise RuntimeError("unrelated subscriber failed")

        observer = runtime.events.subscribe(module(plugin, "events").SummarizeMemory, failing_observer)
        try:
            await deliver(runtime, event("first", text="最早的群聊信息"))
            channel = next(iter(plugin.sessions.channels.values()))
            session = channel.session
            old_id = session.record.id
            fill_history(plugin, channel, 190000)
            await deliver(runtime, event("next", text="新的问题"))
            assert [e.reason for e in events] == ["budget"]
            assert events[0].ticket == old_id and events[0].turn_count == 1
            assert channel.session is session and not session.record.sealed
            assert session.record.id != old_id and len(session.record.turns) == 1
            assert "最早的群聊信息" in str(model.requests[-2][0])
            assert model.requests[-2][2]["tool_choice"] == "auto"
            assert "最早的群聊信息" not in str(model.requests[-1][0])
            assert "新的问题" in str(model.requests[-1][0]) and MEMORY["summary"] in str(
                model.requests[-1][0]
            )
            assert "最早的群聊信息" not in channel.store.pending_path.read_text("utf-8")
            # A duplicate event cannot clear the new segment of the same active session.
            runtime.events.unsubscribe(observer)
            receipt = await plugin.ctx.publish(events[0])
            assert not await receipt.wait() and len(session.record.turns) == 1
        finally:
            await runtime.close()

    asyncio.run(run())


def test_budget_event_after_reply_clears_journal_but_keeps_session_and_timer(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        try:
            await deliver(runtime, event("first"))
            channel = next(iter(plugin.sessions.channels.values()))
            session = channel.session
            fill_history(plugin, channel, 189000)
            timer = None
            wake = None

            async def respond(items, tools, options):
                nonlocal timer, wake
                timer, wake = channel.idle_ticket, session.last_wake
                return GenerationResult(items=(ModelItem.text("assistant", "y" * 6000),))

            model.responder = respond
            await deliver(runtime, event("second"))
            assert channel.session is session and session.last_wake == wake
            assert channel.idle_ticket == timer and not session.record.turns
            assert not channel.store.pending_path.exists()
            assert plugin.status()["active_sessions"] == 1 and plugin.status()["pending_memories"] == 0
            assert channel.store.load().summary == MEMORY["summary"]
            assert model.requests[-1][2]["tool_choice"] == "auto"
            # Idle expiry of an already summarized empty session performs no extra model call.
            count = len(model.requests)
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            assert not channel.records and len(model.requests) == count
        finally:
            await runtime.close()

    asyncio.run(run())


def test_messages_arriving_during_budget_summary_remain_in_order(setup_ai):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        runtime, plugin, model = await setup_ai()
        try:
            await deliver(runtime, event("first", text="旧历史"))
            channel = next(iter(plugin.sessions.channels.values()))
            fill_history(plugin, channel, 190000)

            async def summarize(items, tools, options):
                entered.set()
                await release.wait()
                return GenerationResult(
                    items=(
                        ModelItem.text(
                            "assistant",
                            channel.store.load()
                            .model_copy(update={"summary": "更新后的群记忆"})
                            .model_dump_json(),
                        ),
                    )
                )

            model.summary_responder = summarize
            await deliver(runtime, event("second", text="正在等待回答"), drain=False)
            await asyncio.wait_for(entered.wait(), 2)
            await deliver(runtime, event("third", text="后来唤醒的消息"), drain=False)
            plugin.sessions.dice = lambda: 1
            await deliver(runtime, event("fourth", text="未唤醒的补充", mention=False), drain=False)
            release.set()
            await runtime.jobs.drain()
            record = channel.session.record
            assert len(record.turns) == 2 and record.waiting[0].message_id == "fourth"
            assert "旧历史" not in str(record.turns)
            assert "正在等待回答" in str(model.requests[-2][0])
            assert "后来唤醒的消息" not in str(model.requests[-2][0])
            assert "后来唤醒的消息" in str(model.requests[-1][0])
            assert "更新后的群记忆" in str(model.requests[-1][0])
            assert "未唤醒的补充" in channel.store.pending_path.read_text("utf-8")
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


def test_failed_budget_summary_keeps_history_and_retries_on_next_wake(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        try:
            await deliver(runtime, event("first", text="必须保留的历史"))
            channel = next(iter(plugin.sessions.channels.values()))
            session = channel.session
            fill_history(plugin, channel, 190000)
            old_id = session.record.id
            model.summary_failures = 1
            await deliver(runtime, event("second"))
            assert channel.session is session and session.record.id == old_id
            assert "必须保留的历史" in channel.store.pending_path.read_text("utf-8")
            assert not channel.store.memory_path.exists()
            await deliver(runtime, event("third"))
            assert channel.session is session and session.record.id != old_id
            assert channel.store.load().summary == MEMORY["summary"]
            assert "必须保留的历史" not in channel.store.pending_path.read_text("utf-8")
        finally:
            await runtime.close()

    asyncio.run(run())


def test_budget_commit_does_not_mark_unsummarized_messages_committed(setup_ai, monkeypatch):
    async def run():
        runtime, plugin, _ = await setup_ai()
        try:
            await deliver(runtime, event("first"))
            channel = next(iter(plugin.sessions.channels.values()))
            record = channel.session.record
            record.turns.append([ModelItem.text("user", "还没有整理的新消息")])
            channel.store.journal(channel.records)
            saved = channel.store.pending_path.read_text("utf-8")

            def fail_journal(records):
                raise OSError("disk unavailable")

            with monkeypatch.context() as patch:
                patch.setattr(channel.store, "journal", fail_journal)
                with pytest.raises(OSError):
                    await plugin.sessions.compact(
                        channel,
                        module(plugin, "events").SummarizeMemory(
                            memory_key=channel.store.key, ticket=record.id, reason="budget", turn_count=1
                        ),
                    )
            assert channel.store.pending_path.read_text("utf-8") == saved
            recovered = channel.store.recover()[0]
            assert not channel.store.committed(recovered.id)
            assert "还没有整理的新消息" in str(recovered.turns)
        finally:
            await runtime.close()

    asyncio.run(run())
