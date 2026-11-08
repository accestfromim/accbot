"""Immutable transport-neutral identities and chat content."""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid4().hex


class FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("immutable mapping")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(v) for v in value]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def freeze_containers(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, (dict, list, tuple)):
                object.__setattr__(self, name, freeze(value))
        return self


class UserRef(FrozenModel):
    platform: str
    scope: str
    id: str


class GroupRef(UserRef):
    pass


class User(FrozenModel):
    ref: UserRef
    name: str | None = None


class Group(FrozenModel):
    ref: GroupRef
    name: str | None = None


class Member(FrozenModel):
    user: User
    group: GroupRef
    nickname: str | None = None
    roles: tuple[str, ...] = ()


class ConnectionRef(FrozenModel):
    id: str
    platform: str
    scope: str
    bot: UserRef


class ConversationRef(FrozenModel):
    connection_id: str
    platform: str
    scope: str
    id: str
    kind: Literal["group", "direct", "channel", "thread"] = "group"
    group: GroupRef | None = None
    parent_id: str | None = None

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.connection_id, self.platform, self.scope, self.kind, self.id)


class MessageRef(FrozenModel):
    conversation: ConversationRef
    id: str


class AssetRef(FrozenModel):
    id: str = Field(default_factory=new_id)
    name: str | None = None
    media_type: str | None = None
    size: int | None = Field(default=None, ge=0)
    url: str | None = None
    path: str | None = None
    platform_file_id: str | None = None
    connection_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    data: str | None = None  # A complete data URL; never downloaded implicitly.


class Segment(FrozenModel):
    type: Literal["text", "mention", "image", "audio", "video", "file", "custom"]
    text: str | None = None
    user: UserRef | None = None
    asset: AssetRef | None = None
    name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_content(self):
        if self.type == "text" and self.text is None:
            raise ValueError("text segment requires text")
        if self.type == "mention" and self.user is None and self.name != "all":
            raise ValueError("mention requires user or name='all'")
        if self.type in {"image", "audio", "video", "file"} and self.asset is None:
            raise ValueError("media segment requires asset")
        if self.type == "custom" and not self.name:
            raise ValueError("custom segment requires a namespaced name")
        return self


class MessageChain(FrozenModel):
    segments: tuple[Segment, ...] = ()

    @classmethod
    def text(cls, text: str):
        return cls(segments=(Segment(type="text", text=text),))

    def __iter__(self):
        return iter(self.segments)

    def __add__(self, other: "MessageChain"):
        return MessageChain(segments=self.segments + other.segments)

    @property
    def plain_text(self) -> str:
        return "".join(s.text or "" for s in self.segments if s.type == "text")

    @property
    def display_text(self) -> str:
        """Readable content with media placeholders; never fetch assets implicitly."""
        labels = {"image": "图片", "audio": "语音", "video": "视频", "file": "文件"}
        parts = []
        for segment in self:
            if segment.type == "text":
                parts.append(segment.text)
            elif segment.type == "mention":
                parts.append("@" + (segment.user.id if segment.user else "全体成员"))
            else:
                parts.append(f"[{labels.get(segment.type, segment.name)}]")
        return "".join(parts)


class MessageBuilder:
    def __init__(self):
        self._segments: list[Segment] = []

    def add(self, segment: Segment):
        self._segments.append(segment)
        return self

    def text(self, text: str):
        return self.add(Segment(type="text", text=text))

    def mention(self, user: UserRef):
        return self.add(Segment(type="mention", user=user))

    def media(self, kind: Literal["image", "audio", "video", "file"], asset: AssetRef):
        return self.add(Segment(type=kind, asset=asset))

    def build(self) -> MessageChain:
        return MessageChain(segments=tuple(self._segments))


class MessageQuote(FrozenModel):
    """An independent, single-level snapshot. None content means unavailable."""

    sender: User | None = None
    content: MessageChain | None = None
    sent_at: datetime | None = None

    @classmethod
    def from_message(cls, message: "ChatMessage") -> "MessageQuote":
        return cls(sender=message.display_sender, content=message.content, sent_at=message.sent_at)


class ChatMessage(FrozenModel):
    ref: MessageRef
    sender: User
    content: MessageChain
    sent_at: datetime | None = None
    member: Member | None = None
    reply_to: MessageQuote | None = None
    is_self: bool = False

    @property
    def conversation(self) -> ConversationRef:
        return self.ref.conversation

    @property
    def display_sender(self) -> User:
        name = (self.member.nickname if self.member else None) or self.sender.name or self.sender.ref.id
        return User(ref=self.sender.ref, name=" ".join(name.split())[:128])


class SendReceipt(FrozenModel):
    request_id: str
    messages: tuple[MessageRef, ...]
    accepted_at: datetime = Field(default_factory=utcnow)
