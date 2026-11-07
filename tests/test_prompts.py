import asyncio
import json
import shutil

import httpx
import pytest
from fastapi.testclient import TestClient

from accbot import FrameworkError, ModelItem, PersonaCard, SystemPromptCard, ToolDefinition
from accbot.config import ConfigStore, ModelCard
from accbot.runtime import Runtime
from accbot.web import create_app

TEMPLATE = "BASE\n<persona>\n<plugins_insert_prompt>\n<tools_insert_prompt>\nEND"
CODE = """from accbot import Plugin as Base, Tool, async_tool
class Plugin(Base):
    @Tool(prompt_file="safe.md")
    async def safe(self):
        return "ok"
    @async_tool(prompt_file="danger.md")
    async def danger(self):
        return "ok"
"""
FILES = {
    "prompts/system.md": "PLUGIN_CONTEXT",
    "prompts/tools/safe.md": "SAFE_GUIDE",
    "prompts/tools/danger.md": "DANGER_GUIDE",
}


def card(content=TEMPLATE, **values):
    return SystemPromptCard(id="test", name="测试", content=content, **values)


def test_prompt_cards_crud_markdown_storage_and_export(workspace):
    runtime = Runtime(workspace[1])
    with TestClient(create_app(runtime)) as client:
        body = card().model_dump()
        assert client.post("/api/prompts", json=body).status_code == 201
        assert client.post("/api/prompts", json=body).status_code == 409
        body["content"] = "# 更新\n<persona>"
        assert client.put("/api/prompts/test", json=body).status_code == 200
        assert runtime.prompts.get("test").content == body["content"]
        exported = client.get("/api/prompts/test/export")
        assert exported.text == body["content"]
        assert 'filename="test.md"' in exported.headers["content-disposition"]
        assert exported.headers["content-type"].startswith("text/markdown")
        directory = workspace[1] / "config/prompts/test"
        assert (directory / "prompt.md").read_text() == body["content"]
        assert "content" not in json.loads((directory / "metadata.json").read_text())
        assert client.get("/api/prompts/test").json() == body
        assert client.post("/api/prompts", json={**body, "id": "../escape"}).status_code == 422
        assert client.put("/api/prompts/test", json={**body, "id": "another"}).status_code == 400
        saved = ConfigStore(workspace[1]).load()
        assert next(c for c in saved.system_prompts if c.id == "test").content == body["content"]
        for item in runtime.prompts.list():
            assert client.delete(f"/api/prompts/{item.id}").status_code == 200
        assert client.get("/api/prompts/test").status_code == 404
    assert not ConfigStore(workspace[1]).load().system_prompts


def test_legacy_prompt_files_migrate_without_overwriting_sources(workspace):
    directory = workspace[0] / "ai/chat/prompts"
    directory.mkdir(parents=True)
    for name in ("group", "owner", "memory"):
        (directory / f"{name}.txt").write_text(f"CUSTOM_{name}")
    shutil.rmtree(workspace[1] / "config/prompts")
    store = ConfigStore(workspace[1])
    settings = store.load()
    cards = {c.id: c for c in settings.system_prompts}
    assert "CUSTOM_group" in cards["chat"].content and "<persona>" in cards["chat"].content
    assert "CUSTOM_owner" in cards["owner"].content
    assert cards["memory"].content == "CUSTOM_memory"
    assert (directory / "group.txt").read_text() == "CUSTOM_group"
    assert ConfigStore(workspace[1]).load() == settings


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_model_assembles_only_requested_card_persona_and_tools(runtime, write_plugin, protocol):
    write_plugin("sample", CODE, files=FILES)

    async def run():
        requests = []

        def provider(request):
            data = json.loads(request.content)
            requests.append(data)
            if protocol == "chat":
                return httpx.Response(
                    200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]}
                )
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "OK"}],
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            runtime.models.http = http
            try:
                await runtime.start()
                await runtime.replace_prompts([card()])
                await runtime.replace_personas(
                    [PersonaCard(id="p", name="人格", system_prompt="PERSONA <tools_insert_prompt>")]
                )
                await runtime.replace_models(
                    [ModelCard(id="m", model="x", endpoint="https://unused.test/v1", protocol=protocol)]
                )
                await runtime.set_active(["sample"])
                model = runtime.models.get("m")
                definitions = {t.name: t for t in runtime.tools.list()}
                items = [ModelItem.text("user", "USER_LITERAL <persona>")]
                await model.generate(
                    items, tools=[definitions["safe"]], system_prompt_id="test", persona_id="p"
                )
                payload = requests[-1]
                assert "system_prompt_id" not in payload and "persona_id" not in payload
                encoded = payload["messages" if protocol == "chat" else "input"]
                system = json.dumps(encoded[0], ensure_ascii=False)
                assert all(
                    text in system for text in ("BASE", "PERSONA", "PLUGIN_CONTEXT", "SAFE_GUIDE", "END")
                )
                assert "DANGER_GUIDE" not in system
                assert "<tools_insert_prompt>" in system  # Inserted persona text is not expanded again.
                assert "USER_LITERAL <persona>" in json.dumps(encoded[1:])
                assert len(items) == 1 and items[0].role == "user"
                assert len(payload["tools"]) == 1
                assert all(
                    k not in json.dumps(payload["tools"])
                    for k in ("system_prompt", "activation_id", "SAFE_GUIDE")
                )
                # The same model client observes prompt/persona edits at the next request.
                await runtime.replace_prompts([card("EDITED <persona> <tools_insert_prompt>")])
                await runtime.replace_personas(
                    [PersonaCard(id="p", name="人格", system_prompt="NEW_PERSONA")]
                )
                await model.generate(items, system_prompt_id="test", persona_id="p")
                system = json.dumps(requests[-1]["messages" if protocol == "chat" else "input"][0])
                assert "EDITED" in system and "NEW_PERSONA" in system
                assert all(
                    t not in system
                    for t in ("PLUGIN_CONTEXT", "SAFE_GUIDE", "DANGER_GUIDE", "<tools_insert_prompt>")
                )
                # The selected card is the entire template: users may reorder, repeat or remove slots.
                guide = f"### {definitions['safe'].wire_name}\nSAFE_GUIDE"
                for template, expected in (
                    ("只使用这段正文。", "只使用这段正文。"),
                    (
                        "<tools_insert_prompt>|<persona>|<plugins_insert_prompt>|<persona>",
                        f"{guide}|NEW_PERSONA|### 插件：sample\nPLUGIN_CONTEXT|NEW_PERSONA",
                    ),
                ):
                    await runtime.replace_prompts([card(template)])
                    await model.generate(
                        items, tools=[definitions["safe"]], system_prompt_id="test", persona_id="p"
                    )
                    encoded = requests[-1]["messages" if protocol == "chat" else "input"]
                    content = encoded[0]["content"]
                    actual = content if protocol == "chat" else "".join(part["text"] for part in content)
                    assert actual == expected and len(encoded) == 2
                    assert len(requests[-1]["tools"]) == 1  # Removing the prompt slot keeps tool schemas.
                # Omitting a card leaves manually supplied items untouched, with no implicit default.
                await model.generate(items)
                assert len(requests[-1]["messages" if protocol == "chat" else "input"]) == 1
                assert "system_prompt_id" not in type(model.card).model_fields
                count = len(requests)
                with pytest.raises(FrameworkError):
                    await model.generate(items, persona_id="p")
                with pytest.raises(FrameworkError):
                    await model.generate(items, system_prompt_id="missing")
                with pytest.raises(FrameworkError):
                    await model.generate(items, tools=[definitions["safe"]] * 2, system_prompt_id="test")
                assert len(requests) == count
            finally:
                await runtime.close()

    asyncio.run(run())


def test_prompt_snapshot_reload_unload_and_stale_tool_identity(runtime, write_plugin):
    path = write_plugin("sample", CODE, files=FILES)
    write_plugin("another", files={"prompts/system.md": "ANOTHER_PLUGIN"})

    async def run():
        try:
            await runtime.start()
            await runtime.replace_prompts([card()])
            assert "PLUGIN_CONTEXT" not in runtime.prompts.render("test")
            await runtime.set_active(["sample", "another"])
            old = runtime.tools.list()
            rendered = runtime.prompts.render("test", tools=old)
            assert all(
                word in rendered
                for word in ("ANOTHER_PLUGIN", "PLUGIN_CONTEXT", "SAFE_GUIDE", "DANGER_GUIDE")
            )
            assert rendered.index("ANOTHER_PLUGIN") < rendered.index("PLUGIN_CONTEXT")
            (path / "prompts/system.md").write_text("NEW_PLUGIN")
            (path / "prompts/tools/safe.md").write_text("NEW_SAFE")
            assert runtime.prompts.render("test", tools=old) == rendered
            assert not (await runtime.reload("sample"))["failed"]
            with pytest.raises(FrameworkError, match="工具声明已失效"):
                runtime.prompts.render("test", tools=old)
            updated = runtime.prompts.render("test", tools=runtime.tools.list())
            assert "NEW_PLUGIN" in updated and "NEW_SAFE" in updated and "SAFE_GUIDE" not in updated
            await runtime.unload("sample")
            assert "NEW_PLUGIN" not in runtime.prompts.render("test")
            assert "ANOTHER_PLUGIN" in runtime.prompts.render("test")
            assert not runtime.tools.list()
        finally:
            await runtime.close()

    asyncio.run(run())


def test_staging_and_failed_activation_never_contribute_prompts(runtime, write_plugin):
    code = (
        CODE
        + """
    async def on_activate(self):
        self.ctx.runtime.entered.set()
        await self.ctx.runtime.release.wait()
        raise RuntimeError("fail activation")
"""
    )
    write_plugin("sample", code, files=FILES)

    async def run():
        try:
            runtime.entered, runtime.release = asyncio.Event(), asyncio.Event()
            await runtime.start()
            await runtime.replace_prompts([card()])
            activation = asyncio.create_task(runtime.set_active(["sample"]))
            await runtime.entered.wait()
            assert "PLUGIN_CONTEXT" not in runtime.prompts.render("test")
            staged = runtime.tools._entries["sample.safe"].definition
            with pytest.raises(FrameworkError):
                runtime.prompts.render("test", tools=[staged])
            runtime.release.set()
            with pytest.raises(RuntimeError):
                await activation
            assert not runtime.prompts._plugins and not runtime.tools.list()
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("filename", ["missing.md", "../outside.md", "/tmp/outside.md", "", "safe.txt"])
def test_invalid_tool_prompt_fails_activation_and_rolls_back(runtime, write_plugin, filename):
    write_plugin("sample", CODE.replace('"safe.md"', repr(filename)), files=FILES)

    async def run():
        try:
            await runtime.start()
            with pytest.raises(FrameworkError):
                await runtime.set_active(["sample"])
            assert not runtime.plugins.active and not runtime.tools.list() and not runtime.prompts._plugins
        finally:
            await runtime.close()

    asyncio.run(run())


def test_prompt_bundle_rejects_external_symlink_and_invalid_utf8(runtime, write_plugin, tmp_path):
    path = write_plugin("sample", CODE, files=FILES)
    outside = tmp_path / "outside.md"
    outside.write_text("must not be imported")
    (path / "prompts/system.md").unlink()
    (path / "prompts/system.md").symlink_to(outside)

    async def run():
        try:
            await runtime.start()
            with pytest.raises(FrameworkError):
                await runtime.set_active(["sample"])
            (path / "prompts/system.md").unlink()
            (path / "prompts/system.md").write_bytes(b"\xff")
            with pytest.raises(FrameworkError):
                await runtime.set_active(["sample"])
            assert not runtime.prompts._plugins
        finally:
            await runtime.close()

    asyncio.run(run())


def test_literal_substitutions_do_not_expand_inserted_plugin_or_tool_text(runtime, write_plugin):
    write_plugin("sample", files={"prompts/system.md": "PLUGIN <persona>"})

    async def run():
        try:
            await runtime.start()
            await runtime.replace_prompts([card()])
            await runtime.set_active(["sample"])
            tool = ToolDefinition(
                id="external", name="external", system_prompt="TOOL <plugins_insert_prompt>"
            )
            rendered = runtime.prompts.render("test", tools=[tool])
            assert "PLUGIN <persona>" in rendered and "TOOL <plugins_insert_prompt>" in rendered
            assert rendered.count("PLUGIN <persona>") == 1
        finally:
            await runtime.close()

    asyncio.run(run())
