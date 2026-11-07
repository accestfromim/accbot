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

协议依据：[OneBot 11 正向 WebSocket](https://github.com/botuniverse/onebot-11/blob/master/communication/ws.md)。
