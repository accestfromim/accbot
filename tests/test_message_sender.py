import asyncio

import pytest

from accbot import AssetRef, ConnectionRef, ConversationRef, FrameworkError, MessageChain, MessageRef, UserRef
from accbot.events import EventBus, FileSent, MessageSendFailed, MessageSent
from accbot.messages import MessageSender
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
            with pytest.raises(FrameworkError):
                await getattr(sender, operation)(target.model_copy(update={"scope": "other"}), content)
            assert len(second.calls) == 1
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
