"""Generation-aware timers. Retired owners expire lazily at the next tick."""

import asyncio
import logging
import math
from asyncio import sleep
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from time import monotonic

from .errors import ErrorCode, FrameworkError
from .events import Event, EventBus, EventOrigin
from .lifecycle import Activation
from .types import new_id, utcnow

log = logging.getLogger(__name__)


def _valid_time(moment: datetime) -> bool:
    restored = datetime.fromtimestamp(moment.timestamp(), moment.tzinfo)
    return restored.replace(tzinfo=None) == moment.replace(tzinfo=None)


def _next_daily(at: time, now: datetime, *, after: date | None = None) -> datetime:
    day = now.astimezone(at.tzinfo).date()
    if after is not None:
        day = max(day, after + timedelta(days=1))
    while True:
        moment = datetime.combine(day, at)
        if moment.timestamp() >= now.timestamp() and _valid_time(moment):
            return moment
        day += timedelta(days=1)


class TimerManager:
    def __init__(self, events: EventBus):
        self.events = events
        self.tasks: dict[str, asyncio.Task] = {}
        self.owners: dict[str, Activation] = {}

    def schedule(
        self,
        owner: Activation,
        event: Event | Callable[[], Event],
        when: float | timedelta | time | datetime,
        *,
        repeat: bool = False,
    ) -> str:
        if owner.retired or not isinstance(repeat, bool):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "invalid timer or retired owner")
        if isinstance(when, timedelta):
            when = when.total_seconds()
        duration = None
        try:
            if isinstance(when, (int, float)) and not isinstance(when, bool):
                duration = float(when)
                if not math.isfinite(duration) or duration < 0 or (repeat and duration == 0):
                    raise ValueError("duration must be finite and nonnegative; repetition requires > 0")
            elif isinstance(when, datetime):
                if not _valid_time(when) or when.timestamp() < utcnow().timestamp():
                    raise ValueError("datetime must be a valid future moment")
            elif not isinstance(when, time):
                raise ValueError("when must be seconds, timedelta, time or datetime")
        except (ValueError, OverflowError, OSError) as exc:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
        timer_id = new_id()

        async def run():
            try:
                await owner.ready.wait()
                deadline = monotonic() + duration if duration is not None else None
                moment = None
                if duration is None:
                    moment = _next_daily(when, utcnow()) if isinstance(when, time) else when
                while owner.active:
                    if duration is not None:
                        await sleep(max(0, deadline - monotonic()))
                    else:
                        # Recheck wall time so clock corrections do not turn an appointment into a delay.
                        while owner.active and (remaining := moment.timestamp() - utcnow().timestamp()) > 0:
                            await sleep(min(remaining, 60))
                    if not owner.active:
                        break
                    emitted = event() if callable(event) else event
                    emitted = emitted.model_copy(
                        update={
                            "event_id": new_id(),
                            "observed_at": utcnow(),
                            "origin": EventOrigin(component=owner.plugin_id, activation_id=owner.id),
                        }
                    )
                    await self.events.publish(emitted, source=owner)
                    if not repeat:
                        break
                    if duration is not None:
                        # Keep the original cadence, skipping missed ticks instead of emitting a burst.
                        deadline += duration * max(1, math.floor((monotonic() - deadline) / duration) + 1)
                    else:
                        moment = _next_daily(moment.timetz(), utcnow(), after=moment.date())
            except Exception:
                log.exception("timer failed: %s", timer_id)

        task = asyncio.create_task(run(), name=f"accbot.timer.{timer_id}")
        self.tasks[timer_id] = task
        self.owners[timer_id] = owner

        def finished(_):
            self.tasks.pop(timer_id, None)
            self.owners.pop(timer_id, None)

        task.add_done_callback(finished)
        return timer_id

    def cancel(self, timer_id: str, *, owner: Activation | None = None):
        if timer_id not in self.tasks:
            return False
        if owner is not None and self.owners[timer_id] is not owner:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "timer belongs to another activation")
        if task := self.tasks.get(timer_id):
            task.cancel()
        return True

    async def close(self):
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
