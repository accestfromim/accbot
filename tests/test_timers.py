import asyncio
import time as system_time
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import accbot.timers as timer_module
from accbot import ErrorCode, Event, FrameworkError
from accbot.events import EventBus
from accbot.lifecycle import Activation
from accbot.timers import TimerManager, _next_daily


@dataclass
class Clock:
    now: datetime = datetime(2030, 1, 1, tzinfo=UTC)
    elapsed: float = 0

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.elapsed += seconds

    async def sleep(self, seconds):
        assert seconds >= 0
        self.advance(seconds)
        await asyncio.sleep(0)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(timer_module, "utcnow", lambda: clock.now)
    monkeypatch.setattr(timer_module, "monotonic", lambda: clock.elapsed)
    monkeypatch.setattr(timer_module, "sleep", clock.sleep)
    return clock


async def collect(clock, when, *, repeat=False, event=None, count=3, publish_delay=0):
    owner = Activation("timer_test")
    owner.activate()
    emitted = []

    async def publish(event, *, source):
        assert source is owner
        assert event.origin.activation_id == owner.id
        emitted.append(event)
        clock.advance(publish_delay)
        if repeat and len(emitted) == count:
            owner.retire()

    bus = AsyncMock(spec=EventBus)
    bus.publish.side_effect = publish
    timers = TimerManager(bus)
    timer = timers.schedule(owner, event or Event(), when, repeat=repeat)
    try:
        await asyncio.wait_for(timers.tasks[timer], 1)
        assert not timers.tasks and not timers.owners
        return emitted
    finally:
        await timers.close()


@pytest.mark.parametrize(
    ("when", "seconds"),
    [
        (0, 0),
        (30, 30),
        (timedelta(seconds=30), 30),
        (time(0, 0, 30, tzinfo=UTC), 30),
        (datetime(2030, 1, 2, tzinfo=UTC), 86400),
    ],
)
def test_single_publication_is_default(clock, when, seconds):
    start = clock.now
    emitted = asyncio.run(collect(clock, when))
    assert len(emitted) == 1
    assert emitted[0].observed_at == start + timedelta(seconds=seconds)


@pytest.mark.parametrize(
    ("when", "offsets"),
    [
        (30, [30, 60, 90]),
        (timedelta(seconds=30), [30, 60, 90]),
        (time(0, 1, tzinfo=UTC), [60, 86460, 172860]),
        (datetime(2030, 1, 2, 0, 1, tzinfo=UTC), [86460, 172860, 259260]),
    ],
)
def test_repetition_uses_interval_or_daily_clock_time(clock, when, offsets):
    start, template = clock.now, Event()
    emitted = asyncio.run(collect(clock, when, repeat=True, event=template))
    assert [event.observed_at for event in emitted] == [start + timedelta(seconds=s) for s in offsets]
    assert len({template.event_id, *(event.event_id for event in emitted)}) == 4
    assert template.origin.activation_id is None


def test_event_factory_and_missed_intervals(clock):
    calls = []

    def factory():
        calls.append(clock.elapsed)
        return Event(correlation_id=str(len(calls)))

    emitted = asyncio.run(collect(clock, 30, repeat=True, event=factory, publish_delay=75))
    assert calls == [30, 120, 210]
    assert [event.correlation_id for event in emitted] == ["1", "2", "3"]


@pytest.mark.parametrize("when", [30, time(0, 1, tzinfo=UTC)])
def test_retirement_while_waiting_prevents_publication(clock, monkeypatch, when):
    async def run():
        owner = Activation("retired_timer")
        owner.activate()
        bus = AsyncMock(spec=EventBus)
        timers = TimerManager(bus)

        async def sleep(seconds):
            owner.retire()
            await clock.sleep(seconds)

        monkeypatch.setattr(timer_module, "sleep", sleep)
        timer = timers.schedule(owner, Event(), when, repeat=True)
        await asyncio.wait_for(timers.tasks[timer], 1)
        bus.publish.assert_not_awaited()
        assert not timers.tasks and not timers.owners

    asyncio.run(run())


@pytest.mark.parametrize(
    ("when", "repeat"),
    [
        (-1, False),
        (timedelta(seconds=-1), False),
        (0, True),
        (float("nan"), False),
        (float("inf"), True),
        (True, False),
        ("12:00", False),
        (None, False),
        (1, "true"),
        (datetime(2029, 12, 31, tzinfo=UTC), False),
        (datetime(2030, 3, 10, 2, 30, tzinfo=ZoneInfo("America/New_York")), True),
    ],
)
def test_invalid_schedule_fails_before_registering_task(clock, when, repeat):
    timers = TimerManager(EventBus())
    with pytest.raises(FrameworkError) as error:
        timers.schedule(Activation("invalid_timer"), Event(), when, repeat=repeat)
    assert error.value.info.code == ErrorCode.INVALID_ARGUMENT
    assert not timers.tasks and not timers.owners


@pytest.mark.parametrize("absolute", [False, True])
def test_staging_defers_duration_but_preserves_absolute_deadline(clock, absolute):
    async def run():
        owner = Activation("staged_timer")
        bus = AsyncMock(spec=EventBus)
        timers = TimerManager(bus)
        when = clock.now + timedelta(seconds=30) if absolute else 30
        timer = timers.schedule(owner, Event(), when)
        await asyncio.sleep(0)
        clock.advance(60)
        bus.publish.assert_not_awaited()
        owner.activate()
        await asyncio.wait_for(timers.tasks[timer], 1)
        bus.publish.assert_awaited_once()
        assert clock.elapsed == (60 if absolute else 90)
        assert not timers.tasks and not timers.owners

    asyncio.run(run())


def test_naive_clock_time_and_datetime_use_server_timezone(clock, monkeypatch):
    if not hasattr(system_time, "tzset"):
        pytest.skip("requires POSIX timezone configuration")
    with monkeypatch.context() as patch:
        patch.setenv("TZ", "UTC-8")
        system_time.tzset()
        try:
            for when in [time(8, 1), datetime(2030, 1, 1, 8, 1)]:
                clock.now = datetime(2030, 1, 1, tzinfo=UTC)
                emitted = asyncio.run(collect(clock, when))
                assert emitted[0].observed_at == datetime(2030, 1, 1, 0, 1, tzinfo=UTC)
        finally:
            patch.undo()
            system_time.tzset()


def test_next_clock_time_rolls_over_year_and_uses_explicit_timezone():
    at = time(8, tzinfo=ZoneInfo("Asia/Shanghai"))
    now = datetime(2030, 12, 31, 0, 1, tzinfo=UTC)
    assert _next_daily(at, now) == datetime(2031, 1, 1, 8, tzinfo=at.tzinfo)


@pytest.mark.parametrize(("month", "day", "hours"), [(3, 9, 23), (11, 2, 25)])
def test_daily_time_follows_calendar_across_dst(month, day, hours):
    at = time(9, tzinfo=ZoneInfo("America/New_York"))
    first = datetime(2030, month, day, 9, tzinfo=at.tzinfo)
    following = _next_daily(at, first, after=first.date())
    assert following.hour == 9
    assert following.timestamp() - first.timestamp() == hours * 3600


def test_daily_time_skips_dst_gap_and_does_not_repeat_on_overlap():
    zone = ZoneInfo("America/New_York")
    gap = _next_daily(time(2, 30, tzinfo=zone), datetime(2030, 3, 10, tzinfo=zone))
    assert gap == datetime(2030, 3, 11, 2, 30, tzinfo=zone)
    first = _next_daily(time(1, 30, tzinfo=zone, fold=1), datetime(2030, 11, 3, tzinfo=zone))
    assert first.fold == 1
    following = _next_daily(first.timetz(), first, after=first.date())
    assert following.date() == first.date() + timedelta(days=1)
