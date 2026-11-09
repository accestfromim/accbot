# 系统提示词卡与补充接口

系统提示词由框架保存、编辑并在模型请求前组装。使用哪张卡由调用插件决定，模型卡不保存默认系统提示词。聊天策略、上下文、记忆和工具选择仍由插件管理。

## 调用方式

```python
model = self.ctx.models.get(self.config.model_id)
result = await model.generate(
    items,
    tools=tools,
    system_prompt_id=self.config.chat_prompt_id,
    persona_id=self.config.persona_id,
)
```

以上为 ModelItem 序列模式：`system_prompt_id` 指向 `SystemPromptCard`；`persona_id` 可省略。模型客户端展开模板，前置一条 system `ModelItem`，再按当前协议编码。两种 ID 都不发送给模型供应商，不修改调用方的 items 或工具 Schema。

不指定 `system_prompt_id` 时，不隐式选择卡片，也不添加任何补充；此时指定 `persona_id` 会报参数错误。不存在的卡片明确报错，不回退到另一张卡。调用方仍可传入自己的 system `ModelItem`，它们不会被替换或再次展开。

`ctx.prompts.list()`、`get(id)` 从运行目录现场读取并校验卡片；人格目录 `ctx.personas` 使用相同方式。`render(id, persona_id=..., tools=...)` 返回与模型层相同的展开文本，方便插件估算上下文预算。每次模型调用都重新读取所选卡片，包括同一次 agent loop 的后续工具轮次；不冻结整轮提示词，不重新读取插件和工具的补充快照。官方聊天插件会将展开文本及工具 Schema 计入预算，框架模型层不裁剪业务上下文。

若 `generate` 的输入是原生消息数组的 JSON 字符串，则进入 [原始输入模式](model-context.md#原始输入)：忽略 `system_prompt_id`、`persona_id`，不执行任何模板展开或补充注入。调用方负责完整上下文正文及角色。

## 模板

每张卡的正文就是该次调用的完整模板，全部由用户编辑。没有框架固定的前缀、后缀或拼接顺序。正文是 Markdown，可以将以下可选占位符放在任意位置，也可以全部删去，只保留自己编写的内容：

```markdown
<persona>

你是群聊中的参与者。结合多人讨论决定是否发言。

<plugins_insert_prompt>

本轮能够使用的工具（如果有）：

<tools_insert_prompt>
```

| 占位符 | 替换内容 |
| --- | --- |
| `<persona>` | 本次 persona_id 对应的人格正文；未选择时为空 |
| `<plugins_insert_prompt>` | 所有已激活插件的非空通用补充，按插件 ID 排序，附插件 ID 标题 |
| `<tools_insert_prompt>` | 本次 tools 列表中各工具的非空补充，保持传入顺序，附模型实际可调用的 wire name 标题 |

只对模板正文做一次替换。插入的人格、插件或工具文本即使含有占位符，也不递归展开；历史与用户消息不参与替换。同一占位符出现多次则分别替换。没有放置的占位符不会自动追加。工具 ID 重复、工具声明所属激活已失效均报错，不把旧声明重新绑定到同名新工具。

例如把 `<tools_insert_prompt>` 移到 `<persona>` 前，输出顺序就随之变化；删去插件和工具占位符后，即使存在活跃插件且传入了 tools，也不会插入它们的补充说明。工具 Schema 仍按 tools 参数传给模型，与是否插入说明独立。初始卡片只是可编辑范例，不是强制的模板结构。

通用插件补充与工具补充是两个独立来源：即使没有选中某插件的工具，该插件的通用补充仍会出现。因此工具特定规则应放在工具文件里。普通群聊过滤掉 Shell 工具后，其 Shell 说明也不会出现在工具补充中。

## 插件文件与工具声明

```text
your-plugin/
├─ plugin.toml
├─ plugin.py
└─ prompts/
   ├─ system.md             # 可选；固定位置，框架自动发现
   └─ tools/
      ├─ search.md
      └─ download.md
```

```python
from accbot import Plugin, Tool, async_tool


class Example(Plugin):
    @Tool(prompt_file="search.md")
    async def search(self, query: str): ...

    @async_tool(prompt_file="download.md")
    async def download(self, url: str): ...
```

`prompt_file` 相对 `prompts/tools/`，也适用于 `ctx.register_tool` 注册的装饰函数；允许该目录内的子路径。文件必须存在、UTF-8、后缀 `.md`，单文件最多 256 KiB，不接受绝对路径、`..` 或指向包外的软链接。省略参数表示没有该工具的额外提示词。

激活时一次读取所有提示词快照；初始化完成前不可见。失败会撤销暂存注册，停用撤销插件补充及工具注册，重载读取新文件。直接修改插件文件在 reload 后生效，旧实例不重新读取磁盘文件。接受的后台工具仍可自然完成。

`ToolDefinition.system_prompt` 和 `activation_id` 是内部元数据，不进入 `to_schema()` 输出。调用方手工构造的未注册工具声明可直接携带 system_prompt；框架注册的工具则校验激活身份并使用登记的快照。

## 管理与持久化

工作台「系统提示词」支持新建、编辑、删除和导出 Markdown，编辑器提供三个占位符插入按钮。卡片结构为 `SystemPromptCard(version=1, id, name, description, content)`；ID 创建后不变，正文上限 65536 字符。

```text
.accbot/config/prompts/<id>/
├─ metadata.json    # version、id、name、description
└─ prompt.md        # content
```

界面/API 保存或直接修改运行目录中的卡片后，下次模型调用立即使用新正文，不需要重启或 reload 插件；已经发出的请求不受影响。管理页面的列表、编辑和导出接口同样读取当前文件。文件无效或卡片已删除时明确报错，不回退到旧内存内容。保存模型、适配器和插件激活配置不会重写卡片；编辑一个卡片目录也不会覆盖另一个目录。导出下载正文 `.md`；各文件单独原子替换，多文件之间不承诺事务。

插件配置页用 `api.listPrompts()` 列出卡片，`SettingsContext.prompts` 可在插件未激活时校验 ID。卡片选择属于插件配置，不写入模型卡。管理 API 见 [公共接口](api.md#管理接口)。

首次初始化提供 chat、owner、memory 三张卡，官方聊天插件分别通过 `chat_prompt_id`、`owner_prompt_id`、`memory_prompt_id` 选择：

- [chat](../src/accbot/prompts/defaults/chat.md) 说明多人群聊中的参与时机、身份与指代、工具调用和回复规则。
- [owner](../src/accbot/prompts/defaults/owner.md) 说明已校验的主人私聊中的任务理解、工具执行和结果核验。
- [memory](../src/accbot/prompts/defaults/memory.md) 说明先按需更新用户画像、再整理会话记忆的顺序，以及记忆字段含义与信息取舍。

源码中的模板用于初始化运行目录中的卡片；人格卡同样从 `src/accbot/personas/default.json` 初始化。`init` 或首次创建尚未初始化的运行时会保存初始卡片。此后前端编辑运行目录中的文件，更新源码模板或重复运行 `init` 不会覆盖已有卡片。若要采用更新后的模板，应通过前端更新正文，或同步到运行目录的对应文件。

chat 和 owner 含三个占位符；memory 通过 `<tools_insert_prompt>` 注入本次画像工具的具体说明，不混入人格或通用插件补充。三张模板在工具占位符前均写明“本轮能够使用的工具（如果有）：”；这句话也由用户编辑，框架不强制追加。模板描述场景和行为规则，具体工具参数与限制由工具 Schema 和补充说明提供。用户可编辑、替换或删除这些卡片，删除后重启不会重新创建。
