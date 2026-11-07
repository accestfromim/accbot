from fastapi import APIRouter
from fastapi.responses import Response

from ...errors import ErrorCode, FrameworkError
from ...prompts import SystemPromptCard


def create_router(runtime):
    router = APIRouter(prefix="/api/prompts")

    @router.get("")
    async def listing():
        return runtime.prompts.list()

    @router.get("/{card_id}")
    async def get(card_id: str):
        return runtime.prompts.get(card_id)

    @router.get("/{card_id}/export")
    async def export(card_id: str):
        card = runtime.prompts.get(card_id)
        return Response(
            card.content,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="{card.id}.md"',
            },
        )

    @router.post("", status_code=201)
    async def create(card: SystemPromptCard):
        async with runtime.management_lock:
            if any(c.id == card.id for c in runtime.prompts.list()):
                raise FrameworkError(ErrorCode.CONFLICT, "系统提示词卡 ID 已存在")
            runtime._replace_prompts([*runtime.prompts.list(), card])
        return card

    @router.put("/{card_id}")
    async def update(card_id: str, card: SystemPromptCard):
        if card.id != card_id:
            raise FrameworkError(ErrorCode.INVALID_ARGUMENT, "编辑时不能修改系统提示词卡 ID")
        async with runtime.management_lock:
            runtime.prompts.get(card_id)
            runtime._replace_prompts([card if c.id == card_id else c for c in runtime.prompts.list()])
        return card

    @router.delete("/{card_id}")
    async def delete(card_id: str):
        async with runtime.management_lock:
            runtime.prompts.get(card_id)
            runtime._replace_prompts([c for c in runtime.prompts.list() if c.id != card_id])
        return {"deleted": card_id}

    return router
