"""Plugin-owned configuration handlers, independent of business activation."""

import importlib
import inspect
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..errors import ErrorCode, FrameworkError
from ..types import new_id
from .frontend import FrontendBundle
from .loader import _GenerationFinder

if TYPE_CHECKING:
    from ..models import ModelRegistry
    from ..personas import PersonaRegistry
    from ..prompts.registry import PromptRegistry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingsContext:
    plugin_id: str
    data_dir: Path
    models: "ModelRegistry"
    personas: "PersonaRegistry | None" = None
    prompts: "PromptRegistry | None" = None


class PluginSettings:
    def __init__(self, ctx: SettingsContext):
        self.ctx = ctx

    async def read(self) -> dict:
        return {}

    async def write(self, values: dict) -> dict:
        raise FrameworkError(ErrorCode.UNSUPPORTED, "此插件未提供配置保存接口")


@dataclass
class PluginPage:
    plugin_id: str
    directory: Path
    bundle: FrontendBundle
    sources: dict[str, bytes]
    activation_id: str | None
    epoch: str

    @property
    def revision(self):
        return self.bundle.revision(self.sources, self.activation_id or self.epoch)

    def verify(self, revision):
        if revision != self.revision:
            raise FrameworkError(ErrorCode.CONFLICT, "插件已发生变化，请重新打开配置页面")

    @contextmanager
    def settings(self, manager):
        if "settings.py" not in self.sources:
            raise FrameworkError(ErrorCode.UNSUPPORTED, "此插件未提供 settings.py 配置接口")
        finder = _GenerationFinder(f"_accbot_settings_{new_id()}", self.directory, sources=self.sources)
        try:
            finder.install()
            cls = getattr(importlib.import_module(f"{finder.namespace}.settings"), "Settings")
            if not inspect.isclass(cls) or not issubclass(cls, PluginSettings):
                raise FrameworkError(ErrorCode.CONFIGURATION, "settings.Settings 必须继承 PluginSettings")
            data_dir = manager.data_root / self.plugin_id
            data_dir.mkdir(parents=True, exist_ok=True)
            yield cls(
                SettingsContext(
                    self.plugin_id,
                    data_dir,
                    manager.runtime.models,
                    manager.runtime.personas,
                    manager.runtime.prompts,
                )
            )
        except FrameworkError:
            raise
        except ValidationError as exc:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "插件配置校验未通过，请检查填写的字段") from exc
        except Exception as exc:
            log.exception("plugin settings handler failed: %s", self.plugin_id)
            raise FrameworkError(ErrorCode.PLUGIN, "插件配置处理失败，请查看服务日志") from exc
        finally:
            finder.remove()


def get_page(manager, plugin_id):
    if plugin_id not in manager.manifests:
        raise FrameworkError(ErrorCode.NOT_FOUND, "未找到插件")
    directory = manager.paths[plugin_id]
    if plugin_id in manager.active:
        bundle, sources = manager.frontends[plugin_id]
        activation_id = manager.instances[plugin_id][1].id
    else:
        bundle = FrontendBundle.capture(directory)
        sources = _GenerationFinder("_unused", directory).sources
        activation_id = None
    if "index.html" not in bundle.files:
        raise FrameworkError(ErrorCode.NOT_FOUND, "此插件未提供配置页面")
    epoch = manager.frontend_epochs.setdefault(plugin_id, new_id())
    return PluginPage(plugin_id, directory, bundle, sources, activation_id, epoch)
