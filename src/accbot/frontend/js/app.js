import { api } from "./api.js";
import { state, refresh, poll, subscribe } from "./state.js";
import { icon, mountIcons } from "./icons.js";
import { dialog, field, bindPasswords, action, empty } from "./components.js";
import { renderPlugins } from "./views/plugins.js";
import { renderModels } from "./views/models.js";
import { renderPersonas } from "./views/personas.js";
import { renderPrompts } from "./views/prompts.js";
import {
  renderAdapters,
  renderOneBot,
  updateOneBotStatus,
} from "./views/adapters.js";
import { renderCredentials } from "./views/credentials.js";

const routes = {
  plugins: ["插件", renderPlugins],
  models: ["模型卡", renderModels],
  personas: ["人格卡", renderPersonas],
  prompts: ["系统提示词", renderPrompts],
  adapters: ["适配器", renderAdapters],
  onebot: ["OneBot", renderOneBot],
  credentials: ["密钥管理", renderCredentials],
};
const root = document.getElementById("content");
let login = null;
function route() {
  return Object.hasOwn(routes, location.hash.slice(1))
    ? location.hash.slice(1)
    : "plugins";
}
function render() {
  if (!state.ready) return;
  const current = route();
  document.title = `${routes[current][0]} · accbot`;
  document.getElementById("breadcrumb-current").textContent =
    routes[current][0];
  document
    .querySelectorAll("[data-route]")
    .forEach((link) =>
      link.classList.toggle("active", link.dataset.route === current),
    );
  routes[current][1](root);
  document.getElementById("nav-plugin-count").textContent =
    state.plugins.length;
  document.getElementById("nav-model-count").textContent = state.models.length;
  document.getElementById("nav-persona-count").textContent =
    state.personas.length;
  document.getElementById("nav-prompt-count").textContent =
    state.prompts.length;
  document.getElementById("nav-onebot-count").textContent =
    state.connections.length;
  online(true);
}
function online(connected) {
  document.getElementById("instance-state").textContent = connected
    ? "实例运行中"
    : "连接已断开";
  document.getElementById("instance-dot").className =
    "status-dot " + (connected ? "good" : "warn");
  document.querySelector(".local-status").innerHTML =
    `<span class="status-dot ${connected ? "good" : "warn"}"></span>${connected ? "本地运行" : "连接中断"}`;
}
subscribe((kind) => {
  if (kind === "data") render();
  else if (kind === "status") {
    online(true);
    if (route() === "onebot") updateOneBotStatus(root);
  } else if (kind === "offline") online(false);
});
window.addEventListener("hashchange", () => {
  closeSidebar();
  render();
  window.scrollTo({ top: 0 });
});
function closeSidebar() {
  document.body.classList.remove("nav-open");
}
document.getElementById("mobile-menu").onclick = () =>
  document.body.classList.toggle("nav-open");
document.getElementById("sidebar-shade").onclick = closeSidebar;
const themeButton = document.getElementById("theme-toggle");
function updateThemeIcon() {
  themeButton.innerHTML = icon(
    document.documentElement.dataset.theme === "dark" ? "sun" : "moon",
  );
}
themeButton.onclick = () => {
  const theme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("accbot-theme", theme);
  } catch {}
  updateThemeIcon();
};
mountIcons(document);
updateThemeIcon();
function showLogin() {
  if (login) return;
  login = dialog({
    title: "管理访问",
    className: "login-dialog",
    body: `<div class="login-content"><div class="login-lock">${icon("lock")}</div><h2 style="text-align:center;font-size:20px">登录此实例</h2><p class="dialog-section-description" style="text-align:center;margin-top:10px">此实例启用了远程管理保护，请使用维护者配置的访问令牌。</p><form>${field("访问令牌", "token", "", { type: "password", required: true })}<div class="form-error" role="alert"></div></form></div>`,
    footer:
      '<button class="button primary" id="login-submit" style="width:100%">登录工作台</button>',
    onClose: () => {
      login = null;
    },
  });
  login.element.addEventListener("cancel", (event) => event.preventDefault());
  bindPasswords(login.body);
  const submit = () => {
    if (!login.body.querySelector("form").reportValidity()) return;
    action(login.footer.querySelector("#login-submit"), async () => {
      try {
        await api("session", {
          method: "POST",
          body: { token: login.body.querySelector("input").value },
        });
        login.close();
        await refresh();
      } catch (error) {
        if (login)
          login.body.querySelector(".form-error").textContent = error.message;
        throw error;
      }
    });
  };
  login.footer.querySelector("#login-submit").onclick = submit;
  login.body.querySelector("form").onsubmit = (event) => {
    event.preventDefault();
    submit();
  };
}
window.addEventListener("accbot-auth-required", showLogin);
async function bootstrap() {
  try {
    const session = await api("session");
    if (session.auth_required && !session.authenticated) {
      showLogin();
      return;
    }
    await refresh();
  } catch (error) {
    online(false);
    if (error.status !== 401) {
      root.innerHTML = empty(
        "无法连接工作台",
        error.message,
        "server",
        '<button class="button" id="retry">重新连接</button>',
      );
      root.querySelector("#retry").onclick = bootstrap;
    }
  }
}
await bootstrap();
setInterval(() => {
  if (state.ready && !login) poll();
}, 5000);
