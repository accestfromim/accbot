from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from ..config import ModelCard, OneBotConfig


class Selection(BaseModel):
    selected: list[str]


class SecretUpdate(BaseModel):
    value: SecretStr


class ModelDraft(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    name: str = ""
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    key: SecretStr | None = None
    protocol: Literal["auto", "chat", "responses"] = "auto"
    description: str = ""
    timeout: float = Field(default=300, gt=0, allow_inf_nan=False)


class ProviderDraft(BaseModel):
    endpoint: str
    key: SecretStr | None = None
    existing_id: str | None = None


class OneBotDraft(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    name: str = ""
    self_id: str = Field(pattern=r"^\d+$")
    mode: Literal["reverse_ws", "forward_ws", "http"] = "reverse_ws"
    http_url: str | None = None
    ws_url: str | None = None
    token: SecretStr | None = None
    scope: str | None = None

    @field_validator("http_url")
    @classmethod
    def validate_url(cls, value):
        return ModelCard.valid_endpoint(value) if value else None

    @model_validator(mode="after")
    def require_transport_url(self):
        if self.mode == "http" and not self.http_url:
            raise ValueError("HTTP 模式需要协议端 API 地址")
        if self.mode == "forward_ws" and not self.ws_url:
            raise ValueError("正向 WebSocket 模式需要协议端 WebSocket 地址")
        return self

    @field_validator("ws_url")
    @classmethod
    def validate_ws_url(cls, value):
        return OneBotConfig.valid_ws_url(value)


class SessionLogin(BaseModel):
    token: SecretStr
