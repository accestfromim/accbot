"""Optional remote-access login. Local default sessions need no credentials."""

import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response

from .schemas import SessionLogin


class SessionAuth:
    def __init__(self, runtime):
        self.runtime = runtime
        self.sessions: dict[str, float] = {}

    def authenticated(self, request):
        name = self.runtime.settings.management_token_env
        if not name:
            return True
        supplied = request.headers.get("x-accbot-token")
        if supplied and secrets.compare_digest(self.runtime.store.get_secret(name), supplied):
            return True
        session = request.cookies.get("accbot_session", "")
        return self.sessions.get(session, 0) > time.time()

    async def authorize(self, request: Request):
        if not self.authenticated(request):
            raise HTTPException(401, "请登录管理台")

    def router(self):
        router = APIRouter()

        @router.get("/api/session")
        async def status(request: Request):
            return {
                "auth_required": bool(self.runtime.settings.management_token_env),
                "authenticated": self.authenticated(request),
            }

        @router.post("/api/session")
        async def login(body: SessionLogin, response: Response):
            name = self.runtime.settings.management_token_env
            if name and not secrets.compare_digest(
                self.runtime.store.get_secret(name), body.token.get_secret_value()
            ):
                raise HTTPException(401, "访问凭据不正确")
            now = time.time()
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            session = secrets.token_urlsafe(32)
            self.sessions[session] = now + 8 * 3600
            response.set_cookie("accbot_session", session, max_age=8 * 3600, httponly=True, samesite="strict")
            return {"authenticated": True}

        @router.delete("/api/session")
        async def logout(request: Request, response: Response):
            self.sessions.pop(request.cookies.get("accbot_session", ""), None)
            response.delete_cookie("accbot_session")
            return {"authenticated": False}

        return router
