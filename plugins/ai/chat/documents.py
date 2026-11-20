"""Access to the saved Markdown documents, independent of active chat sessions."""

from dataclasses import dataclass
from pathlib import Path

from accbot import ConversationRef, ErrorCode, FrameworkError, UserRef

from .identity import MemoryKey
from .memory import memory_text, read_memory_state, validate_memory
from .profiles import Profiles
from .storage import atomic_json


@dataclass(frozen=True)
class Entry:
    target: ConversationRef | UserRef
    path: Path


class MemoryDocuments:
    def __init__(self, root: Path, profiles: Profiles):
        self.root, self.profiles = root, profiles
        self.entries: dict[str, Entry] = {}

    def list(self) -> dict[str, ConversationRef | UserRef]:
        entries = {}
        for path in sorted((self.root / "memory").glob("*/memory.json")):
            state = read_memory_state(path)
            key = MemoryKey.from_state(state)
            if key.kind != "group":
                continue
            target = ConversationRef.model_validate(state["conversation"])
            if MemoryKey.of(target) != key or key.token in entries:
                raise ValueError("群记忆的身份不一致或重复，请检查本地记忆目录")
            entries[key.token] = Entry(target, path)
        for user in self.profiles.users():
            entries[MemoryKey.of_user(user).token] = Entry(user, self.profiles.path(user))
        self.entries = entries
        return {token: entry.target for token, entry in entries.items()}

    def _entry(self, token: str):
        entry = self.entries.get(token)
        if entry is None or not entry.path.is_file():
            self.list()
            entry = self.entries.get(token)
        if entry is None:
            raise FrameworkError(ErrorCode.NOT_FOUND, "找不到这份记忆，请刷新列表")
        return entry

    def read(self, token: str) -> str:
        entry = self._entry(token)
        if isinstance(entry.target, UserRef):
            return self.profiles.read(entry.target).content
        return memory_text(self._group_state(entry)["memory"])

    @staticmethod
    def _group_state(entry):
        state = read_memory_state(entry.path)
        if MemoryKey.from_state(state) != MemoryKey.of(entry.target):
            raise ValueError("群记忆身份已改变，请刷新列表")
        return state

    def edit(self, token: str, content: str) -> str:
        entry = self._entry(token)
        if isinstance(entry.target, UserRef):
            self.profiles.update(entry.target, content)
        else:
            state = self._group_state(entry)
            content = validate_memory(content)
            atomic_json(entry.path, {**state, "memory": content})
        return content
