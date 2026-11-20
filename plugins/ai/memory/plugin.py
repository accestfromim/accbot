import asyncio
import json
from typing import Annotated

from pydantic import Field

from accbot import ConversationRef, ErrorCode, FrameworkError
from accbot import Plugin as BasePlugin
from accbot.config.store import atomic_json
from accbot.types import FrozenModel

Token = Annotated[str, Field(pattern=r"^[a-f0-9]{24}$")]


class Selection(FrozenModel):
    token: Token


class Edit(Selection):
    content: str


class NameQuery(FrozenModel):
    tokens: tuple[Token, ...] = Field(max_length=16)


class Plugin(BasePlugin):
    async def on_activate(self):
        self.chat = self.ctx.plugins.require("ai.chat")
        self.name_file = self.ctx.data_dir / "names.json"
        self.names = json.loads(self.name_file.read_text("utf-8")) if self.name_file.exists() else {}
        self.targets = {}
        self.lookups = asyncio.Semaphore(3)

    async def on_page_request(self, action: str, values: dict) -> dict:
        if action == "list":
            self.targets = self.chat.list_memory_documents()
            return {
                "entries": [
                    {
                        "token": token,
                        "kind": "group" if isinstance(ref, ConversationRef) else "user",
                        "platform": ref.platform,
                        "scope": "" if ref.platform == "qq" else ref.scope,
                        "id": ref.id,
                        "name": self.names.get(token, ""),
                    }
                    for token, ref in self.targets.items()
                ]
            }
        if action == "names":
            tokens = dict.fromkeys(NameQuery.model_validate(values).tokens)
            names = await asyncio.gather(*(self.lookup(token) for token in tokens))
            found = {token: name for token, name in zip(tokens, names) if name}
            if self.ctx.owner.active and any(self.names.get(k) != v for k, v in found.items()):
                self.names.update(found)
                atomic_json(self.name_file, self.names)
            return {"names": found}
        if action == "read":
            token = Selection.model_validate(values).token
            return {"content": self.chat.read_memory_document(token)}
        if action == "save":
            edit = Edit.model_validate(values)
            try:
                content = self.chat.edit_memory_document(edit.token, edit.content)
            except ValueError as exc:
                raise FrameworkError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
            return {"content": content}
        raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "未知的记忆管理操作")

    async def lookup(self, token):
        ref = self.targets.get(token)
        if ref is None:
            return None
        group = isinstance(ref, ConversationRef)
        connections = self.ctx.messages.list_connections()
        if group:
            connections = sorted(connections, key=lambda c: c.id != ref.connection_id)
        async with self.lookups:
            for connection in connections:
                if connection.platform != ref.platform or (
                    ref.platform != "qq" and connection.scope != ref.scope
                ):
                    continue
                target = ConversationRef(
                    platform=ref.platform,
                    scope=connection.scope,
                    connection_id=connection.id,
                    kind="group" if group else "direct",
                    id=ref.id,
                )
                try:
                    name = await self.ctx.messages.lookup_name(target)
                except FrameworkError:
                    continue
                if name:
                    return name
        return None
