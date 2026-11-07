"""OneBot 11 events and actions over HTTP and forward/reverse WebSocket."""

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from ..config import OneBotConfig
from ..errors import ErrorCode, FrameworkError, Outcome
from ..events import (
    CustomEvent,
    EventOrigin,
    FileReceived,
    GroupMessageReceived,
    MemberJoined,
    MemberLeft,
    MessageDeleted,
    MessageReceived,
)
from ..types import (
    AssetRef,
    ChatMessage,
    ConnectionRef,
    ConversationRef,
    Group,
    GroupRef,
    Member,
    MessageChain,
    MessageRef,
    Segment,
    SendReceipt,
    User,
    UserRef,
)
from .onebot_ws import run_forward


class OneBotAdapter:
    def __init__(self, config: OneBotConfig, events, http: httpx.AsyncClient, secret_resolver):
        self.config, self.events, self.http, self.secret_resolver = config, events, http, secret_resolver
        scope = config.scope or config.id
        self.connection = ConnectionRef(
            id=config.id,
            platform="qq",
            scope=scope,
            bot=UserRef(platform="qq", scope=scope, id=config.self_id),
        )
        self.socket = None
        self.retrying = False
        self._runner: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._seen: OrderedDict[tuple, None] = OrderedDict()
        self._sent: OrderedDict[str, None] = OrderedDict()

    @property
    def status(self):
        if self.socket is not None:
            return "connected"
        if self.config.mode == "forward_ws":
            return "reconnecting" if self.retrying else "connecting"
        return "waiting" if self.config.mode == "reverse_ws" else "active"

    def start(self):
        if self.config.enabled and self.config.mode == "forward_ws" and self._runner is None:
            self._runner = asyncio.create_task(run_forward(self), name=f"onebot:{self.config.id}")

    def user(self, value, name=None):
        return User(ref=UserRef(platform="qq", scope=self.connection.scope, id=str(value)), name=name)

    def conversation(self, value, kind="group"):
        return ConversationRef(
            connection_id=self.connection.id,
            platform="qq",
            scope=self.connection.scope,
            id=str(value),
            kind=kind,
            group=GroupRef(platform="qq", scope=self.connection.scope, id=str(value))
            if kind == "group"
            else None,
        )

    def _common(self, raw):
        occurred = datetime.fromtimestamp(raw["time"], tz=timezone.utc) if raw.get("time") else None
        return {
            "connection": self.connection,
            "occurred_at": occurred,
            "origin": EventOrigin(component=f"adapter:{self.connection.id}"),
        }

    def decode(self, raw: dict):
        try:
            return self._decode(raw)
        except FrameworkError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "invalid OneBot event structure") from exc

    def _decode(self, raw: dict):
        if str(raw.get("self_id", self.config.self_id)) != self.config.self_id:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "OneBot self_id does not match connection")
        common = self._common(raw)
        post = raw.get("post_type")
        if post in {"message", "message_sent"}:
            kind = "group" if raw.get("message_type") == "group" else "direct"
            sender_data = raw.get("sender") or {}
            sender = self.user(raw["user_id"], sender_data.get("nickname"))
            conversation = self.conversation(raw["group_id"] if kind == "group" else raw["user_id"], kind)
            segments, reply = [], None
            source = raw.get("message", [])
            if isinstance(source, str):
                # Preserve CQ strings instead of pretending they are plain text.
                source = [{"type": "custom", "data": {"raw_cq": source}}]
            for item in source:
                t, data = item["type"], item.get("data", {})
                if t == "text":
                    segments.append(Segment(type="text", text=data.get("text", "")))
                elif t == "at":
                    segments.append(
                        Segment(type="mention", name="all")
                        if data.get("qq") == "all"
                        else Segment(type="mention", user=self.user(data["qq"]).ref)
                    )
                elif t == "reply":
                    reply = MessageRef(conversation=conversation, id=str(data["id"]))
                elif t in {"image", "record", "video", "file"}:
                    url = data.get("url")
                    file = str(data.get("file", ""))
                    if not url and file.startswith(("https://", "http://")):
                        url = file
                    segments.append(
                        Segment(
                            type="audio" if t == "record" else t,
                            asset=AssetRef(
                                url=url,
                                platform_file_id=file or None,
                                connection_id=self.connection.id,
                                metadata={"group_id": conversation.id}
                                if conversation.kind == "group"
                                else {},
                                name=data.get("name"),
                                size=int(data["file_size"]) if data.get("file_size") else None,
                            ),
                        )
                    )
                else:
                    segments.append(Segment(type="custom", name=f"onebot.{t}", data=data))
            is_self = sender.ref == self.connection.bot or post == "message_sent"
            message = ChatMessage(
                ref=MessageRef(conversation=conversation, id=str(raw["message_id"])),
                sender=sender,
                content=MessageChain(segments=tuple(segments)),
                reply_to=reply,
                sent_at=common["occurred_at"],
                is_self=is_self,
                member=Member(
                    user=sender,
                    group=conversation.group,
                    nickname=sender_data.get("card"),
                    roles=(sender_data["role"],) if sender_data.get("role") else (),
                )
                if conversation.group
                else None,
            )
            return (GroupMessageReceived if kind == "group" else MessageReceived)(message=message, **common)
        notice = raw.get("notice_type")
        if post == "notice" and notice in {"group_increase", "group_decrease"}:
            conversation = self.conversation(raw["group_id"])
            user = self.user(raw["user_id"])
            group = Group(ref=conversation.group)
            operator = self.user(raw["operator_id"]) if raw.get("operator_id") else None
            if notice == "group_increase":
                return MemberJoined(
                    group=group, member=Member(user=user, group=group.ref), inviter=operator, **common
                )
            return MemberLeft(
                group=group,
                user=user,
                operator=operator,
                reason="left" if raw.get("sub_type") == "leave" else "kicked",
                **common,
            )
        if post == "notice" and notice in {"group_recall", "friend_recall"}:
            conversation = self.conversation(
                raw["group_id"] if notice == "group_recall" else raw["user_id"],
                "group" if notice == "group_recall" else "direct",
            )
            return MessageDeleted(
                ref=MessageRef(conversation=conversation, id=str(raw["message_id"])),
                operator=self.user(raw.get("operator_id", raw["user_id"])),
                **common,
            )
        if post == "notice" and notice == "group_upload":
            data = raw["file"]
            return FileReceived(
                conversation=self.conversation(raw["group_id"]),
                sender=self.user(raw["user_id"]),
                asset=AssetRef(
                    platform_file_id=str(data["id"]),
                    name=data.get("name"),
                    size=data.get("size"),
                    connection_id=self.connection.id,
                    metadata={"group_id": str(raw["group_id"]), "busid": data.get("busid")},
                ),
                **common,
            )
        detail = (
            raw.get("notice_type")
            or raw.get("request_type")
            or raw.get("meta_event_type")
            or post
            or "unknown"
        )
        return CustomEvent(name=f"onebot.{post}.{detail}", data=raw, **common)

    @staticmethod
    def _remember(mapping, key, limit=4096):
        mapping[key] = None
        if len(mapping) > limit:
            mapping.popitem(last=False)

    async def ingest(self, raw):
        if "echo" in raw and "post_type" not in raw:
            future = self._pending.get(str(raw["echo"]))
            if future and not future.done():
                future.set_result(raw)
            return None
        event = self.decode(raw)
        if isinstance(event, MessageReceived):
            key = (event.message.conversation.key, event.message.ref.id)
            if key in self._seen:
                return None
            self._remember(self._seen, key)
            if event.message.is_self:
                # Outbound receipts are the authoritative sent event. An uncorrelated
                # own-client message remains visible as a platform-specific event.
                if event.message.ref.id in self._sent:
                    return None
                event = CustomEvent(name="onebot.self_message", data=raw, **self._common(raw))
        return await self.events.publish(event)

    def attach(self, socket):
        if self.socket is not None:
            raise FrameworkError(ErrorCode.CONFLICT, "OneBot connection already has a WebSocket")
        self.socket = socket

    def detach(self, socket):
        if self.socket is socket:
            self.socket = None
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        FrameworkError(ErrorCode.PROVIDER, "OneBot disconnected", outcome=Outcome.UNKNOWN)
                    )

    async def call(self, action, params, request_id):
        try:
            if self.socket is not None:
                future = asyncio.get_running_loop().create_future()
                self._pending[request_id] = future
                try:
                    await self.socket.send_json({"action": action, "params": params, "echo": request_id})
                    result = await asyncio.wait_for(asyncio.shield(future), self.config.timeout)
                finally:
                    self._pending.pop(request_id, None)
                    if not future.done():
                        future.cancel()
                    elif not future.cancelled():
                        future.exception()
            elif self.config.http_url and self.config.mode != "forward_ws":
                headers = {}
                if self.config.token_env:
                    headers["Authorization"] = "Bearer " + self.secret_resolver(self.config.token_env)
                response = await self.http.post(
                    self.config.http_url.rstrip("/") + "/" + action,
                    json=params,
                    headers=headers,
                    timeout=self.config.timeout,
                )
                if response.is_error:
                    raise FrameworkError(
                        ErrorCode.PROVIDER,
                        f"OneBot returned HTTP {response.status_code}",
                        outcome=Outcome.UNKNOWN,
                    )
                result = response.json()
            else:
                raise FrameworkError(ErrorCode.PROVIDER, "OneBot has no connected transport")
            if result.get("status") != "ok" or result.get("retcode", 0) != 0:
                raise FrameworkError(
                    ErrorCode.PROVIDER, "OneBot action was rejected", operation_id=request_id
                )
            return result.get("data") or {}
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise FrameworkError(
                ErrorCode.TIMEOUT, "OneBot action timed out", operation_id=request_id, outcome=Outcome.UNKNOWN
            ) from exc
        except (httpx.HTTPError, ValueError, OSError, ConnectionClosed, WebSocketDisconnect) as exc:
            raise FrameworkError(
                ErrorCode.PROVIDER,
                "invalid OneBot action response",
                operation_id=request_id,
                outcome=Outcome.UNKNOWN,
            ) from exc

    async def send(self, target, content, *, reply_to, request_id):
        message = []
        if reply_to:
            message.append({"type": "reply", "data": {"id": reply_to.id}})
        for segment in content:
            if segment.type == "text":
                message.append({"type": "text", "data": {"text": segment.text}})
            elif segment.type == "mention":
                if segment.user and (
                    segment.user.platform != "qq" or segment.user.scope != self.connection.scope
                ):
                    raise FrameworkError(
                        ErrorCode.INVALID_ARGUMENT, "mention identity does not match connection"
                    )
                message.append({"type": "at", "data": {"qq": segment.user.id if segment.user else "all"}})
            elif segment.type in {"image", "audio", "video"}:
                asset = segment.asset
                location = asset.url or asset.path or asset.platform_file_id
                if asset.data:
                    if ";base64," not in asset.data:
                        raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "OneBot media data must be base64")
                    location = "base64://" + asset.data.split(";base64,", 1)[1]
                if not location:
                    raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "OneBot media has no location")
                message.append(
                    {
                        "type": "record" if segment.type == "audio" else segment.type,
                        "data": {"file": location},
                    }
                )
            elif segment.type == "custom" and segment.name.startswith("onebot."):
                message.append({"type": segment.name.removeprefix("onebot."), "data": dict(segment.data)})
            else:
                raise FrameworkError(ErrorCode.UNSUPPORTED, f"OneBot cannot send segment: {segment.type}")
        if target.kind not in {"group", "direct"}:
            raise FrameworkError(ErrorCode.UNSUPPORTED, "OneBot 11 supports group and direct messages")
        kind = "group" if target.kind == "group" else "private"
        data = await self.call(
            f"send_{kind}_msg",
            {
                "group_id" if kind == "group" else "user_id": target.id,
                "message": message,
            },
            request_id,
        )
        if "message_id" not in data:
            raise FrameworkError(ErrorCode.PROVIDER, "OneBot returned no message id", outcome=Outcome.UNKNOWN)
        message_id = str(data["message_id"])
        self._remember(self._sent, message_id)
        return SendReceipt(request_id=request_id, messages=(MessageRef(conversation=target, id=message_id),))

    async def send_file(self, target, asset, *, request_id):
        """Use upload actions supported by NapCat and other OneBot implementations.

        A path refers to the protocol host's filesystem; a URL is resolved by that host.
        Uploads need not create a message, so an accepted receipt can have no message refs.
        """
        if target.kind not in {"group", "direct"}:
            raise FrameworkError(ErrorCode.UNSUPPORTED, "unsupported OneBot file destination")
        location = asset.url or asset.path
        if not location:
            raise FrameworkError(
                ErrorCode.INVALID_ARGUMENT, "file upload requires a protocol-host path or URL"
            )
        group = target.kind == "group"
        data = await self.call(
            "upload_group_file" if group else "upload_private_file",
            {
                "group_id" if group else "user_id": target.id,
                "file": location,
                "name": asset.name or "file",
            },
            request_id,
        )
        refs = (MessageRef(conversation=target, id=str(data["message_id"])),) if "message_id" in data else ()
        return SendReceipt(request_id=request_id, messages=refs)

    async def fetch(self, ref):
        from ..types import new_id

        if ref.conversation.kind not in {"group", "direct"}:
            raise FrameworkError(ErrorCode.UNSUPPORTED, "unsupported OneBot message destination")
        result = await self.call("get_msg", {"message_id": ref.id}, new_id())
        target = ref.conversation
        kind = "group" if target.kind == "group" else "private"
        if result.get("message_type") != kind or str(result.get("message_id")) != ref.id:
            raise FrameworkError(ErrorCode.PROVIDER, "OneBot returned a different message")
        if target.kind == "group" and str(result.get("group_id", target.id)) != target.id:
            raise FrameworkError(ErrorCode.PROVIDER, "OneBot returned a different conversation")
        sender_id = result.get("sender", {}).get("user_id")
        if sender_id is None:
            raise FrameworkError(ErrorCode.PROVIDER, "OneBot returned no message sender")
        if target.kind == "direct" and str(sender_id) not in {target.id, self.connection.bot.id}:
            raise FrameworkError(ErrorCode.PROVIDER, "OneBot returned an unrelated private message")
        event = self.decode({**result, "post_type": "message", "user_id": sender_id, "group_id": target.id})
        # get_msg's sender can be the bot itself; the requested private peer remains the destination.
        reply = event.message.reply_to
        if reply:
            reply = reply.model_copy(update={"conversation": target})
        return event.message.model_copy(update={"ref": ref, "reply_to": reply})

    async def resolve_asset(self, asset):
        from ..types import new_id

        group_id = asset.metadata.get("group_id")
        if not group_id:
            raise FrameworkError(ErrorCode.UNSUPPORTED, "OneBot file URL lookup requires group metadata")
        params = {"group_id": group_id, "file_id": asset.platform_file_id}
        if asset.metadata.get("busid") is not None:
            params["busid"] = asset.metadata["busid"]
        result = await self.call("get_group_file_url", params, new_id())
        if not result.get("url"):
            raise FrameworkError(ErrorCode.PROVIDER, "OneBot returned no file URL")
        return asset.model_copy(update={"url": result["url"]})

    async def close(self):
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        if self.socket:
            socket = self.socket
            self.detach(socket)
            await socket.close()
