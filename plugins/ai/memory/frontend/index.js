import { createEditor } from "./editor.js";

const pageSize = 12;

function identity(entry) {
  const label = entry.platform === "qq"
    ? (entry.kind === "group" ? "QQ群" : "QQ号")
    : [entry.platform, entry.scope].filter(Boolean).join(" / ");
  return `${label} · ${entry.id}`;
}

export async function mount(root, api) {
  const $ = (s) => root.querySelector(s);
  if (!api.plugin.active) {
    $("[data-inactive]").hidden = false;
    return;
  }
  const dialog = $("dialog"), search = $("[data-search]"), refresh = $("[data-refresh]");
  const editor = createEditor(dialog, api, (token) => {
    render();
    ($(`.entry[data-token="${token}"]`) || search).focus();
  });
  const tabs = [...root.querySelectorAll("[data-kind]")];
  const tried = new Set();
  let entries = [], kind = "group", page = 0, naming = false;
  $("[data-browser]").hidden = false;

  async function resolveNames(candidates) {
    if (naming || !candidates.length) return;
    naming = true;
    const tokens = candidates.slice(0, pageSize).map(e => e.token);
    tokens.forEach(token => tried.add(token));
    try {
      const { names } = await api.request("names", { tokens });
      for (const entry of entries) if (names[entry.token]) entry.name = names[entry.token];
      editor.updateName();
    } catch (error) {
      if (!api.signal.aborted) api.notify(error.message, "warn");
    } finally {
      naming = false;
      if (!api.signal.aborted) render();
    }
  }
  function render() {
    for (const tab of tabs) {
      tab.setAttribute("aria-pressed", String(tab.dataset.kind === kind));
      tab.querySelector("span").textContent = entries.filter(e => e.kind === tab.dataset.kind).length;
    }
    const query = search.value.trim().toLocaleLowerCase();
    const category = entries.filter(e => e.kind === kind);
    const hits = category.filter(e => (e.name + " " + identity(e)).toLocaleLowerCase().includes(query));
    const pages = Math.ceil(hits.length / pageSize);
    page = Math.max(0, Math.min(page, pages - 1));
    const visible = hits.slice(page * pageSize, (page + 1) * pageSize);
    const list = $("[data-entries]");
    list.replaceChildren();
    for (const entry of visible) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "entry";
      row.dataset.token = entry.token;
      row.innerHTML = "<span><strong></strong><small></small></span>";
      row.querySelector("strong").textContent = entry.name || entry.id;
      row.querySelector("small").textContent = identity(entry);
      row.onclick = () => editor.open(entry, identity(entry));
      list.append(row);
    }
    const empty = $("[data-empty]");
    empty.hidden = !!hits.length;
    empty.textContent = query ? "没有匹配的记录" : (kind === "group" ? "还没有已整理的群记忆" : "还没有已建立的人物画像");
    $(".pager").hidden = pages <= 1;
    $("[data-page]").textContent = `${page + 1} / ${pages}`;
    $("[data-prev]").disabled = page === 0;
    $("[data-next]").disabled = page + 1 >= pages;
    if (!dialog.open) resolveNames((query ? category : visible).filter(e => !tried.has(e.token)));
  }
  async function list(refreshNames = false) {
    refresh.disabled = true;
    try {
      ({ entries } = await api.request("list"));
      entries.sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
      tried.clear();
      if (!refreshNames) for (const e of entries) if (e.name) tried.add(e.token);
      render();
    } catch (error) {
      if (!api.signal.aborted) {
        $("[data-empty]").textContent = "读取失败，请刷新重试";
        api.notify(error.message, "error");
      }
    } finally { refresh.disabled = false; }
  }
  for (const tab of tabs) tab.onclick = () => {
    kind = tab.dataset.kind;
    page = 0;
    search.value = "";
    search.placeholder = kind === "group" ? "搜索群名或群号" : "搜索昵称或用户 ID";
    render();
  };
  search.oninput = () => { page = 0; render(); };
  $("[data-prev]").onclick = () => { page--; render(); };
  $("[data-next]").onclick = () => { page++; render(); };
  refresh.onclick = () => list(true);
  await list();
}
