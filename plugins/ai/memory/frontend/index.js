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
  const browser = $("[data-browser]"), editor = $("[data-editor]");
  const search = $("[data-search]"), text = $("textarea"), save = $("[type=submit]");
  const status = $("[data-status]"), refresh = $("[data-refresh]");
  const tabs = [...root.querySelectorAll("[data-kind]")];
  const tried = new Set();
  let entries = [], kind = "group", page = 0, naming = false;
  let selected = null, savedText = "", saving = false;
  browser.hidden = false;

  function setTitle() {
    $("[data-title]").textContent = selected.name || selected.id;
    $("[data-identity]").textContent = identity(selected);
  }
  async function resolveNames(candidates) {
    if (naming || !candidates.length) return;
    naming = true;
    const tokens = candidates.slice(0, pageSize).map(e => e.token);
    tokens.forEach(token => tried.add(token));
    try {
      const { names } = await api.request("names", { tokens });
      for (const entry of entries) if (names[entry.token]) entry.name = names[entry.token];
      if (selected) setTitle();
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
      row.innerHTML = "<span><strong></strong><small></small></span>";
      row.querySelector("strong").textContent = entry.name || entry.id;
      row.querySelector("small").textContent = identity(entry);
      row.onclick = () => open(entry);
      list.append(row);
    }
    const empty = $("[data-empty]");
    empty.hidden = !!hits.length;
    empty.textContent = query ? "没有匹配的记录" : (kind === "group" ? "还没有已整理的群记忆" : "还没有已建立的人物画像");
    $(".pager").hidden = pages <= 1;
    $("[data-page]").textContent = `${page + 1} / ${pages}`;
    $("[data-prev]").disabled = page === 0;
    $("[data-next]").disabled = page + 1 >= pages;
    if (!selected) resolveNames((query ? category : visible).filter(e => !tried.has(e.token)));
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
  function dirty() { return text.value !== savedText; }
  function canDiscard() { return !dirty() || window.confirm("放弃当前未保存的修改？"); }
  function setStatus(message, warning = false) {
    status.textContent = message;
    status.classList.toggle("warning", warning);
  }
  async function open(entry) {
    selected = entry;
    browser.hidden = true;
    editor.hidden = false;
    setTitle();
    $("[data-label]").textContent = (entry.kind === "group" ? "群记忆" : "人物画像") + " · Markdown";
    text.disabled = save.disabled = true;
    setStatus("正在读取…");
    try {
      const latest = await api.request("read", { token: entry.token });
      if (selected !== entry || api.signal.aborted) return;
      text.value = savedText = latest.content;
      text.disabled = false;
      setStatus("");
      text.focus();
    } catch (error) {
      if (selected === entry && !api.signal.aborted) {
        setStatus(error.message, true);
      }
    }
  }
  $("form").onsubmit = async (event) => {
    event.preventDefault();
    if (!dirty() || saving) return;
    saving = true;
    text.disabled = save.disabled = $("[data-back]").disabled = true;
    try {
      const result = await api.request("save", {
        token: selected.token, content: text.value,
      });
      text.value = savedText = result.content;
      setStatus("已保存");
    } catch (error) {
      if (!api.signal.aborted) setStatus(error.message, true);
    } finally {
      saving = false;
      text.disabled = $("[data-back]").disabled = false;
      save.disabled = !dirty();
    }
  };
  text.oninput = () => {
    save.disabled = !dirty() || !text.value.trim();
    setStatus(dirty() ? "有未保存的修改" : "");
  };
  $("[data-back]").onclick = () => {
    if (!canDiscard()) return;
    selected = null;
    text.value = savedText = "";
    editor.hidden = true;
    browser.hidden = false;
    render();
    search.focus();
  };
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
