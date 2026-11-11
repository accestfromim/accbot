"""Fast observation plus FIFO work per conversation; model calls never hold the observer."""

import asyncio
import json
import logging
import random
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import uuid4

from accbot import ChatMessage, ConversationRef, MessageChain, MessageReceived, MessageSent, ModelItem
from accbot.models import count_tokens

from .context import ChatContext, ContextBudgetExceeded, Entry, MemoryContext
from .events import SummarizeMemory
from .identity import MemoryKey
from .memory import Record, Store

log = logging.getLogger(__name__)
event_log = logging.getLogger("accbot.ai.chat.events")


@dataclass
class Session:
    record: Record
    last_wake: float


@dataclass
class Channel:
    conversation: ConversationRef
    store: Store
    recent: deque[Entry] = field(default_factory=lambda: deque(maxlen=15))
    seen: OrderedDict[str, None] = field(default_factory=OrderedDict)
    records: list[Record] = field(default_factory=list)
    queue: deque[Callable[[], Awaitable[None]]] = field(default_factory=deque)
    session: Session | None = None
    worker_id: str | None = None
    idle_timer: str | None = None
    idle_ticket: str | None = None
    retry_timer: str | None = None
    retry_ticket: str | None = None
    retry_delay: float = 60


class Sessions:
    def __init__(self, plugin, *, clock=time.monotonic, dice=random.random):
        self.plugin, self.ctx, self.agent = plugin, plugin.ctx, plugin.agent
        self.clock, self.dice = clock, dice
        self.channels = {}
        self.workers = asyncio.Semaphore(4)
        self.closed = False

    @property
    def config(self):
        return self.plugin.config

    def channel(self, conversation):
        key = MemoryKey.of(conversation).token
        if key not in self.channels:
            store = Store(self.ctx.data_dir, conversation)
            store.load()  # Do not silently replace malformed local memory.
            self.channels[key] = Channel(conversation, store, records=store.recover())
        channel = self.channels[key]
        channel.conversation = channel.store.conversation = conversation
        return channel

    def cancel(self, channel, reason):
        timer = getattr(channel, f"{reason}_timer")
        if timer:
            self.ctx.cancel_timer(timer)
        setattr(channel, f"{reason}_timer", None)
        setattr(channel, f"{reason}_ticket", None)

    def schedule(self, channel, reason, delay):
        self.cancel(channel, reason)
        ticket = uuid4().hex
        event = SummarizeMemory(memory_key=channel.store.key, ticket=ticket, reason=reason)
        setattr(channel, f"{reason}_ticket", ticket)
        setattr(channel, f"{reason}_timer", self.ctx.schedule(event, when=delay))

    def enqueue(self, channel, operation):
        channel.queue.append(operation)
        if channel.worker_id is None:
            channel.worker_id = self.ctx.spawn(lambda: self.work(channel))

    async def work(self, channel):
        try:
            while channel.queue:
                operation = channel.queue.popleft()
                try:
                    async with self.workers:
                        await operation()
                except Exception:
                    log.exception("AI conversation task failed: %s", channel.conversation.key)
        finally:
            channel.worker_id = None

    def observe(self, channel: Channel, message: ChatMessage, now: float) -> Entry | None:
        if message.ref.id in channel.seen:
            return None
        channel.seen[message.ref.id] = None
        if len(channel.seen) > 2048:
            channel.seen.popitem(last=False)
        entry = Entry(message, now)
        channel.recent.append(entry)
        return entry

    def observe_sent(self, event: MessageSent):
        if self.closed or event.source_plugin_id == self.ctx.plugin_id:
            return
        message = event.message
        if message is None or not self.plugin.allowed_conversation(message.conversation):
            return
        channel = self.channel(message.conversation)
        if self.observe(channel, message, self.clock()) is not None and channel.session:
            channel.session.record.waiting.append(ModelItem.from_message(message))
            channel.store.journal(channel.records)

    async def accept(self, event: MessageReceived, *, tools):
        message = event.message
        if self.closed or message.is_self or not self.plugin.allowed(message):
            event_log.debug(
                "AI message ignored: event=%s reason=closed_self_or_unconfigured model=%s",
                event.event_id,
                self.config.default_model,
            )
            return
        channel = self.channel(message.conversation)
        now = self.clock()
        busy = len(channel.recent) == 15 and now - channel.recent[0].arrived <= self.config.busy_seconds
        entry = self.observe(channel, message, now)
        if entry is None:
            event_log.debug("AI message ignored: event=%s reason=duplicate", event.event_id)
            return
        if channel.session and now - channel.session.last_wake >= self.config.idle_seconds:
            self.seal(channel)
        owner = self.plugin.is_owner(message)
        direct = message.conversation.kind == "direct"
        mention = any(p.type == "mention" and p.user == event.connection.bot for p in message.content)
        explicit = owner or (mention and self.plugin.may_mention(message))
        if direct and not explicit:
            event_log.debug("AI message ignored: event=%s reason=private_requires_mention", event.event_id)
            return
        if channel.session:
            record = channel.session.record
            record.waiting.append(ModelItem.from_message(message))
            channel.store.journal(channel.records)
        awake = explicit
        chance = None
        if not direct and not awake:
            active = channel.session is not None
            chance = (
                (self.config.active_busy_probability if busy else self.config.active_probability)
                if active
                else (self.config.busy_probability if busy else self.config.initial_probability)
            )
            awake = (
                not active or now - channel.session.last_wake >= self.config.cooldown_seconds
            ) and self.dice() < chance
        event_log.debug(
            "AI wake decision: event=%s conversation=%s active=%s owner=%s mention=%s explicit=%s "
            "probability=%s awake=%s",
            event.event_id,
            message.conversation.key,
            channel.session is not None,
            owner,
            mention,
            explicit,
            chance,
            awake,
        )
        if not awake:
            return
        # Bound queued model requests without discarding already accepted transcripts.
        if len(channel.queue) >= 128:
            log.warning("AI queue full: %s", message.conversation.key)
            return
        if channel.session is None:
            record = Record(id=uuid4().hex, model=self.config.default_model, owner=owner)
            record.waiting = [
                ModelItem.from_message(e.message)
                for e in channel.recent
                if not direct or e is entry or e.message.is_self
            ]
            channel.session = Session(record, now)
            if not (direct and not owner):
                channel.records.append(record)
        session = channel.session
        session.last_wake = now
        trace = session.record.waiting
        session.record.waiting = []
        session.record.turns.append(trace)
        snapshot = {e.message.ref.id: e.images for e in channel.recent if e.images}
        context = ChatContext(
            conversation=message.conversation,
            correlation_id=event.event_id,
            message=message,
            owner=owner,
            images=snapshot,
            bot=event.connection.bot,
        )
        persistent = not (direct and not owner)
        channel.store.journal(channel.records)
        self.enqueue(channel, lambda: self.turn(channel, session.record, trace, context, persistent, tools))
        if not persistent:
            channel.session = None
        return channel if persistent else None

    async def turn(self, channel, record, trace, context, persistent, tools):
        if self.closed:
            return
        await self.flush(channel)
        index = next(i for i, turn in enumerate(record.turns) if turn is trace)

        def checkpoint():
            if persistent:
                channel.store.journal(channel.records)

        try:
            if (
                persistent
                and index
                and self.budget_reached(channel, record, record.turns[: index + 1], context, tools)
            ):
                await self.request_compaction(channel, record, index)
                index = 0
            answer = await self.agent.run(
                record.model,
                channel.store.load() if persistent else "",
                record.turns[:index],
                trace,
                tools,
                context,
                self.config,
                checkpoint,
                lambda: not self.closed and self.ctx.owner.active,
            )
            if answer and not self.closed and self.ctx.owner.active:
                await self.ctx.messages.send(context.conversation, MessageChain.text(answer))
        except Exception:
            log.exception("AI turn failed: %s", channel.conversation.key)
            if not self.closed and self.ctx.owner.active:
                try:
                    await self.ctx.messages.send(
                        context.conversation, MessageChain.text("这轮对话处理失败了，请稍后再试。")
                    )
                except Exception:
                    log.exception("could not send AI error notice")
            return
        finally:
            checkpoint()
        if persistent and not self.closed and self.ctx.owner.active:
            try:
                if self.budget_reached(channel, record, record.turns[: index + 1], context, tools):
                    await self.request_compaction(channel, record, index + 1)
            except Exception:
                log.exception(
                    "AI budget memory update failed; history retained: %s", channel.conversation.key
                )

    def budget_reached(self, channel, record, turns, context, tools):
        items = self.agent.context(channel.store.load(), record.owner, context.bot, turns, [])
        budget = self.agent.budget(
            self.config,
            self.config.owner_prompt_id if record.owner else self.config.chat_prompt_id,
            self.config.persona_id,
            tools,
        )
        return count_tokens(items) >= budget - self.config.context_budget // 20

    async def request_compaction(self, channel, record, count):
        last = record.turns[count - 1]
        event = SummarizeMemory(
            memory_key=channel.store.key, ticket=record.id, reason="budget", turn_count=count
        )
        receipt = await self.ctx.publish(event)
        await receipt.wait()
        if any(turn is last for turn in record.turns):
            raise ValueError("记忆整理失败，未整理的历史已保留")

    async def compact(self, channel, event):
        record = next((r for r in channel.records if r.id == event.ticket), None)
        if record is None or not 0 < event.turn_count <= len(record.turns):
            return
        await self.fold(channel, record, event.turn_count, event.event_id)

    async def fold(self, channel, record, count, correlation_id):
        """Commit ordered prefixes, shrinking a batch when its summary tool loop cannot fit."""
        remaining = size = count
        while remaining:
            snapshot = record.model_copy(update={"turns": record.turns[:size], "waiting": []})
            try:
                memory = await self.agent.summarize(
                    snapshot,
                    channel.store.load(),
                    self.plugin.memory_tools(),
                    self.config,
                    MemoryContext(conversation=channel.conversation, correlation_id=correlation_id),
                    lambda: not self.closed and self.ctx.owner.active,
                )
            except ContextBudgetExceeded:
                if size == 1:
                    raise  # An indivisible turn or mandatory background cannot be silently discarded.
                size = max(1, size // 2)
                continue
            # Only the final prefix of a sealed record can commit the entire record ID.
            commit_id = record.id if record.sealed and size == len(record.turns) else f"{record.id}:budget"
            channel.store.save(memory, commit_id)
            del record.turns[:size]
            record.id = uuid4().hex
            channel.store.journal(channel.records)
            remaining -= size
            size = remaining

    def seal(self, channel):
        self.cancel(channel, "idle")
        record = channel.session.record
        channel.session = None
        if record.waiting:
            record.turns.append(record.waiting)
            record.waiting = []

        # Mark sealed in the FIFO worker after all accepted turns finish.
        async def finish():
            record.sealed = True
            channel.store.journal(channel.records)
            if not self.closed:
                await self.flush(channel)

        self.enqueue(channel, finish)

    async def flush(self, channel):
        while channel.records and channel.records[0].sealed and not self.closed:
            record = channel.records[0]
            try:
                if record.turns and not channel.store.committed(record.id):
                    await self.fold(channel, record, len(record.turns), record.id)
                channel.records.pop(0)
                channel.store.journal(channel.records)
                self.cancel(channel, "retry")
                channel.retry_delay = 60
            except Exception:
                self.schedule(channel, "retry", channel.retry_delay)
                channel.retry_delay = min(channel.retry_delay * 2, 900)
                log.exception("AI memory update retained for retry: %s", channel.conversation.key)
                break

    async def summarize(self, event: SummarizeMemory):
        channel = self.channels.get(event.memory_key.token)
        if self.closed or channel is None:
            return
        if event.reason == "budget":
            await self.compact(channel, event)
            return
        if event.ticket != getattr(channel, f"{event.reason}_ticket"):
            return
        self.cancel(channel, event.reason)
        if event.reason == "retry":
            self.enqueue(channel, lambda: self.flush(channel))
        elif channel.session:
            remaining = self.config.idle_seconds - (self.clock() - channel.session.last_wake)
            if remaining > 0:
                self.schedule(channel, "idle", remaining)
            else:
                self.seal(channel)

    def recovery_route(self, conversation):
        rules = self.config.groups if conversation.kind == "group" else [self.config.owner]
        for rule in rules:
            if rule is None:
                continue
            identity = rule.group_id if conversation.kind == "group" else rule.user_id
            connection = self.ctx.messages.get_connection(rule.connection_id)
            if identity != conversation.id or connection is None:
                continue
            candidate = conversation.model_copy(
                update={
                    "connection_id": connection.id,
                    "platform": connection.platform,
                    "scope": connection.scope,
                    "group": conversation.group.model_copy(update={"scope": connection.scope})
                    if conversation.group
                    else None,
                }
            )
            if MemoryKey.of(candidate) == MemoryKey.of(conversation) and self.plugin.allowed_conversation(
                candidate
            ):
                return candidate
        return conversation if self.plugin.allowed_conversation(conversation) else None

    async def recover(self):
        for path in (self.ctx.data_dir / "memory").glob("*/pending.json"):
            if not path.exists():  # A previous iteration may already have migrated this directory.
                continue
            value = json.loads(path.read_text("utf-8"))
            conversation = self.recovery_route(ConversationRef.model_validate(value["conversation"]))
            if conversation is not None:
                channel = self.channel(conversation)
                self.schedule(channel, "retry", 0)  # Timer waits for activation to commit.

    async def close(self):
        self.closed = True
        # Journal all queued inputs before cancelling work; do not replay accepted tools on reload.
        for channel in self.channels.values():
            self.cancel(channel, "idle")
            self.cancel(channel, "retry")
            channel.store.journal(channel.records)
        await self.ctx.stop_jobs((c.worker_id for c in self.channels.values() if c.worker_id), timeout=5)
        for channel in self.channels.values():
            channel.store.journal(channel.records)
