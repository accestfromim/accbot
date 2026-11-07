# accbot

**为群聊而设计的 Python AI 机器人框架。**

accbot 关注多人对话中的参与时机、身份识别、会话延续和群体记忆。框架提供消息、事件、模型、工具与插件基础设施；开发者可以完整定义聊天策略，也可以直接使用随仓库提供的 AI 插件。

目前支持 **OneBot 11** 接入，以及兼容 **Chat Completions / Responses** 的模型服务。QQ 登录由外部 OneBot 协议端负责。项目处于早期开发阶段，公共接口仍可能调整。

## 功能

| 能力 | 说明 |
| --- | --- |
| 协议无关的消息与事件 | 类型化身份、消息链、自定义事件，支持带过滤条件的装饰器订阅 |
| 模型与工具 | 模型卡、凭据管理、工具 Schema 和返回解析，同步等待与后台执行工具 |
| 可控的群聊上下文 | 结构化发送者、用户 ID、时间和引用；合并相邻群友发言，保留助手和工具轮次 |
| 人格与提示词 | 共享卡片目录、模板编辑和导出，按活跃插件及当前工具注入提示词 |
| 插件协作 | 显式依赖、公开接口、自定义配置页、激活与卸载、隔离源码热重载 |
| 管理工作台 | 模型、人格、提示词、插件、适配器和密钥管理；支持明暗主题，无需前端构建 |
| 默认 AI 插件 | 概率参与与 @ 唤醒、群记忆、跨群用户画像、网络与消息工具、可选主人私聊通道 |

## 快速开始

需要 Python **3.12+** 和 [uv](https://docs.astral.sh/uv/)。从仓库根目录运行：

```bash
git clone https://github.com/accestfromim/accbot.git
cd accbot
uv sync --extra ai
uv run --extra ai accbot init
uv run --extra ai accbot serve
```

浏览器打开 <http://127.0.0.1:8000>。本地管理界面默认无需令牌。

也可以在虚拟环境中使用 pip：

```bash
python -m pip install -e '.[ai]'
accbot init
accbot serve
```

### 配置并启用聊天

1. **模型卡**：新增模型，填写端点、API Key 和模型名，检测或选择调用协议。聊天模型需要支持工具调用；看图功能还需要图像输入能力。
2. **人格卡**：编辑默认人格，将 `[机器人名称]` 替换为自己的名称，或新建人格卡。它是可编辑的文字占位符，不会自动替换。
3. **系统提示词**：按需编辑聊天、主人和记忆整理模板；所有占位符均可删除或调整顺序。
4. **适配器 → OneBot**：新增连接配置，填写机器人 QQ 号和接入方式，保存后手动激活；将界面给出的地址配置到 OneBot 协议端。
5. **插件 → AI · 群聊与会话**：选择模型、人格和提示词卡，添加连接 ID 与群 ID，保存配置。
6. 勾选 `ai.chat` 及依赖 `ai.tools`，应用选择。插件默认不激活，框架不会自动补选依赖。

默认观察最近 15 条消息，支持概率参与和 @ 唤醒；连续 180 秒未唤醒后结束会话并整理记忆。参与概率、冷却、会话时限和上下文预算均可配置。详见 [AI 插件](docs/ai-plugins.md)。

用户画像由记忆整理模型按需创建，每位用户一份 Markdown；QQ 用户跨群共用同一份画像。上下文自动读取窗口中用户的已有画像，总结时可通过工具更新画像，再保存群记忆。

### OneBot 接入

支持正向 WebSocket、反向 WebSocket 和 HTTP 上报。例如配置 ID 为 `qq-main` 时：

- 正向 WebSocket：在 accbot 中填写协议端地址，例如 SnowLuma 的 `ws://127.0.0.1:3001/`，激活后由 accbot 主动连接。
- HTTP 上报：`http://127.0.0.1:8000/onebot/qq-main/events`，配置协议端的 HTTP API 地址用于发送。
- 反向 WebSocket：`ws://127.0.0.1:8000/onebot/qq-main/ws`，同一连接承载事件、API 调用和回执。

启用 Access Token 时通过 `Authorization: Bearer ...` 传递。协议端位于其他主机或容器时，应使用双方可达的地址。监听地址可通过 `accbot serve --host ... --port ...` 修改。

正向连接支持 `ws://` 和 `wss://`，断线后自动重连，停用即停止重连。使用同时提供事件与 API 的 WebSocket 地址；在 Docker 网络中连接 SnowLuma 时可填写 `ws://snowluma:3001/`。详见 [OneBot 接入](docs/onebot.md)。

文件发送使用协议端的文件上传扩展；协议端需要支持相应 action，并能访问要上传的文件路径。内联图片可跨主机发送。

## 配置与数据

```text
accbot/
├─ src/accbot/          # 框架、适配器、管理 API 和前端
├─ plugins/ai/          # 默认聊天与工具插件
├─ docs/                # 架构、接口和使用说明
├─ tests/               # 离线测试及夹具
└─ .accbot/             # 本机运行数据，不提交到 Git
   ├─ config/           # 模型卡、人格、提示词、适配器与激活集合
   ├─ secrets/          # 本地密钥
   └─ data/plugins/     # 插件配置、记忆、下载等业务数据
```

使用 `--data-dir` 指定其他运行目录。模型和 OneBot 向导自动管理密钥引用，读取 API 不返回已保存的密钥值。备份运行数据时应包含 `.accbot/`，不要将其提交到公共仓库。详细路径见 [目录约定](docs/directories.md)。

服务默认只监听本机。需要远程管理时，可在 `.accbot/config/framework.json` 设置 `management_token_env`，并通过对应环境变量提供访问令牌。主人私聊中的文件读写和 Shell 工具默认关闭，启用后以机器人进程的权限执行；相关设置属于聊天插件。

## 开发插件

插件按 `plugins/<分组>/<插件包>/plugin.toml` 发现。入口继承 `accbot.Plugin`，通过 `@on_message`、`@on_event`、`@Tool` 和 `@async_tool` 声明能力；事件筛选可直接读取插件配置。

插件负责聊天参与、上下文组装、工具选择、会话和记忆。框架负责分发、模型协议、执行与生命周期。插件通过声明依赖和公开接口协作；源码重载使用独立模块实例，已接受的后台工具可以自然完成。

人格卡和系统提示词卡由框架共享管理。调用插件通过 `model.generate(items, tools=tools, system_prompt_id=..., persona_id=...)` 选择卡片。模板支持 `<persona>`、`<plugins_insert_prompt>` 和 `<tools_insert_prompt>`，也可直接传入原生消息数组的 JSON 字符串，完全自行组织上下文。

框架 wheel 仅包含 `src/accbot`。使用默认 AI 插件时，需要保留源码仓库中的 `plugins/ai/`，并安装 `ai` 可选依赖。

| 文档 | 内容 |
| --- | --- |
| [架构与设计](docs/design.md) | 职责边界、消息与事件、模型接口、依赖和热重载契约 |
| [公共接口](docs/api.md) | 完整插件示例、过滤器、消息链、模型与管理 API |
| [AI 插件](docs/ai-plugins.md) | 聊天策略、工具、记忆与私聊配置 |
| [OneBot 接入](docs/onebot.md) | 三种连接方式、SnowLuma 示例、认证与重连 |
| [系统提示词](docs/system-prompts.md) | 卡片、模板、插件补充和工具补充 |
| [模型上下文](docs/model-context.md) | ModelItem、消息合并、多模态和原始输入 |
| [插件配置页面](docs/plugin-frontend.md) | 配置组件、前端 SDK 和生命周期 |
| [目录约定](docs/directories.md) | 源码、配置、资源与业务文件布局 |

## 开发与检查

```bash
uv sync --extra ai --extra dev
uv run --extra ai --extra dev pytest
uv run --extra ai --extra dev ruff check .
uv run --extra ai --extra dev ruff format --check .
```

测试使用临时目录和模拟服务，不需要 QQ 登录或真实模型密钥。提交接口改动时同步更新文档；生命周期、协议解析和失败隔离的改动应保留对应回归测试。
