import { icon } from "./icons.js";
export const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const protocolName = (value) =>
  value === "responses" ? "Responses" : "Chat Completions";
export function badge(label, tone = "", dot = false) {
  return `<span class="badge ${tone}">${dot ? '<span class="status-dot"></span>' : ""}${esc(label)}</span>`;
}
export function heading(
  title,
  description,
  actions = "",
  eyebrow = "WORKSPACE",
) {
  return `<div class="page-heading"><div><div class="page-eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><p class="page-subtitle">${esc(description)}</p></div><div class="heading-actions">${actions}</div></div>`;
}
export function metric(label, value, unit, detail, kind) {
  return `<div class="metric"><span class="metric-icon">${icon(kind)}</span><p class="metric-label">${esc(label)}</p><div class="metric-value">${esc(value)}<small>${esc(unit)}</small></div><p class="metric-detail">${esc(detail)}</p></div>`;
}
export function empty(title, description, kind = "box", action = "") {
  return `<div class="empty-state"><div class="empty-illustration">${icon(kind)}</div><h2>${esc(title)}</h2><p>${esc(description)}</p>${action}</div>`;
}
export function toast(message, error = false) {
  const item = document.createElement("div");
  item.className = "toast" + (error ? " error" : "");
  item.setAttribute("role", error ? "alert" : "status");
  item.innerHTML =
    icon(error ? "alert" : "circlecheck") + `<span>${esc(message)}</span>`;
  document.getElementById("toasts").append(item);
  setTimeout(() => item.remove(), 6000);
}
export async function action(button, work) {
  const errorBox = button?.closest("dialog")?.querySelector(".form-error");
  if (errorBox) errorBox.textContent = "";
  const html = button?.innerHTML;
  if (button) {
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>处理中';
  }
  try {
    return await work();
  } catch (error) {
    if (errorBox?.isConnected) errorBox.textContent = error.message;
    toast(error.message, true);
  } finally {
    if (button?.isConnected) {
      button.disabled = false;
      button.innerHTML = html;
    }
  }
}
export function dialog({
  title,
  description = "",
  body = "",
  footer = "",
  onClose,
  className = "",
}) {
  const element = document.createElement("dialog");
  element.className = "dialog " + className;
  element.innerHTML = `<div class="dialog-header"><div><h2>${esc(title)}</h2><p>${esc(description)}</p></div><button type="button" class="icon-button" data-close aria-label="关闭">${icon("x")}</button></div><div class="dialog-body">${body}</div><div class="dialog-footer">${footer || '<button class="button" data-close>关闭</button>'}</div>`;
  document.getElementById("dialogs").append(element);
  element.showModal();
  const close = () => element.close();
  element
    .querySelectorAll("[data-close]")
    .forEach((button) => (button.onclick = close));
  element.addEventListener("click", (event) => {
    if (event.target === element) {
      const rect = element.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        close();
    }
  });
  element.addEventListener(
    "close",
    () => {
      onClose?.();
      element.remove();
    },
    { once: true },
  );
  return {
    element,
    body: element.querySelector(".dialog-body"),
    footer: element.querySelector(".dialog-footer"),
    close,
  };
}
export function steps(labels, current) {
  return `<div class="steps">${labels.map((label, i) => `${i ? '<span class="step-line"></span>' : ""}<div class="step ${i === current ? "active" : i < current ? "done" : ""}"><span class="step-number">${i < current ? icon("check") : i + 1}</span><span>${esc(label)}</span></div>`).join("")}</div>`;
}
export function field(label, name, value = "", options = {}) {
  const {
    type = "text",
    placeholder = "",
    help = "",
    optional = false,
    readonly = false,
    required = false,
    list = "",
  } = options;
  return `<label class="field"><span class="field-label">${esc(label)}${optional ? '<span class="optional">选填</span>' : ""}</span>${type === "password" ? '<div class="password-input">' : ""}<input class="input ${name === "id" ? "mono" : ""}" name="${name}" ${name === "id" ? 'pattern="[a-zA-Z0-9][a-zA-Z0-9_.\\-]*" title="使用英文字母、数字、点、下划线或连字符，并以字母或数字开头" maxlength="100"' : ""} value="${esc(value)}" type="${type}" placeholder="${esc(placeholder)}" ${readonly ? "readonly" : ""} ${required ? "required" : ""} ${list ? `list="${list}"` : ""} autocomplete="${type === "password" ? "new-password" : "off"}">${type === "password" ? `<button type="button" class="icon-button" data-password="${name}" aria-label="显示密钥">${icon("eye")}</button></div>` : ""}${help ? `<p class="field-help">${esc(help)}</p>` : ""}</label>`;
}
export function bindPasswords(root) {
  root.querySelectorAll("[data-password]").forEach(
    (button) =>
      (button.onclick = () => {
        const input = root.querySelector(`[name="${button.dataset.password}"]`);
        input.type = input.type === "password" ? "text" : "password";
        button.innerHTML = icon(input.type === "password" ? "eye" : "eye-off");
        button.setAttribute(
          "aria-label",
          input.type === "password" ? "显示密钥" : "隐藏密钥",
        );
      }),
  );
}
export function formValues(root) {
  return Object.fromEntries(
    [...root.querySelectorAll("input[name],select[name]")].map((input) => [
      input.name,
      input.type === "checkbox" ? input.checked : input.value,
    ]),
  );
}
export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    toast("复制失败，请手动选择并复制。", true);
  }
}
export function confirm({
  title,
  message,
  items = [],
  label = "确认",
  danger = false,
  onConfirm,
}) {
  const view = dialog({
    title,
    body: `<p class="section-description">${esc(message)}</p>${items.length ? `<div class="confirm-list">${items.map((item) => `<div>${esc(item)}</div>`).join("")}</div>` : ""}`,
    footer: `<button class="button ghost" data-cancel>取消</button><button class="button ${danger ? "danger" : "primary"}" data-confirm>${esc(label)}</button>`,
  });
  view.footer.querySelector("[data-cancel]").onclick = view.close;
  view.footer.querySelector("[data-confirm]").onclick = (event) =>
    action(event.currentTarget, async () => {
      await onConfirm();
      view.close();
    });
  return view;
}
