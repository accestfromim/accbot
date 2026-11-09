"""Structured framework settings, secret references and v1 migration."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from pydantic import SecretStr

from ..errors import ErrorCode, FrameworkError
from ..personas import PersonaCard
from ..prompts import SystemPromptCard, default_prompts
from .models import ModelCard, OneBotConfig, Settings


def atomic_json(path: Path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ConfigStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.config_dir = directory / "config"
        self.path = self.config_dir / "framework.json"
        self.models_dir = self.config_dir / "models"
        self.personas_dir = self.config_dir / "personas"
        self.prompts_dir = self.config_dir / "prompts"
        self.onebot_dir = self.config_dir / "adapters" / "onebot"
        self.secret_path = directory / "secrets" / "env.json"
        self._legacy_config = directory / "config.json"
        self._legacy_secrets = directory / "secrets.json"
        self._secrets: dict[str, SecretStr] = {}
        source = self.secret_path if self.secret_path.exists() else self._legacy_secrets
        if source.exists():
            self._secrets = {key: SecretStr(value) for key, value in json.loads(source.read_text()).items()}

    def load(self) -> Settings:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                data["active_plugins"] = json.loads(
                    (self.config_dir / "plugins.json").read_text(encoding="utf-8")
                ).get("active", [])
                data["models"] = self._read_cards(self.models_dir, ModelCard)
                data["onebot"] = self._read_cards(self.onebot_dir, OneBotConfig)
                if self.personas_dir.exists():
                    data["personas"] = self.read_personas()
                if self.prompts_dir.exists():
                    data["system_prompts"] = self.read_prompts()
                else:
                    self._migrate_prompts(data)
                settings = (
                    Settings.model_validate(data)
                    if self.personas_dir.exists()
                    else self._migrate_personas(data)
                )
                if not self.personas_dir.exists() or not self.prompts_dir.exists():
                    self.save(settings)
                return settings
            if self._legacy_config.exists():
                data = json.loads(self._legacy_config.read_text(encoding="utf-8"))
                self._migrate_prompts(data)
                settings = self._migrate_personas(data)
                self.save(settings)
                backup = self.directory / "backups" / "v1"
                backup.mkdir(parents=True, exist_ok=True)
                for source in (self._legacy_config, self._legacy_secrets):
                    if source.exists() and not (backup / source.name).exists():
                        source.replace(backup / source.name)
                return settings
            return Settings()
        except Exception as exc:
            raise FrameworkError(ErrorCode.CONFIGURATION, "框架配置无效，请检查配置目录") from exc

    @staticmethod
    def _migrate_personas(data):
        # One-time migration: persona resources no longer require an active plugin.
        legacy = Path(data.get("plugin_directory", "plugins")) / "ai" / "persona" / "group.txt"
        if "personas" not in data and legacy.exists():
            data["personas"] = [
                PersonaCard(
                    id="default",
                    name="[机器人名称]",
                    description="从 ai.persona 迁移",
                    system_prompt=legacy.read_text("utf-8"),
                )
            ]
        data["active_plugins"] = [p for p in data.get("active_plugins", []) if p != "ai.persona"]
        return Settings.model_validate(data)

    @staticmethod
    def _migrate_prompts(data):
        if "system_prompts" in data:
            return
        legacy = Path(data.get("plugin_directory", "plugins")) / "ai" / "chat" / "prompts"
        cards = []
        for card in default_prompts():
            filename = {"chat": "group", "owner": "owner", "memory": "memory"}[card.id]
            file = legacy / f"{filename}.txt"
            if file.exists():
                text = file.read_text("utf-8")
                if filename != "memory":
                    text = f"<persona>\n\n{text}\n\n<plugins_insert_prompt>\n\n<tools_insert_prompt>\n"
                card = SystemPromptCard.model_validate({**card.model_dump(), "content": text})
            cards.append(card)
        data["system_prompts"] = cards

    def read_personas(self):
        return self._read_cards(self.personas_dir, PersonaCard)

    def save_personas(self, cards):
        self._write_cards(self.personas_dir, cards)

    def read_prompts(self):
        result = []
        for metadata in sorted(self.prompts_dir.glob("*/metadata.json")):
            data = json.loads(metadata.read_text("utf-8"))
            data["content"] = metadata.with_name("prompt.md").read_text("utf-8")
            card = SystemPromptCard.model_validate(data)
            if card.id != metadata.parent.name:
                raise ValueError("system prompt ID does not match its directory")
            result.append(card)
        return result

    def save_prompts(self, cards):
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        keep = {card.id for card in cards}
        for card in cards:
            directory = self.prompts_dir / card.id
            atomic_text(directory / "prompt.md", card.content)
            atomic_json(directory / "metadata.json", card.model_dump(mode="json", exclude={"content"}))
        for metadata in self.prompts_dir.glob("*/metadata.json"):
            if metadata.parent.name not in keep:
                metadata.with_name("prompt.md").unlink(missing_ok=True)
                metadata.unlink()
                if not any(metadata.parent.iterdir()):
                    metadata.parent.rmdir()

    @staticmethod
    def _read_cards(directory, card_type):
        return [
            card_type.model_validate_json(file.read_text(encoding="utf-8"))
            for file in sorted(directory.glob("*.json"))
        ]

    @staticmethod
    def _write_cards(directory, cards):
        directory.mkdir(parents=True, exist_ok=True)
        filenames = set()
        for card in cards:
            filename = quote(card.id, safe="") + ".json"
            filenames.add(filename)
            atomic_json(directory / filename, card.model_dump(mode="json"))
        for existing in directory.glob("*.json"):
            if existing.name not in filenames:
                existing.unlink()

    def save(self, settings: Settings, *, include_prompt_cards=True):
        self._write_cards(self.models_dir, settings.models)
        if include_prompt_cards:
            self.save_personas(settings.personas)
            self.save_prompts(settings.system_prompts)
        self._write_cards(self.onebot_dir, settings.onebot)
        atomic_json(self.config_dir / "plugins.json", {"active": list(settings.active_plugins)})
        if self._secrets or self._legacy_secrets.exists():
            self._save_secrets()
        atomic_json(
            self.path,
            settings.model_dump(
                mode="json", exclude={"models", "personas", "system_prompts", "onebot", "active_plugins"}
            ),
        )

    def get_secret(self, name: str) -> str:
        if name in self._secrets:
            return self._secrets[name].get_secret_value()
        value = os.environ.get(name)
        if not value:
            raise FrameworkError(ErrorCode.CONFIGURATION, f"密钥尚未配置：{name}")
        return value

    def set_secret(self, name: str, value: str):
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "环境变量名称无效")
        previous = self._secrets.copy()
        self._secrets[name] = SecretStr(value)
        try:
            self._save_secrets()
        except BaseException:
            self._secrets = previous
            raise

    def delete_secret(self, name: str):
        previous = self._secrets.copy()
        self._secrets.pop(name, None)
        try:
            self._save_secrets()
        except BaseException:
            self._secrets = previous
            raise

    def _save_secrets(self):
        atomic_json(self.secret_path, {key: value.get_secret_value() for key, value in self._secrets.items()})

    @staticmethod
    def managed_name(kind: str, identifier: str) -> str:
        slug = re.sub(r"[^A-Z0-9]", "_", identifier.upper())[:40].strip("_") or "KEY"
        digest = hashlib.sha256(identifier.encode()).hexdigest()[:8].upper()
        return f"ACCBOT_{kind.upper()}_{slug}_{digest}"

    def secret_status(self, names=()) -> list[dict]:
        return [
            {
                "name": name,
                "configured": bool(self._secrets[name].get_secret_value())
                if name in self._secrets
                else bool(os.environ.get(name)),
                "source": "local" if name in self._secrets else "environment",
            }
            for name in sorted(set(names) | set(self._secrets))
        ]
