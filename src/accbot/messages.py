"""Message routing and lazy asset reads. Adapters are framework-owned implementations."""

import base64
import logging
from pathlib import Path
from typing import Protocol

import httpx

from .errors import ErrorCode, FrameworkError, Outcome, error_info
from .events import EventBus, FileSent, MessageSendFailed, MessageSent
from .types import AssetRef, ConnectionRef, ConversationRef, MessageChain, MessageRef, SendReceipt, new_id

log = logging.getLogger(__name__)


class Adapter(Protocol):
    connection: ConnectionRef

    async def send(
        self, target: ConversationRef, content: MessageChain, *, reply_to: MessageRef | None, request_id: str
    ) -> SendReceipt: ...


class MessageSender:
    def __init__(self, events: EventBus):
        self.events = events
        self.adapters: dict[str, Adapter] = {}

    def add_adapter(self, adapter: Adapter):
        key = adapter.connection.id
        if key in self.adapters:
            raise FrameworkError(ErrorCode.CONFLICT, f"connection already exists: {key}")
        self.adapters[key] = adapter

    async def fetch(self, ref: MessageRef):
        """Fetch a known message reference when the adapter supports history lookup."""
        target = ref.conversation
        adapter = self.adapters.get(target.connection_id)
        if adapter is None or not hasattr(adapter, "fetch"):
            raise FrameworkError(ErrorCode.UNSUPPORTED, "adapter does not support message lookup")
        if (target.platform, target.scope) != (adapter.connection.platform, adapter.connection.scope):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "message identity does not match connection")
        message = await adapter.fetch(ref)
        if message.ref != ref:
            raise FrameworkError(ErrorCode.PROVIDER, "adapter returned a different message reference")
        return message

    async def send(
        self, target: ConversationRef, content: MessageChain | str, *, reply_to=None
    ) -> SendReceipt:
        adapter = self.adapters.get(target.connection_id)
        if adapter is None:
            raise FrameworkError(ErrorCode.NOT_FOUND, "conversation connection is not configured")
        if (target.platform, target.scope) != (adapter.connection.platform, adapter.connection.scope):
            raise FrameworkError(
                ErrorCode.INVALID_ARGUMENT, "conversation identity does not match connection"
            )
        if reply_to is not None and reply_to.conversation != target:
            raise FrameworkError(
                ErrorCode.INVALID_ARGUMENT, "cannot quote a message from another conversation"
            )
        content = MessageChain.text(content) if isinstance(content, str) else content
        if not isinstance(content, MessageChain):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "send requires a MessageChain or text")
        if not content.segments:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "empty message")
        request_id = new_id()
        try:
            receipt = await adapter.send(target, content, reply_to=reply_to, request_id=request_id)
        except Exception as exc:
            failure = error_info(exc, request_id)
            if not isinstance(exc, FrameworkError):
                failure = failure.model_copy(update={"outcome": Outcome.UNKNOWN})
            await self.events.publish(
                MessageSendFailed(
                    connection=adapter.connection, request_id=request_id, target=target, error=failure
                )
            )
            raise
        try:
            await self.events.publish(
                MessageSent(connection=adapter.connection, receipt=receipt, content=content)
            )
        except FrameworkError:
            log.warning("sent receipt notification could not be queued: %s", request_id)
        return receipt

    async def send_file(self, target: ConversationRef, asset: AssetRef) -> SendReceipt:
        adapter = self.adapters.get(target.connection_id)
        if adapter is None:
            raise FrameworkError(ErrorCode.NOT_FOUND, "conversation connection is not configured")
        if (target.platform, target.scope) != (adapter.connection.platform, adapter.connection.scope):
            raise FrameworkError(
                ErrorCode.INVALID_ARGUMENT, "conversation identity does not match connection"
            )
        request_id = new_id()
        upload = getattr(adapter, "send_file", None)
        if upload is None:
            raise FrameworkError(ErrorCode.UNSUPPORTED, "adapter does not support file uploads")
        try:
            receipt = await upload(target, asset, request_id=request_id)
        except Exception as exc:
            await self.events.publish(
                MessageSendFailed(
                    connection=adapter.connection,
                    request_id=request_id,
                    target=target,
                    error=error_info(exc, request_id),
                )
            )
            raise
        try:
            await self.events.publish(
                FileSent(connection=adapter.connection, conversation=target, asset=asset, receipt=receipt)
            )
        except FrameworkError:
            log.warning("file receipt notification could not be queued: %s", request_id)
        return receipt


class AssetReader:
    def __init__(self, http: httpx.AsyncClient, resolver=None):
        self.http = http
        self.resolver = resolver

    async def read(self, asset: AssetRef, *, max_bytes: int = 25 * 1024 * 1024) -> bytes:
        if max_bytes <= 0:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "invalid asset size limit")
        if not (asset.data or asset.path or asset.url) and asset.platform_file_id and self.resolver:
            asset = await self.resolver(asset)
        if asset.data:
            try:
                header, encoded = asset.data.split(",", 1)
                if not header.startswith("data:") or not header.endswith(";base64"):
                    raise ValueError("not base64 data URL")
                if len(encoded) > ((max_bytes + 2) // 3) * 4:
                    raise ValueError("asset too large")
                data = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "invalid or oversized asset data") from exc
        elif asset.path:
            with Path(asset.path).open("rb") as stream:
                data = stream.read(max_bytes + 1)
        elif asset.url:
            chunks, size = [], 0
            async with self.http.stream("GET", asset.url, follow_redirects=True) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "asset exceeds size limit")
                    chunks.append(chunk)
            data = b"".join(chunks)
        else:
            raise FrameworkError(ErrorCode.UNSUPPORTED, "asset has no directly readable location")
        if len(data) > max_bytes:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "asset exceeds size limit")
        return data
