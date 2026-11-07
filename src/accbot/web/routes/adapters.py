from fastapi import APIRouter

from ...config import OneBotConfig
from ...errors import ErrorCode, FrameworkError
from ..schemas import OneBotDraft


def create_router(runtime):
    router = APIRouter(prefix="/api")

    @router.get("/adapters")
    async def listing():
        return [
            {
                "id": "onebot",
                "name": "OneBot",
                "version": "11",
                "platform": "QQ",
                "description": "通过 NapCat 等 OneBot 11 协议端连接 QQ 群聊。",
                "saved": len(runtime.settings.onebot),
                "active": sum(config.enabled for config in runtime.settings.onebot),
            }
        ]

    @router.get("/adapters/onebot")
    async def configurations():
        result = []
        for config in runtime.settings.onebot:
            adapter = runtime.messages.adapters.get(config.id)
            connected = adapter is not None and adapter.socket is not None
            state = (
                "connected"
                if connected
                else "waiting"
                if config.enabled and config.mode == "reverse_ws"
                else ("active" if config.enabled else "inactive")
            )
            result.append(
                {
                    **config.model_dump(mode="json"),
                    "status": state,
                    "token_configured": bool(
                        config.token_env
                        and any(
                            item["configured"]
                            for item in runtime.store.secret_status([config.token_env])
                            if item["name"] == config.token_env
                        )
                    ),
                }
            )
        return result

    @router.put("/connections")
    async def replace(configs: list[OneBotConfig]):
        await runtime.replace_connections(configs)
        return {"connections": [c.model_dump(mode="json") for c in configs]}

    async def save(body, editing=False):
        async with runtime.management_lock:
            previous = next((c for c in runtime.settings.onebot if c.id == body.id), None)
            if bool(previous) != editing:
                raise FrameworkError(
                    ErrorCode.CONFLICT if previous else ErrorCode.NOT_FOUND,
                    "配置 ID 已存在" if previous else "未找到配置",
                )
            variable = previous.token_env if previous else None
            token = body.token.get_secret_value().strip() if body.token else ""
            if token:
                variable = variable or runtime.store.managed_name("ONEBOT", body.id)
            config = OneBotConfig(
                id=body.id,
                name=body.name,
                self_id=body.self_id,
                mode=body.mode,
                http_url=body.http_url or None,
                scope=body.scope or None,
                token_env=variable,
                enabled=previous.enabled if previous else False,
            )
            if token:
                runtime.store.set_secret(variable, token)
            configs = (
                [config if c.id == body.id else c for c in runtime.settings.onebot]
                if previous
                else [*runtime.settings.onebot, config]
            )
            await runtime._replace_connections(configs)
        return next(c for c in await configurations() if c["id"] == config.id)

    @router.post("/adapters/onebot", status_code=201)
    async def create(body: OneBotDraft):
        return await save(body)

    @router.put("/adapters/onebot/{config_id}")
    async def update(config_id: str, body: OneBotDraft):
        if body.id != config_id:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "编辑时不能修改配置 ID")
        return await save(body, editing=True)

    async def switch(config_id, enabled):
        async with runtime.management_lock:
            if not any(c.id == config_id for c in runtime.settings.onebot):
                raise FrameworkError(ErrorCode.NOT_FOUND, "未找到配置")
            await runtime._replace_connections(
                [
                    c.model_copy(update={"enabled": enabled}) if c.id == config_id else c
                    for c in runtime.settings.onebot
                ]
            )
        return next(c for c in await configurations() if c["id"] == config_id)

    @router.post("/adapters/onebot/{config_id}/activate")
    async def activate(config_id: str):
        return await switch(config_id, True)

    @router.post("/adapters/onebot/{config_id}/deactivate")
    async def deactivate(config_id: str):
        return await switch(config_id, False)

    @router.delete("/adapters/onebot/{config_id}")
    async def delete(config_id: str):
        async with runtime.management_lock:
            await runtime._replace_connections([c for c in runtime.settings.onebot if c.id != config_id])
        return {"deleted": config_id}

    return router
