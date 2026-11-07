from fastapi import APIRouter

from ...config import ModelCard
from ...errors import ErrorCode, FrameworkError
from ...models.discovery import detect_protocol, fetch_model_names, normalize_endpoint
from ..schemas import ModelDraft, ProviderDraft


def resolve_key(runtime, existing_id, key):
    if key and key.get_secret_value().strip():
        return key.get_secret_value().strip()
    existing = next((card for card in runtime.settings.models if card.id == existing_id), None)
    if existing and existing.api_key_env:
        return runtime.store.get_secret(existing.api_key_env)
    return None


def create_router(runtime):
    router = APIRouter(prefix="/api/models")

    @router.get("")
    async def listing():
        statuses = {
            item["name"]: item["configured"]
            for item in runtime.store.secret_status(
                [card.api_key_env for card in runtime.settings.models if card.api_key_env]
            )
        }
        return [
            {**card.model_dump(mode="json"), "credential_configured": statuses.get(card.api_key_env, False)}
            for card in runtime.settings.models
        ]

    @router.put("")
    async def replace(cards: list[ModelCard]):
        await runtime.replace_models(cards)
        return {"models": await listing()}

    @router.post("/catalog")
    async def catalog(body: ProviderDraft):
        key = resolve_key(runtime, body.existing_id, body.key)
        return {"models": await fetch_model_names(runtime.http, body.endpoint, key)}

    @router.post("/detect")
    async def detect(body: ModelDraft):
        return await detect_protocol(
            runtime.http, body.endpoint, body.model, resolve_key(runtime, body.id, body.key)
        )

    async def save(body, *, editing=False):
        previous = next((card for card in runtime.settings.models if card.id == body.id), None)
        if bool(previous) != editing:
            raise FrameworkError(
                ErrorCode.CONFLICT if previous else ErrorCode.NOT_FOUND,
                "模型卡 ID 已存在" if previous else "未找到模型卡",
            )
        endpoint = normalize_endpoint(body.endpoint)
        protocol = body.protocol
        if protocol == "auto":
            protocol = (await detect(body))["protocol"]
        async with runtime.management_lock:
            previous = next((card for card in runtime.settings.models if card.id == body.id), None)
            if bool(previous) != editing:
                raise FrameworkError(
                    ErrorCode.CONFLICT if previous else ErrorCode.NOT_FOUND,
                    "模型卡 ID 已存在" if previous else "未找到模型卡",
                )
            variable = previous.api_key_env if previous else None
            new_key = body.key.get_secret_value().strip() if body.key else ""
            if new_key:
                variable = variable or runtime.store.managed_name("MODEL", body.id)
            card = ModelCard(
                id=body.id,
                name=body.name,
                description=body.description,
                endpoint=endpoint,
                model=body.model,
                protocol=protocol,
                api_key_env=variable,
                parameters=previous.parameters if previous else {},
            )
            if new_key:
                runtime.store.set_secret(variable, new_key)
            cards = (
                [card if c.id == body.id else c for c in runtime.settings.models]
                if previous
                else [*runtime.settings.models, card]
            )
            runtime._replace_models(cards)
        return next(item for item in await listing() if item["id"] == body.id)

    @router.post("/cards", status_code=201)
    async def create(body: ModelDraft):
        return await save(body)

    @router.put("/cards/{model_id}")
    async def update(model_id: str, body: ModelDraft):
        if body.id != model_id:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "编辑时不能修改模型卡 ID")
        return await save(body, editing=True)

    @router.delete("/cards/{model_id}")
    async def delete(model_id: str):
        async with runtime.management_lock:
            if not any(card.id == model_id for card in runtime.settings.models):
                raise FrameworkError(ErrorCode.NOT_FOUND, "未找到模型卡")
            runtime._replace_models([c for c in runtime.settings.models if c.id != model_id])
        return {"deleted": model_id}

    return router
