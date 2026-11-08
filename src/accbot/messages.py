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

    def get_connection(self, connection_id: str) -> ConnectionRef | None:
        adapter = self.adapters.get(connection_id)
        return adapter.connection if adapter is not None else None

    def _adapter(self, target: ConversationRef) -> Adapter:
        adapter = self.adapters.get(target.connection_id)
        if adapter is None:
            raise FrameworkError(ErrorCode.NOT_FOUND, "conversation connection is not configured")
        if (target.platform, target.scope) != (adapter.connection.platform, adapter.connection.scope):
            raise FrameworkError(
                ErrorCode.INVALID_ARGUMENT, "conversation identity does not match connection"
            )
        return adapter

    async def fetch(self, ref: MessageRef):
        """Fetch a known message reference when the adapter supports history lookup."""
        adapter = self._adapter(ref.conversation)
        if not hasattr(adapter, "fetch"):
            raise FrameworkError(ErrorCode.UNSUPPORTED, "adapter does not support message lookup")
        message = await adapter.fetch(ref)
        if message.ref != ref:
            raise FrameworkError(ErrorCode.PROVIDER, "adapter returned a different message reference")
        return message

    async def send(
        self, target: ConversationRef, content: MessageChain | str, *, reply_to=None
    ) -> SendReceipt:
        if reply_to is not None and reply_to.conversation != target:
            raise FrameworkError(
                ErrorCode.INVALID_ARGUMENT, "cannot quote a message from another conversation"
            )
        content = MessageChain.text(content) if isinstance(content, str) else content
        if not isinstance(content, MessageChain):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "send requires a MessageChain or text")
        if not content.segments:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "empty message")
        return await self._send(target, "send", content, reply_to=reply_to)

    async def send_file(self, target: ConversationRef, asset: AssetRef) -> SendReceipt:
        return await self._send(target, "send_file", asset)

    async def _notify(self, event):
        try:
            await self.events.publish(event)
        except Exception:
            # Notification failure must not change the result of the platform operation.
            log.exception("send notification could not be queued: %s", event.event_type)

    async def _send(self, target, operation, content, **options) -> SendReceipt:
        adapter = self._adapter(target)
        send = getattr(adapter, operation, None)
        if send is None:
            raise FrameworkError(ErrorCode.UNSUPPORTED, f"adapter does not support {operation}")
        request_id = new_id()
        try:
            receipt = await send(target, content, request_id=request_id, **options)
        except Exception as exc:
            failure = error_info(exc, request_id)
            if not isinstance(exc, FrameworkError):
                failure = failure.model_copy(update={"outcome": Outcome.UNKNOWN})
            await self._notify(
                MessageSendFailed(
                    connection=adapter.connection, request_id=request_id, target=target, error=failure
                )
            )
            raise
        event = (
            MessageSent(connection=adapter.connection, receipt=receipt, content=content)
            if operation == "send"
            else FileSent(connection=adapter.connection, conversation=target, asset=content, receipt=receipt)
        )
        await self._notify(event)
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
