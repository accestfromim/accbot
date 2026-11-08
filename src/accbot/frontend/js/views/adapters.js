import { api } from "../api.js";
import { state, refresh } from "../state.js";
import { icon } from "../icons.js";
import {
  esc,
  badge,
  heading,
  empty,
  toast,
  action,
  confirm,
  dialog,
  field,
  steps,
  bindPasswords,
  formValues,
  copy,
} from "../components.js";

const wsURL = (id) =>
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/onebot/${encodeURIComponent(id)}/ws`;
const httpURL = (id) =>
  `${location.origin}/onebot/${encodeURIComponent(id)}/events`;
const modeNames = {
  reverse_ws: "反向 WebSocket",
  forward_ws: "正向 WebSocket",
  http: "HTTP",
};
const connectionURL = (config) =>
  config.mode === "forward_ws"
    ? config.ws_url
    : config.mode === "http"
      ? httpURL(config.id)
      : wsURL(config.id);
export function renderAdapters(root) {
  root.innerHTML =
    heading(
      "适配器",
      "连接聊天平台，把平台消息转换为统一事件。",
      "",
      "CONNECTIONS",
    ) +
    `<div class="adapter-catalog"><a class="card adapter-type-card" href="#onebot"><div class="adapter-type-header"><div class="onebot-mark">${icon("cable")}</div><div><h2>OneBot</h2><p>QQ · OneBot 11</p></div>${badge("官方适配器", "accent")}</div><p>连接 SnowLuma、NapCat 等 OneBot 协议端，接收群聊消息、成员事件，并通过统一接口发送消息。</p><div class="adapter-type-footer"><span>${state.connections.length} 个配置 · ${state.connections.filter((c) => c.enabled).length} 个已激活</span><span>管理配置 ${icon("arrow-right")}</span></div></a></div>`;
}
function status(config) {
  const live = state.status.connections.find((c) => c.id === config.id);
  if (!config.enabled) return badge("未激活");
  if (config.mode === "http") return badge("已激活", "good", true);
  return live?.websocket
    ? badge("已连接", "good", true)
    : badge(
        config.mode === "forward_ws"
          ? live?.status === "reconnecting" ? "重连中" : "连接中"
          : "等待连接",
        "warn",
        true,
      );
}
export function renderOneBot(root) {
  root.innerHTML =
    heading(
      "OneBot",
      "管理 QQ 接入配置，保存后可独立激活或停用。",
      `<button class="button primary" id="add-onebot">${icon("plus")}新增配置</button>`,
      "ADAPTER / ONEBOT 11",
    ) +
    `<div class="notice info adapter-intro">${icon("info")}<div class="notice-body"><strong>先保存配置，再激活接入。</strong> 正向 WebSocket 由 accbot 主动连接协议端；反向 WebSocket 由协议端连接 accbot；HTTP 模式需配置 API 和事件上报地址。</div></div>` +
    (state.connections.length
      ? `<div class="section-toolbar"><span class="section-label">已保存的配置</span><span class="toolbar-total">${state.connections.length} 个配置</span></div><div class="card-grid">${state.connections.map((config) => `<article class="card connection-card"><div class="card-main"><div class="card-header"><div class="card-icon">${icon("cable")}</div><div class="card-heading"><h3>${esc(config.name || config.id)}</h3><div class="card-id">${esc(config.id)}</div></div><span data-connection-status="${esc(config.id)}">${status(config)}</span></div><div class="connection-identity"><span class="avatar">${icon("user")}</span><span>机器人 QQ</span><span class="mono">${esc(config.self_id)}</span></div><p class="connection-method">${modeNames[config.mode]} · ${config.token_configured ? "已设置连接密钥" : "未设置连接密钥"}</p><div class="connection-url">${icon("link")}<code title="${esc(connectionURL(config))}">${esc(connectionURL(config))}</code><button class="icon-button" data-copy="${esc(config.id)}" aria-label="复制连接地址">${icon("copy")}</button></div></div><div class="card-footer"><button class="button small ghost" data-edit="${esc(config.id)}">${icon("edit")}编辑</button><div class="actions"><button class="button small ${config.enabled ? "ghost" : "primary"}" data-switch="${esc(config.id)}">${icon(config.enabled ? "stop" : "play")}${config.enabled ? "停用" : "激活"}</button><button class="icon-button danger" data-delete="${esc(config.id)}" aria-label="删除 ${esc(config.id)}">${icon("trash")}</button></div></div></article>`).join("")}</div>`
      : empty(
          "连接你的第一个 QQ 账号",
          "添加 OneBot 配置后，再到协议端完成连接。",
          "cable",
          `<button class="button primary" id="first-onebot">${icon("plus")}新增配置</button>`,
        ));
  root
    .querySelectorAll("#add-onebot,#first-onebot")
    .forEach((button) => (button.onclick = () => onebotWizard()));
  root
    .querySelectorAll("[data-edit]")
    .forEach(
      (button) =>
        (button.onclick = () =>
          onebotWizard(
            state.connections.find((c) => c.id === button.dataset.edit),
          )),
    );
  root.querySelectorAll("[data-copy]").forEach(
    (button) =>
      (button.onclick = () => {
        const c = state.connections.find((c) => c.id === button.dataset.copy);
        copy(connectionURL(c));
      }),
  );
  root.querySelectorAll("[data-switch]").forEach(
    (button) =>
      (button.onclick = () =>
        action(button, async () => {
          const c = state.connections.find(
            (c) => c.id === button.dataset.switch,
          );
          await api(
            `adapters/onebot/${encodeURIComponent(c.id)}/${c.enabled ? "deactivate" : "activate"}`,
            { method: "POST" },
          );
          await refresh();
          toast(c.enabled ? "配置已停用" : "配置已激活");
        })),
  );
  root.querySelectorAll("[data-delete]").forEach(
    (button) =>
      (button.onclick = () =>
        confirm({
          title: "删除 OneBot 配置",
          message: "正在使用此配置的连接将关闭。",
          items: [button.dataset.delete],
          label: "删除配置",
          danger: true,
          onConfirm: async () => {
            await api(
              `adapters/onebot/${encodeURIComponent(button.dataset.delete)}`,
              { method: "DELETE" },
            );
            await refresh();
            toast("配置已删除");
          },
        })),
  );
}
export function updateOneBotStatus(root) {
  root.querySelectorAll("[data-connection-status]").forEach((element) => {
    const c = state.connections.find(
      (c) => c.id === element.dataset.connectionStatus,
    );
    if (c) element.innerHTML = status(c);
  });
}

function onebotWizard(existing) {
  let step = 0,
    draft = {
      id: existing?.id || "",
      name: existing?.name || "",
      self_id: existing?.self_id || "",
      mode: existing?.mode || "reverse_ws",
      http_url: existing?.http_url || "http://127.0.0.1:3000",
      ws_url: existing?.ws_url || "ws://127.0.0.1:3001",
      token: "",
      scope: existing?.scope || "",
    };
  const view = dialog({
    title: existing ? "编辑 OneBot 配置" : "新增 OneBot 配置",
    description: "一个配置对应一个 QQ 接入实例，可以保存多个配置。",
  });
  function draw() {
    view.body.innerHTML =
      steps(["账号信息", "连接方式", "确认配置"], step) +
      '<div class="form-error" role="alert" id="wizard-error"></div>' +
      (step === 0
        ? `<form>${field("配置 ID", "id", draft.id, { required: true, readonly: !!existing, placeholder: "例如：qq-main", help: "用于区分不同的接入配置，也会出现在连接地址中。" })}${field("显示名称", "name", draft.name, { optional: true, placeholder: "例如：日常群聊机器人" })}${field("机器人 QQ 号", "self_id", draft.self_id, { required: true, placeholder: "填写登录协议端的 QQ 号" })}</form>`
        : step === 1
          ? `<form><label class="radio-card"><input type="radio" name="mode" value="reverse_ws" ${draft.mode === "reverse_ws" ? "checked" : ""}><div><strong>反向 WebSocket</strong><p>由协议端主动连接 accbot，同一连接负责消息收发。</p></div></label><label class="radio-card"><input type="radio" name="mode" value="forward_ws" ${draft.mode === "forward_ws" ? "checked" : ""}><div><strong>正向 WebSocket</strong><p>accbot 主动连接 SnowLuma 等协议端，同一连接负责消息收发。</p></div></label><label class="radio-card"><input type="radio" name="mode" value="http" ${draft.mode === "http" ? "checked" : ""}><div><strong>HTTP</strong><p>accbot 调用协议端 HTTP API，协议端通过 HTTP 上报事件。</p></div></label><div id="http-field" ${draft.mode === "http" ? "" : "hidden"}>${field("协议端 HTTP API 地址", "http_url", draft.http_url, { type: "url", required: true, placeholder: "http://127.0.0.1:3000", help: "这是 NapCat 等协议端的地址，不是 accbot 的管理地址。" })}</div><div id="ws-field" ${draft.mode === "forward_ws" ? "" : "hidden"}>${field("协议端 WebSocket 地址", "ws_url", draft.ws_url, { type: "url", required: true, placeholder: "ws://127.0.0.1:3001/", help: "填写同时提供事件和 API 的地址，例如 SnowLuma 的 ws://127.0.0.1:3001/。跨容器时使用协议端服务名。" })}</div>${field("连接密钥 / Access Token", "token", draft.token, { type: "password", optional: true, placeholder: existing?.token_configured ? "已保存，留空保持原密钥" : "与协议端保持一致", help: "工作台自动管理密钥。协议端如设置了 Access Token，请填写相同的值。" })}</form>`
          : `<h3 class="dialog-section-title">准备保存</h3><p class="dialog-section-description">${existing?.enabled ? "此配置已激活，修改连接信息会更新当前连接。" : "新配置保存后处于未激活状态，点击配置卡片上的「激活」即可启用。"}</p><div class="wizard-summary"><dl class="detail-list"><div><dt>配置</dt><dd>${esc(draft.name || draft.id)}</dd></div><div><dt>QQ 号</dt><dd class="mono">${esc(draft.self_id)}</dd></div><div><dt>连接方式</dt><dd>${modeNames[draft.mode]}</dd></div><div><dt>连接密钥</dt><dd>${draft.token ? "保存新密钥" : existing?.token_configured ? "使用已保存的密钥" : "未设置"}</dd></div>${draft.mode === "http" ? `<div><dt>协议端 API</dt><dd class="mono">${esc(draft.http_url)}</dd></div>` : ""}</dl></div><p class="section-label" style="margin-top:23px">${draft.mode === "forward_ws" ? "accbot 将连接的协议端 WebSocket 地址" : draft.mode === "http" ? "事件上报地址" : "协议端应填写的反向 WebSocket 地址"}</p><div class="code-box"><code>${esc(connectionURL(draft))}</code><button class="icon-button" id="copy-address" aria-label="复制地址">${icon("copy")}</button></div>`);
    view.footer.innerHTML = `<button class="button ghost" id="previous">${step ? "上一步" : "取消"}</button><button class="button primary" id="next">${step === 2 ? "保存配置" : "下一步"}${icon(step === 2 ? "check" : "arrow-right")}</button>`;
    bindPasswords(view.body);
    function updateTransportFields(mode) {
      for (const [kind, name] of [
        ["http", "http_url"],
        ["forward_ws", "ws_url"],
      ]) {
        const input = view.body.querySelector(`[name="${name}"]`);
        if (!input) continue;
        input.disabled = mode !== kind;
        input.closest(kind === "http" ? "#http-field" : "#ws-field").hidden =
          mode !== kind;
      }
    }
    updateTransportFields(draft.mode);
    view.footer.querySelector("#previous").onclick = () => {
      capture();
      if (step) {
        step--;
        draw();
      } else view.close();
    };
    view.footer.querySelector("#next").onclick = next;
    view.body.querySelector("form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      next();
    });
    view.body.querySelectorAll('[name="mode"]').forEach(
      (input) =>
        (input.onchange = () => {
          updateTransportFields(input.value);
        }),
    );
    view.body
      .querySelector("#copy-address")
      ?.addEventListener("click", () => copy(connectionURL(draft)));
  }
  function capture() {
    if (step === 0) Object.assign(draft, formValues(view.body));
    if (step === 1) {
      draft.mode = view.body.querySelector('[name="mode"]:checked').value;
      draft.token = view.body.querySelector('[name="token"]').value;
      draft.http_url = view.body.querySelector('[name="http_url"]').value;
      draft.ws_url = view.body.querySelector('[name="ws_url"]').value.trim();
    }
  }
  function next() {
    const form = view.body.querySelector("form");
    if (form && !form.reportValidity()) return;
    capture();
    let error = "";
    if (step === 0 && !/^\d+$/.test(draft.self_id))
      error = "QQ 号应只包含数字。";
    if (step === 1 && draft.mode === "http" && !draft.http_url)
      error = "请填写协议端的 HTTP API 地址。";
    if (
      step === 1 &&
      draft.mode === "forward_ws" &&
      !/^wss?:\/\//.test(draft.ws_url)
    )
      error = "请填写 ws:// 或 wss:// 开头的协议端 WebSocket 地址。";
    if (error) {
      view.body.querySelector("#wizard-error").textContent = error;
      return;
    }
    if (step < 2) {
      step++;
      draw();
      return;
    }
    action(view.footer.querySelector("#next"), async () => {
      await api(
        existing
          ? `adapters/onebot/${encodeURIComponent(existing.id)}`
          : "adapters/onebot",
        {
          method: existing ? "PUT" : "POST",
          body: {
            ...draft,
            http_url: draft.mode === "http" ? draft.http_url : null,
            ws_url: draft.mode === "forward_ws" ? draft.ws_url : null,
            token: draft.token || null,
          },
        },
      );
      view.close();
      await refresh();
      toast("OneBot 配置已保存");
    });
  }
  draw();
}
