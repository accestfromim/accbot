import inspect
import json
import logging
import mimetypes

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...errors import ErrorCode, FrameworkError
from ...plugins.settings import get_page

log = logging.getLogger(__name__)


class SettingsUpdate(BaseModel):
    revision: str = Field(min_length=64, max_length=64)
    values: dict


async def call_settings(handler, method, *args):
    result = getattr(handler, method)(*args)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise FrameworkError(ErrorCode.PLUGIN, "插件配置接口必须返回 JSON 对象")
    json.dumps(result, allow_nan=False)
    return result


def create_router(runtime):
    router = APIRouter(prefix="/api/plugins")

    @router.get("/{plugin_id}/ui")
    async def page(plugin_id: str):
        async with runtime.management_lock:
            view = get_page(runtime.plugins, plugin_id)
            return {
                "html": view.bundle.component(),
                "revision": view.revision,
                "activation_id": view.activation_id,
                "has_script": "index.js" in view.bundle.files,
                "has_style": "style.css" in view.bundle.files,
                "has_settings": "settings.py" in view.sources,
            }

    @router.get("/{plugin_id}/ui/assets/{revision}/{path:path}")
    async def asset(plugin_id: str, revision: str, path: str):
        async with runtime.management_lock:
            view = get_page(runtime.plugins, plugin_id)
            view.verify(revision)
            data = view.bundle.read(path)
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            return Response(
                data,
                media_type=mime,
                headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
            )

    @router.get("/{plugin_id}/settings")
    async def read(plugin_id: str, revision: str = Query(min_length=64, max_length=64)):
        async with runtime.management_lock:
            view = get_page(runtime.plugins, plugin_id)
            view.verify(revision)
            with view.settings(runtime.plugins) as handler:
                return {"values": await call_settings(handler, "read")}

    @router.put("/{plugin_id}/settings")
    async def write(plugin_id: str, body: SettingsUpdate):
        async with runtime.management_lock:
            view = get_page(runtime.plugins, plugin_id)
            view.verify(body.revision)
            with view.settings(runtime.plugins) as handler:
                values = await call_settings(handler, "write", body.values)
            warning, notified = None, False
            entry = runtime.plugins.instances.get(plugin_id)
            if entry:
                instance, owner = entry
                owner.acquire()
                try:
                    await instance.on_settings_changed(values)
                    notified = True
                except Exception:
                    log.exception("saved plugin settings could not be applied: %s", plugin_id)
                    warning = "配置已保存，但运行中的插件未能更新；请重载插件。"
                finally:
                    owner.release()
            return {"values": values, "active_notified": notified, "warning": warning}

    return router
