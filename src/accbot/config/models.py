from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from ..personas import PersonaCard, default_persona
from ..prompts import SystemPromptCard, default_prompts
from ..types import FrozenModel


class ModelCard(FrozenModel):
    id: str
    name: str = ""
    description: str = ""
    endpoint: str
    model: str
    protocol: Literal["chat", "responses"] = "chat"
    api_key_env: str | None = None
    timeout: float = Field(default=60, gt=0)
    capabilities: tuple[str, ...] = ("text", "tools")
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint must be an HTTP URL without embedded credentials")
        return value


class OneBotConfig(FrozenModel):
    id: str
    name: str = ""
    self_id: str
    enabled: bool = True
    mode: Literal["reverse_ws", "forward_ws", "http"] = "reverse_ws"
    scope: str | None = None
    http_url: str | None = None
    ws_url: str | None = None
    token_env: str | None = None
    timeout: float = Field(default=30, gt=0)

    @field_validator("http_url")
    @classmethod
    def valid_http_url(cls, value):
        return ModelCard.valid_endpoint(value) if value else None

    @field_validator("ws_url")
    @classmethod
    def valid_ws_url(cls, value):
        if not value:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"ws", "wss"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError("WebSocket 地址必须使用 ws:// 或 wss://，且不含凭据、查询参数或片段")
        _ = parsed.port  # Validate the port before starting a connection task.
        return value

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_mode(cls, value):
        if isinstance(value, dict) and "mode" not in value and value.get("http_url"):
            return {**value, "mode": "http"}
        return value

    @model_validator(mode="after")
    def validate_transport(self):
        if self.mode == "http" and not self.http_url:
            raise ValueError("HTTP 模式需要填写协议端 API 地址")
        if self.mode == "forward_ws" and not self.ws_url:
            raise ValueError("正向 WebSocket 模式需要填写协议端 WebSocket 地址")
        return self


class Settings(FrozenModel):
    active_plugins: tuple[str, ...] = ()
    plugin_directory: str = "plugins"
    models: tuple[ModelCard, ...] = ()
    personas: tuple[PersonaCard, ...] = Field(default_factory=lambda: (default_persona(),))
    system_prompts: tuple[SystemPromptCard, ...] = Field(default_factory=default_prompts)
    onebot: tuple[OneBotConfig, ...] = ()
    event_capacity: int = Field(default=1024, ge=1)
    management_token_env: str | None = None
