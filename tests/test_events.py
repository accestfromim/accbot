import asyncio

import pytest
from pydantic import ValidationError

from accbot import (
    ChatMessage,
    ConnectionRef,
    ConversationRef,
    Event,
    Filter,
    FrameworkError,
    GroupMessageReceived,
    MessageBuilder,
    MessageChain,
    MessageReceived,
    MessageRef,
    Segment,
    User,
    UserRef,
)
from accbot.events import CustomEvent, EventBus, message_filter
from accbot.lifecycle import Activation


def message(number="1", group="g"):
    user = UserRef(platform="qq", scope="main", id="u")
    conv = ConversationRef(connection_id="main", platform="qq", scope="main", id=group)
    return GroupMessageReceived(
        connection=ConnectionRef(id="main", platform="qq", scope="main", bot=user),
        message=ChatMessage(
            ref=MessageRef(conversation=conv, id=number),
            sender=User(ref=user),
            content=MessageChain.text(number),
        ),
    )


def test_immutable_messages_and_builder_snapshot():
    builder = MessageBuilder().text("a")
    chain = builder.build()
    builder.text("b")
    assert chain.plain_text == "a"
    assert builder.build().plain_text == "ab"
    segment = Segment(type="custom", name="test.x", data={"nested": [{"value": 1}]})
    with pytest.raises(TypeError):
        segment.data["nested"][0]["value"] = 2
    with pytest.raises(ValidationError):
        chain.segments = ()
    assert Segment.model_validate_json(segment.model_dump_json()) == segment
    bus = EventBus()
    original = message()
    assert bus.decode(original.to_dict()) == original


def test_inheritance_filter_order_and_failure_isolation():
    async def run():
        bus = EventBus()
        called = []

        async def good(event):
            await asyncio.sleep(0.001)
            called.append(event.message.ref.id)

        def bad(event):
            raise ValueError("intentional")

        bus.subscribe(
            MessageReceived, good, where=message_filter(platform="qq", sender="u") & ~Filter(lambda e: False)
        )
        bus.subscribe(Event, bad)
        receipts = [await bus.publish(message(str(i))) for i in range(4)]
        for receipt in receipts:
            assert len(await receipt.wait()) == 1
        assert called == ["0", "1", "2", "3"]
        await bus.close()

    asyncio.run(run())


def test_cross_conversation_is_not_blocked():
    async def run():
        bus = EventBus()
        gate = asyncio.Event()
        seen = asyncio.Event()

        async def handler(event):
            if event.message.conversation.id == "slow":
                await gate.wait()
            else:
                seen.set()

        bus.subscribe(MessageReceived, handler)
        await bus.publish(message(group="slow"))
        await bus.publish(message(group="fast"))
        await asyncio.wait_for(seen.wait(), 1)
        gate.set()
        await bus.close()

    asyncio.run(run())


def test_unsubscribe_skips_queued_and_custom_generation_is_rejected():
    async def run():
        bus = EventBus()
        owner = Activation("sample")

        class Ping(Event):
            event_type = "sample.ping"

        bus.register(Ping, owner)
        called = []
        bus.subscribe(Event, lambda e: called.append(e), owner=owner)
        owner.activate()
        receipt = await bus.publish(Ping())
        owner.retire()
        assert not await receipt.wait()
        assert not called
        replacement = Activation("sample")

        class NewPing(Event):
            event_type = "sample.ping"

        bus.register(NewPing, replacement)
        replacement.activate()
        with pytest.raises(FrameworkError):
            await bus.publish(Ping())
        await bus.publish(NewPing())
        replacement.retire()
        await bus.close()

    asyncio.run(run())


def test_nested_publish_reports_backpressure_instead_of_deadlocking():
    async def run():
        bus = EventBus(capacity=1)

        async def handler(event):
            await bus.publish(Event())

        bus.subscribe(Event, handler)
        receipt = await bus.publish(Event())
        errors = await asyncio.wait_for(receipt.wait(), 1)
        assert errors[0].code == "backpressure"
        await bus.close()

    asyncio.run(run())


def test_custom_event_payload_cannot_be_mutated():
    event = CustomEvent(connection=message().connection, name="onebot.poke", data={"x": [1]})
    with pytest.raises(TypeError):
        event.data["x"] = [2]


def test_retired_event_is_rechecked_after_backpressure():
    async def run():
        bus = EventBus(capacity=1)
        owner = Activation("sample")
        gate = asyncio.Event()

        class Ping(Event):
            event_type = "sample.waiting"

        async def busy(event):
            await gate.wait()

        bus.register(Ping, owner)
        owner.activate()
        bus.subscribe(Event, busy)
        receipt = await bus.publish(Event())
        waiting = asyncio.create_task(bus.publish(Ping()))
        await asyncio.sleep(0)
        owner.retire()
        gate.set()
        await receipt.wait()
        with pytest.raises(FrameworkError, match="retired while waiting"):
            await waiting
        await bus.close()

    asyncio.run(run())


def test_invalid_custom_dispatch_key_cannot_kill_dispatcher():
    async def run():
        bus = EventBus()
        owner = Activation("sample")

        class Bad(Event):
            event_type = "sample.bad_key"

            @property
            def dispatch_key(self):
                raise ValueError("bad key")

        bus.register(Bad, owner)
        owner.activate()
        first = await bus.publish(Bad())
        assert len(await first.wait()) == 1
        second = await bus.publish(Event())
        assert not await second.wait()
        owner.retire()
        await bus.close()

    asyncio.run(run())
