from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.routes import auth, groups, health, members, users
from app.config import BASE_DIR, settings

api = FastAPI(title="MoveNation Assistant API")


class NoCacheMiniAppMiddleware(BaseHTTPMiddleware):
    """Force browsers/Telegram's WebView to revalidate `/miniapp/*` on every
    load instead of silently serving a stale `app.js`/`index.html` from a
    local cache for an arbitrary period. `StaticFiles` still answers with
    304s for unchanged files (via ETag/Last-Modified), so this doesn't cost
    a full re-download — it only removes the "skip the network entirely"
    behavior that made Mini App logic/UI fixes appear not to take effect
    after a deploy."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        if request.url.path.startswith("/miniapp"):
            response.headers["Cache-Control"] = "no-cache"
        return response


api.add_middleware(NoCacheMiniAppMiddleware)

# The Mini App is served from this very app under /miniapp, so requests from
# it are same-origin and need no CORS headers. Only add the middleware if the
# operator explicitly configured extra origins (e.g. a separately hosted
# front-end during development).
if settings.cors_allowed_origins:
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Telegram-Init-Data"],
    )

api.include_router(health.router)
api.include_router(groups.router)
api.include_router(members.router)
api.include_router(users.router)
api.include_router(auth.router)

api.mount("/miniapp", StaticFiles(directory=str(BASE_DIR / "miniapp"), html=True), name="miniapp")
