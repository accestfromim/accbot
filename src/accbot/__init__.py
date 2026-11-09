"""accbot public framework API."""

from .errors import ErrorCode, FrameworkError, Outcome
from .events import (
    CustomEvent,
    Event,
    Filter,
    GroupMatch,
    GroupMessageReceived,
    MessageReceived,
    MessageSent,
    UserMatch,
    message_filter,
    on_event,
    on_message,
)
from .models import GenerationResult, ModelItem, ModelPart, ModelQuote
from .personas import PersonaCard, PersonaRegistry
from .plugins import Plugin, PluginContext, PluginManifest
from .plugins.settings import PluginSettings, SettingsContext
from .prompts import SystemPromptCard
from .resources import ResourceBundle
from .runtime import Runtime
from .tools import Tool, ToolCall, ToolContext, ToolDefinition, ToolResult, async_tool
from .types import (
    AssetRef,
    ChatMessage,
    ConnectionRef,
    ConversationRef,
    Group,
    GroupRef,
    Member,
    MessageBuilder,
    MessageChain,
    MessageQuote,
    MessageRef,
    Segment,
    User,
    UserRef,
)

__all__ = [
    "AssetRef",
    "ChatMessage",
    "ConnectionRef",
    "ConversationRef",
    "CustomEvent",
    "ErrorCode",
    "Event",
    "Filter",
    "FrameworkError",
    "GenerationResult",
    "Group",
    "GroupMessageReceived",
    "GroupMatch",
    "GroupRef",
    "Member",
    "MessageBuilder",
    "MessageChain",
    "MessageQuote",
    "MessageReceived",
    "MessageRef",
    "MessageSent",
    "ModelItem",
    "ModelPart",
    "ModelQuote",
    "Outcome",
    "PersonaCard",
    "PersonaRegistry",
    "SystemPromptCard",
    "Plugin",
    "PluginContext",
    "PluginManifest",
    "PluginSettings",
    "SettingsContext",
    "Runtime",
    "ResourceBundle",
    "Segment",
    "Tool",
    "ToolCall",
    "ToolContext",
    "ToolDefinition",
    "ToolResult",
    "User",
    "UserRef",
    "UserMatch",
    "async_tool",
    "message_filter",
    "on_event",
    "on_message",
]
