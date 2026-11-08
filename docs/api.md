# accbot 公共接口与生命周期

## 插件可用服务

入口类继承 `accbot.Plugin`，构造参数是 `PluginContext`。框架先扫描装饰器声明，再调用 `on_activate`，完成后使本次激活的所有注册整体可见。

| 接口 | 职责 |
| --- | --- |
| `ctx.subscribe(EventClass, handler, where=...)` | 注册属于本次激活的广播订阅，处理器接收一个事件 |
| `@on_event(EventClass, where=...)` | 为实例方法声明订阅 |
| `@on_message(sender=..., group=..., connection=..., platform=..., private=True, include_self=False, where=...)` | 消息订阅过滤；额外 Filter 与快捷条件取交集 |
| `message_filter(...)` | 返回同样的消息过滤器，可与其他 Filter 组合 |
| `Filter(predicate)` | 支持 `&`、`\|`、`~` 的同步过滤器 |
| `UserMatch(connection_id=..., user_id=...)` | 配置用发送者条件，限定一条连接中的用户；可直接交给 sender |
| `GroupMatch(connection_id=..., group_id=...)` | 配置用群条件，限定一条连接中的群；可直接交给 group |
| `ctx.register_event(EventClass)` | 注册自定义事件子类，声明独立 `event_type` |
| `await ctx.publish(event)` | 发布事件并获得 EventReceipt；`await receipt.wait()` 等待广播结束 |
| `ctx.schedule(event_or_factory, delay=..., interval=...)` | 创建按激活身份失效的定时器 |
| `ctx.cancel_timer(timer_id)` | 取消本次激活创建的定时器；返回是否仍有任务，不撤回已发布事件 |
| `ctx.export(api)` | 导出当前插件公开对象 |
| `ctx.plugins.require(id).api` | 获得带激活检查的公开接口句柄 |
| `ctx.models.list()` / `ctx.models.get(id)` | 模型卡目录与无会话模型客户端 |
| `ctx.personas.list()` / `ctx.personas.get(id)` | 全插件共享的 PersonaCard 目录；无需声明资源插件依赖 |
| `ctx.prompts.list()` / `ctx.prompts.get(id)` | 全插件共享的 SystemPromptCard 目录 |
| `ctx.prompts.render(id, persona_id=..., tools=...)` | 按同一模型组装规则预览文本，可供插件估算上下文预算 |
| `await model.generate(items, tools=..., system_prompt_id=..., persona_id=...)` | 选择并展开系统提示词，调用 Chat Completions 或 Responses，返回 GenerationResult |
| `ctx.messages.send(conversation, chain, reply_to=...)` | 按连接路由消息，返回发送回执 |
| `ctx.messages.send_file(conversation, asset)` | 使用适配器的独立文件上传操作并发布 FileSent |
| `ctx.messages.fetch(message_ref)` | 在适配器支持时查询消息；不发布新的接收事件，不支持或消息已失效时抛异常。入站引用由框架补齐，插件无需调用此接口补查 |
| `ctx.messages.get_connection(id)` | 查询已配置的活动接入身份，返回不可变 ConnectionRef；不存在时返回 None，不暴露适配器对象 |
| `ctx.tools.list()` | 当前活跃工具目录，调用插件自行筛选 |
| `ToolDefinition.to_schema(protocol)` | 把内部工具声明编码为 chat/responses 的函数 Schema |
| `await ctx.tools.execute(call, context)` | 参数验证、执行与工具结果 |
| `ctx.spawn(async_operation)` | 通过框架登记后台任务，立即返回任务 ID |
| `await ctx.stop_jobs(task_ids, timeout=0)` | 等待指定任务至超时，再取消尚未完成者；仅处理本次激活拥有的任务，忽略其他或已结束的任务 |
| `ctx.runtime.assets.read(asset, max_bytes=...)` | 按需读取有大小限制的媒体 |
| `ctx.data_dir` | 插件自主管理的数据目录路径 |
| `PluginSettings.read()` / `write(values)` | 插件配置页面的读写接口，在 settings.py 中实现 |
| `Plugin.on_settings_changed(values)` | 配置保存成功后通知活跃业务实例 |

`@Tool` 根据函数类型注解生成参数 Schema，保留 `Annotated[T, Field(...)]` 的范围、长度等约束，执行时用相同模型严格校验；注解为 `ToolContext` 或其子类的单个参数由执行器注入，不出现在模型参数中。Pydantic 参数类会以该类的实例交给工具函数。工具 ID 为 `plugin_id.local_name`；工具声明生成稳定、无冲突的合法 wire name，模型协议层将返回调用映射回工具 ID。

固定结构的业务上下文应由插件定义 `ToolContext` 子类，例如官方聊天插件的 `ChatContext(message, bot, owner, images)` 和 `MemoryContext(profile_users)`。调用 `execute(call, context)` 时传入相应实例；执行器检查类型并原样注入，缺失或类型不匹配时返回参数错误，不从模型参数中补建上下文。通用 `metadata` 仍可用于开放扩展，不替代已知字段的声明。

`@async_tool` 和普通异步函数不同：它表示接受后台作业。执行返回 accepted 与 task_id，最终由 TaskCompleted 或 TaskFailed 报告结果。停用不取消这些作业，回执不自动交给新插件实例。

`stop_jobs` 是插件显式管理自身任务的接口，不由卸载操作自动调用。官方聊天插件用它停止保存了 ID 的会话工作任务，不扫描任务表，也不停止工具执行器接受的其他后台任务。

后台任务状态使用 `JobInfo` 记录，管理 API 将其序列化为含 `id`、`owner`、`activation_id`、`status` 的 JSON 对象。结果事件通知失败不改写任务的执行状态。

两个工具装饰器都接受 `prompt_file="name.md"`，从所属插件的 `prompts/tools/name.md` 读取补充提示词。`ToolDefinition.system_prompt` 保存激活时正文快照，`activation_id` 标记所属激活；二者不进入供应商工具 Schema。`prompts/system.md` 自动注册为插件补充；详细规则见 [系统提示词](system-prompts.md)。

## 消息与工具对象

| 对象 | 用途 |
| --- | --- |
| `ChatMessage` | 实际聊天消息，包含发送者、会话引用、时间和 MessageChain |
| `MessageQuote` | ChatMessage.reply_to 中的单层快照，包含发送者、时间和 MessageChain；正文为 None 表示不可用，无消息 ID 或递归引用 |
| `MessageChain` / `Segment` | 平台消息的不可变有序内容；文本、提及、媒体、自定义片段 |
| `MessageBuilder` | 以 `.text()`、`.mention()`、`.media()`、`.add()` 组合消息，`.build()` 生成快照 |
| `ModelItem` / `ModelPart` | 独立的模型上下文表示，不直接复用 ChatMessage |
| `ModelQuote` | 模型上下文中的单层引用快照，展示发送者、时间及多模态正文，无需查询原消息 |
| `GenerationResult` | 模型返回的内部消息、工具调用、用量与供应商状态 |
| `ToolDefinition` | 工具 ID、说明和参数 Schema；注册表和模型输入都使用此类 |
| `ToolCall` | 模型选择的工具及实参，交给执行器 |
| `ToolContext` | 运行时注入的会话及关联信息，不是模型可填写参数 |
| `ToolResult` | 调用结果、错误或后台 task_id，业务值位于 value |

接收和发送事件的消息内容均为 MessageChain。发送纯文本可显式使用 `MessageChain.text(text)`；`messages.send(target, text)` 是在入口立即转换为 MessageChain 的快捷形式，不会让裸字符串进入发送事件。裸 list 不是发送消息类型。

接收消息的 `message.reply_to` 保存引用内容快照。发送引用回复时使用 `messages.send(message.conversation, chain, reply_to=message.ref)`，由适配器把 MessageRef 转成平台引用字段。

消息和文件均由 MessageSender 校验会话身份、按连接选择适配器、取得回执并发布结果事件。事件通知失败不会改变已经取得的发送结果或覆盖原始发送异常；无法判定结果的底层异常标记为 unknown，不自动重发。

`model.generate` 接受 `Sequence[ModelItem] | str` 和 `Sequence[ToolDefinition]`，返回 GenerationResult。ModelItem 模式不需要自行拼接供应商消息字典；字符串模式要求当前协议原生消息数组的 JSON 文本。上下文中的工具执行结果使用 `ModelItem.from_tool_result(result)`，在这里才把 ToolResult 序列化为模型可读 JSON。工具的业务返回内容和自定义平台载荷仍可为 JSON 值。

## 完整插件例子

下面的处理器名称可以任意命名；只有装饰器声明决定事件订阅。Plugin 基类不定义 `on_message` 或某个自定义事件的虚函数。生命周期钩子只用于初始化、释放资源和更新配置。

```python
from typing import Annotated

from pydantic import Field

from accbot import (
    ConversationRef,
    Event,
    Filter,
    MessageBuilder,
    MessageReceived,
    Plugin as BasePlugin,
    Tool,
    ToolContext,
    on_event,
    on_message,
)


class Reminder(Event):
    event_type = "example.reminder"
    conversation: ConversationRef
    text: str

    @property
    def dispatch_key(self):
        return self.conversation.key


class Plugin(BasePlugin):
    async def on_activate(self):
        self.ctx.register_event(Reminder)

    @on_message(
        platform="qq",
        connection="qq-main",
        group="123",
        sender="456",
        include_self=False,
        where=Filter(lambda event: event.message.content.plain_text == "提醒我"),
    )
    async def accept_request(self, event: MessageReceived):
        await self.ctx.publish(
            Reminder(
                conversation=event.message.conversation,
                text="提醒已收到",
            )
        )

    @on_event(Reminder)
    async def deliver_reminder(self, event: Reminder):
        chain = MessageBuilder().text(event.text).build()
        await self.ctx.messages.send(event.conversation, chain)

    @Tool
    async def send_notice(
        self,
        text: Annotated[str, Field(min_length=1, max_length=1000)],
        ctx: ToolContext,
    ):
        receipt = await self.ctx.messages.send(
            ctx.conversation,
            MessageBuilder().text(text).build(),
        )
        return {"message_ids": [message.id for message in receipt.messages]}
```

装饰器声明在实例激活时绑定并归属该次激活；on_activate 注册自定义事件之后，整个实例才可接收事件。停用撤销类型、订阅和工具；重载会绑定新实例的方法。无需修改基类，也无需在 on_deactivate 手工逐个退订。需要运行时决定订阅类型的插件仍可使用 `ctx.subscribe`，其生命周期与装饰器完全相同。

官方 AI 插件示范见 [事件入口](../plugins/ai/chat/plugin.py)、[内部工具循环](../plugins/ai/chat/agent.py) 和 [消息链发送](../plugins/ai/tools/plugin.py)。

## 配置驱动的消息过滤

过滤条件直接出现在装饰器参数中。静态条件可以使用用户/群 ID、`User` / `Group`、完整 `UserRef` / `GroupRef`，或指定连接的 `UserMatch` / `GroupMatch`。列表表示任选一个匹配，多种参数之间取交集。多个连接与用户组成的列表逐对匹配，不会拼成连接和用户的笛卡尔积。

```python
from types import SimpleNamespace

from accbot import Plugin, UserMatch, on_message


class Example(Plugin):
    async def on_activate(self):
        # 实际插件可从自己的配置文件读取；框架只匹配连接和用户。
        self.config = SimpleNamespace(owner=UserMatch(connection_id="qq-main", user_id="123456"))

    @on_message(sender=lambda plugin: plugin.config.owner, private=True, include_self=False)
    async def receive(self, event): ...
```

类体执行方法装饰器时，尚未创建实例，不能引用 `self.config`。因此参数接受 `lambda plugin: plugin.config.owner` 这样的同步读取函数。框架在激活时绑定实例，每次事件匹配时读取当前配置；配置更新无需重注册，重载绑定新实例。不要在这个函数里进行 I/O。

- 不填写某个条件表示不限制该字段；显式 `None` 或空列表表示不匹配任何消息。因此没有设置主人时不会订阅所有私聊。
- `private=True` 限定私聊，`False` 排除私聊，省略时不限制。发送者身份和私聊会话 ID 是不同概念，不要求平台的私聊会话 ID 等于用户 ID。
- `sender=UserRef(...)` 比较平台、scope 和用户 ID；`sender=UserMatch(...)` 比较连接 ID 和用户 ID。群条件同理。
- `group=lambda plugin: [g for g in plugin.config.groups if g.enabled]` 直接读取当前启用的群。官方聊天插件的 `GroupRule` 继承通用 `GroupMatch`，自行增加启用状态和 @ 规则。
- 普通私聊可用 `sender=lambda plugin: [u for u in plugin.config.private_users if u != plugin.config.owner]` 排除主人，避免两个订阅重复处理。
- 任意事件的额外条件仍使用 `where=Filter(predicate)`，支持 `&`、`|`、`~`；不支持以字符串引用插件方法。手动 `ctx.subscribe` 使用配置读取函数时，传入 `message_filter(...).bind(self)`。

所有订阅都受激活归属、排队回调检查和卸载撤销机制管理。

## 事件分发

事件模型和载荷不可变。普通 `publish` 在容量耗尽时等待；处理器内部嵌套发布若容量已满，明确抛 Backpressure 错误以避免自己等待自己。完成等待和入队确认分离。

同一订阅者、同一会话的回调顺序执行；不同订阅者与会话独立推进。过滤器和回调异常记录在 EventReceipt 中，不中断其他订阅者。入队时固定当时活跃的订阅集合，停用后的排队回调跳过，尚未激活的订阅不接收此前入队的事件。自定义事件类型失效后，基类订阅也不再收到它的排队事件。公开接口、工具和回调进入执行后持有旧代码租约，执行完释放。

后台任务可在所属插件停用后完成。框架的无归属诊断订阅仍可记录 TaskCompleted/TaskFailed，但失效来源的结果不会投递到业务插件实例。需要消化历史任务结果的业务应显式设计自己的恢复机制，不能依赖重新激活后自动续接旧回执。

## 激活与源码重载

`plugin.toml` 必须能独立读取，不能在导入业务源码后才确定依赖。一次检查完整候选图并生成拓扑顺序，再逐个初始化。初始化期间注册属于暂存激活，失败自动撤销；不回滚业务文件和外部副作用。

源码快照覆盖插件目录中的 Python 模块。每次激活使用新命名空间，不复用字节码缓存；旧任务继续看到原快照，插件内部后续相对导入仍从旧快照加载。租约归零后移除旧命名空间。插件使用框架登记后台工作；自行创建的线程、Task、全局注册或外部引用不在框架自动生命周期内。

插件内部参数热更新、业务状态迁移和第三方库升级由插件或维护者处理，不属于源码重载的保证。

纯资源包设置 `kind = "resource"`。它的公开 API 是 ResourceBundle，支持 `list()`、`read(path)` 和 `read_text(path)`。每次激活读取独立的文件快照；停用及重载遵循相同依赖与激活规则。人格卡使用框架的 PersonaCard 服务，调用插件选择后通过 [系统提示词模板](system-prompts.md) 应用。

## 模型上下文

ModelItem 独立于 ChatMessage。插件自行把群消息和动态记忆组织成 ModelItem；普通 user 消息可填写 `sender: User`、`sent_at: datetime`、`message_id: str`、`conversation: ConversationRef`、`reply_to: ModelQuote`，字段均可选。`ModelItem.text(role, text, **metadata)` 支持这些字段，工具返回可用 `ModelItem.tool_result(call_id, output)` 构造。

`ModelItem.from_message(message)` 提供默认转换，保留身份、群名片、时间、消息 ID 和单层引用快照，以文字占位符表示媒体，不触发网络读取。引用独立于窗口中的原消息，始终展示已有发送者和正文；框架查询不到原文时明确标记不可用，插件无需维护任何引用索引。自定义内容仍可直接构造 ModelItem。

模型层把相邻的普通 user 消息渲染为同一条群聊历史消息，保留文字和多模态内容的顺序。system/developer、助手、工具调用、工具结果及原生供应商项目阻断合并并保持原样；不能只用 role 判断工具结果。`accbot.models.render_items(items)` 提供同一渲染过程，供插件估算预算。框架不保存合并状态，不自动冻结此前请求；保持历史稳定由插件决定。详见 [群聊上下文](model-context.md)。

ModelItem 模式下，调用插件通过 `system_prompt_id` 选择系统提示词卡、通过可选的 `persona_id` 选择人格；模型客户端在发送前展开模板，并前置一条 system 消息，不修改传入对象。不指定系统提示词卡时不自动添加任何卡片；单独指定 persona_id 会报参数错误。选择不存在的卡片明确报错，不隐式回退。

字符串模式直接使用调用方给出的协议消息数组：不合并、不添加标题或元数据、不注入任何提示词，`system_prompt_id` 和 `persona_id` 均被忽略，不读取这些卡片。字符串必须解析为由对象组成的 JSON 数组；不接受自由文本角色标记或完整请求对象。模型端点、凭据、参数、工具 Schema 与返回解析仍由框架管理。

生成结果的 items 包含消息、工具调用及 opaque provider 项目，可追加到后续上下文。原始供应商项目按原协议无损传回；切换协议遇到不兼容的 opaque 项目时明确报告不支持，调用插件负责转换或裁剪。

框架不自动执行模型返回的工具、不自动发送生成文本、不保存上下文，也不执行记忆总结。

插件还可以提供自己的前端组件和配置读写接口，具体约定见 [插件配置页面](plugin-frontend.md)。

## 管理接口

工作台使用以下接口。默认本地访问不需要令牌；只有主动设置 `management_token_env` 时才要求登录。完整请求结构可查看运行实例的 `/docs`。

| 路径 | 用途 |
| --- | --- |
| `GET /api/plugins` | 含目录分组、依赖、当前激活身份的插件列表 |
| `POST /api/plugins/discover` | 扫描安装目录，保持运行实例的原元数据 |
| `POST /api/plugins/preview` | 返回依赖问题、推荐勾选项和可执行顺序，不改变状态 |
| `PUT /api/plugins/active` | 全图校验后应用 `selected` 集合 |
| `POST /api/plugins/{id}/unload` | 递归卸载活跃依赖者并保存实际状态 |
| `POST /api/plugins/{id}/reload` | 重载源码、恢复原活跃集合并报告失败项 |
| `GET /api/models` | 模型卡和凭据配置状态 |
| `POST /api/models/catalog` | 从端点读取可选模型列表，失败可手动填写 |
| `POST /api/models/detect` | 有限测试请求识别可用协议，不保存密钥 |
| `POST /api/models/cards` | 添加模型卡，自动生成新密钥的变量引用 |
| `PUT /api/models/cards/{id}` | 编辑模型，空密钥表示保留原凭据 |
| `DELETE /api/models/cards/{id}` | 删除模型卡；凭据可另行清理 |
| `GET /api/personas` / `GET /api/personas/{id}` | 人格卡目录与详情 |
| `POST /api/personas` | 新建人格卡，ID 不可重复 |
| `PUT /api/personas/{id}` | 编辑人格卡，ID 不变 |
| `DELETE /api/personas/{id}` | 删除人格卡，使用方随后需改选 |
| `GET /api/personas/{id}/export` | 下载可分享的 JSON 人格卡 |
| `GET /api/prompts` / `GET /api/prompts/{id}` | 系统提示词卡目录与详情 |
| `POST /api/prompts` | 新建系统提示词卡，ID 不可重复 |
| `PUT /api/prompts/{id}` | 编辑系统提示词卡，ID 不变 |
| `DELETE /api/prompts/{id}` | 删除系统提示词卡，使用方随后需改选 |
| `GET /api/prompts/{id}/export` | 下载 Markdown 模板正文 |
| `GET /api/adapters` | 官方适配器目录 |
| `GET /api/adapters/onebot` | OneBot 保存配置、激活及连接状态 |
| `POST /api/adapters/onebot` | 新增未激活的接入配置 |
| `PUT /api/adapters/onebot/{id}` | 编辑配置，保留原激活状态和未填写的旧凭据 |
| `POST /api/adapters/onebot/{id}/activate` | 激活已保存配置 |
| `POST /api/adapters/onebot/{id}/deactivate` | 停用配置并关闭相应连接 |
| `DELETE /api/adapters/onebot/{id}` | 删除配置并关闭相应连接 |
| `GET /api/secrets` | 只返回名称、设置状态、来源与使用位置 |
| `PUT /api/secrets/{name}` | 更新密钥 |
| `DELETE /api/secrets/{name}` | 清理未引用的本地密钥 |
| `GET/POST/DELETE /api/session` | 查询会话、可选令牌登录、退出 |

编辑或激活一个 OneBot 配置不会中断未修改配置的 WebSocket 连接。HTTP 模式的“已激活”仅表示框架接收该实例的事件并可调用其 API，不代表已完成 QQ 登录或网络连通性验证。

OneBot 配置的 `mode` 支持 `reverse_ws`、`forward_ws`、`http`。正向模式要求 `ws_url` 为不含凭据和查询参数的 `ws://` 或 `wss://` 地址；向导通过独立 `token` 字段保存密钥。保存默认未激活，激活后异步建立连接；配置列表和 `/api/status` 的连接条目提供 `connecting`、`reconnecting`、`connected` 状态。连接及重试行为见 [OneBot 接入](onebot.md)。
