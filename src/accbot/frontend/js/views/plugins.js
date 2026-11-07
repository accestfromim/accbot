import { openPluginPanel } from "../plugin-panel.js";
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
} from "../components.js";

let selected = new Set(),
  actualSignature = null,
  query = "",
  filter = "all",
  preview = null,
  previewSequence = 0;
const collapsed = new Set();
const nameOf = (id) =>
  state.plugins.find((plugin) => plugin.id === id)?.name || id;
function pluginIcon(plugin) {
  if (plugin.kind === "resource") return ["file", "violet"];
  if (plugin.tags.includes("tool")) return ["terminal", ""];
  if (plugin.tags.includes("observer")) return ["message", "green"];
  if (plugin.tags.includes("timer")) return ["clock", ""];
  return ["puzzle", "neutral"];
}
function card(plugin) {
  const [kind, tone] = pluginIcon(plugin),
    chosen = selected.has(plugin.id);
  const deps = Object.entries(plugin.requires)
    .map(([id, spec]) => {
      const exists = state.plugins.some((p) => p.id === id);
      return `<span class="dependency-chip ${exists ? "" : "missing"}" title="${esc(id + " " + spec)}">${esc(nameOf(id))}${exists ? "" : " · 未安装"}</span>`;
    })
    .join("");
  return `<article class="card plugin-card ${chosen ? "selected" : ""}" data-card="${esc(plugin.id)}"><div class="card-main"><div class="card-header"><input class="check-input" type="checkbox" data-select="${esc(plugin.id)}" aria-label="选择 ${esc(plugin.name || plugin.id)}" ${chosen ? "checked" : ""}><div class="card-icon ${tone}">${icon(kind)}</div><div class="card-heading"><h3>${plugin.has_frontend ? `<button class="plugin-title" data-configure="${esc(plugin.id)}">${esc(plugin.name || plugin.id)}</button>` : esc(plugin.name || plugin.id)}</h3><div class="card-id">${esc(plugin.id)}</div></div>${badge(plugin.active ? "已激活" : "未激活", plugin.active ? "good" : "", plugin.active)}</div><p class="card-description">${esc(plugin.description || "此插件未提供说明。")}</p><div class="plugin-dependencies">${Object.keys(plugin.requires).length ? "<span>依赖</span>" + deps : "<span>无额外依赖</span>"}</div></div><div class="card-footer"><div class="plugin-meta"><span class="mono">v${esc(plugin.version)}</span><span class="meta-separator"></span><span>${plugin.kind === "resource" ? "资源插件" : "代码插件"}</span></div><div class="actions">${plugin.has_frontend ? `<button class="button small primary" data-configure="${esc(plugin.id)}">配置</button>` : ""}${plugin.active ? `<button class="button small ghost" data-unload="${esc(plugin.id)}" title="unload">${icon("stop")}卸载</button><button class="button small ghost" data-reload="${esc(plugin.id)}" title="reload">${icon("refresh")}重载</button>` : `<span class="mono" title="${esc(plugin.directory)}">${esc(plugin.directory.split("/").at(-1))}</span>`}</div></div></article>`;
}
function filtered() {
  return state.plugins.filter(
    (p) =>
      (filter === "all" || (filter === "active" ? p.active : !p.active)) &&
      [p.name, p.id, p.description, p.directory].some((value) =>
        value?.toLowerCase().includes(query.toLowerCase()),
      ),
  );
}
export function renderPlugins(root) {
  const actual = state.plugins
      .filter((p) => p.active)
      .map((p) => p.id)
      .sort(),
    signature = actual.join("|");
  if (actualSignature !== signature) {
    actualSignature = signature;
    selected = new Set(actual);
    preview = null;
  }
  const groups = new Set(state.plugins.map((p) => p.group));
  root.innerHTML =
    heading(
      "插件",
      "按目录管理能力，检查依赖并控制插件的运行状态。",
      `<button class="button ghost" id="plugin-help">${icon("folder-open")}安装说明</button><button class="button" id="scan-plugins">${icon("refresh")}扫描目录</button>`,
      "EXTENSIONS",
    ) +
    `<div class="metrics">${metric("已发现插件", state.plugins.length, "个", "来自本地插件目录", "puzzle")}${metric("正在运行", actual.length, "个", "已激活的插件实例", "activity")}${metric("目录分组", groups.size, "组", "按插件所在子目录归类", "folder")}</div>` +
    `<div class="section-toolbar"><div class="filter-tabs">${[
      ["all", "全部", state.plugins.length],
      ["active", "已激活", actual.length],
      ["inactive", "未激活", state.plugins.length - actual.length],
    ]
      .map(
        ([key, label, count]) =>
          `<button class="filter-tab ${filter === key ? "active" : ""}" data-filter="${key}">${label}<span class="tab-count">${count}</span></button>`,
      )
      .join(
        "",
      )}</div><label class="search-box">${icon("search")}<input id="plugin-search" aria-label="搜索插件" placeholder="搜索名称、ID 或目录…" value="${esc(query)}"></label></div><div id="dependency-preview"></div><div id="plugin-groups"></div><div id="selection-footer"></div>`;
  root.querySelector("#plugin-help").onclick = () =>
    dialog({
      title: "从本地安装插件",
      description: "插件可以按用途分组，框架会递归发现目录中的插件包。",
      body: `<p class="section-description">将插件包复制到插件目录，点击「扫描目录」，再勾选插件及其依赖并应用。</p><div class="code-box"><code>${esc(state.status.plugin_directory || "plugins/")}<br>├─ ai/chat/plugin.toml<br>├─ ai/tools/plugin.toml<br>└─ your-group/your-plugin/plugin.toml</code></div><p class="field-help">推荐的插件业务文件目录：.accbot/data/plugins/&lt;插件 ID&gt;/。插件内部参数和记忆文件由插件自己管理。</p>`,
    });
  root.querySelector("#scan-plugins").onclick = (event) =>
    action(event.currentTarget, async () => {
      await api("plugins/discover", { method: "POST" });
      await refresh();
      toast("插件目录已更新");
    });
  root.querySelectorAll("[data-filter]").forEach(
    (button) =>
      (button.onclick = () => {
        filter = button.dataset.filter;
        renderPlugins(root);
      }),
  );
  root.querySelector("#plugin-search").oninput = (event) => {
    query = event.target.value;
    renderGroups(root);
  };
  renderGroups(root);
  renderFooter(root);
  checkDependencies(root);
}
function renderGroups(root) {
  const list = filtered(),
    groups = new Map();
  for (const plugin of list) {
    if (!groups.has(plugin.group)) groups.set(plugin.group, []);
    groups.get(plugin.group).push(plugin);
  }
  const container = root.querySelector("#plugin-groups");
  if (!list.length) {
    container.innerHTML = state.plugins.length
      ? '<div class="plugin-empty-search">没有匹配的插件，试试其他关键词。</div>'
      : empty(
          "还没有安装插件",
          "将插件放入 plugins/ 下的任意分组目录，再点击扫描目录。",
          "puzzle",
        );
    return;
  }
  container.innerHTML = [...groups]
    .map(
      ([group, plugins]) =>
        `<section class="group-section ${collapsed.has(group) ? "folder-collapsed" : ""}"><button class="group-heading" data-group="${esc(group)}" aria-expanded="${!collapsed.has(group)}">${icon("chevron-down")}${icon("folder")}<strong>${esc(group || "根目录")}</strong><span class="count">${plugins.length} 个插件</span><span class="group-line"></span></button><div class="card-grid">${plugins.map(card).join("")}</div></section>`,
    )
    .join("");
  container.querySelectorAll("[data-configure]").forEach((button) => {
    button.onclick = () =>
      openPluginPanel(
        state.plugins.find((plugin) => plugin.id === button.dataset.configure),
      );
  });
  container.querySelectorAll("[data-group]").forEach(
    (button) =>
      (button.onclick = () => {
        const id = button.dataset.group;
        collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id);
        renderGroups(root);
      }),
  );
  container.querySelectorAll("[data-select]").forEach(
    (input) =>
      (input.onchange = () => {
        input.checked
          ? selected.add(input.dataset.select)
          : selected.delete(input.dataset.select);
        input.closest(".card").classList.toggle("selected", input.checked);
        renderFooter(root);
        checkDependencies(root);
      }),
  );
  container.querySelectorAll("[data-reload]").forEach(
    (button) =>
      (button.onclick = () =>
        action(button, async () => {
          const result = await api(
            `plugins/${encodeURIComponent(button.dataset.reload)}/reload`,
            { method: "POST" },
          );
          await refresh();
          if (Object.keys(result.failed).length)
            throw Error(
              "部分插件恢复失败：" + Object.values(result.failed).join("；"),
            );
          toast("插件已重载，依赖者已按顺序恢复");
        })),
  );
  container.querySelectorAll("[data-unload]").forEach(
    (button) =>
      (button.onclick = () => {
        const id = button.dataset.unload,
          affected = new Set([id]);
        let change = true;
        while (change) {
          change = false;
          for (const p of state.plugins.filter((p) => p.active))
            if (
              !affected.has(p.id) &&
              Object.keys(p.requires).some((dep) => affected.has(dep))
            ) {
              affected.add(p.id);
              change = true;
            }
        }
        const work = async () => {
          await api(`plugins/${encodeURIComponent(id)}/unload`, {
            method: "POST",
          });
          await refresh();
          toast("插件已卸载");
        };
        if (affected.size > 1)
          confirm({
            title: "卸载插件及其依赖者",
            message: "以下活跃插件会一并卸载。已经开始的后台任务将自然完成。",
            items: [...affected].map(nameOf),
            label: "卸载这些插件",
            onConfirm: work,
          });
        else action(button, work);
      }),
  );
}
function renderFooter(root) {
  const active = new Set(
      state.plugins.filter((p) => p.active).map((p) => p.id),
    ),
    added = [...selected].filter((id) => !active.has(id)).length,
    removed = [...active].filter((id) => !selected.has(id)).length;
  const footer = root.querySelector("#selection-footer");
  if (!added && !removed) {
    footer.innerHTML = "";
    return;
  }
  footer.innerHTML = `<div class="selection-bar"><div class="selection-summary">已选择 <strong>${selected.size}</strong> 个插件<small>新增激活 ${added} 个 · 停用 ${removed} 个</small></div><div class="selection-actions"><button class="button ghost" id="reset-selection">还原选择</button><button class="button primary" id="apply-selection" ${!preview?.valid ? "disabled" : ""}>${icon("check")}应用变更</button></div></div>`;
  footer.querySelector("#reset-selection").onclick = () => {
    selected = active;
    renderPlugins(root);
  };
  footer.querySelector("#apply-selection").onclick = (event) =>
    action(event.currentTarget, async () => {
      await api("plugins/active", {
        method: "PUT",
        body: { selected: [...selected] },
      });
      await refresh();
      toast("插件激活集合已更新");
    });
}
async function checkDependencies(root) {
  preview = null;
  renderFooter(root);
  const request = ++previewSequence;
  try {
    const result = await api("plugins/preview", {
      method: "POST",
      body: { selected: [...selected] },
    });
    if (
      request !== previewSequence ||
      !root.querySelector("#dependency-preview")
    )
      return;
    preview = result;
    const panel = root.querySelector("#dependency-preview");
    panel.innerHTML = result.valid
      ? ""
      : `<div class="notice ${result.issues.some((i) => i.kind !== "unselected") ? "error" : "warn"} dependency-panel">${icon("alert")}<div class="notice-body"><strong>还需要处理 ${result.issues.length} 项依赖</strong><ul class="notice-list">${result.issues.map((issue) => `<li>${esc(issue.kind === "unselected" ? `${nameOf(issue.plugin)} 需要同时勾选 ${nameOf(issue.dependency)}` : issue.message)}</li>`).join("")}</ul></div>${result.recommended.length ? '<button class="button small" id="select-dependencies">勾选所需依赖</button>' : ""}</div>`;
    panel
      .querySelector("#select-dependencies")
      ?.addEventListener("click", () => {
        result.recommended.forEach((id) => selected.add(id));
        renderPlugins(root);
      });
    renderFooter(root);
  } catch (error) {
    toast(error.message, true);
  }
}
