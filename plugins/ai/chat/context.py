"""Conversation rendering and whole-turn context budgeting, independent of providers."""

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from accbot import AssetRef, ChatMessage, ModelItem, ModelPart, ModelQuote, User
from accbot.models import render_items


@dataclass
class Entry:
    message: ChatMessage
    arrived: float
    id: str
    text: str
    nickname: str
    images: tuple[AssetRef, ...]

    @classmethod
    def from_message(cls, message: ChatMessage, arrived: float) -> "Entry":
        nickname = (
            (message.member.nickname if message.member else None)
            or message.sender.name
            or message.sender.ref.id
        )
        nickname = " ".join(nickname.split())[:128]
        text, images = [], []
        for part in message.content:
            if part.type == "text":
                text.append(part.text)
            elif part.type == "mention":
                text.append("@" + (part.name or (part.user.id if part.user else "全体成员")))
            elif part.type == "image":
                text.append("[图片]")
                images.append(part.asset)
        text = "".join(text)
        digest = hashlib.sha256(
            (nickname + "\0" + text + "".join(a.platform_file_id or a.id for a in images)).encode()
        ).hexdigest()[:16]
        return cls(message, arrived, "id_" + digest, text, nickname, tuple(images))

    def render(self, known, visible):
        message = self.message
        quote = None
        if message.reply_to:
            quoted = known.get(message.reply_to.id)
            if quoted:
                quote = ModelQuote(
                    message_id=quoted.id,
                    sender=User(ref=quoted.message.sender.ref, name=quoted.nickname),
                    sent_at=quoted.message.sent_at,
                    content=(ModelPart(text=quoted.text),)
                    if quoted.id not in visible or quoted.message.is_self
                    else (),
                )
            else:
                quote = ModelQuote(message_id=message.reply_to.id)
        return ModelItem.text(
            "user",
            self.text,
            sender=User(ref=message.sender.ref, name=self.nickname),
            sent_at=message.sent_at,
            message_id=self.id,
            conversation=message.conversation,
            reply_to=quote,
        )


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
