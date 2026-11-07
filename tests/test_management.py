import json
import shutil
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from accbot.config import ConfigStore, Settings
from accbot.runtime import Runtime
from accbot.web import create_app


def test_grouped_demo_plugins_preview_and_lifecycle(workspace):
    plugins, data = workspace
    shutil.copytree(Path(__file__).parent / "fixtures" / "demo", plugins / "demo")
    runtime = Runtime(data)
    with TestClient(create_app(runtime)) as client:
        entries = client.get("/api/plugins").json()
        assert len(entries) == 6
        assert {item["group"] for item in entries} == {"demo/core", "demo/extra"}
        preview = client.post("/api/plugins/preview", json={"selected": ["demo.observer"]}).json()
        assert not preview["valid"] and preview["recommended"] == ["demo.service"]
        assert client.put("/api/plugins/active", json={"selected": ["demo.observer"]}).status_code == 400
        assert not runtime.plugins.active
        desired = ["demo.service", "demo.observer", "demo.tools", "demo.background", "demo.persona"]
        assert client.put("/api/plugins/active", json={"selected": desired}).status_code == 200
        assert runtime.plugins.require("demo.service").describe() == "accbot 演示服务"
        old_owner = runtime.plugins.instances["demo.service"][1].id
        result = client.post("/api/plugins/demo.service/reload").json()
        assert not result["failed"] and runtime.plugins.instances["demo.service"][1].id != old_owner
        assert runtime.plugins.active == set(desired)
        client.post("/api/plugins/demo.service/unload")
        assert runtime.plugins.active == {"demo.persona"}
        missing = client.post("/api/plugins/preview", json={"selected": ["demo.missing"]}).json()
        assert not missing["valid"] and missing["issues"][0]["kind"] == "missing"
    assert ConfigStore(data).load().active_plugins == ("demo.persona",)


def test_model_wizard_detects_chat_and_manages_credentials(workspace):
    calls = []

    def provider(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer private-test-key"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "chat-a"}, {"id": "chat-b"}]})
        if request.url.path == "/v1/responses":
            return httpx.Response(404, json={"error": "unavailable"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    runtime = Runtime(workspace[1], http=http)
    draft = {
        "id": "main",
        "name": "主模型",
        "endpoint": "https://model.test/v1/chat/completions",
        "key": "private-test-key",
        "model": "chat-a",
    }
    with TestClient(create_app(runtime)) as client:
        catalog = client.post(
            "/api/models/catalog", json={"endpoint": draft["endpoint"], "key": draft["key"]}
        )
        assert catalog.json()["models"] == ["chat-a", "chat-b"]
        probe = client.post("/api/models/detect", json=draft)
        assert probe.json()["protocol"] == "chat"
        assert probe.json()["endpoint"] == "https://model.test/v1"
        saved = client.post("/api/models/cards", json={**draft, "protocol": "chat"})
        assert saved.status_code == 201 and saved.json()["credential_configured"]
        variable = saved.json()["api_key_env"]
        assert variable.startswith("ACCBOT_MODEL_MAIN_")
        assert runtime.store.get_secret(variable) == draft["key"]
        updated = client.put("/api/models/cards/main", json={**draft, "key": None, "protocol": "chat"})
        assert updated.status_code == 200 and updated.json()["api_key_env"] == variable
        assert client.post("/api/models/detect", json={**draft, "key": None}).json()["protocol"] == "chat"
        for path in ("/api/models", "/api/settings", "/api/secrets"):
            assert draft["key"] not in client.get(path).text
        assert client.delete(f"/api/secrets/{variable}").status_code == 409
        assert client.delete("/api/models/cards/main").status_code == 200
        assert client.delete(f"/api/secrets/{variable}").status_code == 200
        client.portal.call(http.aclose)
    assert len(calls) == 5


def test_model_probe_responses_and_failure_do_not_leak_keys(workspace):
    def provider(request):
        if request.url.host == "good.test":
            return httpx.Response(
                200,
                json={
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
                    "status": "completed",
                },
            )
        return httpx.Response(401, json={"error": "secret-test-key"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    with TestClient(create_app(Runtime(workspace[1], http=http))) as client:
        draft = {"id": "test", "endpoint": "https://good.test/v1", "model": "test", "key": "secret-test-key"}
        assert client.post("/api/models/detect", json=draft).json()["protocol"] == "responses"
        failed = client.post("/api/models/detect", json={**draft, "endpoint": "https://bad.test/v1"})
        assert failed.status_code == 400 and draft["key"] not in failed.text
        invalid = client.post("/api/models/cards", json={"key": draft["key"]})
        assert invalid.status_code == 422 and draft["key"] not in invalid.text
        invalid = client.post(
            "/api/adapters/onebot",
            json={"id": "test", "self_id": "100", "mode": "http", "token": draft["key"]},
        )
        assert invalid.status_code == 422 and draft["key"] not in invalid.text
        assert client.post("/api/models/detect", json={**draft, "endpoint": "invalid"}).status_code == 400
        client.portal.call(http.aclose)


def test_saved_onebot_configuration_is_inactive_until_activated(workspace):
    runtime = Runtime(workspace[1])
    with TestClient(create_app(runtime)) as client:
        first = client.post(
            "/api/adapters/onebot",
            json={"id": "qq-main", "name": "群聊", "self_id": "100", "token": "private-qq-token"},
        )
        assert first.status_code == 201 and first.json()["status"] == "inactive"
        assert not runtime.messages.adapters
        assert client.post("/onebot/qq-main/events", json={}).status_code == 404
        assert client.post("/api/adapters/onebot/qq-main/activate").json()["status"] == "waiting"
        with client.websocket_connect(
            "/onebot/qq-main/ws", headers={"authorization": "Bearer private-qq-token"}
        ):
            connected = runtime.messages.adapters["qq-main"]
            assert client.get("/api/adapters/onebot").json()[0]["status"] == "connected"
            client.post("/api/adapters/onebot", json={"id": "qq-other", "self_id": "101"})
            assert runtime.messages.adapters["qq-main"] is connected
            assert runtime.messages.adapters["qq-main"].socket is not None
        assert "private-qq-token" not in client.get("/api/adapters/onebot").text
        client.post("/api/adapters/onebot/qq-main/deactivate")
        assert not runtime.messages.adapters
    settings = ConfigStore(workspace[1]).load()
    assert len(settings.onebot) == 2 and not any(c.enabled for c in settings.onebot)
    assert len(list((workspace[1] / "config/adapters/onebot").glob("*.json"))) == 2


def test_legacy_config_migration_preserves_credentials(tmp_path):
    old = {
        "plugin_directory": "plugins",
        "active_plugins": [],
        "models": [{"id": "legacy", "endpoint": "https://test/v1", "model": "a", "api_key_env": "OLD_KEY"}],
        "onebot": [{"id": "qq", "self_id": "100", "http_url": "http://localhost:3000"}],
    }
    (tmp_path / "config.json").write_text(json.dumps(old))
    (tmp_path / "secrets.json").write_text(json.dumps({"OLD_KEY": "preserved-secret"}))
    store = ConfigStore(tmp_path)
    loaded = store.load()
    assert loaded.onebot[0].enabled and loaded.onebot[0].mode == "http"
    assert store.get_secret("OLD_KEY") == "preserved-secret"
    assert (tmp_path / "config/models/legacy.json").is_file()
    assert (tmp_path / "config/adapters/onebot/qq.json").is_file()
    assert (tmp_path / "backups/v1/config.json").is_file()
    assert (tmp_path / "backups/v1/secrets.json").is_file()
    assert not (tmp_path / "secrets.json").exists()
    assert ConfigStore(tmp_path).load() == loaded


def test_dependency_preview_reports_invalid_version_constraint(write_plugin, runtime):
    write_plugin("a", requires={"b": "invalid spec"})
    write_plugin("b")
    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/plugins/preview", json={"selected": ["a", "b"]})
        assert response.status_code == 200
        assert not response.json()["valid"]
        assert response.json()["issues"][0]["kind"] == "version"
        assert client.put("/api/plugins/active", json={"selected": ["a", "b"]}).status_code == 400


def test_local_default_needs_no_token_but_remote_login_uses_cookie(workspace):
    with TestClient(create_app(Runtime(workspace[1]))) as client:
        assert client.get("/api/session").json() == {"auth_required": False, "authenticated": True}
        assert client.get("/api/plugins").status_code == 200
        assert client.get("/assets/js/app.js").status_code == 200
    store = ConfigStore(workspace[1])
    store.save(Settings(plugin_directory=str(workspace[0]), management_token_env="ADMIN_TEST"))
    store.set_secret("ADMIN_TEST", "private-admin-token")
    with TestClient(create_app(Runtime(workspace[1]))) as client:
        assert client.get("/api/plugins").status_code == 401
        assert client.post("/api/session", json={"token": "incorrect"}).status_code == 401
        login = client.post("/api/session", json={"token": "private-admin-token"})
        assert login.status_code == 200 and "HttpOnly" in login.headers["set-cookie"]
        assert "private-admin-token" not in login.text
        assert client.get("/api/plugins").status_code == 200
        client.delete("/api/session")
        assert client.get("/api/plugins").status_code == 401
