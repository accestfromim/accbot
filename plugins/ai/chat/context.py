"""Typed chat context and interrupted tool-call recovery."""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import Field

from accbot import AssetRef, ChatMessage, ModelItem, ToolContext, UserRef


class ChatContext(ToolContext):
    message: ChatMessage
    bot: UserRef | None = None
    owner: bool = False
    images: dict[str, tuple[AssetRef, ...]] = Field(default_factory=dict)


class MemoryContext(ToolContext):
    profile_users: tuple[UserRef, ...] = ()


class ContextBudgetExceeded(ValueError):
    """The requested context does not fit; history may be folded before retrying."""


@dataclass
class Entry:
    message: ChatMessage
    arrived: float

    @property
    def images(self) -> tuple[AssetRef, ...]:
        return tuple(part.asset for part in self.message.content if part.type == "image")


def close_tool_calls(items: Sequence[ModelItem]) -> list[ModelItem]:
    """After interruption, record unknown outcomes without replaying side effects."""
    answered = {item.call_id for item in items if item.kind == "tool_result"}
    return list(items) + [
        ModelItem.tool_result(call.id, {"ok": False, "error": "调用被中断，结果未知；不会自动重试"})
        for item in items
        for call in item.calls
        if call.id not in answered
    ]
