import asyncio

from accbot import Plugin as BasePlugin
from accbot.tools import async_tool


class Plugin(BasePlugin):
    @async_tool(description="短暂等待后回传文本，用于验证后台任务。")
    async def delayed_echo(self, text: str) -> str:
        await asyncio.sleep(0.2)
        return text
