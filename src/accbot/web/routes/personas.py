from fastapi import APIRouter
from fastapi.responses import Response

from ...errors import ErrorCode, FrameworkError
from ...personas import PersonaCard


def create_router(runtime):
    router = APIRouter(prefix="/api/personas")

    @router.get("")
    async def listing():
        return runtime.personas.list()

    @router.get("/{card_id}")
    async def get(card_id: str):
        return runtime.personas.get(card_id)

    @router.get("/{card_id}/export")
    async def export(card_id: str):
        card = runtime.personas.get(card_id)
        return Response(
            card.model_dump_json(indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{card.id}.json"'},
        )

    @router.post("", status_code=201)
    async def create(card: PersonaCard):
        async with runtime.management_lock:
            if any(c.id == card.id for c in runtime.personas.list()):
                raise FrameworkError(ErrorCode.CONFLICT, "人格卡 ID 已存在")
            runtime._replace_personas([*runtime.personas.list(), card])
        return card

    @router.put("/{card_id}")
    async def update(card_id: str, card: PersonaCard):
        if card.id != card_id:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "编辑时不能修改人格卡 ID")
        async with runtime.management_lock:
            runtime.personas.get(card_id)
            runtime._replace_personas([card if c.id == card_id else c for c in runtime.personas.list()])
        return card

    @router.delete("/{card_id}")
    async def delete(card_id: str):
        async with runtime.management_lock:
            runtime.personas.get(card_id)
            runtime._replace_personas([c for c in runtime.personas.list() if c.id != card_id])
        return {"deleted": card_id}

    return router
