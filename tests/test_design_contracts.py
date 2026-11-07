"""Executable checks for the public contracts listed in docs/design.md."""

import asyncio
from typing import Annotated

import httpx
import pytest
from pydantic import BaseModel, Field, ValidationError

from accbot import (
    AssetRef,
    ChatMessage,
    ConnectionRef,
    ConversationRef,
    Event,
    FrameworkError,
    GroupMessageReceived,
    MessageBuilder,
    MessageChain,
    MessageRef,
    ModelItem,
    ModelPart,
    Tool,
    ToolCall,
    ToolContext,
    User,
    UserRef,
    async_tool,
)
from accbot.config import ModelCard
from accbot.events import EventBus, MessageSent, TaskCompleted, TaskFailed
from accbot.lifecycle import Activation
from accbot.models.client import ModelClient
from accbot.tools import JobManager, ToolRegistry
from accbot.types import SendReceipt


def message(number="1", *, user="u", group="g", connection="main", is_self=False, text="hello"):
    sender = UserRef(platform="qq", scope="scope", id=user)
    conversation = ConversationRef(connection_id=connection, platform="qq", scope="scope", id=group)
    return GroupMessageReceived(
        connection=ConnectionRef(
            id=connection, platform="qq", scope="scope", bot=UserRef(platform="qq", scope="scope", id="bot")
        ),
        message=ChatMessage(
            ref=MessageRef(conversation=conversation, id=number),
            sender=User(ref=sender),
            content=MessageChain.text(text),
            is_self=is_self,
        ),
    )


def test_decorated_filtered_and_custom_handlers_reload_without_virtual_methods(runtime, write_plugin):
    code = """from accbot import Plugin as Base, Event, Filter, on_event, on_message
class Ping(Event):
    event_type = 'sample.ping'
    value: int
class Plugin(Base):
    async def on_activate(self):
        self.seen = []
        self.ctx.register_event(Ping)
        self.ctx.export(self)
    @on_message(platform='qq', connection='main', group='g', sender='u', include_self=False,
                where=Filter(lambda e: e.message.content.plain_text == 'hello'))
    async def arbitrary_method_name(self, event):
        self.seen.append(event.message.ref.id)
    @on_event(Ping, where=Filter(lambda e: e.value > 0))
    def another_arbitrary_name(self, event):
        self.seen.append(event.value)
    def ping(self, value):
        return Ping(value=value)
"""
    path = write_plugin("sample", code)

    async def run():
        await runtime.start()
        await runtime.set_active(["sample"])
        old = runtime.plugins.instances["sample"][0]
        for event in [
            message(),
            message("user", user="other"),
            message("group", group="other"),
            message("connection", connection="other"),
            message("self", is_self=True),
            message("text", text="ignored"),
            old.ping(-1),
            old.ping(2),
        ]:
            assert not await (await runtime.events.publish(event)).wait()
        assert old.seen == ["1", 2]
        old_ping = old.ping(10)
        path.joinpath("plugin.py").write_text(code.replace("e.value > 0", "e.value > 5"))
        await runtime.reload("sample")
        new = runtime.plugins.instances["sample"][0]
        for event in [new.ping(2), new.ping(6), message("new")]:
            await (await runtime.events.publish(event)).wait()
        assert new.seen == [6, "new"] and old.seen == ["1", 2]
        with pytest.raises(FrameworkError):
            await runtime.events.publish(old_ping)
        await runtime.unload("sample")
        assert "sample.ping" not in runtime.events._types
        await (await runtime.events.publish(message("unloaded"))).wait()
        assert new.seen == [6, "new"]
        await runtime.close()

    asyncio.run(run())


def test_instance_filter_composition_rebinds_on_reload(runtime, write_plugin):
    write_plugin(
        "dynamic",
        """from accbot import Filter, Plugin as Base, on_message
class Plugin(Base):
    async def on_activate(self):
        self.user = "u"
        self.seen = []
    @on_message(sender=lambda plugin: plugin.user, private=False, where=~Filter(lambda e: e.message.is_self))
    def receive(self, event):
        self.seen.append(event.message.ref.id)
""",
    )

    async def run():
        try:
            await runtime.start()
            await runtime.set_active(["dynamic"])
            old = runtime.plugins.instances["dynamic"][0]
            await (await runtime.events.publish(message())).wait()
            old.user = "other"
            await (await runtime.events.publish(message("ignored"))).wait()
            await (await runtime.events.publish(message("other", user="other"))).wait()
            assert old.seen == ["1", "other"]
            await runtime.reload("dynamic")
            fresh = runtime.plugins.instances["dynamic"][0]
            await (await runtime.events.publish(message("fresh"))).wait()
            assert fresh.seen == ["fresh"] and old.seen == ["1", "other"]
        finally:
            await runtime.close()

    asyncio.run(run())


def test_timer_cancellation_is_owned_and_cleans_up_before_task_starts():
    from accbot.timers import TimerManager

    async def run():
        bus = EventBus()
        timers = TimerManager(bus)
        owner, other = Activation("a"), Activation("b")
        owner.activate()
        other.activate()
        seen = []
        bus.subscribe(Event, seen.append)
        timer = timers.schedule(owner, Event(), delay=0)
        with pytest.raises(FrameworkError):
            timers.cancel(timer, owner=other)
        assert timers.cancel(timer, owner=owner)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not seen and not timers.tasks and not timers.owners
        assert not timers.cancel(timer, owner=owner)
        await timers.close()
        await bus.close()

    asyncio.run(run())


def test_queued_custom_event_expires_even_for_base_class_subscribers():
    async def run():
        bus = EventBus()
        owner = Activation("source")
        observer = Activation("observer")
        owner.activate()
        observer.activate()
        gate, entered = asyncio.Event(), asyncio.Event()
        seen = []

        class Ping(Event):
            event_type = "source.ping"

        async def consume(event):
            if type(event) is Event:
                entered.set()
                await gate.wait()
            seen.append(event)

        bus.register(Ping, owner)
        bus.subscribe(Event, consume, owner=observer)
        first = await bus.publish(Event())
        await entered.wait()
        pending = await bus.publish(Ping())
        await asyncio.sleep(0)
        owner.retire()
        gate.set()
        await first.wait()
        assert not await pending.wait()
        assert len(seen) == 1 and type(seen[0]) is Event
        observer.retire()
        await bus.close()

    asyncio.run(run())


def test_accepted_event_cannot_migrate_to_a_later_subscription():
    async def run():
        bus = EventBus()
        old = Activation("plugin")
        old.activate()
        seen = []
        bus.subscribe(Event, seen.append, owner=old)
        receipt = await bus.publish(Event())
        # No yield: dispatcher has not processed the accepted event yet.
        old.retire()
        new = Activation("plugin")
        new.activate()
        bus.subscribe(Event, seen.append, owner=new)
        assert not await receipt.wait() and not seen
        await (await bus.publish(Event())).wait()
        assert len(seen) == 1
        new.retire()
        await bus.close()

    asyncio.run(run())


def test_staged_subscription_does_not_receive_events_accepted_before_activation():
    async def run():
        bus = EventBus()
        owner = Activation("staging")
        seen = []
        bus.subscribe(Event, seen.append, owner=owner)
        receipt = await bus.publish(Event())
        owner.activate()
        assert not await receipt.wait() and not seen
        await (await bus.publish(Event())).wait()
        assert len(seen) == 1
        owner.retire()
        await bus.close()

    asyncio.run(run())


def test_background_result_is_observable_but_not_delivered_to_new_plugins(runtime, write_plugin):
    write_plugin(
        "sample",
        """import asyncio
from accbot import Plugin as Base, async_tool, on_event
from accbot.events import TaskCompleted
class Plugin(Base):
    async def on_activate(self):
        self.gate = asyncio.Event()
        self.results = []
    @async_tool
    async def work(self):
        await self.gate.wait()
        return 'old task completed'
    @on_event(TaskCompleted)
    def record_result(self, event):
        self.results.append(event.result)
""",
    )

    async def run():
        await runtime.start()
        await runtime.set_active(["sample"])
        old = runtime.plugins.instances["sample"][0]
        diagnostic = []
        runtime.events.subscribe(TaskCompleted, diagnostic.append)
        accepted = await runtime.tools.execute(ToolCall(name="sample.work"))
        await runtime.reload("sample")
        new = runtime.plugins.instances["sample"][0]
        old.gate.set()
        await runtime.jobs.drain()
        await runtime.events.drain()
        assert diagnostic[0].task_id == accepted.task_id
        assert diagnostic[0].result == "old task completed"
        assert not new.results and not old.results
        new.gate.set()
        await runtime.tools.execute(ToolCall(name="sample.work"))
        await runtime.jobs.drain()
        await runtime.events.drain()
        assert new.results == ["old task completed"]
        await runtime.close()

    asyncio.run(run())


def test_retired_context_cannot_leave_registration_residue(runtime, write_plugin):
    write_plugin("sample")

    async def run():
        await runtime.start()
        await runtime.set_active(["sample"])
        ctx = runtime.plugins.instances["sample"][0].ctx
        await runtime.unload("sample")

        class Ping(Event):
            event_type = "sample.late"

        @Tool
        def action():
            return 1

        for operation in [
            lambda: ctx.register_event(Ping),
            lambda: ctx.subscribe(Event, lambda _: None),
            lambda: ctx.register_tool(action),
            lambda: ctx.export(object()),
        ]:
            with pytest.raises(FrameworkError):
                operation()
        assert "sample.late" not in runtime.events._types
        assert not runtime.events._subscriptions and not runtime.tools._entries and not runtime.plugins.apis
        await runtime.close()

    asyncio.run(run())


def test_message_chain_is_preserved_in_send_events_and_json():
    user = UserRef(platform="qq", scope="scope", id="u")
    builder = (
        MessageBuilder()
        .text("hello")
        .mention(user)
        .media("image", AssetRef(url="https://example.test/a.png"))
    )
    chain = builder.build()
    builder.text("later")
    receipt = SendReceipt(request_id="send", messages=(message().message.ref,))
    event = MessageSent(connection=message().connection, receipt=receipt, content=chain)
    restored = EventBus().decode(event.to_dict())
    assert isinstance(restored.content, MessageChain)
    assert [part.type for part in restored.content] == ["text", "mention", "image"]
    assert (
        restored.content.plain_text == "hello"
        and restored.dispatch_key == receipt.messages[0].conversation.key
    )
    with pytest.raises(ValidationError):
        MessageSent(connection=event.connection, receipt=receipt, content=["hello"])


def test_tool_constraints_schema_and_typed_arguments_agree():
    class Query(BaseModel):
        term: str

    @Tool
    async def search(query: Query, limit: Annotated[int, Field(ge=1, le=3)], ctx: ToolContext):
        assert isinstance(query, Query) and isinstance(ctx, ToolContext)
        return {"term": query.term, "limit": limit}

    async def run():
        bus = EventBus()
        registry = ToolRegistry(JobManager(bus))
        owner = Activation("sample")
        definition = registry.register(owner, search)
        owner.activate()
        schema = definition.to_schema("chat")["function"]
        assert schema["parameters"]["properties"]["limit"]["maximum"] == 3
        assert "ctx" not in schema["parameters"]["properties"]
        assert definition.to_schema("responses")["name"] == schema["name"]
        schema["parameters"]["properties"].clear()
        assert "limit" in definition.parameters["properties"]
        bad = await registry.execute(
            ToolCall(name=definition.id, arguments={"query": {"term": "q"}, "limit": 4})
        )
        assert not bad.ok and bad.error.code == "invalid_argument"
        good = await registry.execute(
            ToolCall(name=definition.id, arguments={"query": {"term": "q"}, "limit": 2})
        )
        assert good.ok and good.value["term"] == "q"
        context = ModelItem.from_tool_result(good)
        assert context.call_id == good.call_id and context.output["ok"]
        owner.retire()
        await bus.close()

    asyncio.run(run())


def test_background_timeout_uses_timeout_error_contract():
    async def run():
        bus = EventBus()
        jobs = JobManager(bus)
        registry = ToolRegistry(jobs)
        owner = Activation("sample")

        @async_tool(timeout=0.001)
        async def slow():
            await asyncio.Event().wait()

        registry.register(owner, slow)
        owner.activate()
        failures = []
        bus.subscribe(TaskFailed, failures.append)
        await registry.execute(ToolCall(name="sample.slow"))
        await jobs.drain()
        await bus.drain()
        assert failures[0].error.code == "timeout" and failures[0].error.outcome == "unknown"
        owner.retire()
        await bus.close()

    asyncio.run(run())


def test_model_boundary_rejects_raw_messages_and_tools_before_http():
    async def run():
        def unexpected(request):
            pytest.fail("invalid inputs must not reach the provider")

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
            model = ModelClient(
                ModelCard(id="test", endpoint="https://example.test/v1", model="test"), http, lambda _: ""
            )
            with pytest.raises(FrameworkError, match="ModelItem"):
                await model.generate(["raw string"])
            with pytest.raises(FrameworkError, match="ToolDefinition"):
                await model.generate([ModelItem.text("user", "hi")], tools=[{"name": "raw tool"}])
        for kwargs in ({"type": "image"}, {"type": "text"}):
            with pytest.raises(ValidationError):
                ModelPart(**kwargs)
        with pytest.raises(ValidationError):
            ModelItem(kind="tool_result")

    asyncio.run(run())
