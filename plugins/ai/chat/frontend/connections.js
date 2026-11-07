// Connection existence is advisory: users may configure chat before adapters.
export function connectionHints(root, api) {
  const selector = 'input[name="connection_id"], input[name="owner_connection"]';
  const options = document.createElement("datalist");
  options.id = "chat-connection-options";
  root.append(options);
  let connections;
  let refreshing = false;
  let sequence = 0;

  function update(input) {
    const hint = input.parentElement.querySelector(".connection-hint");
    const id = input.value.trim();
    hint.textContent =
      !id || connections === undefined
        ? ""
        : connections === null
          ? "暂时无法读取适配器配置，可继续保存，稍后重新打开页面核对。"
          : connections.some((item) => item.id === id)
            ? ""
            : `指定的适配器 ID「${id}」不存在。仍可保存，之后请在「适配器 → OneBot」中添加同 ID 的配置。`;
    hint.hidden = !hint.textContent;
  }

  async function refresh() {
    if (refreshing || api.signal.aborted) return;
    refreshing = true;
    try {
      connections = await api.listConnections();
    } catch {
      connections = null;
    } finally {
      refreshing = false;
    }
    if (api.signal.aborted) return;
    options.replaceChildren(
      ...(connections || []).map((item) =>
        new Option(item.name || item.id, item.id),
      ),
    );
    root.querySelectorAll(selector).forEach(update);
  }

  root.addEventListener(
    "input",
    (event) => {
      if (event.target.matches(selector)) update(event.target);
    },
    { signal: api.signal },
  );
  // Returning from another tab may reveal a newly created adapter configuration.
  window.addEventListener("focus", refresh, { signal: api.signal });
  refresh();

  return (input) => {
    input.setAttribute("list", options.id);
    input.placeholder = "选择或填写接入配置 ID";
    const hint = document.createElement("span");
    hint.className = "connection-hint";
    hint.id = `chat-connection-hint-${++sequence}`;
    hint.setAttribute("role", "status");
    input.setAttribute("aria-describedby", hint.id);
    input.after(hint);
    update(input);
  };
}
