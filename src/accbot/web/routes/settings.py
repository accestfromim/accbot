from fastapi import APIRouter

from ...errors import ErrorCode, FrameworkError
from ..schemas import SecretUpdate


def create_router(runtime):
    router = APIRouter(prefix="/api")

    @router.get("/status")
    async def status():
        return {
            "active": sorted(runtime.plugins.active),
            "plugin_activations": {
                key: runtime.plugins.instances[key][1].id for key in runtime.plugins.active
            },
            "jobs": list(runtime.jobs.history.values()),
            "plugin_directory": str(runtime.plugins.directory),
            "config_directory": str(runtime.store.config_dir),
            "connections": [
                {
                    "id": a.connection.id,
                    "websocket": a.socket is not None,
                    "mode": a.config.mode,
                    "status": a.status,
                }
                for a in runtime.messages.adapters.values()
            ],
        }

    @router.get("/settings")
    async def settings():
        return runtime.settings.model_copy(
            update={"personas": runtime.personas.list(), "system_prompts": runtime.prompts.list()}
        ).model_dump(mode="json")

    @router.get("/secrets")
    async def secret_status():
        usages = {}
        for card in runtime.settings.models:
            if card.api_key_env:
                usages.setdefault(card.api_key_env, []).append(
                    {"kind": "model", "id": card.id, "name": card.name or card.id}
                )
        for config in runtime.settings.onebot:
            if config.token_env:
                usages.setdefault(config.token_env, []).append(
                    {"kind": "onebot", "id": config.id, "name": config.name or config.id}
                )
        if runtime.settings.management_token_env:
            usages.setdefault(runtime.settings.management_token_env, []).append(
                {"kind": "management", "id": "admin", "name": "管理访问"}
            )
        return [
            {**item, "usages": usages.get(item["name"], [])} for item in runtime.store.secret_status(usages)
        ]

    @router.put("/secrets/{name}")
    async def secret_update(name: str, body: SecretUpdate):
        runtime.store.set_secret(name, body.value.get_secret_value())
        return {"name": name, "configured": True}

    @router.delete("/secrets/{name}")
    async def secret_delete(name: str):
        usages = await secret_status()
        entry = next((item for item in usages if item["name"] == name), None)
        if entry and entry["usages"]:
            raise FrameworkError(ErrorCode.CONFLICT, "密钥仍在使用中，请先移除对应配置")
        runtime.store.delete_secret(name)
        return {"deleted": name}

    return router
