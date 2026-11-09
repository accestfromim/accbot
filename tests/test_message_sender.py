import asyncio

import pytest

from accbot import AssetRef, ConnectionRef, ConversationRef, FrameworkError, MessageChain, MessageRef, UserRef
from accbot.events import EventBus, FileSent, MessageSendFailed, MessageSent
from accbot.lifecycle import Activation
from accbot.messages import MessageSender, PluginMessageSender
from accbot.types import SendReceipt


class Adapter:
    def __init__(self, connection_id):
        self.connection = ConnectionRef(
            id=connection_id,
            platform="test",
            scope="scope",
            bot=UserRef(platform="test", scope="scope", id="bot"),
        )
        self.calls = []
        self.error = None

    async def send(self, target, content, *, request_id, **options):
        self.calls.append((target, content, request_id, options))
        if self.error:
            raise self.error
        return SendReceipt(request_id=request_id, messages=(MessageRef(conversation=target, id="sent"),))

    send_file = send


@pytest.mark.parametrize("operation", ["send", "send_file"])
def test_sends_route_through_manager_and_preserve_payload_and_receipts(operation):
    async def run():
        bus = EventBus()
        sender = MessageSender(bus)
        first, second = Adapter("first"), Adapter("second")
        sender.add_adapter(first)
        sender.add_adapter(second)
        target = ConversationRef(connection_id="second", platform="test", scope="scope", id="group")
        content = MessageChain.text("hello") if operation == "send" else AssetRef(path="document.txt")
        notices = []
        bus.subscribe(MessageSent if operation == "send" else FileSent, notices.append)
        try:
            assert sender.get_connection("second") == second.connection
            assert sender.get_connection("missing") is None
            receipt = await getattr(sender, operation)(target, content)
            await bus.drain()
            assert not first.calls
            assert second.calls[0][:3] == (target, content, receipt.request_id)
            assert notices[0].connection == second.connection
            assert notices[0].receipt == receipt
            assert (notices[0].content if operation == "send" else notices[0].asset) == content
            if operation == "send":
                assert notices[0].source_plugin_id is None
            with pytest.raises(FrameworkError):
                await getattr(sender, operation)(target.model_copy(update={"scope": "other"}), content)
            assert len(second.calls) == 1
        finally:
            await bus.close()

    asyncio.run(run())


def test_plugin_senders_keep_separate_origins_and_share_current_routing():
    async def run():
        bus, owner, other = EventBus(), Activation("first"), Activation("second")
        owner.activate()
        other.activate()
        sender = MessageSender(bus)
        first, second = PluginMessageSender(sender, owner), PluginMessageSender(sender, other)
        adapter = Adapter("main")
        # Bound plugin services must see replacements of the manager's adapter mapping.
        sender.adapters = {"main": adapter}
        target = ConversationRef(connection_id="main", platform="test", scope="scope", id="group")
        notices, failures, files = [], [], []
        bus.subscribe(MessageSent, notices.append)
        bus.subscribe(MessageSendFailed, failures.append)
        bus.subscribe(FileSent, files.append)
        try:
            assert first.get_connection("main") == adapter.connection
            await asyncio.gather(first.send(target, "one"), second.send(target, "two"))
            await first.send_file(target, AssetRef(path="document.txt"))
            adapter.error = OSError("send failed")
            with pytest.raises(OSError):
                await second.send(target, "failed")
            await bus.drain()
            assert [e.source_plugin_id for e in notices] == ["first", "second"]
            for event, source in zip(notices, (owner, other)):
                assert event.origin.component == source.plugin_id
                assert event.origin.activation_id == source.id
                restored = bus.decode(event.to_dict())
                assert restored.source_plugin_id == source.plugin_id
                assert restored.message.is_self
                assert restored.message.sender.ref == adapter.connection.bot
                assert restored.message.content == event.content
                assert restored.message.sent_at == event.receipt.accepted_at
                assert restored.message.ref == event.receipt.messages[0]
            assert files[0].origin.activation_id == owner.id
            assert failures[0].origin.activation_id == other.id
            assert len(notices) == 2
            assert (
                notices[0]
                .model_copy(update={"receipt": SendReceipt(request_id="empty", messages=())})
                .message
                is None
            )
        finally:
            await bus.close()

    asyncio.run(run())


def test_retired_sender_can_finish_without_delivering_into_new_plugin_activation():
    async def run():
        bus, sender_owner, observer = EventBus(), Activation("sender"), Activation("observer")
        sender_owner.activate()
        observer.activate()
        sender = MessageSender(bus)
        sender.add_adapter(Adapter("main"))
        bound = PluginMessageSender(sender, sender_owner)
        target = ConversationRef(connection_id="main", platform="test", scope="scope", id="group")
        diagnostics, received = [], []
        bus.subscribe(MessageSent, diagnostics.append)
        bus.subscribe(MessageSent, received.append, owner=observer)
        sender_owner.retire()
        try:
            # An already accepted background tool may still send after its plugin is unloaded.
            await bound.send(target, "completed")
            await bus.drain()
            assert not received
            assert len(diagnostics) == 1 and diagnostics[0].origin.activation_id == sender_owner.id
        finally:
            await bus.close()

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["send", "send_file"])
def test_notification_failure_does_not_replace_the_platform_result(operation):
    async def run():
        bus = EventBus()
        sender, adapter = MessageSender(bus), Adapter("main")
        sender.add_adapter(adapter)
        target = ConversationRef(connection_id="main", platform="test", scope="scope", id="group")
        content = MessageChain.text("hello") if operation == "send" else AssetRef(path="document.txt")
        failures = []
        bus.subscribe(MessageSendFailed, failures.append)
        adapter.error = OSError("connection lost after sending")
        try:
            with pytest.raises(OSError) as raised:
                await getattr(sender, operation)(target, content)
            assert raised.value is adapter.error
            await bus.drain()
            assert failures[0].error.outcome == "unknown"
            assert failures[0].request_id == adapter.calls[0][2]
        finally:
            await bus.close()
        with pytest.raises(OSError) as raised:
            await getattr(sender, operation)(target, content)
        assert raised.value is adapter.error
        adapter.error = None
        receipt = await getattr(sender, operation)(target, content)
        assert receipt.messages[0].conversation == target

    asyncio.run(run())
