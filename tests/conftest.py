import json

import pytest

from accbot.config import ConfigStore, Settings
from accbot.runtime import Runtime


@pytest.fixture
def workspace(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    data = tmp_path / "data"
    store = ConfigStore(data)
    store.save(Settings(plugin_directory=str(plugins)))
    return plugins, data


@pytest.fixture
def write_plugin(workspace):
    plugins, _ = workspace

    def write(plugin_id, code="from accbot import Plugin\n", *, requires=None, version="1.0", files=None):
        path = plugins / plugin_id
        path.mkdir(exist_ok=True)
        dependencies = ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in (requires or {}).items())
        (path / "plugin.toml").write_text(
            f'id = "{plugin_id}"\nversion = "{version}"\nrequires = {{ {dependencies} }}\n', encoding="utf-8"
        )
        (path / "plugin.py").write_text(code, encoding="utf-8")
        for name, source in (files or {}).items():
            destination = path / name
            destination.parent.mkdir(exist_ok=True, parents=True)
            destination.write_text(source, encoding="utf-8")
        return path

    return write


@pytest.fixture
def runtime(workspace):
    return Runtime(workspace[1])
