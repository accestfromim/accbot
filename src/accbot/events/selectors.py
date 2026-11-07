"""Connection-qualified message filters, also usable as configuration fields."""

from pydantic import BaseModel, ConfigDict, Field


class UserMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    connection_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class GroupMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    connection_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
