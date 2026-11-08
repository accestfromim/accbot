import asyncio
import sys

import pytest

from accbot import Event, FrameworkError, ToolCall
from accbot.events import TaskCompleted


def test_dependency_preflight_has_no_import_side_effects(runtime, write_plugin, tmp_path):
    marker = tmp_path / "imported"
    write_plugin(
        "a",
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nfrom accbot import Plugin\n",
        requires={"b": ">=1"},
    )

    async def run():
        runtime.plugins.discover()
        with pytest.raises(FrameworkError, match="requires selected"):
            await runtime.plugins.activate({"a"})
        assert not marker.exists()
        write_plugin("b", requires={"a": ">=1"})
        runtime.plugins.discover()
        with pytest.raises(FrameworkError, match="cycle"):
            await runtime.plugins.activate({"a", "b"})
        write_plugin("b", version="0.1")
        runtime.plugins.discover()
        with pytest.raises(FrameworkError, match="requires b"):
            runtime.plugins.plan({"a", "b"})
        await runtime.close()

    asyncio.run(run())


def test_api_dependency_order_and_recursive_deactivation(runtime, write_plugin):
    write_plugin(
        "b",
        """from accbot import Plugin as Base
class Plugin(Base):
    async def on_activate(self): self.ctx.export(self)
    def value(self): return 7
""",
    )
    write_plugin(
        "a",
        """from accbot import Plugin as Base
class Plugin(Base):
    async def on_activate(self):
        self.number = self.ctx.plugins.require('b').api.value()
        self.ctx.export(self)
""",
        requires={"b": ">=1"},
    )

    async def run():
        await runtime.start()
        await runtime.set_active({"a", "b"})
        handle = runtime.plugins.require("b")
        method = handle.value
        assert runtime.plugins.require("a").number == 7
        assert await runtime.plugins.deactivate("b") == ["a", "b"]
        with pytest.raises(FrameworkError):
            method()
        await runtime.close()

    asyncio.run(run())


def test_failed_activation_reverts_all_registrations(runtime, write_plugin):
    write_plugin(
        "broken",
        """from accbot import Plugin as Base, Event, Tool
class Ping(Event): event_type = 'broken.ping'
class Plugin(Base):
    @Tool
    async def action(self): return 1
    async def on_activate(self):
        self.ctx.register_event(Ping)
        self.ctx.export(self)
        raise RuntimeError('broken startup')
""",
    )

    async def run():
        await runtime.start()
        with pytest.raises(RuntimeError):
            await runtime.plugins.activate({"broken"})
        assert not runtime.plugins.active
        assert not runtime.tools.list()
        assert "broken.ping" not in runtime.events._types
        assert "broken" not in runtime.plugins.apis
        await runtime.close()

    asyncio.run(run())


def test_reload_source_submodules_and_keep_old_background_work(runtime, write_plugin):
    code = """import asyncio
from accbot import Plugin as Base, async_tool
class Plugin(Base):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.gate = asyncio.Event()
    async def on_activate(self): self.ctx.export(self)
    def value(self):
        from .helper import VALUE
        return VALUE
    @async_tool
    async def slow(self):
        await self.gate.wait()
        from .late import VALUE
        return VALUE
"""
    path = write_plugin("source", code, files={"helper.py": "VALUE='old'\n", "late.py": "VALUE='old'\n"})
    write_plugin("dependent", requires={"source": ">=1"})

    async def run():
        await runtime.start()
        await runtime.set_active({"source", "dependent"})
        handle = runtime.plugins.require("source")
        gate = handle.gate
        assert handle.value() == "old"
        completed = []
        runtime.events.subscribe(TaskCompleted, completed.append)
        accepted = await runtime.tools.execute(ToolCall(name="source.slow"))
        assert accepted.accepted and accepted.task_id
        (path / "helper.py").write_text("VALUE='new'\n")
        (path / "late.py").write_text("VALUE='new'\n")
        report = await runtime.reload("source")
        assert report["active"] == ["dependent", "source"] and not report["failed"]
        assert runtime.plugins.require("source").value() == "new"
        with pytest.raises(FrameworkError):
            handle.value()
        gate.set()
        await runtime.jobs.drain()
        await runtime.events.drain()
        assert completed[0].result == "old"
        await runtime.close()
        assert not any(name.startswith("_accbot_source_") for name in sys.modules)

    asyncio.run(run())


def test_reload_preflight_failure_preserves_old_activation(runtime, write_plugin):
    write_plugin("a")

    async def run():
        await runtime.start()
        await runtime.set_active({"a"})
        old_owner = runtime.plugins.instances["a"][1]
        write_plugin("a", requires={"missing": ">=1"})
        with pytest.raises(FrameworkError):
            await runtime.reload("a")
        assert runtime.plugins.instances["a"][1] is old_owner and old_owner.active
        await runtime.close()

    asyncio.run(run())


def test_reload_partial_failure_blocks_dependents(runtime, write_plugin):
    write_plugin("base")
    write_plugin("consumer", requires={"base": ">=1"})
    write_plugin("independent")

    async def run():
        await runtime.start()
        await runtime.set_active({"base", "consumer", "independent"})
        write_plugin("base", "raise RuntimeError('new source invalid')\n")
        result = await runtime.reload("base")
        assert result["active"] == ["independent"]
        assert set(result["failed"]) == {"base", "consumer"}
        await runtime.close()

    asyncio.run(run())


def test_retired_timer_cannot_fire_at_new_activation(runtime, write_plugin):
    write_plugin(
        "timer",
        """from accbot import Plugin as Base, Event
class Plugin(Base):
    async def on_activate(self): self.ctx.schedule(Event(), when=.03)
""",
    )

    async def run():
        await runtime.start()
        events = []
        runtime.events.subscribe(Event, events.append)
        await runtime.plugins.activate({"timer"})
        old = runtime.plugins.instances["timer"][1].id
        await runtime.plugins.deactivate("timer")
        await runtime.plugins.activate({"timer"})
        await asyncio.sleep(0.06)
        await runtime.events.drain()
        assert len(events) == 1 and events[0].origin.activation_id != old
        await runtime.close()

    asyncio.run(run())


def test_failed_new_cycle_does_not_deactivate_old_instances(runtime, write_plugin):
    write_plugin("base")
    write_plugin("consumer", requires={"base": ">=1"})

    async def run():
        await runtime.start()
        await runtime.set_active({"base", "consumer"})
        owners = {key: value[1] for key, value in runtime.plugins.instances.items()}
        write_plugin("base", requires={"consumer": ">=1"})
        with pytest.raises(FrameworkError, match="cycle"):
            await runtime.reload("base")
        assert all(
            runtime.plugins.instances[key][1] is owner and owner.active for key, owner in owners.items()
        )
        await runtime.close()

    asyncio.run(run())


def test_staged_timers_wait_until_activation_commits(runtime, write_plugin):
    write_plugin(
        "timer",
        """import asyncio
from accbot import Plugin as Base, Event
class Plugin(Base):
    async def on_activate(self):
        self.ctx.schedule(Event(), when=0)
        await asyncio.sleep(.01)
""",
    )

    async def run():
        await runtime.start()
        events = []
        runtime.events.subscribe(Event, events.append)
        await runtime.plugins.activate({"timer"})
        await asyncio.sleep(0.01)
        await runtime.events.drain()
        assert len(events) == 1
        await runtime.close()

    asyncio.run(run())


def test_discovery_adds_plugins_without_replacing_active_metadata(runtime, write_plugin):
    write_plugin("running")

    async def run():
        await runtime.start()
        await runtime.set_active({"running"})
        old = runtime.plugins.instances["running"][1]
        write_plugin("running", version="2.0", requires={"missing": ">=1"})
        write_plugin("new")
        runtime.plugins.refresh()
        assert runtime.plugins.manifests["running"].version == "1.0"
        await runtime.set_active({"running", "new"})
        assert runtime.plugins.instances["running"][1] is old
        assert runtime.plugins.active == {"running", "new"}
        await runtime.close()

    asyncio.run(run())


def test_plugin_root_package_initializer_is_loaded_and_reloaded(runtime, write_plugin):
    code = """from . import VALUE
from accbot import Plugin as Base
class Plugin(Base):
    async def on_activate(self): self.ctx.export(self)
    def value(self): return VALUE
"""
    path = write_plugin("package", code, files={"__init__.py": "VALUE='first'\n"})

    async def run():
        await runtime.start()
        await runtime.set_active({"package"})
        assert runtime.plugins.require("package").value() == "first"
        (path / "__init__.py").write_text("VALUE='second'\n")
        await runtime.reload("package")
        assert runtime.plugins.require("package").value() == "second"
        await runtime.close()

    asyncio.run(run())


def test_resource_bundle_reload_and_stale_handle(runtime, write_plugin):
    path = write_plugin("persona", files={"card.json": '{"name":"first"}'})
    (path / "plugin.py").unlink()
    manifest = path / "plugin.toml"
    manifest.write_text(manifest.read_text() + 'kind = "resource"\n')

    async def run():
        await runtime.start()
        await runtime.set_active({"persona"})
        handle = runtime.plugins.require("persona")
        assert handle.list() == ("card.json",)
        assert '"first"' in handle.read_text("card.json")
        (path / "card.json").write_text('{"name":"second"}')
        assert '"first"' in handle.read_text("card.json")
        await runtime.reload("persona")
        with pytest.raises(FrameworkError):
            handle.read_text("card.json")
        assert '"second"' in runtime.plugins.require("persona").read_text("card.json")
        await runtime.close()

    asyncio.run(run())
