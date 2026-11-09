"""Shared, persistent persona resources. Applying them is a plugin decision."""

from pathlib import Path
from typing import Literal

from pydantic import Field

from ..errors import ErrorCode, FrameworkError
from ..types import FrozenModel


class PersonaCard(FrozenModel):
    version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$", max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    system_prompt: str = Field(min_length=1, max_length=65536)


def default_persona() -> PersonaCard:
    return PersonaCard.model_validate_json(Path(__file__).with_name("default.json").read_text("utf-8"))


class PersonaRegistry:
    def __init__(self, cards=(), *, loader=None):
        self._loader = loader
        self.replace(cards)

    def replace(self, cards):
        cards = tuple(cards)
        if len({c.id for c in cards}) != len(cards):
            raise FrameworkError(ErrorCode.CONFLICT, "人格卡 ID 重复")
        self._cards = {card.id: card for card in cards}

    def list(self) -> tuple[PersonaCard, ...]:
        if self._loader is not None:
            try:
                self.replace(self._loader())
            except (OSError, ValueError) as exc:
                raise FrameworkError(ErrorCode.CONFIGURATION, "人格卡文件无效，请检查配置目录") from exc
        return tuple(self._cards.values())

    def get(self, card_id: str) -> PersonaCard:
        self.list()
        try:
            return self._cards[card_id]
        except KeyError:
            raise FrameworkError(ErrorCode.NOT_FOUND, f"人格卡不存在：{card_id}") from None
