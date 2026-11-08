from uuid import uuid4

from accbot import MessageReceived, Tool, ToolDefinition, on_event, on_message
from accbot import Plugin as BasePlugin

from .agent import Agent
from .context import ChatContext, MemoryContext
from .events import SummarizeMemory
from .identity import MemoryKey
from .owner_tools import OwnerTools
from .sessions import Sessions
from .settings import Values, load


class Plugin(BasePlugin):
    async def on_activate(self):
        self.config = load(self.ctx.data_dir)
        if self.config.default_model:
            self.ctx.models.get(self.config.default_model)
        if self.config.persona_id:
            self.ctx.personas.get(self.config.persona_id)
        for key in (self.config.chat_prompt_id, self.config.owner_prompt_id, self.config.memory_prompt_id):
            self.ctx.prompts.get(key)
        self.network = self.ctx.plugins.require("ai.tools")
        self.agent = Agent(self.ctx)
        self.sessions = Sessions(self)
        controls = OwnerTools(self.authorize_owner)
        for name in ("read_file", "write_file", "execute_shell"):
            self.ctx.register_tool(getattr(controls, name))
        self.ctx.register_event(SummarizeMemory)
        await self.sessions.recover()
        self.ctx.export(self)

    async def on_deactivate(self):
        if hasattr(self, "sessions"):
            await self.sessions.close()

    async def on_settings_changed(self, values):
        config = Values.model_validate(values)
        # Finish journaling the old configuration before starting fresh sessions.
        await self.sessions.close()
        self.config = config
        self.sessions = Sessions(self)
        await self.sessions.recover()

    @on_message(group=lambda plugin: [g for g in plugin.config.groups if g.enabled], include_self=False)
    async def receive_group_message(self, event: MessageReceived):
        channel = await self.sessions.accept(event, tools=self.tool_list())
        if channel:  # Only this message's group, and only when it actually wakes the bot.
            self.sessions.cancel(channel, "idle")
            channel.idle_ticket = uuid4().hex
            channel.idle_timer = self.ctx.schedule(
                SummarizeMemory(memory_key=channel.store.key, ticket=channel.idle_ticket),
                delay=self.config.idle_seconds,
            )

    @on_message(sender=lambda plugin: plugin.config.owner, private=True, include_self=False)
    async def receive_owner_message(self, event: MessageReceived):
        tools = self.ctx.tools.list() if self.config.owner_controls else self.tool_list(kind="direct")
        channel = await self.sessions.accept(event, tools=tools)
        if channel:
            self.sessions.cancel(channel, "idle")
            channel.idle_ticket = uuid4().hex
            channel.idle_timer = self.ctx.schedule(
                SummarizeMemory(memory_key=channel.store.key, ticket=channel.idle_ticket),
                delay=self.config.idle_seconds,
            )

    @on_message(
        sender=lambda plugin: [user for user in plugin.config.private_users if user != plugin.config.owner],
        private=True,
        include_self=False,
    )
    async def receive_private_message(self, event: MessageReceived):
        await self.sessions.accept(event, tools=self.tool_list(kind="direct"))

    @on_event(SummarizeMemory)
    async def summarize_memory(self, event: SummarizeMemory):
        await self.sessions.summarize(event)

    def group_rule(self, conversation):
        return next(
            (
                g
                for g in self.config.groups
                if g.enabled
                and g.connection_id == conversation.connection_id
                and g.group_id == conversation.id
            ),
            None,
        )

    def is_owner(self, message):
        owner = self.config.owner
        c, user = message.conversation, message.sender.ref
        return bool(
            owner
            and c.kind == "direct"
            and c.connection_id == owner.connection_id
            and c.id == user.id == owner.user_id
            and c.platform == user.platform
            and c.scope == user.scope
        )

    def allowed_conversation(self, conversation):
        if not self.config.default_model:
            return False
        if conversation.kind == "group":
            return self.group_rule(conversation) is not None
        return conversation.kind == "direct" and any(
            r and r.connection_id == conversation.connection_id and r.user_id == conversation.id
            for r in [self.config.owner, *self.config.private_users]
        )

    def allowed(self, message):
        c, u = message.conversation, message.sender.ref
        return self.allowed_conversation(c) and (
            c.kind != "direct" or (c.id == u.id and c.platform == u.platform and c.scope == u.scope)
        )

    def may_mention(self, message):
        rule = self.group_rule(message.conversation)
        return rule is None or not rule.mention_users or message.sender.ref.id in rule.mention_users

    def authorize_owner(self, context: ChatContext):
        message = context.message
        if (
            not self.ctx.owner.active
            or not self.config.owner_controls
            or context.conversation != message.conversation
            or not self.is_owner(message)
        ):
            raise PermissionError("控制工具只允许已配置主人的真实私聊消息")

    def tool_list(self, kind="group") -> tuple[ToolDefinition, ...]:
        names = {"ai.chat.stay_silent"}
        if kind == "group":
            names.add("ai.chat.view_message_images")
        return tuple(
            t
            for t in self.ctx.tools.list()
            if t.id in names or (t.id.startswith("ai.tools.") and (kind == "group" or t.name != "send_file"))
        )

    def memory_tools(self):
        return tuple(t for t in self.ctx.tools.list() if t.id == "ai.chat.update_user_profile")

    @Tool(prompt_file="update_user_profile.md")
    async def update_user_profile(self, user_id: str, content: str, ctx: MemoryContext, revision: str = ""):
        """记忆整理时创建或完整更新上下文中某个用户的 Markdown 画像；revision 使用读取到的版本，新建留空。"""
        users = {MemoryKey.of_user(user): user for user in ctx.profile_users if user.id == user_id}
        if not self.ctx.owner.active or len(users) != 1:
            raise ValueError("仅能在记忆整理阶段更新本次上下文中身份明确的用户画像")
        return self.agent.profiles.update(next(iter(users.values())), content, revision)

    @Tool(prompt_file="stay_silent.md")
    async def stay_silent(self):
        """本轮不发消息并结束；必须无参数、单独调用。"""
        return {"silent": True}

    @Tool(prompt_file="view_message_images.md")
    async def view_message_images(self, message_id: str, ctx: ChatContext):
        """按消息 ID 查看唤醒时最近 15 条群消息中的图片，仅本轮使用。"""
        if ctx.conversation is None or ctx.conversation.kind != "group":
            raise ValueError("只支持群消息图片窗口")
        assets = ctx.images.get(message_id)
        if not assets:
            raise ValueError("消息不在当前图片窗口中，或没有图片")
        images = await self.network.images(assets)
        return {"message_id": message_id, "images": images, "count": len(images)}

    def status(self):
        return {
            "conversations": len(self.sessions.channels),
            "active_sessions": sum(c.session is not None for c in self.sessions.channels.values()),
            "pending_memories": sum(len(c.records) for c in self.sessions.channels.values()),
        }
