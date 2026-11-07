"""Editable system prompt cards; choosing a card remains a caller decision."""

from pathlib import Path
from typing import Literal

from pydantic import Field

from ..types import FrozenModel


class SystemPromptCard(FrozenModel):
    version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$", max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    content: str = Field(min_length=1, max_length=65536)


def default_prompts() -> tuple[SystemPromptCard, ...]:
    root = Path(__file__).with_name("defaults")
    return tuple(
        SystemPromptCard(
            id=key, name=name, description=description, content=(root / f"{key}.md").read_text("utf-8")
        )
        for key, name, description in (
            ("chat", "群聊基础", "群聊参与和自然回复，可组合人格、插件与工具补充。"),
            ("memory", "记忆整理", "将聊天记录整理为官方聊天插件使用的 JSON 记忆。"),
            ("owner", "主人私聊", "配合官方聊天插件已校验的主人私聊通道。"),
        )
    )
