"""Declarative tools, native schemas, execution and detached jobs."""

import asyncio
import functools
import hashlib
import inspect
import logging
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

from pydantic import Field, ValidationError, create_model

from .errors import ErrorCode, ErrorInfo, FrameworkError, error_info
from .events import EventBus, EventOrigin, TaskCompleted, TaskFailed
from .lifecycle import Activation
from .types import ConversationRef, FrozenModel, new_id, thaw

log = logging.getLogger(__name__)


class ToolDefinition(FrozenModel):
    id: str
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    background: bool = False
    system_prompt: str = ""
    activation_id: str | None = None

    @property
    def wire_name(self) -> str:
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", self.id)[:48]
        digest = hashlib.sha256(self.id.encode()).hexdigest()[:10]
        return f"{stem}_{digest}"

    def to_schema(self, protocol: Literal["chat", "responses"]) -> dict[str, Any]:
        """Serialize a tool declaration at the provider boundary."""
        function = {
            "name": self.wire_name,
            "description": self.description,
            "parameters": thaw(self.parameters),
            "strict": False,
        }
        if protocol == "chat":
            return {"type": "function", "function": function}
        if protocol == "responses":
            return {"type": "function", **function}
        raise FrameworkError(ErrorCode.UNSUPPORTED, f"unsupported tool protocol: {protocol}")


class ToolCall(FrozenModel):
    id: str = Field(default_factory=new_id)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(FrozenModel):
    call_id: str
    ok: bool
    value: Any = None
    error: ErrorInfo | None = None
    task_id: str | None = None
    accepted: bool = False

    @classmethod
    def failure(cls, call_id: str, message: str, *, code: ErrorCode = ErrorCode.INVALID_ARGUMENT):
        return cls(call_id=call_id, ok=False, error=ErrorInfo(code=code, message=message))


class ToolContext(FrozenModel):
    conversation: ConversationRef | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class _ToolOptions:
    name: str
    description: str
    timeout: float
    background: bool
    prompt_file: str | None


def Tool(func=None, *, name=None, description=None, timeout=60.0, background=False, prompt_file=None):
    def decorate(target):
        target.__accbot_tool__ = _ToolOptions(
            name=name or target.__name__,
            description=description or inspect.getdoc(target) or "",
            timeout=timeout,
            background=background,
            prompt_file=prompt_file,
        )
        return target

    return decorate(func) if func else decorate


def async_tool(func=None, **kwargs):
    return Tool(func, background=True, **kwargs)


@dataclass
class JobInfo:
    id: str
    owner: str
    activation_id: str
    status: Literal["running", "completed", "failed", "cancelled"] = "running"


class JobManager:
    def __init__(self, events: EventBus, history_limit: int = 1000):
        self.events = events
        self.tasks: dict[str, asyncio.Task] = {}
        self.history: OrderedDict[str, JobInfo] = OrderedDict()
        self.history_limit = history_limit

    def submit(self, owner: Activation, operation: Callable, *, correlation_id=None) -> str:
        accepted_active = owner.active
        owner.acquire(allow_staging=True)
        task_id = new_id()
        info = self.history[task_id] = JobInfo(task_id, owner.plugin_id, owner.id)

        async def run():
            await owner.ready.wait()
            if not accepted_active and owner.retired:
                raise asyncio.CancelledError
            try:
                result = await operation()
            except Exception as exc:
                info.status = "failed"
                event = TaskFailed(
                    task_id=task_id,
                    owner_plugin_id=owner.plugin_id,
                    activation_id=owner.id,
                    error=error_info(exc, task_id),
                    correlation_id=correlation_id,
                    origin=EventOrigin(component=owner.plugin_id, activation_id=owner.id),
                )
            else:
                info.status = "completed"
                event = TaskCompleted(
                    task_id=task_id,
                    owner_plugin_id=owner.plugin_id,
                    activation_id=owner.id,
                    result=result,
                    correlation_id=correlation_id,
                    origin=EventOrigin(component=owner.plugin_id, activation_id=owner.id),
                )
            try:
                await self.events.publish(event, source=owner)
            except Exception:
                log.exception("could not publish background task result: %s", task_id)

        def finished(task):
            # A task can be cancelled before its coroutine enters any try/finally.
            if task.cancelled():
                info.status = "cancelled"
            owner.release()
            self.tasks.pop(task_id, None)
            for key in tuple(self.history):
                if len(self.history) <= self.history_limit:
                    break
                if key not in self.tasks:
                    self.history.pop(key)

        task = self.tasks[task_id] = asyncio.create_task(run(), name=f"accbot.job.{task_id}")
        task.add_done_callback(finished)
        return task_id

    async def stop(self, owner: Activation, task_ids, *, timeout=0):
        if timeout < 0:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "job stop timeout must not be negative")
        selected = set(task_ids)
        tasks = [
            task
            for key, task in self.tasks.items()
            if key in selected and self.history[key].activation_id == owner.id
        ]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)

    async def close(self):
        # Process shutdown only. Plugin deactivation never calls this method.
        for task in tuple(self.tasks.values()):
            task.cancel()
        await self.drain()


class _ToolEntry:
    def __init__(self, owner, func, options: _ToolOptions, system_prompt=""):
        self.owner, self.func, self.timeout = owner, func, options.timeout
        signature = inspect.signature(func)
        hints = get_type_hints(func, include_extras=True)
        fields, self.context_parameter = {}, None
        for name, param in signature.parameters.items():
            if param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
                raise FrameworkError(ErrorCode.CONFIGURATION, "tools require named parameters")
            annotation = hints.get(name, Any)
            parameter_type = get_args(annotation)[0] if get_origin(annotation) is Annotated else annotation
            if isinstance(parameter_type, type) and issubclass(parameter_type, ToolContext):
                if self.context_parameter is not None:
                    raise FrameworkError(ErrorCode.CONFIGURATION, "a tool can declare only one ToolContext")
                self.context_parameter = name
                self.context_type = parameter_type
                continue
            fields[name] = (annotation, ... if param.default is inspect.Parameter.empty else param.default)
        self.arguments = create_model(f"ToolArguments_{new_id()}", __config__={"extra": "forbid"}, **fields)
        self.definition = ToolDefinition(
            id=f"{owner.plugin_id}.{options.name}",
            name=options.name,
            description=options.description,
            parameters=self.arguments.model_json_schema(),
            background=options.background,
            system_prompt=system_prompt,
            activation_id=owner.id,
        )

    async def invoke(self, arguments):
        async with asyncio.timeout(self.timeout):
            if inspect.iscoroutinefunction(self.func):
                return await self.func(**arguments)
            self.owner.retain_inflight()
            worker = asyncio.create_task(asyncio.to_thread(functools.partial(self.func, **arguments)))

            def finished(task):
                self.owner.release()
                if not task.cancelled():
                    task.exception()

            worker.add_done_callback(finished)
            result = await asyncio.shield(worker)
            return await result if inspect.isawaitable(result) else result


class ToolRegistry:
    def __init__(self, jobs: JobManager):
        self.jobs = jobs
        self._entries: dict[str, _ToolEntry] = {}

    def register(self, owner: Activation, func: Callable, *, prompt_bundle=None) -> ToolDefinition:
        if owner.retired:
            raise FrameworkError(ErrorCode.INACTIVE, "cannot register a tool for a retired plugin")
        options = getattr(func, "__accbot_tool__", None)
        if options is None:
            raise FrameworkError(ErrorCode.CONFIGURATION, "tool requires @Tool or @async_tool")
        content = ""
        filename = options.prompt_file
        if filename is not None:
            if prompt_bundle is None:
                raise FrameworkError(
                    ErrorCode.CONFIGURATION, "file-backed tool prompts require a plugin context"
                )
            content = prompt_bundle.tool(filename)
        entry = _ToolEntry(owner, func, options, content)
        key = entry.definition.id
        if key in self._entries:
            raise FrameworkError(ErrorCode.CONFLICT, f"tool already registered: {key}")
        self._entries[key] = entry
        owner.own(lambda: self._entries.pop(key, None))
        return entry.definition

    def list(self) -> tuple[ToolDefinition, ...]:
        return tuple(e.definition for e in self._entries.values() if e.owner.active)

    def prompt_for(self, tool: ToolDefinition) -> str:
        if tool.activation_id is None:
            return tool.system_prompt
        entry = self._entries.get(tool.id)
        if entry is None or not entry.owner.active or entry.owner.id != tool.activation_id:
            raise FrameworkError(ErrorCode.INACTIVE, f"工具声明已失效，请重新获取工具列表：{tool.id}")
        return entry.definition.system_prompt

    async def execute(self, call: ToolCall, context: ToolContext | None = None) -> ToolResult:
        entry = self._entries.get(call.name)
        if entry is None or not entry.owner.active:
            return ToolResult.failure(call.id, f"tool not found: {call.name}", code=ErrorCode.NOT_FOUND)
        try:
            validated = entry.arguments.model_validate(dict(call.arguments), strict=True)
            args = {name: getattr(validated, name) for name in type(validated).model_fields}
            if entry.context_parameter:
                context = context or ToolContext()
                if not isinstance(context, entry.context_type):
                    return ToolResult.failure(call.id, "tool context does not match the required type")
                args[entry.context_parameter] = context
            if entry.definition.background:
                task_id = self.jobs.submit(
                    entry.owner,
                    lambda: entry.invoke(args),
                    correlation_id=context.correlation_id if context else None,
                )
                return ToolResult(call_id=call.id, ok=True, task_id=task_id, accepted=True)
            entry.owner.acquire()
            try:
                value = await entry.invoke(args)
                return ToolResult(call_id=call.id, ok=True, value=value)
            finally:
                entry.owner.release()
        except ValidationError:
            return ToolResult.failure(call.id, "tool arguments do not match schema")
        except Exception as exc:
            return ToolResult(call_id=call.id, ok=False, error=error_info(exc, call.id))
