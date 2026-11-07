import re
from typing import Literal

from packaging.version import Version
from pydantic import Field, field_validator

from ..types import FrozenModel


class PluginManifest(FrozenModel):
    id: str
    name: str = ""
    version: str
    entrypoint: str = "plugin:Plugin"
    description: str = ""
    kind: Literal["code", "resource"] = "code"
    tags: tuple[str, ...] = ()
    requires: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
            raise ValueError("invalid plugin id")
        return value

    @field_validator("version")
    @classmethod
    def valid_version(cls, value):
        Version(value)
        return value

    @field_validator("entrypoint")
    @classmethod
    def valid_entrypoint(cls, value):
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", value):
            raise ValueError("entrypoint must be module:Class")
        return value
