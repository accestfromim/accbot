import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from accbot.config import ConfigStore, ModelCard
from accbot.web import create_app

HANDLER = """
import json
from accbot import PluginSettings
class Settings(PluginSettings):
    async def read(self):
        file = self.ctx.data_dir / "own-settings.json"
        return json.loads(file.read_text()) if file.exists() else {"default_model": None}
    async def write(self, values):
        (self.ctx.data_dir / "own-settings.json").write_text(json.dumps(values))
        return values
"""
PLUGIN = """
from accbot import Plugin as BasePlugin
class Plugin(BasePlugin):
    async def on_activate(self):
        self.settings = None
    async def on_settings_changed(self, values):
        self.settings = values
"""


def ui_plugin(write_plugin, *, code=PLUGIN, html='<div><label>设置<input name="example"></label></div>'):
    return write_plugin(
        "example",
        code,
        files={
            "settings.py": HANDLER,
            "frontend/index.html": html,
            "frontend/index.js": "export function mount(root, api) {}",
            "frontend/style.css": "div { color: green; }",
            "frontend/helper.js": "export const value = 1;",
            "frontend/.secret.json": '{"secret": "private"}',
            "frontend/private.py": 'SECRET = "private"',
        },
    )


def test_inactive_plugin_settings_owned_by_plugin_and_applied_when_active(write_plugin, runtime):
    ui_plugin(write_plugin)
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/plugins").json()[0]["has_frontend"]
        page = client.get("/api/plugins/example/ui").json()
        assert page["activation_id"] is None and page["has_settings"]
        assert page["html"].startswith("<div>")
        revision = page["revision"]
        assert client.get(f"/api/plugins/example/settings?revision={revision}").json()["values"] == {
            "default_model": None
        }
        saved = client.put(
            "/api/plugins/example/settings", json={"revision": revision, "values": {"default_model": "main"}}
        )
        assert saved.status_code == 200 and not saved.json()["active_notified"]
        assert not runtime.plugins.active
        file = runtime.plugins.data_root / "example" / "own-settings.json"
        assert json.loads(file.read_text()) == {"default_model": "main"}
        assert "default_model" not in client.get("/api/settings").text
        client.put("/api/plugins/active", json={"selected": ["example"]})
        assert (
            client.put("/api/plugins/example/settings", json={"revision": revision, "values": {}}).status_code
            == 409
        )
        revision = client.get("/api/plugins/example/ui").json()["revision"]
        result = client.put(
            "/api/plugins/example/settings",
            json={"revision": revision, "values": {"default_model": "second"}},
        )
        assert result.json()["active_notified"]
        assert runtime.plugins.instances["example"][0].settings == {"default_model": "second"}
        assert not any(name.startswith("_accbot_settings_") for name in sys.modules)


def test_frontend_and_settings_follow_activation_snapshots(write_plugin, runtime):
    path = ui_plugin(write_plugin)
    with TestClient(create_app(runtime)) as client:
        client.put("/api/plugins/active", json={"selected": ["example"]})
        old = client.get("/api/plugins/example/ui").json()
        (path / "frontend/index.html").write_text("<div>new version</div>")
        (path / "frontend/index.js").write_text("export function mount() { return 'new'; }")
        (path / "settings.py").write_text(
            HANDLER.replace('{"default_model": None}', '{"default_model": "new"}')
        )
        assert client.get("/api/plugins/example/ui").json() == old
        asset = f"/api/plugins/example/ui/assets/{old['revision']}/index.js"
        assert "new" not in client.get(asset).text
        assert client.get(f"/api/plugins/example/settings?revision={old['revision']}").json()["values"] == {
            "default_model": None
        }
        assert client.post("/api/plugins/example/reload").status_code == 200
        current = client.get("/api/plugins/example/ui").json()
        assert current["revision"] != old["revision"] and current["html"] == "<div>new version</div>"
        assert client.get(asset).status_code == 409
        assert (
            client.put(
                "/api/plugins/example/settings", json={"revision": old["revision"], "values": {}}
            ).status_code
            == 409
        )
        assert client.get(f"/api/plugins/example/settings?revision={current['revision']}").json()[
            "values"
        ] == {"default_model": "new"}
        client.post("/api/plugins/example/unload")
        assert client.get(f"/api/plugins/example/settings?revision={current['revision']}").status_code == 409
        assert not runtime.plugins.frontends
        assert client.get("/api/plugins/example/ui").json()["activation_id"] is None


def test_private_files_and_parent_paths_are_not_served(write_plugin, runtime):
    ui_plugin(write_plugin)
    with TestClient(create_app(runtime)) as client:
        revision = client.get("/api/plugins/example/ui").json()["revision"]
        prefix = f"/api/plugins/example/ui/assets/{revision}/"
        assert client.get(prefix + "helper.js").status_code == 200
        assert client.get(prefix + "style.css").headers["content-type"].startswith("text/css")
        for name in (
            "private.py",
            ".secret.json",
            "%2e%2e%2fsettings.py",
            "%2e%2e%5csettings.py",
            "missing.js",
        ):
            assert client.get(prefix + name).status_code == 404


def test_inactive_page_stays_stale_after_activation_and_unload(write_plugin, runtime):
    ui_plugin(write_plugin)
    with TestClient(create_app(runtime)) as client:
        old = client.get("/api/plugins/example/ui").json()["revision"]
        client.put("/api/plugins/active", json={"selected": ["example"]})
        client.post("/api/plugins/example/unload")
        assert client.get("/api/plugins/example/ui").json()["revision"] != old
        assert (
            client.put("/api/plugins/example/settings", json={"revision": old, "values": {}}).status_code
            == 409
        )


def test_frontend_symlink_cannot_publish_private_plugin_files(write_plugin, runtime):
    path = ui_plugin(write_plugin)
    (path / "frontend/leak.json").symlink_to(path / "settings.py")
    with TestClient(create_app(runtime)) as client:
        response = client.get("/api/plugins/example/ui")
        assert response.status_code == 400
        assert "目录外部" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "html",
    [
        "<div></div><div></div>",
        "<section></section>",
        "<!doctype html><div></div>",
        "<div><script>alert(1)</script></div>",
        "outside<div></div>",
        "<div><p></div>",
    ],
)
def test_invalid_component_is_reported_without_breaking_other_management(write_plugin, runtime, html):
    ui_plugin(write_plugin, html=html)
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/plugins/example/ui").status_code == 400
        assert client.get("/api/models").status_code == 200


def test_handler_failure_is_isolated_and_saved_hook_failure_is_explicit(write_plugin, runtime):
    code = PLUGIN.replace("self.settings = values", "raise RuntimeError('hook failed')")
    path = ui_plugin(write_plugin, code=code)
    with TestClient(create_app(runtime)) as client:
        client.put("/api/plugins/active", json={"selected": ["example"]})
        revision = client.get("/api/plugins/example/ui").json()["revision"]
        response = client.put(
            "/api/plugins/example/settings", json={"revision": revision, "values": {"value": 1}}
        )
        assert (
            response.status_code == 200
            and response.json()["warning"]
            and not response.json()["active_notified"]
        )
        assert client.get(f"/api/plugins/example/settings?revision={revision}").json()["values"] == {
            "value": 1
        }
        client.post("/api/plugins/example/unload")
        (path / "settings.py").write_text(
            HANDLER.replace("return values", 'raise RuntimeError("private-token")')
        )
        revision = client.get("/api/plugins/example/ui").json()["revision"]
        failed = client.put("/api/plugins/example/settings", json={"revision": revision, "values": {}})
        assert failed.status_code == 400 and "private-token" not in failed.text
        assert client.get("/api/status").status_code == 200


def test_plugin_ui_and_assets_require_management_auth(write_plugin, runtime):
    ui_plugin(write_plugin)
    runtime.store.set_secret("ADMIN", "test-only-token")
    runtime.settings = runtime.settings.model_copy(update={"management_token_env": "ADMIN"})
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/plugins/example/ui").status_code == 401
        client.post("/api/session", json={"token": "test-only-token"})
        revision = client.get("/api/plugins/example/ui").json()["revision"]
        assert client.get(f"/api/plugins/example/ui/assets/{revision}/index.js").status_code == 200
        client.delete("/api/session")
        assert client.get(f"/api/plugins/example/ui/assets/{revision}/index.js").status_code == 401
        assert (
            client.put("/api/plugins/example/settings", json={"revision": revision, "values": {}}).status_code
            == 401
        )


def test_observer_demo_persists_model_choice_and_updates_active_instance(workspace, runtime):
    shutil.copytree(Path(__file__).parent / "fixtures/demo", workspace[0] / "demo")
    card = ModelCard(id="main", name="主模型", endpoint="https://unused.test/v1", model="offline")
    with TestClient(create_app(runtime)) as client:
        client.portal.call(runtime.replace_models, [card])
        base = "/api/plugins/demo.observer"
        revision = client.get(base + "/ui").json()["revision"]
        assert (
            client.put(
                base + "/settings", json={"revision": revision, "values": {"default_model": "missing"}}
            ).status_code
            == 400
        )
        values = {"default_model": "main", "count_messages": False}
        assert (
            client.put(base + "/settings", json={"revision": revision, "values": values}).status_code == 200
        )
        client.put("/api/plugins/active", json={"selected": ["demo.service", "demo.observer"]})
        instance = runtime.plugins.instances["demo.observer"][0]
        assert instance.default_model == "main" and instance.count_messages is False
        revision = client.get(base + "/ui").json()["revision"]
        assert (
            client.put(
                base + "/settings", json={"revision": revision, "values": {**values, "count_messages": True}}
            ).status_code
            == 200
        )
        assert instance.count_messages is True
        client.post(base + "/reload")
        assert runtime.plugins.instances["demo.observer"][0].default_model == "main"
    settings = ConfigStore(workspace[1]).load()
    assert not hasattr(settings, "default_model")
    assert (runtime.plugins.data_root / "demo.observer/config/settings.json").is_file()
