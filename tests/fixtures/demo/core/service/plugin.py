from accbot import Plugin as BasePlugin


class Service:
    def describe(self):
        return "accbot 演示服务"


class Plugin(BasePlugin):
    async def on_activate(self):
        self.ctx.export(Service())
