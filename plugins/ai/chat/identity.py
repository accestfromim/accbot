"""Business memory identity, independent of the transport used to reach it."""

import hashlib

from accbot import ConversationRef
from accbot.types import FrozenModel


class MemoryKey(FrozenModel):
    platform: str
    scope: str = ""
    kind: str
    id: str

    @classmethod
    def of(cls, conversation: ConversationRef):
        # QQ group/user IDs are global. Other platforms may need a stable tenant scope.
        return cls(
            platform=conversation.platform,
            scope="" if conversation.platform == "qq" else conversation.scope,
            kind=conversation.kind,
            id=conversation.id,
        )

    @property
    def token(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:24]

    @classmethod
    def from_state(cls, state):
        if "memory_key" in state:
            return cls.model_validate(state["memory_key"])
        return cls.of(ConversationRef.model_validate(state["conversation"]))
