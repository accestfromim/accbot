from accbot import Plugin as BasePlugin
from accbot.tools import Tool


class Plugin(BasePlugin):
    @Tool(description="返回传入的文本，不执行外部操作。")
    async def echo(self, text: str) -> str:
        return text
