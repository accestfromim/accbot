import asyncio
import contextvars
import inspect
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from ..errors import ErrorCode, ErrorInfo, FrameworkError, error_info
from ..lifecycle import Activation
from ..types import new_id
from .filters import Filter
from .models import BUILTIN_EVENTS, Event

log = logging.getLogger(__name__)
_in_handler = contextvars.ContextVar("accbot_in_handler", default=False)


@dataclass
class EventReceipt:
    event_id: str
    _future: asyncio.Future

    async def wait(self) -> tuple[ErrorInfo, ...]:
        return await asyncio.shield(self._future)


@dataclass
class Subscription:
    id: str
    event_class: type[Event]
    handler: Callable
    where: Filter | None
    owner: Activation | None
    active: bool = True


@dataclass
class _Envelope:
    event: Event
    future: asyncio.Future
    subscriptions: tuple[Subscription, ...]
    event_owner: Activation | None
    source: Activation | None
    remaining: int = 0
    errors: list[ErrorInfo] = field(default_factory=list)


class EventBus:
    def __init__(self, capacity: int = 1024):
        self._types: dict[str, tuple[type[Event], Activation | None]] = {
            t.event_type: (t, None) for t in BUILTIN_EVENTS
        }
        self._subscriptions: dict[str, Subscription] = {}
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=capacity)
        self._slots = asyncio.BoundedSemaphore(capacity)
        self._pending: set[asyncio.Future] = set()
        self._mailboxes: dict[tuple, deque] = {}
        self._workers: set[asyncio.Task] = set()
        self._dispatcher: asyncio.Task | None = None
        self._closed = False

    def register(self, event_class: type[Event], owner: Activation):
        if owner.retired:
            raise FrameworkError(ErrorCode.INACTIVE, "cannot register an event for a retired plugin")
        if not isinstance(event_class, type) or not issubclass(event_class, Event):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "event type must subclass Event")
        key = event_class.event_type
        if not isinstance(key, str) or "." not in key or not all(key.split(".")):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "custom event requires a namespaced event_type")
        if key in self._types:
            raise FrameworkError(ErrorCode.CONFLICT, f"event type already registered: {key}")
        self._types[key] = (event_class, owner)
        owner.own(lambda: self._types.pop(key, None))

    def decode(self, payload: dict) -> Event:
        data = dict(payload)
        key, version = data.pop("event_type"), data.pop("schema_version", 1)
        entry = self._types.get(key)
        if entry is None or (entry[1] is not None and not entry[1].active):
            raise FrameworkError(ErrorCode.UNKNOWN_EVENT, f"unknown event: {key}")
        if version != entry[0].schema_version:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "unsupported event schema version")
        return entry[0].model_validate(data)

    def subscribe(self, event_class, handler, *, where=None, owner=None) -> Subscription:
        if owner is not None and owner.retired:
            raise FrameworkError(ErrorCode.INACTIVE, "cannot subscribe for a retired plugin")
        if not isinstance(event_class, type) or not issubclass(event_class, Event):
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "subscription requires an Event subclass")
        sub = Subscription(new_id(), event_class, handler, where, owner)
        self._subscriptions[sub.id] = sub
        if owner:
            owner.own(lambda: self.unsubscribe(sub))
        return sub

    def unsubscribe(self, sub: Subscription):
        sub.active = False
        self._subscriptions.pop(sub.id, None)

    async def start(self):
        if self._closed:
            raise FrameworkError(ErrorCode.CLOSED, "event bus is closed")
        if self._dispatcher is None:
            self._dispatcher = asyncio.create_task(self._dispatch(), name="accbot.events")

    async def publish(self, event: Event, *, source: Activation | None = None) -> EventReceipt:
        if self._closed:
            raise FrameworkError(ErrorCode.CLOSED, "event bus is closed")
        entry = self._types.get(event.event_type)
        if entry is None or entry[0] is not type(event) or (entry[1] and not entry[1].active):
            raise FrameworkError(ErrorCode.UNKNOWN_EVENT, f"unknown or retired event: {event.event_type}")
        if _in_handler.get() and self._slots.locked():
            raise FrameworkError(ErrorCode.BACKPRESSURE, "nested publish exceeds event capacity")
        await self._slots.acquire()
        if self._closed:
            self._slots.release()
            raise FrameworkError(ErrorCode.CLOSED, "event bus is closed")
        entry = self._types.get(event.event_type)
        if entry is None or entry[0] is not type(event) or (entry[1] and not entry[1].active):
            self._slots.release()
            raise FrameworkError(ErrorCode.UNKNOWN_EVENT, f"event retired while waiting: {event.event_type}")
        await self.start()
        future = asyncio.get_running_loop().create_future()
        self._pending.add(future)
        subscriptions = tuple(
            s for s in self._subscriptions.values() if s.active and (s.owner is None or s.owner.active)
        )
        self._queue.put_nowait(_Envelope(event, future, subscriptions, entry[1], source))
        return EventReceipt(event.event_id, future)

    @staticmethod
    def _can_deliver(envelope, sub):
        return (
            sub.active
            and (sub.owner is None or sub.owner.active)
            and (envelope.event_owner is None or envelope.event_owner.active)
            # Late background results remain visible to framework diagnostics, not a new plugin instance.
            and (envelope.source is None or envelope.source.active or sub.owner is None)
        )

    async def _dispatch(self):
        while True:
            envelope = await self._queue.get()
            try:
                try:
                    dispatch_key = envelope.event.dispatch_key
                    hash(dispatch_key)
                except Exception as exc:
                    envelope.errors.append(error_info(exc, envelope.event.event_id))
                    self._complete(envelope)
                    log.exception("invalid event dispatch key")
                    continue
                matches = []
                for sub in envelope.subscriptions:
                    try:
                        if (
                            self._can_deliver(envelope, sub)
                            and isinstance(envelope.event, sub.event_class)
                            and (sub.where is None or sub.where(envelope.event))
                        ):
                            matches.append(sub)
                            log.debug(
                                "Event matched: event=%s subscription=%s plugin=%s handler=%s",
                                envelope.event.event_id,
                                sub.id,
                                sub.owner.plugin_id if sub.owner else "framework",
                                getattr(sub.handler, "__qualname__", type(sub.handler).__name__),
                            )
                    except Exception as exc:
                        envelope.errors.append(error_info(exc, envelope.event.event_id))
                        log.exception("event filter failed")
                envelope.remaining = len(matches)
                log.debug(
                    "Event dispatched: event=%s type=%s matches=%s",
                    envelope.event.event_id,
                    envelope.event.event_type,
                    len(matches),
                )
                if not matches:
                    self._complete(envelope)
                for sub in matches:
                    key = (sub.id, dispatch_key)
                    if key not in self._mailboxes:
                        self._mailboxes[key] = deque()
                        task = asyncio.create_task(self._consume(key))
                        self._workers.add(task)
                        task.add_done_callback(self._workers.discard)
                    self._mailboxes[key].append((sub, envelope))
            finally:
                self._queue.task_done()

    async def _consume(self, key):
        queue = self._mailboxes[key]
        try:
            while queue:
                sub, envelope = queue.popleft()
                owner = sub.owner
                token = _in_handler.set(True)
                acquired = False
                try:
                    if self._can_deliver(envelope, sub):
                        if owner:
                            owner.acquire()
                            acquired = True
                        result = sub.handler(envelope.event)
                        if inspect.isawaitable(result):
                            await result
                except Exception as exc:
                    envelope.errors.append(error_info(exc, envelope.event.event_id))
                    log.exception("event handler failed: %s", sub.id)
                finally:
                    _in_handler.reset(token)
                    if acquired:
                        owner.release()
                    envelope.remaining -= 1
                    if not envelope.remaining:
                        self._complete(envelope)
        finally:
            self._mailboxes.pop(key, None)

    def _complete(self, envelope):
        if not envelope.future.done():
            envelope.future.set_result(tuple(envelope.errors))
        self._pending.discard(envelope.future)
        self._slots.release()

    async def drain(self):
        while self._pending:
            await asyncio.gather(*tuple(self._pending))

    async def close(self):
        self._closed = True
        await self.drain()
        if self._dispatcher:
            self._dispatcher.cancel()
            try:
                await self._dispatcher
            except asyncio.CancelledError:
                pass
