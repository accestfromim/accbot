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
  dialog,
  field,
  bindPasswords,
  confirm,
} from "../components.js";

export function renderCredentials(root) {
  root.innerHTML =
    heading(
      "密钥管理",
      "查看配置使用的凭据，集中更新和清理。",
      "",
      "SECURITY",
    ) +
    `<div class="notice info credentials-header-note">${icon("lock")}<div class="notice-body"><strong>密钥会在添加模型或适配器时自动创建。</strong> 这里仅展示保存状态和使用位置，不回显密钥内容。环境变量名称由工作台管理，无需手动配置。</div></div>` +
    (state.secrets.length
      ? `<div class="table-panel"><table><thead><tr><th>凭据与环境变量</th><th>使用位置</th><th>保存状态</th><th style="text-align:right">操作</th></tr></thead><tbody>${state.secrets.map((secret) => `<tr><td><div class="table-name">${icon("key")}<div><strong>${esc(secret.usages[0]?.name || "未使用的凭据")}</strong><small class="credential-variable" title="${esc(secret.name)}">${esc(secret.name)}</small></div></div></td><td>${secret.usages.length ? secret.usages.map((u) => `<a class="credential-usage" href="#${u.kind === "model" ? "models" : u.kind === "onebot" ? "onebot" : "credentials"}">${icon(u.kind === "model" ? "brain" : "cable")}${esc(u.id)}</a>`).join("<br>") : '<span class="muted">未被引用</span>'}</td><td>${badge(secret.configured ? "已设置" : "未设置", secret.configured ? "good" : "warn")}<p class="key-caption">${secret.source === "environment" ? "来自进程环境变量" : secret.configured ? "保存在本地" : "等待填写"}</p></td><td><div class="table-actions"><button class="button small" data-rotate="${esc(secret.name)}">更新密钥</button>${!secret.usages.length && secret.source === "local" ? `<button class="icon-button danger" data-delete="${esc(secret.name)}" aria-label="清理 ${esc(secret.name)}">${icon("trash")}</button>` : ""}</div></td></tr>`).join("")}</tbody></table></div>`
      : empty(
          "还没有保存密钥",
          "添加模型卡或 OneBot 配置时填写密钥，工作台会在这里统一管理。",
          "key",
          '<a class="button" href="#models">前往模型卡 ' +
            icon("arrow-right") +
            "</a>",
        ));
  root.querySelectorAll("[data-rotate]").forEach(
    (button) =>
      (button.onclick = () => {
        const name = button.dataset.rotate,
          entry = state.secrets.find((s) => s.name === name);
        const view = dialog({
          title: "更新密钥",
          description: entry.usages[0]?.name || name,
          body: `<form>${field("新的密钥", "value", "", { required: true, type: "password", placeholder: "输入新的密钥", help: "保存后会用于后续请求，无需修改模型或适配器配置。" })}</form>`,
          footer:
            '<button class="button ghost" data-cancel>取消</button><button class="button primary" data-save>保存密钥</button>',
        });
        bindPasswords(view.body);
        view.footer.querySelector("[data-cancel]").onclick = view.close;
        const save = () => {
          if (!view.body.querySelector("form").reportValidity()) return;
          action(view.footer.querySelector("[data-save]"), async () => {
            await api(`secrets/${encodeURIComponent(name)}`, {
              method: "PUT",
              body: { value: view.body.querySelector("input").value },
            });
            view.close();
            await refresh();
            toast("密钥已更新");
          });
        };
        view.footer.querySelector("[data-save]").onclick = save;
        view.body.querySelector("form").onsubmit = (event) => {
          event.preventDefault();
          save();
        };
      }),
  );
  root.querySelectorAll("[data-delete]").forEach(
    (button) =>
      (button.onclick = () =>
        confirm({
          title: "清理未使用的密钥",
          message: "这将删除本地保存的凭据。",
          items: [button.dataset.delete],
          label: "清理密钥",
          danger: true,
          onConfirm: async () => {
            await api(`secrets/${encodeURIComponent(button.dataset.delete)}`, {
              method: "DELETE",
            });
            await refresh();
            toast("密钥已清理");
          },
        })),
  );
}
