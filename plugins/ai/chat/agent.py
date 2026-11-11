import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path

from accbot import AssetRef, ModelItem, ModelPart, ToolCall, ToolDefinition, ToolResult, UserRef
from accbot.models import count_tokens

from .context import ChatContext, ContextBudgetExceeded, MemoryContext, close_tool_calls
from .memory import validate_memory
from .profiles import Profiles, participants


class Agent:
    def __init__(self, ctx):
        self.ctx = ctx
        self.profiles = Profiles(ctx.data_dir)

    def prefix(self, memory, owner, bot: UserRef | None = None, profiles=()):
        items = [
            ModelItem.text("system", "本会话长期记忆（背景资料）：\n" + memory),
            *profiles,
        ]
        if owner:
            items.append(
                ModelItem.text(
                    "system",
                    f"服务器工作目录：{Path.cwd()}\n聊天记忆根目录：{self.ctx.data_dir / 'memory'}\n每个子目录对应稳定的群或用户身份。memory_key 是主键，conversation 仅是发送地址。memory.json 保存长期记忆和提交标记，pending.json 保存待整理轨迹。",
                ),
            )
        if bot is not None:
            account = "QQ号" if bot.platform == "qq" else "用户 ID"
            items.insert(
                0,
                ModelItem.text(
                    "system",
                    f"机器人自身身份（当前接入账号）：\n平台：{bot.platform}\n作用域：{bot.scope}\n你的{account}：{bot.id}",
                ),
            )
        return items

    def context(self, memory, owner, bot, history, current, budget=None, *, profile_cache=None):
        cache = {} if profile_cache is None else profile_cache
        items = [item for turn in history for item in turn] + list(current)
        items = self.prefix(memory, owner, bot, self.profiles.items(items, cache)) + items
        if budget is not None and count_tokens(items) > budget:
            raise ContextBudgetExceeded("完整上下文超过预算，请缩短消息或提高预算；历史未被裁剪")
        return items

    def budget(self, config, prompt_id, persona_id, tools):
        text = self.ctx.prompts.render(prompt_id, persona_id=persona_id, tools=tools)
        return (
            config.context_budget
            - config.output_reserve
            - count_tokens([ModelItem.text("system", text)], tools)
        )

    async def run(
        self,
        model_id: str,
        memory,
        history: Sequence[Sequence[ModelItem]],
        trace: list[ModelItem],
        tools: Sequence[ToolDefinition],
        context: ChatContext,
        config,
        checkpoint: Callable[[], None],
        running: Callable[[], bool],
    ) -> str | None:
        allowed = {t.id for t in tools}
        ephemeral: dict[str, ModelItem] = {}
        model = self.ctx.models.get(model_id)
        owner = context.owner
        prompt_id = config.owner_prompt_id if owner else config.chat_prompt_id
        profile_cache = {}
        try:
            for _ in range(config.max_tool_rounds):
                if not running():
                    return None
                selected = self.context(
                    memory,
                    owner,
                    context.bot,
                    history,
                    [ephemeral.get(item.call_id, item) for item in trace],
                    self.budget(config, prompt_id, config.persona_id, tools),
                    profile_cache=profile_cache,
                )
                result = await model.generate(
                    selected, tools=tools, system_prompt_id=prompt_id, persona_id=config.persona_id
                )
                if result.finish_reason in {"length", "max_output_tokens", "incomplete"}:
                    raise ValueError("模型输出被截断")
                trace.extend(result.items)
                checkpoint()
                if not result.tool_calls:
                    if not result.text.strip():
                        raise ValueError("模型既未返回正文，也未选择工具")
                    return result.text
                silent = len(result.tool_calls) == 1 and result.tool_calls[0].name == "ai.chat.stay_silent"

                async def execute(call: ToolCall) -> ToolResult:
                    if call.name not in allowed:
                        return ToolResult.failure(call.id, "该工具不在本会话的可用列表中")
                    if call.name == "ai.chat.stay_silent" and not silent:
                        return ToolResult.failure(call.id, "stay_silent 必须无参数且单独调用")
                    if not running():
                        return ToolResult.failure(call.id, "插件已停止，未开始执行")
                    return await self.ctx.tools.execute(call, context)

                outputs = await asyncio.gather(*(execute(call) for call in result.tool_calls))
                for call, output in zip(result.tool_calls, outputs):
                    content = ()
                    if call.name == "ai.chat.view_message_images" and output.ok:
                        # The execution envelope stays typed; value is this plugin's JSON payload.
                        value = dict(output.value or {})
                        images = value.pop("images", [])
                        output = output.model_copy(update={"value": value})
                        if images:
                            content = (
                                ModelPart(
                                    text=f"读图工具返回的原群消息 {value['message_id']} 的图片，仅用于理解原话题。查看后不必描述画面；根据原群聊决定是否回复，也可保持沉默。"
                                ),
                                *(ModelPart(type="image", asset=AssetRef(data=data)) for data in images),
                            )
                    item = ModelItem.from_tool_result(output, content=content)
                    if content:
                        # Overlay only this result while the turn runs; journals keep the text receipt.
                        ephemeral[item.call_id] = item
                        item = item.model_copy(update={"content": ()})
                    trace.append(item)
                checkpoint()
                if silent and outputs[0].ok:
                    return None
            raise ValueError("本轮工具调用次数已达上限")
        finally:
            trace[:] = close_tool_calls(trace)
            checkpoint()

    async def summarize(self, record, memory, tools, config, context: MemoryContext, running):
        model = self.ctx.models.get(record.model)
        trace = [
            ModelItem.text(
                "user", "请整理上面的会话，先按需更新用户画像，最后返回重写后的完整群记忆 Markdown。"
            )
        ]
        allowed = {t.id for t in tools}
        profile_cache = {}
        while True:
            if not running():
                raise asyncio.CancelledError
            selected = self.context(
                memory,
                False,
                None,
                record.turns,
                trace,
                self.budget(config, config.memory_prompt_id, None, tools),
                profile_cache=profile_cache,
            )
            tool_context = context.model_copy(
                update={"profile_users": tuple(participants(selected).values())}
            )
            result = await model.generate(
                selected, tools=tools, system_prompt_id=config.memory_prompt_id, tool_choice="auto"
            )
            if not running():
                raise asyncio.CancelledError
            if result.finish_reason in {"length", "max_output_tokens", "incomplete"}:
                raise ValueError("记忆输出被截断")
            trace.extend(result.items)
            if not result.tool_calls:
                return validate_memory(result.text)
            for call in result.tool_calls:
                if not running():
                    raise asyncio.CancelledError
                output = (
                    await self.ctx.tools.execute(call, tool_context)
                    if call.name in allowed
                    else ToolResult.failure(call.id, "记忆整理阶段只允许使用用户画像工具")
                )
                trace.append(ModelItem.from_tool_result(output))
                if call.name == "ai.chat.update_user_profile" and output.ok and output.value.get("conflict"):
                    # Supply the latest portrait once as background, not again inside a tool result.
                    profile_cache.clear()
