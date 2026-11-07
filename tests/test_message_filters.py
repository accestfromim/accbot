import asyncio
from types import SimpleNamespace

import pytest

from accbot import (
    ChatMessage,
    ConnectionRef,
    ConversationRef,
    Filter,
    Group,
    GroupMatch,
    GroupRef,
    MessageChain,
    MessageReceived,
    MessageRef,
    User,
    UserMatch,
    UserRef,
    message_filter,
)


def incoming(*, user="u", connection="main", group="g", private=False, scope="tenant", is_self=False):
    source = ConnectionRef(
        id=connection,
        platform="example",
        scope=scope,
        bot=UserRef(platform="example", scope=scope, id="bot"),
    )
    conversation = ConversationRef(
        connection_id=connection,
        platform=source.platform,
        scope=scope,
        id="dm-room-id" if private else group,
        kind="direct" if private else "group",
    )
    return MessageReceived(
        connection=source,
        message=ChatMessage(
            ref=MessageRef(conversation=conversation, id="message"),
            sender=User(ref=UserRef(platform=source.platform, scope=scope, id=user)),
            content=MessageChain.text("hello"),
            is_self=is_self,
        ),
    )


@pytest.mark.parametrize(
    "target",
    [
        "u",
        UserMatch(connection_id="main", user_id="u"),
        UserRef(platform="example", scope="tenant", id="u"),
        User(ref=UserRef(platform="example", scope="tenant", id="u")),
    ],
)
def test_sender_is_an_explicit_user_identity(target):
    selected = message_filter(sender=target, private=True, include_self=False)
    # Private conversation IDs need not equal the sender ID on other platforms.
    assert selected(incoming(private=True))
    assert not selected(incoming(private=True, user="other"))
    assert not selected(incoming())
    assert not selected(incoming(private=True, is_self=True))


@pytest.mark.parametrize(
    "target",
    [
        "g",
        GroupMatch(connection_id="main", group_id="g"),
        GroupRef(platform="example", scope="tenant", id="g"),
        Group(ref=GroupRef(platform="example", scope="tenant", id="g")),
    ],
)
def test_group_is_an_explicit_group_identity(target):
    selected = message_filter(group=target)
    assert selected(incoming())
    assert not selected(incoming(group="another"))
    assert not selected(incoming(private=True))


@pytest.mark.parametrize("empty", [None, [], (), frozenset()])
def test_empty_config_is_not_a_wildcard(empty):
    event = incoming()
    assert message_filter()(event)
    assert not message_filter(sender=empty)(event)
    assert not message_filter(group=empty)(event)
    config = SimpleNamespace(contact=empty, groups=empty)
    assert not message_filter(sender=lambda p: p.contact).bind(config)(event)
    assert not message_filter(group=lambda p: p.groups).bind(config)(event)


def test_connection_pairs_scope_and_configuration_replacement():
    config = SimpleNamespace(
        contacts=[
            UserMatch(connection_id="main", user_id="u"),
            UserMatch(connection_id="other", user_id="v"),
        ],
    )
    selected = message_filter(sender=lambda p: p.contacts, private=True).bind(config)
    assert selected(incoming(private=True))
    assert selected(incoming(private=True, user="v", connection="other"))
    assert not selected(incoming(private=True, user="v"))
    assert not selected(incoming(private=True, connection="other"))
    config.contacts = [UserRef(platform="example", scope="new-tenant", id="u")]
    assert not selected(incoming(private=True))
    assert selected(incoming(private=True, scope="new-tenant"))
    group = message_filter(group=[GroupMatch(connection_id="main", group_id="g")])
    assert not group(incoming(connection="other"))


def test_configuration_getters_compose_with_regular_filters_and_reject_async():
    selected = message_filter(sender=lambda p: p.user) & ~Filter(lambda e: e.message.is_self)
    a, b = SimpleNamespace(user="u"), SimpleNamespace(user="v")
    first, second = selected.bind(a), selected.bind(b)
    assert first(incoming()) and not second(incoming())
    assert not first(incoming(is_self=True))
    assert second(incoming(user="v"))
    a.user = None
    assert not first(incoming())
    with pytest.raises(TypeError, match="plugin instance"):
        selected(incoming())

    async def invalid(_):
        return "u"

    with pytest.raises(TypeError, match="synchronous"):
        message_filter(sender=invalid)
    with pytest.raises(TypeError, match="synchronous"):
        message_filter(sender=lambda p: invalid(p)).bind(a)(incoming())
    with pytest.raises(TypeError):
        message_filter(private="direct")


def test_decorator_filters_before_handler_and_keeps_instance_configuration(runtime, write_plugin):
    code = """from types import SimpleNamespace
from accbot import Plugin as Base, on_message
class Plugin(Base):
    async def on_activate(self):
        self.config = SimpleNamespace(contact=None, groups=[])
        self.received = []
    @on_message(sender=lambda plugin: plugin.config.contact, private=True, include_self=False)
    def direct(self, event):
        self.received.append(("direct", event.message.sender.ref.id))
    @on_message(group=lambda plugin: plugin.config.groups, include_self=False)
    def group(self, event):
        self.received.append(("group", event.message.conversation.id))
"""
    write_plugin("sample", code)

    async def run():
        try:
            await runtime.start()
            await runtime.set_active(["sample"])
            plugin = runtime.plugins.instances["sample"][0]

            async def send(event):
                assert not await (await runtime.events.publish(event)).wait()

            await send(incoming(private=True))
            await send(incoming())
            assert not plugin.received
            plugin.config.contact = UserMatch(connection_id="main", user_id="u")
            plugin.config.groups = [GroupMatch(connection_id="other", group_id="g")]
            for event in (
                incoming(private=True),
                incoming(private=True, connection="other"),
                incoming(),
                incoming(connection="other"),
            ):
                await send(event)
            assert plugin.received == [("direct", "u"), ("group", "g")]
            plugin.config = SimpleNamespace(contact=UserMatch(connection_id="main", user_id="v"), groups=[])
            await send(incoming(private=True))
            await send(incoming(private=True, user="v"))
            assert plugin.received[-1] == ("direct", "v") and len(plugin.received) == 3
            await runtime.reload("sample")
            fresh = runtime.plugins.instances["sample"][0]
            await send(incoming(private=True, user="v"))
            assert not fresh.received
            fresh.config.contact = UserMatch(connection_id="main", user_id="u")
            await send(incoming(private=True))
            assert fresh.received == [("direct", "u")] and len(plugin.received) == 3
            await runtime.unload("sample")
            await send(incoming(private=True))
            assert len(fresh.received) == 1
        finally:
            await runtime.close()

    asyncio.run(run())
