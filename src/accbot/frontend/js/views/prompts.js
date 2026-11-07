import { renderTextCards } from "./text-cards.js";

export function renderPrompts(root) {
  renderTextCards(root, {
    resource: "prompts",
    label: "系统提示词卡",
    contentField: "content",
    glyph: "file",
    extension: "md",
    introduction:
      "每张卡就是一份完整模板，正文和顺序由你决定。由调用插件选择使用哪张卡。",
    eyebrow: "SYSTEM PROMPTS",
    description:
      "自由编辑完整的 Markdown 模板。三个占位符均可删除、重复或移动位置。",
    contentLabel: "提示词正文 · Markdown",
    help: "占位符均为可选。人格取本次选择，插件补充取活跃插件，工具补充取本次工具。删除占位符就不注入，框架不会在模板外追加内容。",
    defaultContent:
      "<persona>\n\n<plugins_insert_prompt>\n\n<tools_insert_prompt>",
    slots: [
      ["<persona>", "人格"],
      ["<plugins_insert_prompt>", "插件补充"],
      ["<tools_insert_prompt>", "工具补充"],
    ],
  });
}
