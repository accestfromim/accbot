import asyncio
import json
import shutil

from fastapi.testclient import TestClient

from accbot import PersonaCard
from accbot.config import ConfigStore
from accbot.runtime import Runtime
from accbot.web import create_app


def test_persona_crud_export_and_persistence_without_plugins(workspace):
    runtime = Runtime(workspace[1])
    draft = {"id": "gentle", "name": "温柔", "description": "群聊伙伴", "system_prompt": "温和而准确。"}
    with TestClient(create_app(runtime)) as client:
        assert not runtime.plugins.active
        assert client.post("/api/personas", json=draft).status_code == 201
        assert client.post("/api/personas", json=draft).status_code == 409
        draft["system_prompt"] = "冷静地思考，温和地回答。"
        assert client.put("/api/personas/gentle", json=draft).status_code == 200
        assert runtime.personas.get("gentle").system_prompt == draft["system_prompt"]
        exported = client.get("/api/personas/gentle/export")
        assert 'filename="gentle.json"' in exported.headers["content-disposition"]
        assert PersonaCard.model_validate_json(exported.content) == runtime.personas.get("gentle")
        assert client.get("/api/personas/gentle").json()["system_prompt"] == draft["system_prompt"]
        assert client.put("/api/personas/gentle", json={**draft, "id": "new"}).status_code == 400
        assert client.post("/api/personas", json={**draft, "id": "../escape"}).status_code == 422
        assert client.post("/api/personas", json={**draft, "system_prompt": ""}).status_code == 422
        saved = workspace[1] / "config/personas/gentle.json"
        assert PersonaCard.model_validate_json(saved.read_text()) == runtime.personas.get("gentle")
        assert len(ConfigStore(workspace[1]).load().personas) == 2
        for card in runtime.personas.list():
            assert client.delete(f"/api/personas/{card.id}").status_code == 200
        assert client.get("/api/personas/gentle").status_code == 404
    assert not ConfigStore(workspace[1]).load().personas  # Don't resurrect deleted defaults.


def test_legacy_persona_migration_removes_only_retired_resource_dependency(workspace):
    plugins, data = workspace
    old = plugins / "ai/persona"
    old.mkdir(parents=True)
    (old / "group.txt").write_text("维护者修改过的人格正文", encoding="utf-8")
    shutil.rmtree(data / "config/personas")
    (data / "config/plugins.json").write_text(json.dumps({"active": ["ai.persona", "ai.tools", "ai.chat"]}))
    settings = ConfigStore(data).load()
    assert settings.active_plugins == ("ai.tools", "ai.chat")
    assert settings.personas[0].system_prompt == "维护者修改过的人格正文"
    assert (old / "group.txt").exists()
    assert ConfigStore(data).load() == settings


def test_plugins_share_persona_catalog_without_dependencies(runtime, write_plugin):
    code = """from accbot import Plugin as Base
class Plugin(Base):
    async def on_activate(self):
        self.ctx.export(self)
    def prompt(self):
        return self.ctx.personas.get("default").system_prompt
"""
    for key in ("a", "b"):
        write_plugin(key, code)

    async def run():
        try:
            await runtime.start()
            await runtime.set_active(["a", "b"])
            first = runtime.plugins.require("a")
            second = runtime.plugins.require("b")
            assert first.prompt() == second.prompt()
            await runtime.replace_personas(
                [PersonaCard(id="default", name="新人格", system_prompt="改变性格")]
            )
            assert first.prompt() == second.prompt() == "改变性格"
            await runtime.unload("a")
            assert second.prompt() == "改变性格"
            assert runtime.personas.get("default").name == "新人格"
        finally:
            await runtime.close()

    asyncio.run(run())
