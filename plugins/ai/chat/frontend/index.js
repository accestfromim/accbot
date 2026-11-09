import { connectionHints } from "./connections.js";

const numeric = [
  ["initial_probability", "首次唤醒概率", 0, 1, 0.01],
  ["busy_probability", "高频首次唤醒概率", 0, 1, 0.01],
  ["active_probability", "会话内唤醒概率", 0, 1, 0.01],
  ["active_busy_probability", "高频会话内唤醒概率", 0, 1, 0.01],
  ["busy_seconds", "高频时间窗（秒）", 1, 3600, 1],
  ["cooldown_seconds", "普通唤醒冷却（秒）", 0, 3600, 1],
  ["idle_seconds", "会话空闲超时（秒）", 1, 86400, 1],
  ["context_budget", "上下文预算", 4096, 2000000, 1],
  ["output_reserve", "输出预留", 256, 200000, 1],
  ["max_tool_rounds", "聊天工具循环上限（不限制记忆整理）", 1, 256, 1],
];

function field(label, name, value = "") {
  const wrapper = document.createElement("label");
  wrapper.className = "field";
  const title = document.createElement("span");
  title.textContent = label;
  const input = document.createElement("input");
  input.className = "input";
  input.name = name;
  input.value = value;
  wrapper.append(title, input);
  return wrapper;
}

export async function mount(root, api) {
  const form = root.querySelector("form");
  const errorBox = root.querySelector(".form-error");
  const submit = root.querySelector('[type="submit"]');
  const hint = root.querySelector("[data-save-hint]");
  const [models, personas, prompts, values] = await Promise.all([
    api.listModels(),
    api.listPersonas(),
    api.listPrompts(),
    api.getSettings(),
  ]);
  if (api.signal.aborted) return;
  const connectField = connectionHints(root, api);
  const select = form.elements.default_model;
  for (const model of models)
    select.add(
      new Option(`${model.name || model.id} · ${model.model}`, model.id),
    );
  if (
    values.default_model &&
    !models.some((m) => m.id === values.default_model)
  ) {
    select.add(
      new Option(`模型已删除 · ${values.default_model}`, values.default_model),
    );
  }
  select.value = values.default_model || "";
  const personaSelect = form.elements.persona_id;
  for (const persona of personas)
    personaSelect.add(
      new Option(`${persona.name} · ${persona.id}`, persona.id),
    );
  if (values.persona_id && !personas.some((p) => p.id === values.persona_id))
    personaSelect.add(
      new Option(`人格已删除 · ${values.persona_id}`, values.persona_id),
    );
  personaSelect.value = values.persona_id || "";
  const promptFields = [
    "chat_prompt_id",
    "owner_prompt_id",
    "memory_prompt_id",
  ];
  for (const name of promptFields) {
    const select = form.elements[name];
    select.add(new Option("请选择系统提示词卡", ""));
    for (const card of prompts)
      select.add(new Option(`${card.name} · ${card.id}`, card.id));
    if (values[name] && !prompts.some((card) => card.id === values[name]))
      select.add(new Option(`提示词已删除 · ${values[name]}`, values[name]));
    select.value = values[name] || "";
  }
  function modelHint() {
    const model = models.find((m) => m.id === select.value);
    root.querySelector("[data-model-info]").textContent = model
      ? `${model.protocol} · ${model.endpoint}`
      : "请先在模型卡页面添加模型。";
  }
  modelHint();
  select.addEventListener("change", modelHint, { signal: api.signal });
  for (const [name, label, min, max, step] of numeric) {
    const item = field(label, name, values[name]);
    Object.assign(item.querySelector("input"), {
      type: "number",
      min,
      max,
      step,
      required: true,
    });
    root.querySelector("[data-numbers]").append(item);
  }
  function add(kind, value = {}) {
    const list = root.querySelector(`[data-list="${kind}"]`);
    const group = kind === "groups";
    const row = document.createElement("div");
    row.className = "entry";
    const head = document.createElement("div");
    head.className = "entry-head";
    const title = document.createElement("span");
    title.textContent = group ? "群聊" : "私聊用户";
    const remove = document.createElement("button");
    Object.assign(remove, {
      type: "button",
      className: "button",
      textContent: "移除",
    });
    remove.addEventListener(
      "click",
      () => {
        row.remove();
        root.querySelector(`[data-empty="${kind}"]`).hidden =
          list.children.length > 0;
      },
      { signal: api.signal },
    );
    head.append(title, remove);
    const grid = document.createElement("div");
    grid.className = "grid";
    grid.append(
      field("接入配置 ID", "connection_id", value.connection_id),
      field(
        group ? "群 ID" : "用户 ID",
        group ? "group_id" : "user_id",
        value.group_id || value.user_id,
      ),
    );
    for (const input of grid.querySelectorAll("input")) input.required = true;
    connectField(grid.querySelector('[name="connection_id"]'));
    row.append(head, grid);
    if (group) {
      const mentions = field(
        "可 @ 唤醒的用户 ID（逗号分隔，留空允许所有成员）",
        "mention_users",
        (value.mention_users || []).join(", "),
      );
      const check = document.createElement("label");
      check.className = "check";
      const input = document.createElement("input");
      Object.assign(input, {
        type: "checkbox",
        name: "enabled",
        checked: value.enabled !== false,
      });
      check.append(input, document.createTextNode("允许在这个群参与聊天"));
      row.append(mentions, check);
    }
    list.append(row);
    root.querySelector(`[data-empty="${kind}"]`).hidden = true;
  }
  for (const kind of ["groups", "private_users"]) {
    for (const value of values[kind]) add(kind, value);
    root
      .querySelector(`[data-add="${kind}"]`)
      .addEventListener("click", () => add(kind), { signal: api.signal });
  }
  form.elements.owner_connection.value = values.owner?.connection_id || "";
  connectField(form.elements.owner_connection);
  form.elements.owner_user.value = values.owner?.user_id || "";
  form.elements.owner_controls.checked = values.owner_controls;
  submit.disabled = false;
  hint.textContent = "修改后点击保存";
  form.addEventListener(
    "submit",
    async (event) => {
      event.preventDefault();
      submit.disabled = true;
      errorBox.textContent = "";
      try {
        const settings = {
          default_model: select.value || null,
          persona_id: personaSelect.value || null,
          ...Object.fromEntries(
            promptFields.map((name) => [name, form.elements[name].value]),
          ),
          owner_controls: form.elements.owner_controls.checked,
        };
        for (const [name] of numeric)
          settings[name] = Number(form.elements[name].value);
        for (const kind of ["groups", "private_users"]) {
          settings[kind] = [
            ...root.querySelectorAll(`[data-list="${kind}"] .entry`),
          ].map((row) => {
            const read = (name) =>
              row.querySelector(`[name="${name}"]`).value.trim();
            return kind === "groups"
              ? {
                  connection_id: read("connection_id"),
                  group_id: read("group_id"),
                  enabled: row.querySelector('[name="enabled"]').checked,
                  mention_users: read("mention_users")
                    .split(/[,，\s]+/)
                    .filter(Boolean),
                }
              : {
                  connection_id: read("connection_id"),
                  user_id: read("user_id"),
                };
          });
        }
        const connection = form.elements.owner_connection.value.trim();
        const user = form.elements.owner_user.value.trim();
        if (!!connection !== !!user)
          throw new Error("主人接入配置 ID 和用户 ID 需要一起填写");
        settings.owner =
          connection && user
            ? { connection_id: connection, user_id: user }
            : null;
        await api.saveSettings(settings);
        if (!api.signal.aborted) hint.textContent = "已保存，新会话使用此配置";
      } catch (error) {
        if (!api.signal.aborted) errorBox.textContent = error.message;
      } finally {
        if (!api.signal.aborted) submit.disabled = false;
      }
    },
    { signal: api.signal },
  );
}
