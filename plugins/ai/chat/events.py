from typing import Literal

from accbot import Event

from .identity import MemoryKey


class SummarizeMemory(Event):
    event_type = "ai.chat.summarize_memory"
    memory_key: MemoryKey
    ticket: str
    reason: Literal["idle", "retry"] = "idle"

    @property
    def dispatch_key(self):
        return self.memory_key.token
