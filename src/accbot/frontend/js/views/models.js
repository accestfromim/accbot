import { api } from "../api.js";
import { state, refresh } from "../state.js";
import { icon } from "../icons.js";
import {
  esc,
  badge,
  heading,
  metric,
  empty,
  toast,
  action,
  confirm,
  dialog,
  field,
  steps,
  bindPasswords,
  formValues,
  protocolName,
} from "../components.js";

export function renderModels(root) {
  root.innerHTML =
    heading(
      "模型卡",
      "将模型接入工作台，供插件按 ID 选用。",
      `<button class="button primary" id="add-model">${icon("plus")}新增模型卡</button>`,
      "INTELLIGENCE",
    ) +
    `<div class="metrics">${metric("已配置模型", state.models.length, "个", "可由插件自由组合调用", "brain")}${metric("API 端点", new Set(state.models.map((m) => m.endpoint)).size, "个", "支持兼容 OpenAI 的服务", "server")}${metric("协议类型", new Set(state.models.map((m) => m.protocol)).size, "种", "Responses / Chat Completions", "cable")}</div>` +
    (state.models.length
      ? `<div class="section-toolbar"><span class="section-label">全部模型卡</span><span class="toolbar-total">${state.models.length} 个模型</span></div><div class="card-grid">${state.models.map((model) => `<article class="card model-card"><div class="card-main"><div class="card-header"><div class="card-icon violet">${icon("brain")}</div><div class="card-heading"><h3>${esc(model.name || model.id)}</h3><div class="card-id">${esc(model.id)}</div></div>${badge(protocolName(model.protocol), "violet")}</div><div class="model-details"><div class="model-detail">${icon("box")}<span class="detail-label">模型名</span><span class="mono" title="${esc(model.model)}">${esc(model.model)}</span></div><div class="model-detail">${icon("link")}<span class="detail-label">端点</span><span class="mono" title="${esc(model.endpoint)}">${esc(model.endpoint)}</span></div><div class="model-detail">${icon("key")}<span class="detail-label">密钥</span><span>${model.credential_configured ? "已安全保存" : "未设置"}</span></div></div>${model.description ? `<p class="card-description">${esc(model.description)}</p>` : ""}</div><div class="card-footer"><span>供插件按 ID 调用</span><div class="actions"><button class="button small ghost" data-edit="${esc(model.id)}">${icon("edit")}编辑</button><button class="icon-button danger" data-delete="${esc(model.id)}" aria-label="删除 ${esc(model.id)}">${icon("trash")}</button></div></div></article>`).join("")}</div>`
      : empty(
          "添加第一个模型",
          "输入端点、密钥和模型名，工作台会帮你识别可用协议。",
          "brain",
          '<button class="button primary" id="first-model">' +
            icon("plus") +
            "新增模型卡</button>",
        ));
  root
    .querySelectorAll("#add-model,#first-model")
    .forEach((button) => (button.onclick = () => modelWizard()));
  root
    .querySelectorAll("[data-edit]")
    .forEach(
      (button) =>
        (button.onclick = () =>
          modelWizard(state.models.find((m) => m.id === button.dataset.edit))),
    );
  root.querySelectorAll("[data-delete]").forEach(
    (button) =>
      (button.onclick = () =>
        confirm({
          title: "删除模型卡",
          message:
            "删除后插件将无法再按此 ID 获取模型。已保存的密钥可在密钥管理中单独清理。",
          items: [button.dataset.delete],
          label: "删除模型卡",
          danger: true,
          onConfirm: async () => {
            await api(
              `models/cards/${encodeURIComponent(button.dataset.delete)}`,
              { method: "DELETE" },
            );
            await refresh();
            toast("模型卡已删除");
          },
        })),
  );
}

export function modelWizard(existing) {
  let step = 0,
    draft = {
      id: existing?.id || "",
      name: existing?.name || "",
      endpoint: existing?.endpoint || "",
      key: "",
      model: existing?.model || "",
      description: existing?.description || "",
      protocol: existing?.protocol || "auto",
    },
    catalog = [],
    detected = null;
  const view = dialog({
    title: existing ? "编辑模型卡" : "新增模型卡",
    description: "连接服务、选择模型，再自动识别调用协议。",
  });
  function draw() {
    view.body.innerHTML =
      steps(["连接服务", "选择模型", "识别协议"], step) +
      '<div class="form-error" role="alert" id="wizard-error"></div>' +
      (step === 0
        ? `<form id="model-form">${field("模型卡 ID", "id", draft.id, { placeholder: "例如：main-chat", required: true, readonly: !!existing, help: "插件通过这个唯一 ID 选择模型，创建后不可更改。" })}${field("显示名称", "name", draft.name, { optional: true, placeholder: "例如：群聊主模型" })}${field("API 端点", "endpoint", draft.endpoint, { placeholder: "https://api.example.com/v1", required: true, type: "url", help: "填写 API 基础地址；也可以粘贴完整的 chat/completions 或 responses 地址。" })}${field("API 密钥", "key", draft.key, { type: "password", optional: true, placeholder: existing?.credential_configured ? "已保存，留空保持原密钥" : "sk-… 或本地服务留空", help: "直接填写密钥即可，环境变量名称和保存位置由工作台管理。" })}</form>`
        : step === 1
          ? `<form id="model-form">${field("模型名称", "model", draft.model, { required: true, placeholder: "输入服务商提供的模型名", list: "model-options", help: "使用服务商的真实模型名，和上一步的模型卡 ID 相互独立。" })}<datalist id="model-options">${catalog.map((name) => `<option value="${esc(name)}">`).join("")}</datalist><button class="button small ghost" type="button" id="fetch-catalog">${icon("refresh")}获取可选模型</button><p class="catalog-note" id="catalog-note">${catalog.length ? `已发现 ${catalog.length} 个模型，可输入或从候选中选择。` : "可尝试读取服务商的模型列表，也可以直接手动填写。"}</p><div style="height:22px"></div>${field("说明", "description", draft.description, { optional: true, placeholder: "例如：适合日常群聊，支持工具调用" })}</form>`
          : `<h3 class="dialog-section-title">识别可用的调用协议</h3><p class="dialog-section-description">将发送少量测试内容，依次检查 Responses 和 Chat Completions。此操作可能产生少量 API 用量。</p><div class="wizard-summary"><dl class="detail-list"><div><dt>模型卡</dt><dd>${esc(draft.name || draft.id)}</dd></div><div><dt>端点</dt><dd class="mono" title="${esc(draft.endpoint)}">${esc(draft.endpoint)}</dd></div><div><dt>模型</dt><dd class="mono">${esc(draft.model)}</dd></div><div><dt>密钥</dt><dd>${draft.key ? "保存新密钥" : existing?.credential_configured ? "使用已保存的密钥" : "无需密钥"}</dd></div></dl></div><div id="probe-result" class="model-probe-result">${detected ? probeResult() : `<div class="probe-visual">${icon("cable")}<strong>等待检测</strong><small>点击下方按钮，确认此模型支持的协议。</small></div>`}</div><div style="display:flex;justify-content:center;margin-top:16px"><button class="button" id="detect-protocol">${icon("activity")}${detected ? "重新检测" : "检测连接与协议"}</button></div><details style="margin-top:20px"><summary class="field-help">手动选择协议</summary><p class="field-help">如果服务商限制测试请求，可以直接指定协议后保存。</p><select class="input" id="manual-protocol" aria-label="手动选择协议"><option value="auto">自动检测</option><option value="responses" ${draft.protocol === "responses" ? "selected" : ""}>Responses</option><option value="chat" ${draft.protocol === "chat" ? "selected" : ""}>Chat Completions</option></select></details>`);
    view.footer.innerHTML = `<button class="button ghost" id="previous">${step ? "上一步" : "取消"}</button><button class="button primary" id="next" ${step === 2 && draft.protocol === "auto" ? "disabled" : ""}>${step === 2 ? "保存模型卡" : "下一步"}${icon(step === 2 ? "check" : "arrow-right")}</button>`;
    bindPasswords(view.body);
    view.body.querySelector("form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      next();
    });
    view.footer.querySelector("#previous").onclick = () => {
      capture();
      if (step) {
        step--;
        draw();
      } else view.close();
    };
    view.footer.querySelector("#next").onclick = next;
    view.body
      .querySelector("#fetch-catalog")
      ?.addEventListener("click", (event) =>
        action(event.currentTarget, async () => {
          capture();
          const result = await api("models/catalog", {
            method: "POST",
            body: {
              endpoint: draft.endpoint,
              key: draft.key || null,
              existing_id: existing?.id,
            },
          });
          catalog = result.models;
          draw();
          if (!catalog.length)
            toast("服务未返回模型列表，请手动填写模型名称。");
        }),
      );
    view.body
      .querySelector("#detect-protocol")
      ?.addEventListener("click", (event) =>
        action(event.currentTarget, async () => {
          view.body.querySelector("#wizard-error").textContent = "";
          try {
            detected = await api("models/detect", {
              method: "POST",
              body: { ...draft, protocol: "auto", key: draft.key || null },
            });
            draft.protocol = detected.protocol;
            draft.endpoint = detected.endpoint;
            draw();
          } catch (error) {
            view.body.querySelector("#wizard-error").textContent =
              error.message;
            throw error;
          }
        }),
      );
    view.body
      .querySelector("#manual-protocol")
      ?.addEventListener("change", (event) => {
        draft.protocol = event.target.value;
        detected = null;
        view.footer.querySelector("#next").disabled = draft.protocol === "auto";
      });
  }
  function probeResult() {
    return `<div class="probe-visual success">${icon("circlecheck")}<strong>连接成功 · ${protocolName(detected.protocol)}</strong><small>响应耗时 ${detected.latency_ms} ms · 已自动选用可用协议</small></div>`;
  }
  function capture() {
    if (step < 2) {
      const values = formValues(view.body);
      if (
        Object.entries(values).some(
          ([key, value]) =>
            ["endpoint", "key", "model"].includes(key) && value !== draft[key],
        )
      ) {
        detected = null;
        draft.protocol = "auto";
      }
      Object.assign(draft, values);
    }
  }
  function next() {
    const form = view.body.querySelector("form");
    if (form && !form.reportValidity()) return;
    capture();
    if (step < 2) {
      step++;
      draw();
      return;
    }
    action(view.footer.querySelector("#next"), async () => {
      await api(
        existing
          ? `models/cards/${encodeURIComponent(existing.id)}`
          : "models/cards",
        {
          method: existing ? "PUT" : "POST",
          body: { ...draft, key: draft.key || null },
        },
      );
      view.close();
      await refresh();
      toast("模型卡已保存");
    });
  }
  draw();
}
