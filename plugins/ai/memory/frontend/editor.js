import MarkdownIt from "./vendor/markdown-it.js";
import "./vendor/markdown-toolbar.js";

const markdown = new MarkdownIt({ html: false });

function scrollFraction(pane) {
  return pane.scrollTop / Math.max(1, pane.scrollHeight - pane.clientHeight);
}

function scrollToFraction(pane, fraction) {
  pane.scrollTop = fraction * (pane.scrollHeight - pane.clientHeight);
}

function renderDocument(article, source) {
  const template = document.createElement("template");
  template.innerHTML = markdown.render(source);
  for (const link of template.content.querySelectorAll("a")) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  // Memory can contain group-provided URLs. Fetch an image only when the reader opens it.
  for (const image of template.content.querySelectorAll("img")) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "inline-image";
    button.textContent = image.alt ? `查看图片 · ${image.alt}` : "查看图片";
    image.referrerPolicy = "no-referrer";
    button.onclick = (event) => { event.preventDefault(); button.replaceWith(image); };
    image.replaceWith(button);
  }
  article.replaceChildren(template.content);
}

export function createEditor(dialog, api, returnToList) {
  const $ = (selector) => dialog.querySelector(selector);
  const source = $("textarea"), article = $("article"), paper = $(".paper");
  const back = $("[data-back]"), save = $("[data-save]"), status = $("[data-status]");
  const read = $('[data-mode="read"]'), edit = $('[data-mode="edit"]');
  let current = null, saved = "", saving = false, frame = 0;

  function message(text, warning = false) {
    if (status.textContent !== text) status.textContent = text;
    status.classList.toggle("warning", warning);
  }
  function controls() {
    const dirty = source.value !== saved;
    const busy = source.disabled || saving;
    save.hidden = !dirty && dialog.dataset.mode !== "edit";
    save.disabled = busy || !dirty || !source.value.trim();
    edit.disabled = busy;
    back.disabled = saving;
    source.readOnly = saving;
    $("markdown-toolbar").inert = busy;
  }
  function mode(value) {
    dialog.dataset.mode = value;
    read.setAttribute("aria-pressed", String(value === "read"));
    edit.setAttribute("aria-pressed", String(value === "edit"));
    controls();
  }
  function switchView(value) {
    if (dialog.dataset.mode === value) return;
    const editing = value === "edit";
    const position = scrollFraction(editing ? paper : source);
    const target = editing ? source : paper;
    mode(value);
    target.focus({ preventScroll: true });
    scrollToFraction(target, position);
  }
  function updateName() {
    if (current) $("#document-title").textContent = current.entry.name || current.entry.id;
  }
  function preview() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => renderDocument(article, source.value));
  }
  function setContent(content) {
    if (source.value !== content) source.value = content;
    saved = source.value;
    preview();
  }
  async function open(entry, identity) {
    const opened = { entry };
    current = opened;
    updateName();
    $("[data-identity]").textContent = `${entry.kind === "group" ? "群记忆" : "人物画像"} · ${identity}`;
    source.disabled = true;
    mode("read");
    message("正在读取…");
    dialog.showModal();
    try {
      const { content } = await api.request("read", { token: entry.token });
      if (current !== opened || api.signal.aborted) return;
      setContent(content);
      source.disabled = false;
      controls();
      message("");
    } catch (error) {
      if (current === opened && !api.signal.aborted) message(error.message, true);
    }
  }
  async function saveChanges() {
    if (save.disabled) return;
    saving = true;
    controls();
    message("正在保存…");
    try {
      const { content } = await api.request("save", { token: current.entry.token, content: source.value });
      if (api.signal.aborted) return;
      setContent(content);
      message("已保存");
    } catch (error) {
      if (!api.signal.aborted) message(error.message, true);
    } finally {
      saving = false;
      controls();
    }
  }
  function close() {
    if (saving || (source.value !== saved && !window.confirm("放弃当前未保存的修改？"))) return;
    const token = current.entry.token;
    current = null;
    dialog.close();
    setContent("");
    paper.scrollTop = source.scrollTop = 0;
    returnToList(token);
  }
  read.onclick = () => switchView("read");
  edit.onclick = () => switchView("edit");
  source.onscroll = () => {
    if (source.clientHeight && paper.clientHeight) scrollToFraction(paper, scrollFraction(source));
  };
  source.oninput = () => {
    controls();
    message(source.value !== saved ? "有未保存的修改" : "");
    preview();
  };
  save.onclick = saveChanges;
  back.onclick = close;
  dialog.oncancel = (event) => { event.preventDefault(); close(); };
  dialog.onkeydown = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveChanges();
    }
  };
  api.onCleanup(() => {
    cancelAnimationFrame(frame);
    dialog.close();
  });
  return { open, updateName };
}
