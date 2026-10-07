"""The event stream, the config summary, and the console's own files."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import independence_problems

from .context import CONSOLE_DIR, Context

log = logging.getLogger(__name__)


def register(app: FastAPI, ctx: Context) -> None:
    """The stream, the config, the console, and the error handler."""
    cfg = ctx.cfg
    hub = ctx.hub

    # ================================================================
    # stream, config, console
    # ================================================================

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(
            hub.stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.get("/api/config")
    def config_summary() -> dict[str, Any]:
        return {
            "roles": {name: {"model": r.model, "temperature": r.temperature,
                             "reasoning_effort": r.reasoning_effort,
                             "providers": r.providers, "allow_fallbacks": r.allow_fallbacks,
                             "fallback": r.fallback,
                             "fallback_route": r.fallback_route,
                             "fallback_model": r.fallback_model}
                      for name, r in cfg.roles.items()},
            # The lanes themselves, and whether the split they exist to keep
            # actually holds. A fallback is chosen at rest and taken at 2am.
            "fallbacks": {name: lane.model_dump() for name, lane in cfg.fallbacks.items()},
            "independence": independence_problems(cfg),
            "executor": cfg.executor.kind,
            "evidence": str(cfg.evidence_path),
            "sandboxes": str(cfg.sandbox_path),
            "api_key_present": bool(cfg.api.api_key),
            "source": cfg.source_path,
            # The review offers "open this file in your editor", which is a
            # scheme handler and therefore resolves on the *browser's*
            # filesystem, against a path that only exists on the factory's. The
            # console shows the link only when it is served from localhost --
            # the one case where those two are the same machine -- and the
            # `git diff` command otherwise.
            "editor_url": cfg.editor_url,
        }

    @app.middleware("http")
    async def no_console_cache(request, call_next):
        """The console has no build step and no cache-busting hashes, so a stale
        script after an edit is the default experience without this."""
        response = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    if CONSOLE_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(CONSOLE_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        target = CONSOLE_DIR / "index.html"
        if not target.exists():
            raise HTTPException(status_code=500, detail=f"console not found at {CONSOLE_DIR}")
        return FileResponse(str(target), media_type="text/html")

    @app.exception_handler(Exception)
    async def unhandled(request, exc: Exception) -> JSONResponse:  # pragma: no cover
        log.exception("unhandled error serving %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
