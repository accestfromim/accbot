"""Conversation rendering and whole-turn context budgeting, independent of providers."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import Field

from accbot import AssetRef, ChatMessage, ModelItem, ToolContext, UserRef
from accbot.models import render_items


class ChatContext(ToolContext):
    message: ChatMessage
    bot: UserRef | None = None
    owner: bool = False
    images: dict[str, tuple[AssetRef, ...]] = Field(default_factory=dict)


class MemoryContext(ToolContext):
    profile_users: tuple[UserRef, ...] = ()


@dataclass
class Entry:
    message: ChatMessage
    arrived: float

    @property
    def images(self) -> tuple[AssetRef, ...]:
        return tuple(part.asset for part in self.message.content if part.type == "image")


def cost(items: Sequence[ModelItem]) -> int:
    total = 0
    for item in render_items(items):
        # Do not count encoded images as millions of text tokens.
        data = item.model_dump(mode="json", exclude_none=True)
        data["content"] = []
        total += len(json.dumps(data, ensure_ascii=False).encode())
        for part in item.content:
            total += 8192 if part.type == "image" else len((part.text or "").encode())
    return total


@dataclass
class Window:
    items: list[ModelItem]
    first_turn: int


def window(
    prefix: Sequence[ModelItem] | Callable[[Sequence[ModelItem]], Sequence[ModelItem]],
    turns: Sequence[Sequence[ModelItem]],
    current: Sequence[ModelItem],
    budget: int,
) -> Window:
    def prepend(items):
        return list(prefix(items) if callable(prefix) else prefix)

    items, first_turn = list(current), len(turns)
    header, used = prepend(items), cost(current)
    if cost(header) + used > budget:
        raise ValueError("当前轮次超过上下文预算，请缩短消息或提高预算")
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        size = cost(turn)
        candidate = list(turn) + items
        candidate_header = prepend(candidate)
        if cost(candidate_header) + used + size > budget:
            break
        used += size
        items, header, first_turn = candidate, candidate_header, index
    return Window(header + items, first_turn)


def close_tool_calls(items: Sequence[ModelItem]) -> list[ModelItem]:
    """After interruption, record unknown outcomes without replaying side effects."""
    answered = {item.call_id for item in items if item.kind == "tool_result"}
    return list(items) + [
        ModelItem.tool_result(call.id, {"ok": False, "error": "调用被中断，结果未知；不会自动重试"})
        for item in items
        for call in item.calls
        if call.id not in answered
    ]
