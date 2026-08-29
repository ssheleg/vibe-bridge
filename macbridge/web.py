"""One ASGI app, four doors: /mcp for the robot, / + /api + /events for the
owner's panel. The walking skeleton of vibe-bridge (spec §1, §7).

Mounting the FastMCP transport requires its session manager to run for the
process lifetime — that lives in this app's lifespan (research-notes §A).
Bearer auth for /mcp is OUR ASGI guard around the mount: with no
`token_verifier` the SDK installs no middleware of its own, so nothing
conflicts. Panel routes are gated by a cookie carrying the panel token.

Events: the walking skeleton polls the consent engine and audit tail into
snapshots and pushes them over SSE. T-CORE replaces polling with direct
engine hooks and per-request ids; the wire contract (event `state`, JSON
snapshot) is what the panel binds to and it survives that swap.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route

from .audit import AuditLog
from .capabilities import (
    Capability,
    Runner,
    build_capabilities,
    probe_availability,
)
from .consent import ConsentEngine, Decision, ToolClass
from .server import build_server
from .state import BridgeState

PANEL_COOKIE = "vb_panel"
_WEBUI = Path(__file__).parent / "webui"

_DECISIONS = {
    "allow": Decision.ALLOW,
    "allow_grant": Decision.ALLOW_GRANT,
    "deny": Decision.DENY,
}


class BearerGuard:
    """401 before the MCP transport unless the configured robot token rides
    the Authorization header. No token configured (gateway mode) → pass."""

    def __init__(self, app, state: BridgeState) -> None:
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send) -> None:
        token = self.state.robot_token
        if token and scope["type"] == "http":
            auth = ""
            for k, v in scope.get("headers", []):
                if k == b"authorization":
                    auth = v.decode("latin-1")
                    break
            if auth != f"Bearer {token}":
                await JSONResponse(
                    {"error": "unauthorized"}, status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _snapshot(consent: ConsentEngine, audit: AuditLog) -> dict[str, Any]:
    reqs = consent.pending_all()
    req = reqs[0] if reqs else None
    return {
        "paused": consent.paused,
        "pending": ({"id": req.id, "tool": req.tool,
                     "class": req.tool_class.value,
                     "summary": req.summary} if req else None),
        "pending_count": len(reqs),
        "grant_left_s": int(consent.grant_active(ToolClass.ACT)),
        "recent": audit.recent(20),
    }


class EventBus:
    """Snapshot poller → per-subscriber queues. Skeleton-grade by design."""

    def __init__(self, snapshot_fn, interval: float = 0.2) -> None:
        self._snapshot_fn = snapshot_fn
        self._interval = interval
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subs.add(q)
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(self._snapshot_fn())        # initial state, always
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def pump(self) -> None:
        last: dict | None = None
        while True:
            snap = self._snapshot_fn()
            if snap != last:
                last = snap
                for q in list(self._subs):
                    with contextlib.suppress(asyncio.QueueFull):
                        q.put_nowait(snap)
            await asyncio.sleep(self._interval)


def build_app(*, consent: ConsentEngine, audit: AuditLog, state: BridgeState,
              runner: Runner | None = None,
              capabilities: dict[str, Capability] | None = None) -> Starlette:
    caps = capabilities or build_capabilities()
    availability = probe_availability(caps)
    mcp = build_server(consent=consent, audit=audit, runner=runner,
                       capabilities=caps, availability=availability)
    # The transport's own path collapses to "/" so the mount point IS /mcp —
    # the exact URL the agentgateway already targets (README wire contract).
    mcp.settings.streamable_http_path = "/"
    bus = EventBus(lambda: _snapshot(consent, audit))

    def _authed(request: Request) -> bool:
        return request.cookies.get(PANEL_COOKIE) == state.panel_token

    async def index(request: Request) -> Response:
        token = request.query_params.get("token")
        if token is not None:
            if token != state.panel_token:
                return JSONResponse({"error": "forbidden"}, status_code=403)
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(PANEL_COOKIE, state.panel_token, httponly=True,
                            samesite="lax")
            return resp
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return FileResponse(_WEBUI / "index.html")

    async def api_state(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(_snapshot(consent, audit))

    async def consent_decide(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        decision = _DECISIONS.get(str(body.get("decision", "")))
        if decision is None:
            return JSONResponse({"error": "bad decision"}, status_code=400)
        req_id = body.get("id")
        if req_id:
            done = consent.resolve_by_id(str(req_id), decision, by="panel")
        else:
            req = consent.pending()
            done = req.resolve(decision, by="panel") if req else False
        if not done:
            return JSONResponse(
                {"error": "запрос уже решён или истёк"}, status_code=404)
        return JSONResponse({"ok": True})

    async def api_pause(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        consent.paused = bool(body.get("paused"))
        return JSONResponse({"ok": True, "paused": consent.paused})

    async def api_revoke_grants(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        consent.revoke_grants()
        return JSONResponse({"ok": True})

    async def api_capabilities(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({
            name: {"class": caps[name].tool_class.value, **info}
            for name, info in availability.items()
        })

    async def events(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        async def stream():
            q = bus.subscribe()
            try:
                while True:
                    snap = await q.get()
                    yield f"event: state\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            pump = asyncio.create_task(bus.pump())
            try:
                yield
            finally:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/state", api_state),
            Route("/api/consent/decide", consent_decide, methods=["POST"]),
            Route("/api/pause", api_pause, methods=["POST"]),
            Route("/api/grants/revoke", api_revoke_grants, methods=["POST"]),
            Route("/api/capabilities", api_capabilities),
            Route("/events", events),
            Mount("/mcp", app=BearerGuard(mcp.streamable_http_app(), state)),
        ],
        lifespan=lifespan,
    )
