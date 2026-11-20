import asyncio
import json

import httpx
from test_ai_budget import module
from test_ai_plugins import PLUGINS, deliver, event
from test_ai_plugins import setup_ai as setup_ai

from accbot import UserRef
from accbot.adapters.onebot import OneBotAdapter
from accbot.config import OneBotConfig
from accbot.web import create_app


def test_memory_page_edits_existing_documents_and_next_turn_reads_them(setup_ai):
    async def run():
        runtime, chat, model = await setup_ai()
        store_type = module(chat, "memory").Store
        group = store_type(chat.ctx.data_dir, event().message.conversation)
        group.save("群原文", "previous-summary")
        # Include a legacy directory and a private memory in a cold catalog.
        group.directory.rename(group.directory.with_name("old-group-location"))
        private = store_type(chat.ctx.data_dir, event(direct=True).message.conversation)
        private.save("主人私聊", "private-summary")
        user = event().message.sender.ref
        other = UserRef(platform="tenant.chat", scope="org/A", id=user.id)
        for ref in (user, other):
            chat.agent.profiles.update(ref, "画像原文", "")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://localhost"
        ) as web:
            base = "/api/plugins/ai.memory/ui"

            async def request(action, **values):
                return await web.post(
                    base + "/request", json={"revision": revision, "action": action, "values": values}
                )

            try:
                revision = (await web.get(base)).json()["revision"]
                assert (await request("list")).status_code == 409
                await runtime.set_active([*PLUGINS, "ai.memory"])
                revision = (await web.get(base)).json()["revision"]
                entries = (await request("list")).json()["entries"]
                assert len(entries) == 3 and all("content" not in e and "path" not in e for e in entries)
                ids = {(e["kind"], e["platform"]): e["token"] for e in entries}
                gid, uid = ids["group", "qq"], ids["user", "qq"]
                assert (await request("read", token=gid)).json()["content"] == "群原文"
                assert (await request("save", token=gid, content="网页写入")).is_success
                # A normal wake migrates the old directory and creates a pending journal.
                await deliver(runtime, event("first"))
                pending = group.pending_path.read_bytes()
                assert (await request("save", token=gid, content="  群记忆新版  ")).json() == {
                    "content": "群记忆新版"
                }
                assert group.committed("previous-summary") and group.pending_path.read_bytes() == pending
                assert (await request("save", token=uid, content="画像新版")).is_success
                assert chat.agent.profiles.read(other).content == "画像原文"
                assert (await request("save", token=uid, content=" ")).status_code == 400
                assert (await request("read", token="a" * 24)).status_code == 404
                assert (await request("read", token="../../private")).status_code == 400
                await deliver(runtime, event("next"))
                context = [i.content[0].text for i in model.requests[-1][0] if i.role == "system"]
                assert any("群记忆新版" in t for t in context) and any("画像新版" in t for t in context)
                await runtime.reload("ai.chat")
                assert (await request("save", token=uid, content="old page")).status_code == 409
            finally:
                await runtime.close()

    asyncio.run(run())


def test_known_names_use_onebot_and_survive_a_disconnected_restart(setup_ai):
    async def run():
        runtime, chat, _ = await setup_ai()
        store = module(chat, "memory").Store(chat.ctx.data_dir, event().message.conversation)
        store.save("群记忆", "summary")
        chat.agent.profiles.update(event().message.sender.ref, "画像", "")

        def bridge(request):
            params = json.loads(request.content)
            if request.url.path == "/get_group_info":
                assert params == {"group_id": 100}
                data = {**params, "group_name": "读书会"}
            else:
                assert request.url.path == "/get_stranger_info" and params == {"user_id": 200}
                data = {**params, "nickname": "书友"}
            return httpx.Response(200, json={"status": "ok", "data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(bridge)) as http:
            adapter = OneBotAdapter(
                OneBotConfig(id="lookup", self_id="999", http_url="http://bridge.test"),
                runtime.events,
                http,
                None,
            )
            runtime.messages.add_adapter(adapter)
            try:
                await runtime.set_active([*PLUGINS, "ai.memory"])

                async def page(action, **values):
                    plugin, _ = runtime.plugins.instances["ai.memory"]
                    return await plugin.on_page_request(action, values)

                entries = (await page("list"))["entries"]
                tokens = [e["token"] for e in entries]
                assert set((await page("names", tokens=tokens))["names"].values()) == {"读书会", "书友"}
                runtime.messages.adapters.pop("lookup")
                await runtime.reload("ai.memory")
                entries = (await page("list"))["entries"]
                assert {e["name"] for e in entries} == {"读书会", "书友"}
                assert await page("names", tokens=tokens) == {"names": {}}
            finally:
                await runtime.close()

    asyncio.run(run())
