import { api } from "./api.js";
import { state, subscribe } from "./state.js";
import { dialog, esc } from "./components.js";
import { icon } from "./icons.js";

let current = null;

export function openPluginPanel(plugin) {
  current?.close();
  const controller = new AbortController();
  const cleanups = [];
  let descriptor = null,
    closed = false,
    stale = false;
  const view = dialog({
    title: plugin.name || plugin.id,
    description: `${plugin.id} · v${plugin.version}`,
    className: "plugin-drawer",
    body: '<div class="plugin-panel-notice" role="status" hidden></div><div class="plugin-panel-host"><div class="loading-state"><span class="spinner"></span><p>正在打开配置…</p></div></div>',
    footer: `<span class="plugin-panel-state">${plugin.active ? "已激活" : "未激活 · 可以提前配置"}</span><button class="button" data-close>关闭</button>`,
    onClose: dispose,
  });
  const host = view.body.querySelector(".plugin-panel-host");
  const notice = view.body.querySelector(".plugin-panel-notice");
  current = view;
  const onCleanup = (callback) => {
    if (typeof callback !== "function") return;
    if (controller.signal.aborted) runCleanup(callback);
    else cleanups.push(callback);
  };
  function runCleanup(callback) {
    try {
      Promise.resolve(callback()).catch((error) =>
        console.error("Plugin page cleanup failed", error),
      );
    } catch (error) {
      console.error("Plugin page cleanup failed", error);
    }
  }
  function dispose() {
    if (closed) return;
    closed = true;
    controller.abort();
    for (const callback of cleanups.reverse()) runCleanup(callback);
    host.shadowRoot?.replaceChildren();
    host.replaceChildren();
    if (current === view) current = null;
  }
  function notify(message, tone = "success") {
    if (closed) return;
    notice.hidden = false;
    notice.className = `plugin-panel-notice notice ${["error", "warn", "success"].includes(tone) ? tone : "info"}`;
    notice.innerHTML = `${icon(tone === "error" || tone === "warn" ? "alert" : "circlecheck")}<span class="notice-body">${esc(message)}</span>`;
  }
  function invalidate() {
    if (closed || stale) return;
    stale = true;
    controller.abort();
    host.inert = true;
    notify("插件状态已改变，请重新打开配置以继续。", "warn");
    const button = document.createElement("button");
    button.className = "button small";
    button.textContent = "重新打开";
    button.onclick = () =>
      openPluginPanel(
        state.plugins.find((item) => item.id === plugin.id) || plugin,
      );
    notice.append(button);
    for (const callback of cleanups.splice(0).reverse()) runCleanup(callback);
  }
  const unsubscribe = subscribe((kind) => {
    if (!descriptor || kind === "offline") return;
    const activation = state.status.plugin_activations?.[plugin.id] ?? null;
    if (activation !== descriptor.activation_id) invalidate();
  });
  // Subscription must remain until close, including when an old generation is invalidated.
  view.element.addEventListener("close", unsubscribe, { once: true });
  const onNavigate = () => view.close();
  window.addEventListener("hashchange", onNavigate);
  view.element.addEventListener(
    "close",
    () => window.removeEventListener("hashchange", onNavigate),
    { once: true },
  );

  async function request(path, options) {
    if (closed || stale)
      throw new DOMException("页面已关闭或失效", "AbortError");
    try {
      return await api(path, { ...options, signal: controller.signal });
    } catch (error) {
      if (error.status === 409) invalidate();
      throw error;
    }
  }
  async function load() {
    try {
      const prefix = `plugins/${encodeURIComponent(plugin.id)}`;
      descriptor = await request(`${prefix}/ui`);
      if (closed) return;
      view.footer.querySelector(".plugin-panel-state").textContent =
        descriptor.activation_id ? "已激活" : "未激活 · 可以提前配置";
      const base = new URL(
        `/api/${prefix}/ui/assets/${descriptor.revision}/`,
        location.origin,
      );
      const assetUrl = (path) => {
        const url = new URL(path, base);
        if (
          url.origin !== base.origin ||
          !url.pathname.startsWith(base.pathname)
        )
          throw Error("资源路径必须位于插件 frontend 目录内");
        return url.href;
      };
      const template = document.createElement("template");
      template.innerHTML = descriptor.html;
      const roots = [...template.content.childNodes].filter(
        (node) =>
          node.nodeType === Node.ELEMENT_NODE ||
          (node.nodeType === Node.TEXT_NODE && node.textContent.trim()),
      );
      if (roots.length !== 1 || roots[0].tagName !== "DIV")
        throw Error("插件页面需要一个根 div");
      host.replaceChildren();
      const shadow = host.attachShadow({ mode: "open" });
      for (const href of [
        "/assets/styles/components.css",
        "/assets/styles/plugin-component.css",
        ...(descriptor.has_style ? [assetUrl("style.css")] : []),
      ]) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = href;
        shadow.append(link);
      }
      const component = roots[0];
      for (const element of [
        component,
        ...component.querySelectorAll("[src],[href]"),
      ]) {
        for (const attr of ["src", "href"]) {
          const value = element.getAttribute(attr);
          if (value && !/^(?:[a-z][a-z0-9+.-]*:|\/|#)/i.test(value))
            element.setAttribute(attr, assetUrl(value));
        }
      }
      shadow.append(component);
      const context = Object.freeze({
        plugin: Object.freeze({
          id: plugin.id,
          name: plugin.name,
          active: !!descriptor.activation_id,
        }),
        signal: controller.signal,
        onCleanup,
        assetUrl,
        notify,
        listModels: () => request("models"),
        listPersonas: () => request("personas"),
        listPrompts: () => request("prompts"),
        getSettings: async () =>
          (await request(`${prefix}/settings?revision=${descriptor.revision}`))
            .values,
        saveSettings: async (values) => {
          const result = await request(`${prefix}/settings`, {
            method: "PUT",
            body: { revision: descriptor.revision, values },
          });
          if (result.warning) notify(result.warning, "warn");
          else notify("配置已保存");
          return result.values;
        },
      });
      if (descriptor.has_script) {
        const module = await import(assetUrl("index.js"));
        if (closed || stale) return;
        if (typeof module.mount !== "function")
          throw Error("插件 index.js 需要导出 mount(root, api) 函数");
        onCleanup(await module.mount(component, context));
      }
    } catch (error) {
      if (closed || stale || error.name === "AbortError") return;
      controller.abort();
      for (const callback of cleanups.splice(0).reverse()) runCleanup(callback);
      host.inert = true;
      notify(error.message || "插件页面加载失败", "error");
      if (!host.shadowRoot) host.replaceChildren();
    }
  }
  load();
  return view;
}
