import asyncio
import json

import pytest
from test_ai_budget import module
from test_ai_plugins import event
from test_ai_plugins import setup_ai as setup_ai


def test_legacy_memory_reads_as_markdown_and_rewrite_replaces_body_atomically(setup_ai):
    async def run():
        runtime, plugin, _ = await setup_ai()
        try:
            store = module(plugin, "memory").Store(plugin.ctx.data_dir, event().message.conversation)
            state = {
                **store.identity(),
                "last_record": "old",
                "memory": {
                    "version": 1,
                    "summary": "本群讨论读书",
                    "members": [{"qq": "200", "nickname": "群友", "notes": ["组织者"]}],
                    "facts": ["活动已经结束"],
                    "open_tasks": ["过期的安排"],
                },
            }
            store.directory.mkdir(parents=True)
            old = json.dumps(state, ensure_ascii=False)
            store.memory_path.write_text(old, encoding="utf-8")
            memory = store.load()
            assert all(
                text in memory
                for text in ("本群讨论读书", "200", "群友", "组织者", "活动已经结束", "过期的安排")
            )
            assert store.committed("old") and store.memory_path.read_text("utf-8") == old
            assert plugin.agent.prefix(memory, False)[0].content[0].text.endswith(memory)
            revised = "## 长期共识与背景\n- 群友（200）组织读书会。"
            store.save(revised, "new")
            assert store.load() == revised and store.committed("new") and not store.committed("old")
            assert "过期的安排" not in store.memory_path.read_text("utf-8")
            for invalid in (" \n", " x" * 40000):
                with pytest.raises(ValueError):
                    store.save(invalid, "invalid")
                assert store.load() == revised and store.committed("new")
        finally:
            await runtime.close()

    asyncio.run(run())
