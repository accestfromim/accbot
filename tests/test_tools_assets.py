import asyncio
from typing import Annotated

import httpx
import pytest

from accbot import AssetRef, FrameworkError, Tool, ToolCall, ToolContext, async_tool
from accbot.events import EventBus, TaskFailed
from accbot.lifecycle import Activation
from accbot.messages import AssetReader
from accbot.plugins.base import PluginContext
from accbot.tools import JobManager, ToolRegistry
from accbot.web import create_app


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
            assert runtime.jobs.history[selected].status == "cancelled"
            assert set(runtime.jobs.tasks) == {remaining, foreign}
            release.set()
            await context.stop_jobs([remaining], timeout=1)
            await runtime.jobs.drain()
            assert runtime.jobs.history[remaining].status == "completed"
            assert runtime.jobs.history[foreign].status == "completed"
            assert owner.inflight == other.inflight == 0
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://test"
            ) as client:
                jobs = (await client.get("/api/status")).json()["jobs"]
            assert {job["id"]: job["status"] for job in jobs} == {
                selected: "cancelled",
                remaining: "completed",
                foreign: "completed",
            }
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
        assert jobs.history[job_id].status == "cancelled"
        owner.retire()
        await bus.close()

    asyncio.run(run())


def test_job_result_is_preserved_when_notification_fails(monkeypatch):
    async def run():
        bus, owner = EventBus(), Activation("job")
        jobs = JobManager(bus)
        notifications = []

        async def unavailable(event, **kwargs):
            notifications.append(event)
            raise RuntimeError("notification failed")

        async def complete():
            return "done"

        monkeypatch.setattr(bus, "publish", unavailable)
        owner.activate()
        task_id = jobs.submit(owner, complete)
        await jobs.drain()
        assert jobs.history[task_id].status == "completed"
        assert len(notifications) == 1 and notifications[0].result == "done"
        assert not jobs.tasks and owner.inflight == 0
        owner.retire()
        await bus.close()

    asyncio.run(run())


@pytest.mark.parametrize("background", [False, True])
def test_typed_tool_context_is_injected_without_becoming_a_model_argument(background):
    class Context(ToolContext):
        requester: str

    async def run():
        bus = EventBus()
        jobs, owner = JobManager(bus), Activation("typed")
        registry = ToolRegistry(jobs)
        context = Context(requester="trusted caller")
        calls = []

        @Tool(background=background)
        async def inspect_context(value: int, ctx: Annotated[Context, "injected"]):
            assert ctx is context
            calls.append(ctx.requester)
            return value

        definition = registry.register(owner, inspect_context)
        owner.activate()
        try:
            assert set(definition.parameters["properties"]) == {"value"}
            call = ToolCall(name=definition.id, arguments={"value": 1})
            for invalid in (None, ToolContext(metadata={"requester": "forged caller"})):
                result = await registry.execute(call, invalid)
                assert not result.ok and result.error.code == "invalid_argument"
            forged = call.model_copy(update={"arguments": {"value": 1, "ctx": {"requester": "forged"}}})
            assert not (await registry.execute(forged, context)).ok
            assert not calls
            result = await registry.execute(call, context)
            assert result.ok and result.accepted == background
            await jobs.drain()
            assert calls == ["trusted caller"]
        finally:
            owner.retire()
            await jobs.close()
            await bus.close()

    asyncio.run(run())
