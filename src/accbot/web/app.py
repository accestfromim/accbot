import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..errors import ErrorCode, FrameworkError
from ..runtime import Runtime
from .auth import SessionAuth
from .routes import adapters, models, onebot, personas, plugin_ui, plugins, prompts, settings

log = logging.getLogger(__name__)


def create_app(runtime: Runtime | None = None):
    runtime = runtime or Runtime()

    @asynccontextmanager
    async def lifespan(app):
        try:
            await runtime.start()
            yield
        finally:
            await runtime.close()

    app = FastAPI(title="accbot", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime
    auth = SessionAuth(runtime)
    app.include_router(auth.router())
    for module in (plugins, plugin_ui, models, personas, prompts, adapters, settings):
        app.include_router(module.create_router(runtime), dependencies=[Depends(auth.authorize)])
    app.include_router(onebot.create_router(runtime))
    frontend = Path(__file__).parent.parent / "frontend"
    app.mount("/assets", StaticFiles(directory=frontend), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})

    @app.exception_handler(FrameworkError)
    async def framework_error(request, exc):
        status = (
            404
            if exc.info.code == ErrorCode.NOT_FOUND
            else 409
            if exc.info.code in {ErrorCode.CONFLICT, ErrorCode.INACTIVE}
            else 400
        )
        return JSONResponse(status_code=status, content={"error": exc.info.model_dump(mode="json")})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default response includes rejected input, which can contain credentials.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in exc.errors()
                ]
            },
        )

    @app.exception_handler(Exception)
    async def last_resort(request, exc):
        log.exception("management request failed")
        return JSONResponse(
            status_code=500, content={"error": {"code": "internal", "message": "请求失败，请查看服务日志"}}
        )

    return app
