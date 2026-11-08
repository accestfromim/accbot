# 群聊上下文与原始输入

ModelItem 表示模型上下文，包含群聊常见元数据，由框架统一序列化。插件负责构造对象、选择历史和保持历史稳定；框架不保存会话状态。

## 结构化消息

普通 `kind="message", role="user"` 的 ModelItem 可以填写以下字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| sender | User，可省略 | 发送者身份及展示名；插件可使用群名片 |
| sent_at | datetime，可省略 | 原始发送时间；按 ISO 8601 输出，不填当前请求时间 |
| message_id | str，可省略 | 插件选定的消息标识，不自动生成或重编号 |
| conversation | ConversationRef，可省略 | 发言所在会话；用于区分群聊与私聊记录 |
| reply_to | ModelQuote，可省略 | 单层快照，包含 sender、sent_at 和 content；不依赖原消息或窗口 |

正文继续使用有序的 `tuple[ModelPart, ...]`，支持文本、图片与文件。ModelQuote 使用相同的正文类型，不递归嵌套引用。元数据不能与工具结果、助手输出或原生供应商项目混用，以免在原样传递时静默丢弃。

接收事件的 `ChatMessage.reply_to` 是适配器补齐的 `MessageQuote`，其中正文为 MessageChain，不包含原消息 ID 或原消息对象。原文查询失败时 `content=None`，与没有引用（`reply_to=None`）区分。它作为当前消息的一部分独立保存，原消息离开观察窗口后无需再查询。

默认转换只需要 `ModelItem.from_message(message)`：框架填写发送者（优先群名片）、时间、消息 ID、会话和引用，将提及及媒体转换为可读文字，不隐式读取媒体。插件也可按自己的策略直接构造 ModelItem 或多模态内容：

```python
from accbot import ModelItem, ModelPart, ModelQuote

item = ModelItem.text(
    "user",
    "你明天帮我带来吧。",
    sender=message.sender,
    sent_at=message.sent_at,
    message_id=message.ref.id,
    conversation=message.conversation,
    reply_to=ModelQuote(
        sender=message.reply_to.sender,
        content=(ModelPart(text="你的雨伞落在教室了。"),),
    ),
)
result = await model.generate([item], system_prompt_id="chat", persona_id="default")
```

插件可以自由构造这些对象，不要求从 ChatMessage 转换，也不依赖特定聊天策略。

引用始终输出快照中的发送者和正文，即使原消息也出现在历史中，两处内容仍分别保留。缺少原文时输出“引用原文不可用”，不提示模型凭消息 ID 查找。ModelQuote 的可选 `message_id` 仅作为调用方记录的来源标识，不参与查询和渲染。

用户 ID 保存在 `sender.ref.id` 中（QQ 平台就是 QQ 号），引用发送者同理。序列化显式标注 `发送者：昵称（用户 ID：102；平台：qq；作用域：test）`；同名用户仍有各自的 ID。

官方聊天插件从每条入站事件的 `connection.bot` 获取机器人账号，在独立 system 上下文中提供平台、作用域及自身 QQ 号（其他平台标为用户 ID），计入上下文预算。不从人格昵称推断身份、不硬编码账号，也不写入群记忆；多个接入账号分别使用当前事件的身份。它和长期记忆一样属于插件提供的运行时资料，不改变提示词卡模板。按 ID 区分同名用户的规则位于可移除的插件提示词补充中；删除模板的 `<plugins_insert_prompt>` 就不会注入该规则。记忆整理调用不扮演群聊账号，不添加这条自身身份资料。

## 合并规则

模型编码前，`accbot.models.render_items(items)` 把所有相邻的普通 user 消息合并为一条。存在群/频道/话题元数据时使用“下面是群聊历史：”；私聊或未指定会话的合并块使用“下面是对话记录：”。各条发言保留发送者、时间、消息 ID、会话和引用，以固定分隔符区分。

单条无元数据的普通消息保持其正文不变。相邻的无元数据 user 消息仍会合并。原生 provider_item 项目不参与改写。

```text
system：提示词卡
system：机器人自身账号
system：长期记忆
user：下面是群聊历史：A 的发言、B 的发言
assistant：调用工具
tool：工具结果
assistant：回复
user：下面是群聊历史：C 的发言、D 的发言
```

system/developer、助手、工具调用、工具结果和不透明供应商项目都阻断合并。工具结果虽然内部默认 role 为 user，但以 kind="tool_result" 识别，绝不会变成群友发言。图片和文件保留原顺序，不转换为字符串；相邻文本片段可合并为一个文本部分。

合并仅发生在请求编码阶段，不修改原 ModelItem 或插件的业务日志。模型输出及其 reasoning、工具关联和供应商字段仍原样续接；两个协议共享同一渲染过程。

## 插件与缓存

框架不记录“这段历史是否已发送”，不替调用方冻结块、添加假助手轮次或调整历史。若插件把 `[A, B]` 改成 `[A, B, C]`，框架就重新合并；是否这样做完全由插件决定。

官方聊天插件在正常调用后追加助手回复，或保留 stay_silent 工具调用和结果，因此下一批发言自然被分隔。渲染旧块时不插入当前时间、消息总数或重新编号；后续追加消息不会改变这些旧块。异常恢复和上下文裁剪仍由插件处理。

聊天插件的预算使用相同的 render_items，计算包含标签与引用正文的上下文大小。长期记忆使用独立 system 消息；业务日志保存未合并的结构化对象。

缓存取决于实际供应商和模型。框架保证确定性的序列化与输出顺序，不承诺缓存命中；可以从 GenerationResult.usage 查看供应商返回的 cached_tokens。

## 原始输入

`model.generate` 也接受 `str`：它必须是当前协议原生消息数组的 JSON 文本。Chat 使用 messages 数组，Responses 使用 input 项目数组；不包含整个请求的 model、端点或认证字段。

```python
import json

raw = json.dumps(
    [
        {"role": "system", "content": "自定义规则"},
        {"role": "user", "content": "自定义内容 A"},
        {"role": "user", "content": "自定义内容 B"},
    ],
    ensure_ascii=False,
)
result = await model.generate(raw, tools=tools)
```

此时两个 user 消息保持分开。框架只解析 JSON 数组并编码请求，不添加记录标题、元数据或模板；system_prompt_id/persona_id 即使传入也直接忽略，不读取相应卡片。工具 Schema 仍根据 tools 生成，插件和工具提示词补充均不注入。模型卡参数、密钥引用、超时、异常处理和 GenerationResult 解析继续使用同一实现。

普通自然语言字符串、以 `system:` 开头的文本、JSON 标量、包含完整请求的 JSON 对象以及非对象数组均报参数错误。不会猜测角色结构，也不会为原始消息自动做跨协议转换。
