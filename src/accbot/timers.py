"""Generation-aware timers. Retired owners expire lazily at the next tick."""

import asyncio
import logging
from collections.abc import Callable

from .errors import ErrorCode, FrameworkError
from .events import Event, EventBus, EventOrigin
from .lifecycle import Activation
from .types import new_id, utcnow

log = logging.getLogger(__name__)


class TimerManager:
    def __init__(self, events: EventBus):
        self.events = events
        self.tasks: dict[str, asyncio.Task] = {}
        self.owners: dict[str, Activation] = {}

    def schedule(
        self,
        owner: Activation,
        event: Event | Callable[[], Event],
        *,
        delay: float,
        interval: float | None = None,
    ) -> str:
        if owner.retired or delay < 0 or (interval is not None and interval <= 0):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "invalid timer or retired owner")
        timer_id = new_id()

        async def run():
            try:
                await owner.ready.wait()
                await asyncio.sleep(delay)
                while owner.active:
                    emitted = event() if callable(event) else event
                    emitted = emitted.model_copy(
                        update={
                            "event_id": new_id(),
                            "observed_at": utcnow(),
                            "origin": EventOrigin(component=owner.plugin_id, activation_id=owner.id),
                        }
                    )
                    await self.events.publish(emitted, source=owner)
                    if interval is None:
                        break
                    await asyncio.sleep(interval)
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
