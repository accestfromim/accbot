import functools
import inspect
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..events import Event, EventOrigin
from ..lifecycle import Activation

if TYPE_CHECKING:
    from .manager import PluginManager


class Plugin:
    """Lifecycle hooks only. Event methods are bound through @on_event / @on_message."""

    def __init__(self, ctx: "PluginContext"):
        self.ctx = ctx

    async def on_activate(self):
        pass

    async def on_deactivate(self):
        pass

    async def on_settings_changed(self, values: dict):
        """Called after plugin-owned configuration has been saved while active."""
        pass


class PluginContext:
    def __init__(
        self, runtime, manager: "PluginManager", owner: Activation, data_dir: Path, *, prompt_bundle=None
    ):
        self.runtime, self.plugins, self.owner, self.data_dir = runtime, manager, owner, data_dir
        self.models, self.messages, self.tools = runtime.models, runtime.messages, runtime.tools
        self.personas = runtime.personas
        self.prompts = runtime.prompts
        self._prompt_bundle = prompt_bundle

    @property
    def plugin_id(self):
        return self.owner.plugin_id

    def register_event(self, event_class):
        self.runtime.events.register(event_class, self.owner)

    def subscribe(self, event_class, handler, *, where=None):
        return self.runtime.events.subscribe(event_class, handler, where=where, owner=self.owner)

    def register_tool(self, handler):
        return self.tools.register(self.owner, handler, prompt_bundle=self._prompt_bundle)

    async def publish(self, event):
        self.owner.ensure_active()
        event = event.model_copy(
            update={"origin": EventOrigin(component=self.plugin_id, activation_id=self.owner.id)}
        )
        return await self.runtime.events.publish(event, source=self.owner)

    def schedule(
        self,
        event: Event | Callable[[], Event],
        when: float | timedelta | time | datetime,
        *,
        repeat: bool = False,
    ) -> str:
        """Publish after a duration or at a clock time; optionally repeat at that interval or daily."""
        return self.runtime.timers.schedule(self.owner, event, when, repeat=repeat)

    def cancel_timer(self, timer_id):
        return self.runtime.timers.cancel(timer_id, owner=self.owner)

    def export(self, api: Any):
        self.plugins._export(self.owner, api)

    def spawn(self, operation, *, correlation_id=None):
        return self.runtime.jobs.submit(self.owner, operation, correlation_id=correlation_id)

    async def stop_jobs(self, task_ids, *, timeout=0):
        """Wait briefly, then cancel selected jobs belonging to this plugin activation."""
        await self.runtime.jobs.stop(self.owner, task_ids, timeout=timeout)


class ApiHandle:
    def __init__(self, owner: Activation, api):
        self._owner, self._api = owner, api

    @property
    def api(self):
        self._owner.ensure_active()
        return self

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        self._owner.ensure_active()
        target = getattr(self._api, name)
        if not callable(target) or inspect.isclass(target):
            return target
        if inspect.iscoroutinefunction(target):

            @functools.wraps(target)
            async def async_call(*args, **kwargs):
                self._owner.acquire()
                try:
                    return await target(*args, **kwargs)
                finally:
                    self._owner.release()

            return async_call

        @functools.wraps(target)
        def sync_call(*args, **kwargs):
            self._owner.acquire()
            try:
                return target(*args, **kwargs)
            finally:
                self._owner.release()

        return sync_call
