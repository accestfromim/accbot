"""Composition root. Chat policy and memory remain in explicitly activated plugins."""

import asyncio
import logging
from pathlib import Path

import httpx

from .adapters import OneBotAdapter
from .config import ConfigStore
from .errors import ErrorCode, FrameworkError
from .events import EventBus
from .messages import AssetReader, MessageSender
from .models import ModelRegistry
from .personas import PersonaRegistry
from .plugins import PluginManager
from .prompts.registry import PromptRegistry
from .timers import TimerManager
from .tools import JobManager, ToolRegistry

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, directory: str | Path = ".accbot", *, http: httpx.AsyncClient | None = None):
        self.store = ConfigStore(Path(directory).resolve())
        self.settings = self.store.load()
        if not self.store.path.exists():
            self.store.save(self.settings)
        self.http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self.events = EventBus(self.settings.event_capacity)
        self.jobs = JobManager(self.events)
        self.tools = ToolRegistry(self.jobs)
        self.timers = TimerManager(self.events)
        self.messages = MessageSender(self.events)
        self.assets = AssetReader(self.http, self._resolve_asset)
        self.personas = PersonaRegistry(self.settings.personas, loader=self.store.read_personas)
        self.prompts = PromptRegistry(
            self.settings.system_prompts, self.personas, self.tools, loader=self.store.read_prompts
        )
        self.models = ModelRegistry(
            self.settings.models, self.http, self.store.get_secret, prompts=self.prompts
        )
        plugin_path = Path(self.settings.plugin_directory)
        self.plugins = PluginManager(self, plugin_path.resolve(), self.store.directory / "data" / "plugins")
        self.management_lock = asyncio.Lock()
        self.started = False
        self._configure_connections(self.settings.onebot)

    async def _resolve_asset(self, asset):
        adapter = self.messages.adapters.get(asset.connection_id)
        if adapter is None or not hasattr(adapter, "resolve_asset"):
            raise FrameworkError(ErrorCode.UNSUPPORTED, "asset connection cannot resolve this platform file")
        return await adapter.resolve_asset(asset)

    def _configure_connections(self, configs):
        adapters = {}
        for config in configs:
            if not config.enabled:
                continue
            if config.id in adapters:
                raise FrameworkError(ErrorCode.CONFIGURATION, "duplicate connection id")
            adapters[config.id] = OneBotAdapter(config, self.events, self.http, self.store.get_secret)
        self.messages.adapters = adapters

    async def start(self):
        if self.started:
            return
        self.plugins.discover()
        self.plugins.plan(self.settings.active_plugins)
        await self.events.start()
        await self.plugins.activate(self.settings.active_plugins)
        self.started = True
        for config in self.settings.onebot:
            if config.enabled:
                self.messages.adapters[config.id].start()

    def _save_actual(self):
        self.settings = self.settings.model_copy(
            update={"active_plugins": tuple(sorted(self.plugins.active))}
        )
        self.store.save(self.settings, include_prompt_cards=False)

    async def set_active(self, selected):
        async with self.management_lock:
            desired = set(selected)
            self.plugins.plan(desired)
            try:
                for key in reversed(self.plugins.plan(self.plugins.active)):
                    if key not in desired and key in self.plugins.active:
                        await self.plugins.deactivate(key)
                await self.plugins.activate(desired)
            finally:
                self._save_actual()
            return sorted(self.plugins.active)

    async def reload(self, plugin_id):
        async with self.management_lock:
            try:
                return await self.plugins.reload(plugin_id)
            finally:
                self._save_actual()

    async def replace_models(self, cards):
        async with self.management_lock:
            self._replace_models(cards)

    def _replace_models(self, cards):
        if len({card.id for card in cards}) != len(cards):
            raise FrameworkError(ErrorCode.CONFLICT, "模型卡 ID 重复")
        updated = self.settings.model_copy(update={"models": tuple(cards)})
        self.store.save(updated, include_prompt_cards=False)
        self.models.replace(cards)
        self.settings = updated

    async def unload(self, plugin_id):
        async with self.management_lock:
            removed = await self.plugins.deactivate(plugin_id)
            self._save_actual()
            return {"unloaded": removed, "active": sorted(self.plugins.active)}

    def _replace_personas(self, cards):
        registry = PersonaRegistry(cards)
        updated = self.settings.model_copy(update={"personas": registry.list()})
        self.store.save_personas(registry.list())
        self.personas.replace(registry.list())
        self.settings = updated

    async def replace_personas(self, cards):
        async with self.management_lock:
            self._replace_personas(cards)

    def _replace_prompts(self, cards):
        cards = tuple(cards)
        if len({card.id for card in cards}) != len(cards):
            raise FrameworkError(ErrorCode.CONFLICT, "系统提示词卡 ID 重复")
        updated = self.settings.model_copy(update={"system_prompts": cards})
        self.store.save_prompts(cards)
        self.prompts.replace(cards)
        self.settings = updated

    async def replace_prompts(self, cards):
        async with self.management_lock:
            self._replace_prompts(cards)

    async def replace_connections(self, configs):
        async with self.management_lock:
            await self._replace_connections(configs)

    async def _replace_connections(self, configs):
        if len({c.id for c in configs}) != len(configs):
            raise FrameworkError(ErrorCode.CONFLICT, "适配器配置 ID 重复")
        previous, updated = self.messages.adapters, {}
        for config in configs:
            if not config.enabled:
                continue
            old = previous.get(config.id)
            updated[config.id] = (
                old
                if old and old.config == config
                else OneBotAdapter(config, self.events, self.http, self.store.get_secret)
            )
        self.store.save(
            self.settings.model_copy(update={"onebot": tuple(configs)}), include_prompt_cards=False
        )
        self.messages.adapters = updated
        for key, adapter in previous.items():
            if updated.get(key) is not adapter:
                await adapter.close()
        self.settings = self.settings.model_copy(update={"onebot": tuple(configs)})
        if self.started:
            for key, adapter in updated.items():
                if previous.get(key) is not adapter:
                    adapter.start()

    async def close(self):
        await self.plugins.close()
        await self.timers.close()
        await self.jobs.close()
        for adapter in self.messages.adapters.values():
            await adapter.close()
        await self.events.close()
        if self._owns_http:
            await self.http.aclose()
        self.started = False
