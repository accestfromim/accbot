import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from accbot import AssetRef, ModelItem, ModelPart, ToolCall, ToolContext, ToolDefinition, ToolResult, UserRef

from .context import close_tool_calls, cost, window


def tool_cost(tools: Sequence[ToolDefinition]) -> int:
    return len(json.dumps([t.to_schema("chat") for t in tools], ensure_ascii=False).encode())


class Agent:
    def __init__(self, ctx):
        self.ctx = ctx

    def prefix(self, memory, owner, bot: UserRef | None = None):
        items = [
            ModelItem.text("system", "本会话长期记忆（背景资料）：\n" + memory.model_dump_json()),
        ]
        if owner:
            items.insert(
                1,
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

    def prompt_cost(self, prompt_id, persona_id, tools):
        text = self.ctx.prompts.render(prompt_id, persona_id=persona_id, tools=tools)
        return cost([ModelItem.text("system", text)])

    async def run(
        self,
        model_id: str,
        memory,
        history: Sequence[Sequence[ModelItem]],
        trace: list[ModelItem],
        tools: Sequence[ToolDefinition],
        context: ToolContext,
        config,
        checkpoint: Callable[[], None],
        running: Callable[[], bool],
    ) -> str | None:
        allowed = {t.id for t in tools}
        ephemeral = []
        model = self.ctx.models.get(model_id)
        owner = context.metadata.get("owner", False)
        prompt_id = config.owner_prompt_id if owner else config.chat_prompt_id
        try:
            for _ in range(config.max_tool_rounds):
                if not running():
                    return None
                prefix = self.prefix(memory, owner, context.metadata.get("bot"))
                items = window(
                    prefix,
                    history,
                    trace + ephemeral,
                    config.context_budget
                    - config.output_reserve
                    - tool_cost(tools)
                    - self.prompt_cost(prompt_id, config.persona_id, tools),
                )
                result = await model.generate(
                    items, tools=tools, system_prompt_id=prompt_id, persona_id=config.persona_id
                )
                if result.finish_reason in {"length", "max_output_tokens", "incomplete"}:
                    raise ValueError("模型输出被截断")
                trace.extend(result.items)
                checkpoint()
                if not result.tool_calls:
                    if not result.text.strip():
                        raise ValueError("模型既未返回正文，也未选择工具")
                    return result.text
                silent = [c for c in result.tool_calls if c.name == "ai.chat.stay_silent"]
                if len(result.tool_calls) == 1 and silent and not silent[0].arguments:
                    trace.append(
                        ModelItem.from_tool_result(
                            ToolResult(call_id=silent[0].id, ok=True, value={"silent": True})
                        )
                    )
                    checkpoint()
                    return None

                async def execute(call: ToolCall) -> ToolResult:
                    if call.name not in allowed:
                        return ToolResult.failure(call.id, "该工具不在本会话的可用列表中")
                    if call.name == "ai.chat.stay_silent":
                        return ToolResult.failure(call.id, "stay_silent 必须无参数且单独调用")
                    if not running():
                        return ToolResult.failure(call.id, "插件已停止，未开始执行")
                    return await self.ctx.tools.execute(call, context)

                outputs = await asyncio.gather(*(execute(call) for call in result.tool_calls))
                for call, output in zip(result.tool_calls, outputs):
                    if call.name == "ai.chat.view_message_images" and output.ok:
                        # The execution envelope stays typed; value is this plugin's JSON payload.
                        value = dict(output.value or {})
                        images = value.pop("images", [])
                        output = output.model_copy(update={"value": value})
                        if images:
                            ephemeral.append(
                                ModelItem(
                                    role="user",
                                    content=(
                                        ModelPart(text=f"工具 {call.id} 返回的消息图片；仅用于本轮查看"),
                                        *(
                                            ModelPart(type="image", asset=AssetRef(data=data))
                                            for data in images
                                        ),
                                    ),
                                )
                            )
                    trace.append(ModelItem.from_tool_result(output))
                checkpoint()
            raise ValueError("本轮工具调用次数已达上限")
        finally:
            trace[:] = close_tool_calls(trace)
            checkpoint()

    async def summarize(self, record, memory, tools, config):
        model = self.ctx.models.get(record.model)
        task = ModelItem.text("user", "请整理上面的会话，返回更新后的完整记忆。")
        items = window(
            self.prefix(memory, False),
            record.turns,
            [task],
            config.context_budget
            - config.output_reserve
            - tool_cost(tools)
            - self.prompt_cost(config.memory_prompt_id, None, tools),
        )
        options = {"tool_choice": "none"}
        if model.card.protocol == "chat":
            options["response_format"] = {"type": "json_object"}
        else:
            options["text"] = {"format": {"type": "json_object"}}
        result = await model.generate(items, tools=tools, system_prompt_id=config.memory_prompt_id, **options)
        if result.tool_calls or result.finish_reason in {"length", "max_output_tokens", "incomplete"}:
            raise ValueError("记忆输出被截断或包含工具调用")
        from .memory import Memory

        return Memory.model_validate_json(result.text)
