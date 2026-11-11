import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from accbot import ModelItem

from .context import close_tool_calls
from .identity import MemoryKey
from .storage import atomic_json


def memory_text(value) -> str:
    """Read Markdown, or render the previously stored JSON body without losing fields."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("无法识别记忆正文")
    sections = [
        ("概况", [value["summary"]] if value["summary"] else []),
        (
            "群内人物与关系",
            [f"{m['nickname']}（用户 ID：{m['qq']}）：{'；'.join(m['notes'])}" for m in value["members"]],
        ),
        ("长期共识", value["facts"]),
        ("待接续", value["open_tasks"]),
    ]
    return "\n\n".join(
        f"## {title}\n" + "\n".join(f"- {row}" for row in rows) for title, rows in sections if rows
    )


def validate_memory(text: str) -> str:
    text = text.strip()
    if not text or len(text.encode("utf-8")) > 65536:
        raise ValueError("群记忆需要非空 Markdown，最多 64 KiB")
    return text


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
                current["memory"] = memory_text(current["memory"])
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
            return ""
        if self.memory_path.stat().st_size > 256 * 1024:
            raise ValueError("本地记忆文件过大")
        state = json.loads(self.memory_path.read_text("utf-8"))
        self._verify(state)
        return memory_text(state["memory"])

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
                "memory": validate_memory(memory),
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
