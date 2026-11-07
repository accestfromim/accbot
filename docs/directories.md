# 目录约定

源码按领域拆包，前端按页面组织；无需前端构建或外部 CDN。

```text
src/accbot/
├─ config/              # 配置类型与分文件保存
├─ events/              # 事件模型、过滤器与分发器
├─ models/              # ModelItem/ModelQuote、群聊记录渲染、协议编解码与客户端
├─ personas/            # 人格卡类型、共享目录及初始卡片资源
├─ prompts/             # 系统提示词卡、模板组装及初始 Markdown 卡片
├─ plugins/             # 清单、插件接口、源码快照、生命周期管理
├─ adapters/onebot.py   # 官方 OneBot 11 适配器
├─ adapters/onebot_ws.py # 正向连接与两种 WebSocket 共用的事件/回执接收
├─ web/
│  ├─ app.py            # 应用装配和静态资源
│  ├─ auth.py           # 可选远程管理会话
│  ├─ schemas.py        # 表单请求类型
│  └─ routes/           # 插件、模型、适配器、密钥和协议接收路由
└─ frontend/
   ├─ index.html        # 侧栏与页面外壳
   ├─ styles/           # 主题、布局、组件与页面样式
   └─ js/
      ├─ app.js         # 路由、会话、主题与状态更新
      ├─ api.js         # 请求和错误处理
      ├─ state.js       # 后端状态
      ├─ components.js  # 对话框、表单、提示等共享组件
      └─ views/         # 插件、模型卡、人格卡、系统提示词、适配器与密钥页面
```

运行目录默认位于当前工作目录的 `.accbot/`，可通过 `--data-dir` 修改。模型与适配器按 ID 各存一个 JSON 文件；JSON 中只含密钥变量引用。

```text
.accbot/
├─ config/
│  ├─ framework.json         # 框架设置，如插件目录
│  ├─ plugins.json           # 实际激活的插件 ID 集合
│  ├─ models/<模型卡ID>.json
│  ├─ personas/<人格卡ID>.json
│  ├─ prompts/<系统提示词ID>/
│  │  ├─ metadata.json        # ID、名称、简介与版本
│  │  └─ prompt.md            # Markdown 模板正文
│  └─ adapters/onebot/<配置ID>.json
├─ secrets/env.json          # 本地密钥值，管理 API 不提供值的读取
└─ data/plugins/<插件ID>/    # ctx.data_dir；内部结构由插件自行决定
```

`init` 先加载已有配置，不覆盖用户设置。新保存的 OneBot 配置默认停用。单个 JSON 或 Markdown 文件通过临时文件与替换写入；多个文件之间不提供数据库事务保证。

插件源文件和业务文件分开。插件目录默认为 `plugins/`，每个包包含 `plugin.toml`，其所在父目录决定前端分组。框架扫描到插件包后不继续发现其内部嵌套的插件清单；隐藏目录和 `__pycache__` 不扫描。

```text
plugins/
├─ ai/
│  ├─ chat/                # 群聊策略、工具循环、会话、记忆、配置页
│  └─ tools/               # 网络、文件、图片与消息工具
└─ your-group/your-plugin/
   ├─ plugin.toml
   ├─ plugin.py
   └─ ...                    # 自有模块和静态资源
```

两个 AI 插件均默认未激活，详情见 [AI 插件说明](ai-plugins.md)。`plugins/ai/` 纳入版本控制，其他本地插件默认忽略。测试用插件位于 `tests/fixtures/demo/`，不参与运行时插件发现。

人格卡由框架统一保存于 `.accbot/config/personas/`。系统提示词卡保存在 `.accbot/config/prompts/`。插件通过 `ctx.personas` 和 `ctx.prompts` 共享读取，并在 `model.generate` 中指定使用的 ID；管理页面提供编辑与导出。

插件补充和各工具说明随插件包存放，激活时读取快照：

```text
plugins/ai/chat/prompts/
├─ system.md                # 该插件的通用补充，固定文件名
└─ tools/
   ├─ stay_silent.md         # @Tool(prompt_file="stay_silent.md")
   ├─ view_message_images.md
   ├─ read_file.md
   ├─ write_file.md
   └─ execute_shell.md
```

`ai.tools` 使用相同的固定目录约定。框架初始化提供 `chat`、`owner`、`memory` 卡片，详见 [系统提示词](system-prompts.md)。

其他插件仍可自行组织 `images/` 等资源目录。纯资源插件设置 `kind = "resource"`，不需要 `plugin.py`；框架提供 ResourceBundle 快照，声明依赖的消费者读取相对路径。它与统一卡片目录是独立接口。

插件需要复杂业务文件结构时，推荐在 `ctx.data_dir` 下自行划分 `config/`、`memory/`、`cache/` 等目录。框架仅分配该路径，不规定记忆格式，也不替插件管理业务存储。

自定义配置页放在插件包的 `frontend/index.html`，搭配可选的 `frontend/index.js`、`frontend/style.css`；配置读写由包根目录 `settings.py` 提供。AI 聊天插件将配置存入 `.accbot/data/plugins/ai.chat/config/settings.json`，记忆存入 `ai.chat/memory/<稳定记忆身份哈希>/`；网络工具下载存入 `ai.tools/downloads/`。详见 [插件配置页面](plugin-frontend.md)。
