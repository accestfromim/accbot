"""Stateless rendering of structured messages before provider encoding."""

from collections.abc import Sequence
from itertools import groupby

from ..types import User
from .types import ModelItem, ModelPart


def _sender(user: User) -> str:
    return (
        f"{user.name or user.ref.id}（用户 ID：{user.ref.id}；"
        f"平台：{user.ref.platform}；作用域：{user.ref.scope}）"
    )


def _record(item: ModelItem) -> list[ModelPart]:
    if not item.has_chat_metadata:
        return list(item.content)
    lines = []
    if item.conversation is not None:
        ref = item.conversation
        lines.append(f"会话：{ref.platform} / {ref.kind} / {ref.id}")
    if item.sender is not None:
        lines.append(f"发送者：{_sender(item.sender)}")
    if item.sent_at is not None:
        lines.append(f"发送时间：{item.sent_at.isoformat()}")
    if item.message_id is not None:
        lines.append(f"消息id: {item.message_id}")
    parts = [ModelPart(text="\n".join(lines) + "\n内容：\n"), *item.content]
    if item.reply_to is not None:
        quote = item.reply_to
        lines = ["\n引用消息："]
        if quote.sender is not None:
            lines.append(f"引用发送者：{_sender(quote.sender)}")
        if quote.sent_at is not None:
            lines.append(f"引用时间：{quote.sent_at.isoformat()}")
        lines.append("引用原文：" if quote.content else "引用原文不可用。")
        parts.extend([ModelPart(text="\n".join(lines) + "\n"), *quote.content])
    return parts


def render_items(items: Sequence[ModelItem]) -> list[ModelItem]:
    """Merge adjacent ordinary user items, preserving all other items verbatim."""
    result, pending = [], []

    def flush():
        if not pending:
            return
        if len(pending) == 1 and not pending[0].has_chat_metadata:
            result.append(pending[0])
        else:
            group = any(i.conversation and i.conversation.kind != "direct" for i in pending)
            parts = [ModelPart(text="下面是群聊历史：\n" if group else "下面是对话记录：\n")]
            for index, item in enumerate(pending):
                if index:
                    parts.append(ModelPart(text="\n\n---\n\n"))
                parts.extend(_record(item))
            content = []
            for is_text, group in groupby(parts, key=lambda p: p.type == "text"):
                content.extend([ModelPart(text="".join(p.text for p in group))] if is_text else group)
            result.append(ModelItem(role="user", content=tuple(content)))
        pending.clear()

    for item in items:
        if item.kind == "message" and item.role == "user" and not item.calls and item.provider_item is None:
            pending.append(item)
        else:
            flush()
            result.append(item)
    flush()
    return result
