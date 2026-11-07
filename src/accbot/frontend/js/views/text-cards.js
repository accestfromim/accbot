import { api } from "../api.js";
import { state, refresh } from "../state.js";
import { icon } from "../icons.js";
import {
  esc,
  heading,
  empty,
  toast,
  action,
  confirm,
  dialog,
  field,
} from "../components.js";

export function renderTextCards(root, config) {
  const {
    resource,
    label,
    contentField,
    glyph,
    introduction,
    eyebrow,
    description,
    contentLabel,
    help,
    extension,
    defaultContent = "",
    slots = [],
  } = config;
  const cards = state[resource];
  root.innerHTML =
    heading(
      label,
      introduction,
      `<button class="button primary" id="add-card">${icon("plus")}新建${label}</button>`,
      eyebrow,
    ) +
    (cards.length
      ? `
    <div class="section-toolbar"><span class="section-label">全部${label}</span><span class="toolbar-total">${cards.length} 张卡片</span></div>
    <div class="card-grid">${cards
      .map(
        (card) => `
      <article class="card text-card">
        <div class="card-main">
          <div class="card-header"><div class="card-icon violet">${icon(glyph)}</div>
            <div class="card-heading"><h3>${esc(card.name)}</h3><div class="card-id">${esc(card.id)}</div></div>
          </div>
          <p class="card-description">${esc(card.description || "尚未添加简介")}</p>
          <div class="text-card-preview">${esc(card[contentField])}</div>
        </div>
        <div class="card-footer"><span>${card[contentField].length.toLocaleString()} 字符</span><div class="actions">
          <a class="icon-button" href="/api/${resource}/${encodeURIComponent(card.id)}/export" download="${esc(card.id)}.${extension}" aria-label="导出 ${esc(card.name)}" title="导出 ${extension.toUpperCase()}">${icon("download")}</a>
          <button class="button small" data-edit="${esc(card.id)}">${icon("edit")}查看 / 编辑</button>
          <button class="icon-button danger" data-delete="${esc(card.id)}" aria-label="删除 ${esc(card.name)}">${icon("trash")}</button>
        </div></div>
      </article>`,
      )
      .join("")}</div>`
      : empty(`创建第一张${label}`, introduction, glyph));
  root.querySelector("#add-card").onclick = () => editor();
  root.querySelectorAll("[data-edit]").forEach((button) => {
    button.onclick = () =>
      editor(cards.find((c) => c.id === button.dataset.edit));
  });
  root.querySelectorAll("[data-delete]").forEach((button) => {
    button.onclick = () =>
      confirm({
        title: `删除${label}`,
        message: `使用此${label}的插件需要重新选择。已发出的模型请求不受影响。`,
        items: [button.dataset.delete],
        label: `删除${label}`,
        danger: true,
        onConfirm: async () => {
          await api(
            `${resource}/${encodeURIComponent(button.dataset.delete)}`,
            {
              method: "DELETE",
            },
          );
          await refresh();
          toast(`${label}已删除`);
        },
      });
  });

  function editor(existing) {
    const view = dialog({
      title: `${existing ? "编辑" : "新建"}${label}`,
      description,
      className: "text-card-editor",
      body: `<form>
      <div class="text-card-fields">
        ${field(`${label} ID`, "id", existing?.id || "", { required: true, readonly: !!existing, placeholder: "例如：group-chat" })}
        ${field("名称", "name", existing?.name || "", { required: true, placeholder: "便于识别的名称" })}
      </div>
      ${field("简介", "description", existing?.description || "", { optional: true, placeholder: "简要说明用途" })}
      ${slots.length ? `<div class="prompt-slots"><span>插入占位符</span>${slots.map(([token, title]) => `<button type="button" class="button small" data-slot="${esc(token)}" title="${esc(token)}">${esc(title)}</button>`).join("")}</div>` : ""}
      <label class="field"><span class="field-label">${contentLabel}</span>
        <textarea class="input text-card-content" name="${contentField}" rows="13" maxlength="65536" required placeholder="你是……">${esc(existing?.[contentField] ?? defaultContent)}</textarea>
        <span class="field-help">${esc(help)}</span>
      </label>
      <div class="form-error" role="alert"></div>
    </form>`,
      footer: `<button class="button ghost" data-cancel>取消</button><button class="button primary" data-save>${icon("check")}保存${label}</button>`,
    });
    const form = view.body.querySelector("form");
    form.elements.name.maxLength = 200;
    form.elements.description.maxLength = 4000;
    view.body.querySelectorAll("[data-slot]").forEach((button) => {
      button.onclick = () => {
        const input = form.elements[contentField];
        input.setRangeText(
          button.dataset.slot,
          input.selectionStart,
          input.selectionEnd,
          "end",
        );
        input.focus();
      };
    });
    const save = () => {
      if (!form.reportValidity()) return;
      action(view.footer.querySelector("[data-save]"), async () => {
        const card = { version: 1, ...Object.fromEntries(new FormData(form)) };
        await api(
          existing
            ? `${resource}/${encodeURIComponent(existing.id)}`
            : resource,
          {
            method: existing ? "PUT" : "POST",
            body: card,
          },
        );
        view.close();
        await refresh();
        toast(`${label}已保存`);
      });
    };
    view.footer.querySelector("[data-cancel]").onclick = view.close;
    view.footer.querySelector("[data-save]").onclick = save;
    form.onsubmit = (event) => {
      event.preventDefault();
      save();
    };
  }
}
