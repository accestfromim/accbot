# 插件配置页面

插件可以在包内固定的 `frontend/` 目录提供配置组件。框架发现 `frontend/index.html` 后，在插件名称和卡片底部提供配置入口，点击后打开右侧配置栏。

```text
plugins/your-group/your-plugin/
├─ plugin.toml
├─ plugin.py                 # 业务入口，资源插件可省略
├─ settings.py               # 可选的插件配置读写接口
└─ frontend/
   ├─ index.html             # 必需，一个根 div
   ├─ index.js               # 可选，导出 mount(root, api)
   ├─ style.css              # 可选，组件样式
   └─ ...                    # 自有 JS 模块、图片等公开静态文件
```

`index.html` 是组件片段，不包含 `html`、`head`、`body`、DOCTYPE 或 script 标签。标签需要完整闭合，标准空元素如 input、img 不需要结束标签。样式和交互分别放在 CSS、JS 文件中。不需要在 `plugin.toml` 中重复声明页面位置。

```html
<div>
  <form>
    <label class="field">
      <span class="field-label">默认模型</span>
      <select class="input" name="default_model"></select>
    </label>
    <p class="form-error" role="alert"></p>
    <button class="button primary" type="submit">保存配置</button>
  </form>
</div>
```

框架将组件挂载在 Shadow DOM 中，继承明暗主题变量，并提供 `.field`、`.input`、`.button` 等基础组件样式。插件 CSS 的普通选择器只作用于其配置组件。插件前端代码与本地安装的 Python 插件一样是受信任的扩展；Shadow DOM 用来隔离布局和样式，不是脚本安全沙箱。

## 前端接口

`index.js` 使用标准 ES 模块，可通过相对路径导入同一 `frontend/` 下的其他 JS 模块。框架调用导出的 `mount(root, api)`，其中 root 是该组件的根 div。mount 可以是异步函数，也可以返回卸载清理函数。

| 接口 | 行为 |
| --- | --- |
| `api.plugin` | 插件 ID、显示名称和当前是否激活 |
| `await api.listModels()` | 获取现有模型卡，字段包括 id、name、model、protocol；不含密钥值 |
| `await api.listPersonas()` | 读取框架共享人格目录，包含 id、name、description、system_prompt |
| `await api.listPrompts()` | 读取框架共享系统提示词卡，包含 id、name、description、content |
| `await api.listConnections()` | 获取全部已保存的接入配置摘要，包括未激活配置；字段为 id、name、platform、enabled、status，不含凭据 |
| `await api.getSettings()` | 调用本插件配置处理器的 read，返回插件定义的 JSON 对象 |
| `await api.saveSettings(values)` | 调用 write，返回插件确认保存的 JSON 对象，并显示保存结果 |
| `api.notify(text, tone)` | 在侧栏显示提示，tone 支持 success、warn、error、info |
| `api.assetUrl(path)` | 返回本插件当前版本的前端资源地址 |
| `api.signal` | 页面关闭或失效时中止，用于请求、事件监听等 |
| `api.onCleanup(callback)` | 注册计时器、观察器或其他资源的释放回调 |

```js
export async function mount(root, api) {
  const [models, values] = await Promise.all([
    api.listModels(), api.getSettings(),
  ]);
  if (api.signal.aborted) return;
  const select = root.querySelector('[name="default_model"]');
  select.add(new Option('暂不设置', ''));
  for (const model of models) {
    select.add(new Option(model.name || model.id, model.id));
  }
  select.value = values.default_model || '';
  const button = root.querySelector('button');
  const errorBox = root.querySelector('.form-error');
  root.querySelector('form').addEventListener('submit', async event => {
    event.preventDefault();
    button.disabled = true;
    errorBox.textContent = '';
    try {
      await api.saveSettings({ default_model: select.value || null });
    } catch (error) {
      if (!api.signal.aborted) errorBox.textContent = error.message;
    } finally {
      if (!api.signal.aborted) button.disabled = false;
    }
  }, { signal: api.signal });
}
```

组件 HTML 中相对的 src/href 会定位到本插件的前端目录，CSS 的 url 和 JS 的相对 import 也按这个目录解析。动态创建图片等资源时使用 `api.assetUrl()`。每次打开页面都会调用 mount；把每个页面实例的状态放在 mount 内，关闭时通过返回的函数、onCleanup 或 signal 清理。

## 插件自行处理配置

需要保存配置的插件在包根目录提供 `settings.py`，导出继承 `accbot.PluginSettings` 的 `Settings` 类：

```python
from accbot import PluginSettings


class Settings(PluginSettings):
    async def read(self) -> dict:
        # 读取插件自己的文件或其他存储，返回配置。
        ...

    async def write(self, values: dict) -> dict:
        # 校验并保存配置，返回规范化后的已保存配置。
        ...
```

`self.ctx` 提供 `plugin_id`、`data_dir`、`models`、`personas`、`prompts`。框架为每次读写创建处理器实例；持久状态由插件存储，不能依赖处理器实例属性或模块全局变量跨请求保持。read/write 支持同步返回或异步返回 JSON 对象；可抛 FrameworkError 给出业务错误，或使用 Pydantic 进行字段校验。未处理异常会被框架捕获，不返回异常中的原始请求内容。

配置页面在插件未激活时也能使用，框架不会为保存配置调用业务插件的 on_activate，也不会自动激活依赖。settings.py 应独立完成配置读写，不依赖其他插件已激活。只展示内容的组件可以省略 settings.py。

插件活跃时，保存成功后会调用业务实例的 `async on_settings_changed(values)`。插件可在这里更新默认模型等运行参数；也应在 on_activate 中读取持久配置。回调失败时仍保留已保存的配置，并明确提示重载插件。

可运行示例位于 [AI 聊天插件](../plugins/ai/chat/)：

- [settings.py](../plugins/ai/chat/settings.py) 校验模型 ID，自行保存到 `ctx.data_dir / "config/settings.json"`。
- [plugin.py](../plugins/ai/chat/plugin.py) 启动时读取配置，保存后保存旧会话的待整理记录，并以新配置接收消息。
- [frontend/](../plugins/ai/chat/frontend/) 提供模型、人格与三种用途的系统提示词选择，以及群聊、主人通道与触发策略配置。
- 接入配置 ID 提供已有配置的输入建议；不存在时显示字段提示，但不阻止保存，允许先配置聊天规则再添加适配器。读取配置目录失败也只提示无法核对。重新打开页面或返回窗口时刷新目录。

## 重载和管理接口

活跃插件的前端文件与 Python 源码一起使用该次激活的快照。修改后通过 reload 更新；未激活插件重新打开页面即可读取磁盘上的新版本。页面携带版本标识，激活、卸载、重载或未激活源码变更后，旧请求返回 409，不能写入新的插件实例。工作台会在状态刷新时提示重新打开，并清理旧组件的监听器等资源。

页面及静态资源使用相同的管理认证。只发布 frontend 目录中支持的静态文件，隐藏文件、Python 文件和越界路径不对外提供。

| 接口 | 用途 |
| --- | --- |
| `GET /api/plugins/{id}/ui` | 组件 HTML、版本、激活身份与可选 JS/CSS 信息 |
| `GET /api/plugins/{id}/ui/assets/{revision}/{path}` | 当前页面版本的公开静态文件 |
| `GET /api/plugins/{id}/settings?revision=…` | 调用插件 read |
| `PUT /api/plugins/{id}/settings` | 传入 `{revision, values}`，调用插件 write |

框架不为这些业务配置选择文件名或保存格式，也不会将它们并入 `config/framework.json` 或 `config/plugins.json`。
