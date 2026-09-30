"""FastAPI application: GET-only JSON views, an SSE stream, and the built client."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from nexus.command_center.config import CommandCenterSettings
from nexus.command_center.models import (
    CycleSummary,
    CycleView,
    ReplayCatalog,
    ReplayEpisode,
    Snapshot,
)
from nexus.command_center.replay import ReplayLibrary
from nexus.command_center.snapshot import ReplayStatus, SnapshotBuilder
from nexus.command_center.sources import read_system_health
from nexus.command_center.stream import SnapshotHub, TooManySubscribers, event_stream
from nexus.command_center.views import cycle_view
from nexus.diagnostics.models import SystemHealthResult
from nexus.software_engineer.config import SoftwareEngineerSettings

_EPISODE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>NEXUS Command Center</title></head><body style="background:#07080a;color:#d9d4c7;
font-family:monospace;padding:48px"><h1>NEXUS Command Center</h1><p>The API is running.
The client has not been built: run <code>npm ci &amp;&amp; npm run build</code> in
<code>web/command-center</code>, or use <code>npm run dev</code> for development.</p>
<p><a style="color:#ffb454" href="/api/v1/snapshot">/api/v1/snapshot</a></p></body></html>"""


def create_app(
    settings: CommandCenterSettings | None = None,
    *,
    engineer: SoftwareEngineerSettings | None = None,
    replay: ReplayLibrary | None = None,
    health_reader: Callable[[], SystemHealthResult] | None = read_system_health,
    background: bool = True,
) -> FastAPI:
    resolved = settings or CommandCenterSettings()
    builder = SnapshotBuilder(resolved, engineer or SoftwareEngineerSettings())
    library = replay or ReplayLibrary()
    if not resolved.replay_enabled:
        library.disable()
    hub = SnapshotHub(
        builder,
        max_subscribers=resolved.max_subscribers,
        poll_interval=resolved.poll_interval_seconds,
        health_interval=resolved.health_interval_seconds,
        health_reader=health_reader if resolved.health_enabled else None,
    )

    def sync_replay_status() -> None:
        builder.replay_status = ReplayStatus(state=library.state, detail=library.detail)

    async def capture_replay() -> None:
        await asyncio.to_thread(library.capture)
        sync_replay_status()
        await hub.refresh()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        sync_replay_status()
        tasks: list[asyncio.Task[None]] = []
        if background:
            tasks.append(asyncio.create_task(hub.poll_forever()))
            tasks.append(asyncio.create_task(hub.health_forever()))
            if resolved.replay_enabled:
                tasks.append(asyncio.create_task(capture_replay()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    app = FastAPI(
        title="NEXUS Command Center",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    app.state.hub = hub
    app.state.builder = builder
    app.state.replay = library
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved.allowed_hosts)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/snapshot", response_model=Snapshot)
    async def snapshot() -> Snapshot:
        await hub.refresh()
        return await hub.current()

    @app.get("/api/v1/cycles", response_model=list[CycleSummary])
    async def cycles() -> list[CycleSummary]:
        return list((await hub.current()).cycles)

    @app.get("/api/v1/cycles/{cycle_id}", response_model=CycleView)
    async def cycle(cycle_id: UUID) -> CycleView:
        def load() -> CycleView | None:
            record = builder.reader.record(cycle_id)
            if record is None:
                return None
            decision = None
            publication = None
            if record.approval_request is not None:
                request_id = record.approval_request.request_id
                decision = builder.reader.decision(request_id)
                publication = builder.reader.publication(request_id)
            sentinel = None
            if record.selected_candidate_id is not None:
                sentinel = builder.reader.sentinel_verdict(
                    record.cycle_id, record.selected_candidate_id
                )
            return cycle_view(
                record,
                decision=decision,
                publication=publication,
                sentinel=sentinel,
                report_text=builder.reader.report_text(record.cycle_id),
            )

        view = await asyncio.to_thread(load)
        if view is None:
            raise HTTPException(status_code=404, detail="cycle_not_found")
        return view

    @app.get("/api/v1/replay", response_model=ReplayCatalog)
    async def replay_catalog() -> ReplayCatalog:
        return library.catalog()

    @app.get("/api/v1/replay/{name}", response_model=ReplayEpisode)
    async def replay_episode(name: str) -> ReplayEpisode:
        if not _EPISODE_NAME.fullmatch(name):
            raise HTTPException(status_code=404, detail="episode_not_found")
        episode = library.episode(name)
        if episode is None:
            raise HTTPException(status_code=404, detail="episode_not_found")
        return episode

    @app.get("/api/v1/stream")
    async def stream(request: Request) -> Response:
        try:
            queue = hub.subscribe()
        except TooManySubscribers:
            return JSONResponse({"error": "too_many_subscribers"}, status_code=503)
        return StreamingResponse(
            event_stream(hub, queue, request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    client = Path(resolved.client_directory)
    if (client / "index.html").is_file():
        app.mount("/", StaticFiles(directory=client, html=True), name="client")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def not_built() -> str:
            return _NOT_BUILT

    return app


__all__ = ["CONTENT_SECURITY_POLICY", "create_app"]
