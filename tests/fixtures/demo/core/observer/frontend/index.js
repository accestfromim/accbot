export async function mount(root, api) {
  const form = root.querySelector("form");
  const select = root.querySelector('[name="default_model"]');
  const counter = root.querySelector('[name="count_messages"]');
  const button = root.querySelector('[type="submit"]');
  const errorBox = root.querySelector(".form-error");
  const hint = root.querySelector("[data-save-hint]");
  const [models, settings] = await Promise.all([
    api.listModels(),
    api.getSettings(),
  ]);
  if (api.signal.aborted) return;
  select.replaceChildren(new Option("暂不设置默认模型", ""));
  for (const card of models)
    select.add(new Option(`${card.name || card.id} · ${card.model}`, card.id));
  if (
    settings.default_model &&
    !models.some((card) => card.id === settings.default_model)
  ) {
    select.add(
      new Option(
        `模型卡已删除 · ${settings.default_model}`,
        settings.default_model,
      ),
    );
  }
  select.value = settings.default_model || "";
  counter.checked = settings.count_messages;
  select.disabled = false;
  button.disabled = false;
  hint.textContent = "修改后点击保存";
  if (!models.length)
    root.querySelector("[data-model-help]").textContent =
      "还没有模型卡，请先到侧栏的「模型卡」页面添加。";
  function preview() {
    const card = models.find((item) => item.id === select.value);
    root.querySelector(".model-preview").hidden = !card;
    if (card) {
      root.querySelector("[data-model-name]").textContent =
        card.name || card.id;
      root.querySelector("[data-model-id]").textContent =
        `${card.id} / ${card.model}`;
    }
  }
  preview();
  select.addEventListener("change", preview, { signal: api.signal });
  form.addEventListener(
    "submit",
    async (event) => {
      event.preventDefault();
      button.disabled = true;
      errorBox.textContent = "";
      try {
        await api.saveSettings({
          default_model: select.value || null,
          count_messages: counter.checked,
        });
        hint.textContent = "已保存";
      } catch (error) {
        if (!api.signal.aborted) errorBox.textContent = error.message;
      } finally {
        if (!api.signal.aborted) button.disabled = false;
      }
    },
    { signal: api.signal },
  );
}
