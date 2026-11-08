from typing import Literal

from pydantic import Field

from accbot import Event

from .identity import MemoryKey


class SummarizeMemory(Event):
    event_type = "ai.chat.summarize_memory"
    memory_key: MemoryKey
    ticket: str
    reason: Literal["idle", "retry", "budget"] = "idle"
    turn_count: int = Field(default=0, ge=0)

    @property
    def dispatch_key(self):
        return self.memory_key.token
