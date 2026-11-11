import asyncio

import pytest
from test_ai_plugins import deliver, event
from test_ai_plugins import setup_ai as setup_ai

from accbot import GenerationResult, ModelItem, ToolCall
from accbot.models import count_tokens


def padded_history(plugin, channel, *, overflow):
    plugin.config = plugin.config.model_copy(update={"context_budget": 80000, "output_reserve": 1024})
    record = channel.session.record
    record.turns = [[ModelItem.from_message(event(str(i), text=f"HISTORY_{i}").message)] for i in range(2)]
    budget = plugin.agent.budget(plugin.config, plugin.config.memory_prompt_id, None, plugin.memory_tools())
    baseline = plugin.agent.context(channel.store.load(), False, None, record.turns, [])
    padding = (budget - count_tokens(baseline) + (4000 if overflow else -2000)) // 2
    for turn in record.turns:
        item = turn[0]
        turn[0] = item.model_copy(
            update={
                "content": (
                    item.content[0].model_copy(update={"text": item.content[0].text + " x" * padding}),
                )
            }
        )
    channel.store.journal(channel.records)
    return record


def memory_response(items, batches):
    transcript = str([i for i in items if i.role == "user"])
    batch = [f"HISTORY_{i}" for i in range(2) if f"HISTORY_{i}" in transcript]
    batches.append(batch)
    background = next(
        i.content[0].text
        for i in items
        if i.role == "system" and i.content[0].text.startswith("本会话长期记忆")
    )
    previous = background.split("\n", 1)[1]
    return GenerationResult(items=(ModelItem.text("assistant", previous + " ".join(batch)),))


@pytest.mark.parametrize("mode", ["budget", "idle"])
def test_oversized_summary_folds_prefixes_and_keeps_new_messages(setup_ai, mode):
    async def run():
        runtime, plugin, model = await setup_ai()
        batches = []
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            session = channel.session
            wake, ticket = session.last_wake, channel.idle_ticket
            record = padded_history(plugin, channel, overflow=True)
            old_id = record.id
            later = [ModelItem.text("user", "LATER_MESSAGE")]

            async def respond(items, tools, options):
                if not batches and mode == "budget":
                    record.turns.append(later)
                    record.waiting.append(ModelItem.text("user", "WAITING_MESSAGE"))
                return memory_response(items, batches)

            model.summary_responder = respond
            if mode == "budget":
                await plugin.sessions.request_compaction(channel, record, 2)
                assert channel.session is session and session.last_wake == wake
                assert channel.idle_ticket == ticket and record.turns == [later]
                assert "WAITING_MESSAGE" in str(record.waiting)
                assert "HISTORY_" not in channel.store.pending_path.read_text("utf-8")
            else:
                plugin.sessions.seal(channel)
                await runtime.jobs.drain()
                assert not channel.records and not channel.store.pending_path.exists()
            assert batches == [["HISTORY_0"], ["HISTORY_1"]]
            assert record.id != old_id
            assert channel.store.load() == "HISTORY_0HISTORY_1"
        finally:
            await runtime.close()

    asyncio.run(run())


def test_profile_tool_growth_folds_history_and_reads_saved_profile_on_retry(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        batches, calls = [], 0
        portrait = "SAVED_PROFILE " + "p q r " * 1800
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            record = padded_history(plugin, channel, overflow=False)
            old_id = record.id

            async def respond(items, tools, options):
                nonlocal calls
                calls += 1
                if calls == 1:
                    assert all(f"HISTORY_{i}" in str(items) for i in range(2))
                    return GenerationResult(
                        items=(
                            ModelItem(
                                role="assistant",
                                calls=(
                                    ToolCall(
                                        name="ai.chat.update_user_profile",
                                        arguments={"user_id": "200", "content": portrait},
                                    ),
                                ),
                            ),
                        )
                    )
                assert portrait in str(items)
                # A new attempt reads the saved portrait; it never replays an old tool request.
                assert not any(i.calls or i.kind == "tool_result" for i in items)
                return memory_response(items, batches)

            model.summary_responder = respond
            await plugin.sessions.request_compaction(channel, record, 2)
            assert calls == 3 and batches == [["HISTORY_0"], ["HISTORY_1"]]
            assert record.id != old_id and not record.turns
            assert channel.store.load() == "HISTORY_0HISTORY_1"
            assert plugin.agent.profiles.read(event().message.sender.ref).content == portrait
        finally:
            await runtime.close()

    asyncio.run(run())


def test_partial_compaction_failure_is_not_mistaken_for_full_success(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        batches, fail = [], True
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            record = padded_history(plugin, channel, overflow=True)
            old_id, last = record.id, record.turns[-1]

            async def respond(items, tools, options):
                if batches and fail:
                    raise RuntimeError("provider unavailable")
                return memory_response(items, batches)

            model.summary_responder = respond
            with pytest.raises(ValueError, match="记忆整理失败"):
                await plugin.sessions.request_compaction(channel, record, 2)
            assert record.id != old_id and record.turns == [last]
            assert channel.store.load() == "HISTORY_0"
            recovered = channel.store.recover()[0]
            assert recovered.id == record.id and "HISTORY_1" in str(recovered.turns)
            assert "HISTORY_0" not in str(recovered.turns)
            fail = False
            await plugin.sessions.request_compaction(channel, record, 1)
            assert batches == [["HISTORY_0"], ["HISTORY_1"]]
            assert channel.store.load() == "HISTORY_0HISTORY_1" and not record.turns
        finally:
            await runtime.close()

    asyncio.run(run())
