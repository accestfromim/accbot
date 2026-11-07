import { renderTextCards } from "./text-cards.js";

export function renderPersonas(root) {
  renderTextCards(root, {
    resource: "personas",
    label: "人格卡",
    contentField: "system_prompt",
    glyph: "user",
    extension: "json",
    introduction: "为群聊赋予性格。统一保存，供所有插件按 ID 选用。",
    eyebrow: "PERSONALITY",
    description: "描述它是谁、如何说话，以及你希望保持的性格。",
    contentLabel: "人格正文",
    help: "在系统提示词卡中使用 <persona> 引用。保存后，下一次模型调用使用新内容。",
  });
}
