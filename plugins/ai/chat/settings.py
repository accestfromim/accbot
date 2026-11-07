from pydantic import BaseModel, ConfigDict, Field, model_validator

from accbot import ErrorCode, FrameworkError, GroupMatch, PluginSettings, UserMatch

from .storage import atomic_json


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GroupRule(GroupMatch):
    enabled: bool = True
    mention_users: list[str] = Field(default_factory=list)


class Values(Config):
    default_model: str | None = None
    persona_id: str | None = "default"
    chat_prompt_id: str = Field(default="chat", min_length=1)
    owner_prompt_id: str = Field(default="owner", min_length=1)
    memory_prompt_id: str = Field(default="memory", min_length=1)
    groups: list[GroupRule] = Field(default_factory=list)
    private_users: list[UserMatch] = Field(default_factory=list)
    owner: UserMatch | None = None
    owner_controls: bool = False
    idle_seconds: float = Field(default=180, ge=1, le=86400)
    cooldown_seconds: float = Field(default=5, ge=0, le=3600)
    initial_probability: float = Field(default=0.01, ge=0, le=1)
    busy_probability: float = Field(default=0.30, ge=0, le=1)
    active_probability: float = Field(default=0.20, ge=0, le=1)
    active_busy_probability: float = Field(default=0.20, ge=0, le=1)
    busy_seconds: float = Field(default=60, gt=0, le=3600)
    context_budget: int = Field(default=200000, ge=4096, le=2000000)
    output_reserve: int = Field(default=16384, ge=256, le=200000)
    max_tool_rounds: int = Field(default=32, ge=1, le=256)

    @model_validator(mode="after")
    def check(self):
        if self.output_reserve >= self.context_budget:
            raise ValueError("输出预留必须小于上下文预算")
        if self.owner_controls and self.owner is None:
            raise ValueError("启用主人控制工具前必须设置主人身份")
        for entries, field in ((self.groups, "group_id"), (self.private_users, "user_id")):
            keys = [(entry.connection_id, getattr(entry, field)) for entry in entries]
            if len(set(keys)) != len(keys):
                raise ValueError("会话配置不能重复")
        return self


def load(data_dir):
    path = data_dir / "config" / "settings.json"
    return Values.model_validate_json(path.read_text("utf-8")) if path.exists() else Values()


class Settings(PluginSettings):
    async def read(self):
        return load(self.ctx.data_dir).model_dump()

    async def write(self, values):
        saved = Values.model_validate(values)
        if saved.default_model and saved.default_model not in {c.id for c in self.ctx.models.list()}:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "所选模型卡不存在")
        if saved.persona_id and self.ctx.personas is not None:
            self.ctx.personas.get(saved.persona_id)
        if self.ctx.prompts is not None:
            for key in (saved.chat_prompt_id, saved.owner_prompt_id, saved.memory_prompt_id):
                self.ctx.prompts.get(key)
        if (
            any(g.enabled for g in saved.groups) or saved.private_users or saved.owner
        ) and not saved.default_model:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "请先选择默认模型卡")
        atomic_json(self.ctx.data_dir / "config" / "settings.json", saved.model_dump())
        return saved.model_dump()
