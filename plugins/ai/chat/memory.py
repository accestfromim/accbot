import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from accbot import ModelItem

from .context import close_tool_calls
from .identity import MemoryKey
from .storage import atomic_json


class Member(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    qq: str = Field(min_length=1)
    nickname: str
    notes: list[str]


class Memory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    summary: str
    members: list[Member]
    facts: list[str]
    open_tasks: list[str]

    @model_validator(mode="before")
    @classmethod
    def version_is_integer(cls, value):
        if not isinstance(value, dict) or type(value.get("version")) is not int:
            raise ValueError("记忆 version 必须是整数")
        return value

    @model_validator(mode="after")
    def check(self):
        if isinstance(self.version, bool) or len({m.qq for m in self.members}) != len(self.members):
            raise ValueError("记忆版本或成员无效")
        if len(self.model_dump_json().encode()) > 65536:
            raise ValueError("记忆超过 64 KiB")
        return self


def empty_memory():
    return Memory(version=1, summary="", members=[], facts=[], open_tasks=[])


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    model: str
    owner: bool
    turns: list[list[ModelItem]] = Field(default_factory=list)
    sealed: bool = False
    waiting: list[ModelItem] = Field(default_factory=list)


class Store:
    def __init__(self, root, conversation):
        self.conversation = conversation
        self.key = MemoryKey.of(conversation)
        self.directory = root / "memory" / self.key.token
        self.memory_path = self.directory / "memory.json"
        self.pending_path = self.directory / "pending.json"
        self._migrate(root)

    def _migrate(self, root):
        """Import old connection-keyed files without silently choosing conflicting histories."""
        candidates = []
        for directory in sorted((root / "memory").glob("*")):
            if directory == self.directory or not directory.is_dir():
                continue
            files = [p for p in (directory / "memory.json", directory / "pending.json") if p.exists()]
            states = [json.loads(p.read_text("utf-8")) for p in files]
            if states and MemoryKey.from_state(states[0]) == self.key:
                if any(MemoryKey.from_state(s) != self.key for s in states):
                    raise ValueError(f"记忆目录中混入不同身份：{directory}")
                candidates.append(directory)
        if not candidates:
            return
        memory, records = None, {}
        for directory in [*candidates, self.directory]:
            path = directory / "memory.json"
            if path.exists():
                state = json.loads(path.read_text("utf-8"))
                self._verify(state)
                current = {k: state[k] for k in ("memory", "last_record")}
                Memory.model_validate(current["memory"])
                if memory is not None and current != memory:
                    raise ValueError(f"同一群存在多份不同的旧记忆，请先合并：{candidates}")
                memory = current
            path = directory / "pending.json"
            if path.exists():
                state = json.loads(path.read_text("utf-8"))
                self._verify(state)
                for record in state["records"]:
                    if record["id"] in records and records[record["id"]] != record:
                        raise ValueError("旧记忆存在冲突的待整理记录，请先合并")
                    records[record["id"]] = record
        if memory is not None:
            atomic_json(self.memory_path, {**self.identity(), **memory})
        if records:
            atomic_json(self.pending_path, {**self.identity(), "records": list(records.values())})
        backup = root / "memory-legacy"
        backup.mkdir(parents=True, exist_ok=True)
        for directory in candidates:
            target = backup / directory.name
            if target.exists():
                target = backup / f"{directory.name}-{uuid4().hex}"
            directory.replace(target)

    def identity(self):
        return {
            "memory_key": self.key.model_dump(),
            # A routing hint for recovery, never part of the memory key.
            "conversation": self.conversation.model_dump(mode="json"),
        }

    def _verify(self, state):
        if MemoryKey.from_state(state) != self.key:
            raise ValueError("记忆身份不匹配")

    def load(self):
        if not self.memory_path.exists():
            return empty_memory()
        if self.memory_path.stat().st_size > 256 * 1024:
            raise ValueError("本地记忆文件过大")
        state = json.loads(self.memory_path.read_text("utf-8"))
        self._verify(state)
        return Memory.model_validate(state["memory"])

    def committed(self, record_id):
        if not self.memory_path.exists():
            return False
        state = json.loads(self.memory_path.read_text("utf-8"))
        self._verify(state)
        return state["last_record"] == record_id

    def save(self, memory, record_id):
        # Memory and its commit marker change together; a crash before journal cleanup is safe.
        atomic_json(
            self.memory_path,
            {
                **self.identity(),
                "memory": memory.model_dump(),
                "last_record": record_id,
            },
        )

    def journal(self, records):
        records = [record for record in records if record.turns or record.waiting]
        if not records:
            self.pending_path.unlink(missing_ok=True)
            return
        atomic_json(
            self.pending_path,
            {
                **self.identity(),
                "records": [r.model_dump(mode="json") for r in records],
            },
        )

    def recover(self):
        if not self.pending_path.exists():
            return []
        value = json.loads(self.pending_path.read_text("utf-8"))
        self._verify(value)
        records = [Record.model_validate(r) for r in value["records"]]
        for record in records:
            record.turns = [close_tool_calls(turn) for turn in record.turns]
            if record.waiting:
                record.turns.append(record.waiting)
                record.waiting = []
            record.sealed = True
        return records
