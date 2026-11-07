# OneBot 11 接入

accbot 的 OneBot 适配器接收平台事件，并通过统一的消息接口调用协议端 API。QQ 登录由 SnowLuma、NapCat 等协议端完成。每个配置对应一个账号和一个接入地址；保存后默认未激活。

## 连接方式

| 模式 | 连接方向 | 配置地址 |
| --- | --- | --- |
| 正向 WebSocket（`forward_ws`） | accbot → 协议端 | 在 accbot 填写 `ws_url` |
| 反向 WebSocket（`reverse_ws`） | 协议端 → accbot | 在协议端填写 accbot 展示的 `/onebot/<id>/ws` 地址 |
| HTTP（`http`） | accbot 调用 API，协议端上报事件 | 在 accbot 填写 `http_url`；在协议端填写 `/onebot/<id>/events` 上报地址 |

两种 WebSocket 模式均使用同一连接接收事件、发送 action，并通过 echo 匹配回执。正向模式应连接同时提供事件和 API 的端点；OneBot 11 标准的 `/api` 或 `/event` 单用途端点不能单独满足完整聊天收发。

## SnowLuma 正向连接

1. 在 SnowLuma 中开启 OneBot WebSocket 服务，确认账号和 WebSocket 专用 Access Token。
2. 在 accbot 的「适配器 → OneBot」中新增配置，填写配置 ID 和机器人 QQ 号。
3. 选择「正向 WebSocket」，填写协议端地址及 Access Token。
4. 保存配置，再点击「激活」。状态从「连接中」变为「已连接」后即可收发事件与调用 API。

SnowLuma 文档给出的 WebSocket 地址是 `ws://snowluma:3001/`。两端运行在同一主机、且协议端监听本机时，可以使用 `ws://127.0.0.1:3001/`；位于不同容器时使用共享网络中的服务名和实际端口。HTTP 和 WebSocket 的令牌可能不同。参见 [SnowLuma 接入文档](https://snowluma.github.io/en/docs/guide/connect-bot)。

配置文件示例：

```json
{
  "id": "qq-main",
  "self_id": "10001",
  "enabled": true,
  "mode": "forward_ws",
  "ws_url": "ws://127.0.0.1:3001/",
  "token_env": "ACCBOT_ONEBOT_QQ_MAIN_TOKEN",
  "timeout": 30
}
```

通过界面保存时，框架自动创建密钥引用，不需要手动填写环境变量。地址支持 `ws://` 和 `wss://`，可以包含部署所需路径；Token 单独填写并通过 `Authorization: Bearer ...` 发送，不放进 URL。地址不接受内嵌用户名、密码、查询参数或片段。正向连接直连协议端，不继承系统 HTTP/WebSocket 代理配置。

## 生命周期与错误

- 构造或检查配置不会连接网络；已激活的配置在服务启动后发起连接。运行中激活也会立即安排连接，连接尝试不阻塞管理接口。
- 连接失败或断线后自动重试，等待从 1 秒逐步增加至最多 30 秒；建立连接后恢复初始等待。每次尝试重新读取密钥。
- 停用、删除、修改连接配置或关闭服务时，取消连接与重连任务并关闭套接字。未修改的其他连接保持运行。
- 未连接时的 API 调用直接失败。已发出但尚未收到回执的调用，在断线时报告结果未知；超时同样报告结果未知，不自动重发有副作用的 action，也不在正向连接断开时回退到 HTTP。
- 事件和 API 回执分别处理。事件队列阻塞不会阻塞回执；入口队列满时关闭连接，避免无限积压。
- 状态分别显示未激活、连接中、重连中、等待连接和已连接；HTTP 模式显示已激活。WebSocket 已连接表示传输连接建立，不等于 QQ 账号一定在线。

配置持久化于 `.accbot/config/adapters/onebot/<id>.json`。JSON 只保存密钥引用，管理 API 不返回已保存的 Token。

## 消息格式与会话唤醒

`message` 字段支持消息段数组和 CQ 字符串，两者统一转成内部 MessageChain。字符串中的正文、`[CQ:at,qq=...]`、引用和媒体按 OneBot 11 解析；未知片段保留为 `onebot.<类型>` 自定义片段。先识别 CQ 片段再解码转义，因此用户输入的字面文本 `&#91;CQ:at,qq=...&#93;` 不会被当成真实提及。

SnowLuma 的 `messageFormat` 可选 `array` 或 `string`，官方默认配置为 `array`；`wsServers.role` 应为 `Universal`，以便同一连接接收事件和调用 API。参见 [SnowLuma 配置](https://snowluma.github.io/en/docs/guide/configuration) 与 [OneBot 字符串消息](https://github.com/botuniverse/onebot-11/blob/master/message/string.md)。

连接建立和聊天会话唤醒是两个独立步骤。默认 AI 插件需要：

- `ai.tools` 和 `ai.chat` 均已激活，聊天插件配置了默认模型。
- 群规则已启用，`connection_id` 等于 accbot 的 OneBot 配置 ID，`group_id` 等于实际QQ群号。接入 ID 不是机器人 QQ 号，也不是 SnowLuma 的服务名称。
- 入站 `self_id` 等于接入配置的机器人 QQ 号；账号不匹配的事件会被拒绝。
- 群消息满足唤醒策略：有效的 `@机器人` 且发送者通过提及白名单，或概率接话命中。默认首次随机接话概率为 1%，普通消息不保证激活会话。
- 机器人自己的 `message_sent` 或发送者为机器人的回显不作为他人消息交给聊天订阅，以免自我循环。主人私聊需要由配置的主人账号发出。

## 事件跟踪

需要检查消息停在哪一层时，可启动：

```sh
accbot serve --trace-events
```

跟踪日志使用 `event` ID 关联入站解析、订阅匹配和唤醒判断，不输出消息正文、昵称或密钥，也不启用 HTTP/WebSocket 的底层调试日志。

| 记录 | 含义 |
| --- | --- |
| `OneBot event decoded` / `OneBot message` | 已解码；包含事件类型、连接、会话、消息与发送者 ID、消息格式及是否自身消息 |
| `OneBot event rejected` | 事件校验失败，例如 `self_id` 不匹配；继续接收后续帧 |
| `reason=duplicate` / `reason=self_echo` | 去重或自身回显处理 |
| `Event matched` / `Event dispatched` | 匹配到的插件、处理方法与订阅数量；`matches=0` 表示没有匹配的活跃订阅 |
| `AI wake decision` | 消息已到达聊天插件；显示提及、主人、已有会话、概率及 `awake` 判断 |

收到心跳或生命周期事件，只能证明连接收包正常；它们不是消息事件，不会进入 `@on_message`。

协议依据：[OneBot 11 正向 WebSocket](https://github.com/botuniverse/onebot-11/blob/master/communication/ws.md)。
