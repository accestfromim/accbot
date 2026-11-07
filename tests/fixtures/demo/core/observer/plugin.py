from accbot import Plugin as BasePlugin
from accbot.events import MessageReceived

from .settings import Settings


class Plugin(BasePlugin):
    async def on_activate(self):
        self.messages_seen = 0
        await self.on_settings_changed(await Settings(self.ctx).read())
        self.service = self.ctx.plugins.require("demo.service")
        self.service.describe()
        self.ctx.subscribe(MessageReceived, self.on_message)

    async def on_message(self, event):
        if self.count_messages:
            self.messages_seen += 1

    async def on_settings_changed(self, values):
        self.default_model = values["default_model"]
        self.count_messages = values["count_messages"]
