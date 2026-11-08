import asyncio
import importlib
import json

import httpx
import pytest
from test_ai_plugins import MEMORY, Adapter, Model, deliver, event, summarize
from test_ai_plugins import setup_ai as setup_ai

from accbot import GenerationResult, ModelItem, ModelQuote, ToolCall, ToolContext, User, UserRef
from accbot.config import ModelCard
from accbot.models.client import ModelClient
from accbot.runtime import Runtime


def module(plugin, name):
    return importlib.import_module(plugin.__module__.rsplit(".", 1)[0] + "." + name)


def user(number, *, platform="qq", scope="scope"):
    return UserRef(platform=platform, scope=scope, id=str(number))


def update(number, content, revision="", *, call_id="profile"):
    return ToolCall(
        id=call_id,
        name="ai.chat.update_user_profile",
        arguments={"user_id": str(number), "content": content, "revision": revision},
    )


def calls(*values):
    return GenerationResult(items=(ModelItem(role="assistant", calls=values),))


def final_memory():
    return GenerationResult(items=(ModelItem.text("assistant", json.dumps(MEMORY)),))


def test_profiles_follow_users_across_groups_connections_reload_and_restart(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai(
            groups=[{"connection_id": "main", "group_id": group} for group in ("100", "101")]
        )
        restarted = None
        try:
            profiles = plugin.agent.profiles
            assert not profiles.directory.exists()
            await deliver(runtime, event())
            assert not profiles.directory.exists()  # Merely observing a user never creates a file.
            for number in (200, 201, 299):
                assert profiles.update(user(number), f"# 用户 {number}\n稳定偏好-{number}", "")["updated"]
            await deliver(runtime, event("next"))
            items = model.requests[-1][0]
            index = next(i for i, item in enumerate(items) if "本会话长期记忆" in item.content[0].text)
            assert "稳定偏好-200" in items[index + 1].content[0].text
            assert "稳定偏好-201" not in str(items) and "稳定偏好-299" not in str(items)
            await deliver(runtime, event("other-user", group="101", user="201"))
            assert "稳定偏好-201" in str(model.requests[-1][0])
            assert "稳定偏好-200" not in str(model.requests[-1][0])
            await deliver(runtime, event("same-user", group="101"))
            assert "稳定偏好-200" in str(model.requests[-1][0])
            assert "稳定偏好-201" in str(model.requests[-1][0])
            original = profiles.path(user(200))
            assert original == plugin.ctx.data_dir / "profiles/qq/user-200.md"
            assert profiles.path(user(200, scope="new-connection")) == original
            assert profiles.path(user(200, platform="feishu")) != original
            assert profiles.path(user(200, platform="feishu", scope="a")) != profiles.path(
                user(200, platform="feishu", scope="b")
            )
            assert not (await runtime.reload("ai.chat"))["failed"]
            restored = runtime.plugins.instances["ai.chat"][0]
            assert "稳定偏好-200" in restored.agent.profiles.read(user(200)).content
            await runtime.close()
            restarted = Runtime(runtime.store.directory)
            new_model = Model(prompts=restarted.prompts)
            restarted.models._clients["test"] = new_model
            restarted.messages.add_adapter(Adapter(event().connection))
            await restarted.start()
            await deliver(restarted, event("after-restart"))
            assert "稳定偏好-200" in str(new_model.requests[-1][0])
            assert len(list(profiles.directory.rglob("*.md"))) == 3
        finally:
            await (restarted or runtime).close()

    asyncio.run(run())


def test_profile_budget_counts_all_history_and_quotes_without_trimming(setup_ai):
    async def run():
        runtime, plugin, _ = await setup_ai()
        try:
            profiles = plugin.agent.profiles
            profiles.update(user(200), "OLD PROFILE " + "x" * 4000, "")
            profiles.update(user(201), "CURRENT PROFILE", "")
            profiles.update(user(202), "QUOTED PROFILE", "")
            memory = module(plugin, "memory").empty_memory()
            cost = module(plugin, "context").cost
            old = [ModelItem.text("user", "old", sender=User(ref=user(200), name="同名"))]
            current = [
                ModelItem.text(
                    "user",
                    "current",
                    sender=User(ref=user(201), name="同名"),
                    reply_to=ModelQuote(sender=User(ref=user(202)), message_id="quoted"),
                )
            ]
            baseline = plugin.agent.context(memory, False, None, [], current, 100000)
            budget = cost(baseline) + cost(old) + 10
            with pytest.raises(ValueError, match="预算"):
                plugin.agent.context(memory, False, None, [old], current, budget)
            selected = plugin.agent.context(memory, False, None, [old], current, 100000)
            assert all(name in str(selected) for name in ("CURRENT PROFILE", "QUOTED PROFILE", "OLD PROFILE"))
            assert "OLD PROFILE" not in str(baseline)
            with pytest.raises(ValueError, match="预算"):
                plugin.agent.context(memory, False, None, [], current, cost(current) + 10)
            # IDs are path components, never filesystem paths supplied by the model.
            unusual = user("../../outside")
            profiles.update(unusual, "safe location", "")
            assert profiles.path(unusual).is_relative_to(profiles.directory)
            assert profiles.path(unusual).parent.name == "qq"
            before = profiles.read(user(201))
            for content in (" ", "像" * 6000):
                with pytest.raises(ValueError):
                    profiles.update(user(201), content, before.revision)
            assert profiles.read(user(201)) == before
        finally:
            await runtime.close()

    asyncio.run(run())


def test_summary_loops_through_profile_tools_before_committing_group_memory(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        step = 0

        async def respond(items, tools, options):
            nonlocal step
            step += 1
            assert {t.id for t in tools} == {"ai.chat.update_user_profile"}
            assert "必要时主动调用工具" in items[0].content[0].text
            assert "conflict=true" in items[0].content[0].text
            assert not channel.store.memory_path.exists()
            if step == 1:
                return calls(
                    ToolCall(id="forbidden", name="ai.tools.send_text", arguments={"text": "do not send"}),
                    update(299, "不在上下文中", call_id="absent"),
                    update(200, "# 画像\n喜欢猫", call_id="create"),
                )
            outputs = {item.call_id: item.output for item in items if item.kind == "tool_result"}
            assert not outputs["forbidden"]["ok"] and not outputs["absent"]["ok"]
            assert outputs["create"]["value"]["updated"]
            if step == 2:
                return calls(update(200, "# 画像\n喜欢猫，也喜欢茶", outputs["create"]["value"]["revision"]))
            assert outputs["profile"]["value"]["updated"]
            assert "也喜欢茶" in plugin.agent.profiles.read(user(200)).content
            return final_memory()

        model.summary_responder = respond
        try:
            await deliver(runtime, event("observe", user="201", mention=False))
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            assert step == 3 and channel.store.load().summary == MEMORY["summary"]
            assert not channel.records and not channel.store.pending_path.exists()
            assert not plugin.agent.profiles.path(user(201)).exists()
            assert not plugin.agent.profiles.path(user(299)).exists()
            assert len(runtime.messages.adapters["main"].sent) == 1
            # A normal chat tool call cannot impersonate the summary phase.
            denied = await runtime.tools.execute(update(200, "must not replace"), ToolContext())
            assert not denied.ok and "喜欢猫" in plugin.agent.profiles.read(user(200)).content
        finally:
            await runtime.close()

    asyncio.run(run())


def test_concurrent_profile_updates_return_latest_content_instead_of_overwriting(setup_ai):
    async def run():
        runtime, plugin, _ = await setup_ai()
        try:
            context = module(plugin, "context").MemoryContext(profile_users=(user(200),))
            results = await asyncio.gather(
                runtime.tools.execute(update(200, "来自群 A"), context),
                runtime.tools.execute(update(200, "来自群 B"), context),
            )
            assert all(result.ok for result in results)
            success = next(r.value for r in results if r.value["updated"])
            conflict = next(r.value for r in results if not r.value["updated"])
            assert conflict["conflict"] and conflict["revision"] == success["revision"]
            assert conflict["content"] == plugin.agent.profiles.read(user(200)).content
            merged = await runtime.tools.execute(
                update(200, "来自群 A 与群 B", conflict["revision"]), context
            )
            assert merged.value["updated"]
            assert plugin.agent.profiles.read(user(200)).content == "来自群 A 与群 B"
        finally:
            await runtime.close()

    asyncio.run(run())


def test_summary_failure_keeps_profiles_and_retries_with_latest_snapshot(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        requests = 0

        async def respond(items, tools, options):
            nonlocal requests
            requests += 1
            if requests == 1:
                return calls(update(200, "# 长期偏好\n喜欢猫"))
            if requests == 2:
                return GenerationResult(items=(ModelItem.text("assistant", "invalid group memory"),))
            assert "喜欢猫" in str(items) and "revision：" in str(items)
            assert not any(i.calls for i in items if i.kind == "message" and i.role == "assistant")
            return final_memory()

        model.summary_responder = respond
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            profile = plugin.agent.profiles.read(user(200))
            assert profile.content and channel.records and channel.retry_ticket
            assert not channel.store.memory_path.exists()
            await summarize(runtime, plugin, reason="retry")
            await runtime.jobs.drain()
            assert requests == 3 and not channel.records
            assert plugin.agent.profiles.read(user(200)) == profile
            assert channel.store.load().summary == MEMORY["summary"]
        finally:
            await runtime.close()

    asyncio.run(run())


def test_unload_during_summary_does_not_write_late_profiles(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(items, tools, options):
            entered.set()
            await release.wait()
            return calls(update(200, "late update"))

        model.summary_responder = respond
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            plugin.sessions.seal(channel)
            await asyncio.wait_for(entered.wait(), 2)
            stopped = asyncio.create_task(runtime.unload("ai.chat"))
            await asyncio.sleep(0)
            assert not plugin.ctx.owner.active
            release.set()
            await asyncio.wait_for(stopped, 2)
            assert not plugin.agent.profiles.path(user(200)).exists()
            assert channel.store.pending_path.exists() and not channel.store.memory_path.exists()
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_summary_profile_tools_use_real_provider_codecs(setup_ai, protocol):
    async def run():
        runtime, plugin, _ = await setup_ai()
        requests = []
        tool = plugin.memory_tools()[0]

        def provider(request):
            body = json.loads(request.content)
            requests.append(body)
            assert body["tool_choice"] == "auto"
            assert len(body["tools"]) == 1 and tool.wire_name in str(body["tools"])
            transcript = body["messages" if protocol == "chat" else "input"]
            assert "conflict=true" in str(transcript)  # Tool prompt expanded from the selected memory card.
            if len(requests) == 1:
                arguments = json.dumps(update(200, "# 用户画像\n偏好茶").arguments, ensure_ascii=False)
                if protocol == "chat":
                    return httpx.Response(
                        200,
                        json={
                            "choices": [
                                {
                                    "finish_reason": "tool_calls",
                                    "message": {
                                        "role": "assistant",
                                        "content": None,
                                        "tool_calls": [
                                            {
                                                "id": "portrait",
                                                "type": "function",
                                                "function": {
                                                    "name": tool.wire_name,
                                                    "arguments": arguments,
                                                },
                                            }
                                        ],
                                    },
                                }
                            ]
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "portrait",
                                "name": tool.wire_name,
                                "arguments": arguments,
                            }
                        ],
                    },
                )
            assert any(item.get("tool_call_id", item.get("call_id")) == "portrait" for item in transcript)
            assert "updated" in str(transcript)
            if protocol == "chat":
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps(MEMORY),
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": json.dumps(MEMORY)}],
                        }
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            try:
                await deliver(runtime, event())
                channel = next(iter(plugin.sessions.channels.values()))
                runtime.models._clients["test"] = ModelClient(
                    ModelCard(id="test", endpoint="https://unused.test/v1", model="test", protocol=protocol),
                    http,
                    lambda _: "",
                    prompts=runtime.prompts,
                )
                plugin.sessions.seal(channel)
                await runtime.jobs.drain()
                assert len(requests) == 2 and not channel.records
                assert "偏好茶" in plugin.agent.profiles.read(user(200)).content
                assert channel.store.load().summary == MEMORY["summary"]
                assert len(runtime.messages.adapters["main"].sent) == 1
            finally:
                await runtime.close()

    asyncio.run(run())


def test_summary_tool_loop_limit_retains_pending_group_memory(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai(max_tool_rounds=2)
        count = 0

        async def respond(items, tools, options):
            nonlocal count
            count += 1
            return calls(update(200, "画像已保存", call_id=str(count)))

        model.summary_responder = respond
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            assert count == 2 and channel.records and channel.retry_ticket
            assert channel.store.pending_path.exists() and not channel.store.memory_path.exists()
            assert plugin.agent.profiles.read(user(200)).content == "画像已保存"
        finally:
            await runtime.close()

    asyncio.run(run())


def test_summary_does_not_commit_when_profiles_leave_no_room_for_transcript(setup_ai):
    async def run():
        runtime, plugin, model = await setup_ai()
        try:
            await deliver(runtime, event())
            channel = next(iter(plugin.sessions.channels.values()))
            plugin.agent.profiles.update(user(200), "画像内容" * 1000, "")
            plugin.config = plugin.config.model_copy(update={"context_budget": 4096, "output_reserve": 512})
            plugin.sessions.seal(channel)
            await runtime.jobs.drain()
            assert len(model.requests) == 1  # Never ask the model to summarize an absent transcript.
            assert channel.records and channel.store.pending_path.exists() and channel.retry_ticket
            assert not channel.store.memory_path.exists()
        finally:
            await runtime.close()

    asyncio.run(run())
