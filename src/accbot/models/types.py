from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from ..tools import ToolCall, ToolResult
from ..types import AssetRef, ChatMessage, ConversationRef, FrozenModel, User


class ModelPart(FrozenModel):
    type: Literal["text", "image", "file"] = "text"
    text: str | None = None
    asset: AssetRef | None = None

    @model_validator(mode="after")
    def validate_content(self):
        if self.type == "text" and self.text is None:
            raise ValueError("model text part requires text")
        if self.type in {"image", "file"} and self.asset is None:
            raise ValueError("model media part requires an asset")
        return self


class ModelQuote(FrozenModel):
    """A single quoted message; no recursive transcript or provider state."""

    message_id: str | None = None  # Optional provenance; never used to resolve or render a quote.
    sender: User | None = None
    sent_at: datetime | None = None
    content: tuple[ModelPart, ...] = ()


class ModelItem(FrozenModel):
    kind: Literal["message", "tool_call", "tool_result", "provider"] = "message"
    role: Literal["system", "developer", "user", "assistant"] = "user"
    content: tuple[ModelPart, ...] = ()
    calls: tuple[ToolCall, ...] = ()
    call_id: str | None = None
    output: Any = None
    provider_protocol: str | None = None
    provider_item: dict[str, Any] | None = None
    sender: User | None = None
    sent_at: datetime | None = None
    message_id: str | None = None
    conversation: ConversationRef | None = None
    reply_to: ModelQuote | None = None

    @model_validator(mode="after")
    def validate_content(self):
        if self.kind == "tool_result" and not self.call_id:
            raise ValueError("tool result requires call_id")
        if self.kind == "tool_call" and not self.calls:
            raise ValueError("tool call item requires calls")
        if self.kind == "provider" and (not self.provider_protocol or self.provider_item is None):
            raise ValueError("provider item requires protocol and payload")
        if self.has_chat_metadata and (
            self.kind != "message" or self.role != "user" or self.calls or self.provider_item is not None
        ):
            raise ValueError("chat metadata requires an ordinary user message")
        return self

    @property
    def has_chat_metadata(self) -> bool:
        return any(
            value is not None
            for value in (self.sender, self.sent_at, self.message_id, self.conversation, self.reply_to)
        )

    @classmethod
    def text(cls, role, text, **metadata):
        return cls(role=role, content=(ModelPart(text=text),), **metadata)

    @classmethod
    def from_message(cls, message: ChatMessage) -> "ModelItem":
        """Project a received message and its quote to text, keeping media lazy."""
        quote = message.reply_to
        return cls.text(
            "user",
            message.content.display_text,
            sender=message.display_sender,
            sent_at=message.sent_at,
            message_id=message.ref.id,
            conversation=message.conversation,
            reply_to=ModelQuote(
                sender=quote.sender,
                sent_at=quote.sent_at,
                content=(ModelPart(text=quote.content.display_text),) if quote.content is not None else (),
            )
            if quote is not None
            else None,
        )

    @classmethod
    def tool_result(cls, call_id, output):
        return cls(kind="tool_result", call_id=call_id, output=output)

    @classmethod
    def from_tool_result(cls, result: ToolResult) -> "ModelItem":
        """Convert an execution result to JSON content only at the model-context boundary."""
        return cls.tool_result(result.call_id, result.model_dump(mode="json"))


class GenerationResult(FrozenModel):
    items: tuple[ModelItem, ...]
    usage: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str | None = None
    provider_state: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self):
        return "".join(part.text or "" for item in self.items for part in item.content if part.type == "text")

    @property
    def tool_calls(self):
        return tuple(call for item in self.items for call in item.calls)
