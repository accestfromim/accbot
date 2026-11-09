from datetime import datetime
from typing import Any, ClassVar

from pydantic import Field

from ..errors import ErrorInfo
from ..types import (
    AssetRef,
    ChatMessage,
    ConnectionRef,
    ConversationRef,
    FrozenModel,
    Group,
    Member,
    MessageChain,
    MessageRef,
    SendReceipt,
    User,
    new_id,
    utcnow,
)


class EventOrigin(FrozenModel):
    component: str = "framework"
    activation_id: str | None = None


class Event(FrozenModel):
    event_type: ClassVar[str] = "accbot.event"
    schema_version: ClassVar[int] = 1
    event_id: str = Field(default_factory=new_id)
    occurred_at: datetime | None = None
    observed_at: datetime = Field(default_factory=utcnow)
    origin: EventOrigin = Field(default_factory=EventOrigin)
    causation_id: str | None = None
    correlation_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            **self.model_dump(mode="json"),
        }

    @property
    def dispatch_key(self):
        return None


class PlatformEvent(Event):
    event_type = "accbot.platform"
    connection: ConnectionRef


class MessageEvent(PlatformEvent):
    event_type = "accbot.message"
    message: ChatMessage

    @property
    def dispatch_key(self):
        return self.message.conversation.key


class MessageReceived(MessageEvent):
    event_type = "accbot.message.received"


class GroupMessageReceived(MessageReceived):
    event_type = "accbot.message.group_received"


class MessageSent(PlatformEvent):
    event_type = "accbot.message.sent"
    receipt: SendReceipt
    content: MessageChain
    source_plugin_id: str | None = None

    @property
    def message(self) -> ChatMessage | None:
        """Represent one logical send once, even when the adapter returns multiple message IDs."""
        if not self.receipt.messages:
            return None
        return ChatMessage(
            ref=self.receipt.messages[0],
            sender=User(ref=self.connection.bot),
            content=self.content,
            sent_at=self.receipt.accepted_at,
            is_self=True,
        )

    @property
    def dispatch_key(self):
        return self.receipt.messages[0].conversation.key if self.receipt.messages else None


class MessageSendFailed(PlatformEvent):
    event_type = "accbot.message.send_failed"
    request_id: str
    target: ConversationRef
    error: ErrorInfo

    @property
    def dispatch_key(self):
        return self.target.key


class MessageEdited(MessageEvent):
    event_type = "accbot.message.edited"


class MessageDeleted(PlatformEvent):
    event_type = "accbot.message.deleted"
    ref: MessageRef
    operator: User | None = None

    @property
    def dispatch_key(self):
        return self.ref.conversation.key


class MemberJoined(PlatformEvent):
    event_type = "accbot.member.joined"
    group: Group
    member: Member
    inviter: User | None = None


class MemberLeft(PlatformEvent):
    event_type = "accbot.member.left"
    group: Group
    user: User
    operator: User | None = None
    reason: str = "unknown"


class FileReceived(PlatformEvent):
    event_type = "accbot.file.received"
    conversation: ConversationRef
    sender: User
    asset: AssetRef
    message_ref: MessageRef | None = None

    @property
    def dispatch_key(self):
        return self.conversation.key


class FileSent(PlatformEvent):
    event_type = "accbot.file.sent"
    conversation: ConversationRef
    asset: AssetRef
    receipt: SendReceipt

    @property
    def dispatch_key(self):
        return self.conversation.key


class CustomEvent(PlatformEvent):
    event_type = "accbot.custom"
    name: str
    data: dict[str, Any] = Field(default_factory=dict)


class TaskCompleted(Event):
    event_type = "accbot.task.completed"
    task_id: str
    owner_plugin_id: str
    activation_id: str
    result: Any = None


class TaskFailed(Event):
    event_type = "accbot.task.failed"
    task_id: str
    owner_plugin_id: str
    activation_id: str
    error: ErrorInfo


BUILTIN_EVENTS = (
    Event,
    PlatformEvent,
    MessageEvent,
    MessageReceived,
    GroupMessageReceived,
    MessageSent,
    MessageSendFailed,
    MessageEdited,
    MessageDeleted,
    MemberJoined,
    MemberLeft,
    FileReceived,
    CustomEvent,
    FileSent,
    TaskCompleted,
    TaskFailed,
)
