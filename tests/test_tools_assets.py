import asyncio

import httpx
import pytest

from accbot import AssetRef, FrameworkError, Tool, ToolCall, ToolContext, async_tool
from accbot.events import EventBus, TaskFailed
from accbot.lifecycle import Activation
from accbot.messages import AssetReader
from accbot.plugins.base import PluginContext
from accbot.tools import JobManager, ToolRegistry


def test_validation_context_injection_failure_and_timeout():
    async def run():
        bus = EventBus()
        jobs = JobManager(bus)
        tools = ToolRegistry(jobs)
        owner = Activation("test")

        @Tool
        async def add(x: int, ctx: ToolContext):
            return {"sum": x + 1, "correlation": ctx.correlation_id}

        @Tool(timeout=0.001)
        async def slow():
            await asyncio.sleep(1)

        @async_tool
        async def bad():
            raise ValueError("background failure")

        definition = tools.register(owner, add)
        tools.register(owner, slow)
        tools.register(owner, bad)
        owner.activate()
        assert "ctx" not in definition.parameters["properties"]
        result = await tools.execute(
            ToolCall(name="test.add", arguments={"x": 2}), ToolContext(correlation_id="c")
        )
        assert result.value == {"sum": 3, "correlation": "c"}
        invalid = await tools.execute(ToolCall(name="test.add", arguments={"x": "2"}))
        assert not invalid.ok and invalid.error.code == "invalid_argument"
        timed_out = await tools.execute(ToolCall(name="test.slow"))
        assert not timed_out.ok and timed_out.error.outcome == "unknown"
        failures = []
        bus.subscribe(TaskFailed, failures.append)
        accepted = await tools.execute(ToolCall(name="test.bad"))
        owner.retire()
        await jobs.drain()
        await bus.drain()
        assert failures[0].task_id == accepted.task_id
        assert failures[0].error.code == "plugin"
        assert not tools.list()
        await jobs.close()
        await bus.close()

    asyncio.run(run())


def test_asset_read_limits_and_inline_decode(tmp_path):
    file = tmp_path / "asset"
    file.write_bytes(b"12345")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"12345"))
        ) as http:
            assets = AssetReader(http)
            assert await assets.read(AssetRef(data="data:text/plain;base64,aGk=")) == b"hi"
            for asset in (AssetRef(path=str(file)), AssetRef(url="https://media.test/a")):
                with pytest.raises(FrameworkError):
                    await assets.read(asset, max_bytes=4)

    asyncio.run(run())


def test_stop_jobs_only_stops_selected_jobs_owned_by_the_context(runtime, tmp_path):
    async def run():
        owner, other = Activation("first"), Activation("second")
        owner.activate()
        other.activate()
        context = PluginContext(runtime, runtime.plugins, owner, tmp_path)
        release = asyncio.Event()
        selected = context.spawn(release.wait)
        remaining = context.spawn(release.wait)
        foreign = runtime.jobs.submit(other, release.wait)
        try:
            await context.stop_jobs([selected, foreign, "missing"])
            assert runtime.jobs.history[selected]["status"] == "cancelled"
            assert set(runtime.jobs.tasks) == {remaining, foreign}
            release.set()
            await context.stop_jobs([remaining], timeout=1)
            await runtime.jobs.drain()
            assert runtime.jobs.history[remaining]["status"] == "completed"
            assert runtime.jobs.history[foreign]["status"] == "completed"
            assert owner.inflight == other.inflight == 0
        finally:
            release.set()
            owner.retire()
            other.retire()
            await runtime.close()

    asyncio.run(run())


def test_job_cancelled_before_start_releases_its_registration_lease():
    async def run():
        bus = EventBus()
        jobs, owner = JobManager(bus), Activation("sample")
        owner.activate()

        async def never_started():
            pytest.fail("cancelled job must not run")

        job_id = jobs.submit(owner, never_started)
        await jobs.close()
        assert not jobs.tasks and owner.inflight == 0
        assert jobs.history[job_id]["status"] == "cancelled"
        owner.retire()
        await bus.close()

    asyncio.run(run())
