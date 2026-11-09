import re

from ..errors import ErrorCode, FrameworkError

_SLOTS = re.compile(r"<persona>|<plugins_insert_prompt>|<tools_insert_prompt>")


class PromptRegistry:
    def __init__(self, cards, personas, tools, *, loader=None):
        self.personas, self.tools = personas, tools
        self._loader = loader
        self._plugins = {}
        self.replace(cards)

    def replace(self, cards):
        cards = tuple(cards)
        if len({c.id for c in cards}) != len(cards):
            raise FrameworkError(ErrorCode.CONFLICT, "系统提示词卡 ID 重复")
        self._cards = {c.id: c for c in cards}

    def list(self):
        if self._loader is not None:
            try:
                self.replace(self._loader())
            except (OSError, ValueError) as exc:
                raise FrameworkError(ErrorCode.CONFIGURATION, "系统提示词卡文件无效，请检查配置目录") from exc
        return tuple(self._cards.values())

    def get(self, card_id):
        self.list()
        try:
            return self._cards[card_id]
        except KeyError:
            raise FrameworkError(ErrorCode.NOT_FOUND, f"系统提示词卡不存在：{card_id}") from None

    def register(self, owner, content):
        if owner.retired:
            raise FrameworkError(ErrorCode.INACTIVE, "cannot register prompts for a retired plugin")
        if owner.plugin_id in self._plugins:
            raise FrameworkError(ErrorCode.CONFLICT, "plugin prompt already registered")
        self._plugins[owner.plugin_id] = (owner, content)
        owner.own(lambda: self._plugins.pop(owner.plugin_id, None))

    def render(self, card_id, *, persona_id=None, tools=()):
        card = self.get(card_id)
        persona = self.personas.get(persona_id).system_prompt if persona_id is not None else ""
        if len({t.id for t in tools}) != len(tools):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "duplicate tools in model request")
        tool_text = []
        for tool in tools:
            if content := self.tools.prompt_for(tool):
                tool_text.append(f"### {tool.wire_name}\n{content}")
        replacements = {
            "<persona>": persona,
            "<plugins_insert_prompt>": "\n\n".join(
                f"### 插件：{key}\n{content}"
                for key, (owner, content) in sorted(self._plugins.items())
                if owner.active and content.strip()
            ),
            "<tools_insert_prompt>": "\n\n".join(tool_text),
        }
        # A single pass over the template: inserted text is never interpreted as another slot.
        return _SLOTS.sub(lambda match: replacements[match.group()], card.content)
