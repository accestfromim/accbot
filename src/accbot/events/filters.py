import inspect
from collections.abc import Callable, Collection
from typing import Any

from ..types import Group, GroupRef, User, UserRef
from .models import Event, MessageReceived
from .selectors import GroupMatch, UserMatch


class Filter:
    def __init__(self, predicate: Callable[[Event], bool], *, _bind=None):
        if not callable(predicate) or inspect.iscoroutinefunction(predicate):
            raise TypeError("event filters must be synchronous callables")
        self.predicate = predicate
        self._bind = _bind

    def __call__(self, event: Event) -> bool:
        return bool(self.predicate(event))

    def bind(self, plugin) -> "Filter":
        return self._bind(plugin) if self._bind else self

    def __and__(self, other: "Filter"):
        return Filter(lambda e: self(e) and other(e), _bind=lambda p: self.bind(p) & other.bind(p))

    def __or__(self, other: "Filter"):
        return Filter(lambda e: self(e) or other(e), _bind=lambda p: self.bind(p) | other.bind(p))

    def __invert__(self):
        return Filter(lambda e: not self(e), _bind=lambda p: ~self.bind(p))


class _Unspecified:
    """Omission means unrestricted; an explicitly empty selection matches nothing."""


_ANY = _Unspecified()
type Configured[T] = T | Callable[[Any], T]
type Selection[T] = T | Collection[T] | None
type Sender = str | UserRef | User | UserMatch
type GroupSelection = str | GroupRef | Group | GroupMatch


def _resolve(value, plugin):
    if not callable(value):
        return value
    if plugin is None:
        raise TypeError("configuration getters require a plugin instance; use a decorator or .bind(plugin)")
    result = value(plugin)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError("message filter configuration getters must be synchronous")
    return result


def _selected(value, matches):
    if value is _ANY:
        return True
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(matches(item) for item in value)
    return matches(value)


def _sender_matches(target, event):
    sender = event.message.sender.ref
    if isinstance(target, UserMatch):
        return target.connection_id == event.connection.id and target.user_id == sender.id
    if isinstance(target, User):
        target = target.ref
    if isinstance(target, UserRef):
        return target == sender
    if isinstance(target, str):
        return target == sender.id
    raise TypeError("sender expects a user ID, User, UserRef or UserMatch")


def _group_matches(target, event):
    conversation = event.message.conversation
    if conversation.kind == "direct":
        return False
    group = conversation.group
    if group is None and conversation.kind == "group":
        group = GroupRef(platform=conversation.platform, scope=conversation.scope, id=conversation.id)
    if group is None:
        return False
    if isinstance(target, GroupMatch):
        return target.connection_id == event.connection.id and target.group_id == group.id
    if isinstance(target, Group):
        target = target.ref
    if isinstance(target, GroupRef):
        return target == group
    if isinstance(target, str):
        return target == group.id
    raise TypeError("group expects a group ID, Group, GroupRef or GroupMatch")


def message_filter(
    *,
    sender: Configured[Selection[Sender]] | _Unspecified = _ANY,
    group: Configured[Selection[GroupSelection]] | _Unspecified = _ANY,
    connection: Configured[Selection[str]] | _Unspecified = _ANY,
    platform: Configured[Selection[str]] | _Unspecified = _ANY,
    private: bool | None = None,
    include_self: bool = True,
) -> Filter:
    """Match explicit fields. Getters receive the current plugin and run for each event.

    Values can be single identities or collections. None and empty collections match
    nothing; omitted fields impose no restriction. No plugin-specific roles are known here.
    """
    if private is not None and type(private) is not bool:
        raise TypeError("private must be True, False or None")
    if type(include_self) is not bool:
        raise TypeError("include_self must be a boolean")
    for value in (sender, group, connection, platform):
        if inspect.iscoroutinefunction(value):
            raise TypeError("message filter configuration getters must be synchronous")

    def match(event, plugin=None):
        if not isinstance(event, MessageReceived):
            return False
        c, m = event.connection, event.message
        return (
            (include_self or not m.is_self)
            and (private is None or (m.conversation.kind == "direct") == private)
            and _selected(_resolve(platform, plugin), lambda v: v == c.platform)
            and _selected(_resolve(connection, plugin), lambda v: v == c.id)
            and _selected(_resolve(sender, plugin), lambda v: _sender_matches(v, event))
            and _selected(_resolve(group, plugin), lambda v: _group_matches(v, event))
        )

    return Filter(match, _bind=lambda plugin: Filter(lambda event: match(event, plugin)))


def on_event(event_class: type[Event], *, where: Filter | None = None):
    def decorate(func):
        declarations = list(getattr(func, "__accbot_events__", ()))
        declarations.append((event_class, where))
        func.__accbot_events__ = tuple(declarations)
        return func

    return decorate


def on_message(
    *,
    sender: Configured[Selection[Sender]] | _Unspecified = _ANY,
    group: Configured[Selection[GroupSelection]] | _Unspecified = _ANY,
    connection: Configured[Selection[str]] | _Unspecified = _ANY,
    platform: Configured[Selection[str]] | _Unspecified = _ANY,
    private: bool | None = None,
    include_self: bool = True,
    where: Filter | None = None,
):
    selected = message_filter(
        sender=sender,
        group=group,
        connection=connection,
        platform=platform,
        private=private,
        include_self=include_self,
    )
    return on_event(MessageReceived, where=selected if where is None else selected & where)
