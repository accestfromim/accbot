"""Persistence and validation are owned by this plugin."""

import json
import os
import tempfile

from pydantic import BaseModel, ConfigDict

from accbot import ErrorCode, FrameworkError, PluginSettings


class Values(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    default_model: str | None = None
    count_messages: bool = True


class Settings(PluginSettings):
    @property
    def path(self):
        return self.ctx.data_dir / "config" / "settings.json"

    async def read(self):
        if not self.path.exists():
            return Values().model_dump()
        return Values.model_validate_json(self.path.read_text(encoding="utf-8")).model_dump()

    async def write(self, values):
        saved = Values.model_validate(values)
        if saved.default_model and saved.default_model not in {card.id for card in self.ctx.models.list()}:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "所选模型卡不存在，请重新选择")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(saved.model_dump(), file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return saved.model_dump()
