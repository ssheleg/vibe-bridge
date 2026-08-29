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
from collections import deque
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
from .robot import RobotClient
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
              capabilities: dict[str, Capability] | None = None,
              mcp_allowed_hosts: list[str] | None = None,
              robot: RobotClient | None = None,
              notify=None) -> Starlette:
    from .net import allowed_hosts as _net_allowed_hosts

    if robot is None:
        robot = RobotClient(base_url=state.robot_base_url,
                            chat_url=state.robot_chat_url,
                            chat_key=state.robot_chat_key,
                            name=state.robot_name or "робот")
    notify = notify or (lambda title, text: None)
    robot_state: dict = {"configured": robot.configured, "online": False,
                         "reason": "робот не подключён к панели"
                         if not robot.configured else "ещё не проверял"}
    robot_events: deque[dict] = deque(maxlen=50)
    missed_while_paused = {"n": 0}

    caps = capabilities or build_capabilities()
    availability = probe_availability(caps)
    if mcp_allowed_hosts is None:
        mcp_allowed_hosts = _net_allowed_hosts(state)
    mcp = build_server(consent=consent, audit=audit, runner=runner,
                       capabilities=caps, availability=availability,
                       allowed_hosts=mcp_allowed_hosts)

    def _full_snapshot() -> dict[str, Any]:
        snap = _snapshot(consent, audit)
        snap["robot"] = dict(robot_state)
        snap["robot_events"] = list(robot_events)[-10:]
        return snap
    # The transport keeps its own /mcp path and the inner app mounts at the
    # ROOT, after every panel route. Mounting at "/mcp" instead produces a
    # 307 → /mcp/ whose Location is built from the Host header — for the
    # gateway (Host = 100.x:4000, proxied verbatim) that redirect points AT
    # THE GATEWAY ITSELF and the robot's call never lands (measured
    # 2026-08-29). The wire contract is "/mcp exactly, no redirect" (M4).
    mcp.settings.streamable_http_path = "/mcp"
    bus = EventBus(_full_snapshot)

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
        return JSONResponse(_full_snapshot())

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

    async def api_robot_status(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        st = await robot.status()
        robot_state.clear()
        robot_state.update(st)
        return JSONResponse(st)

    async def api_robot_chat(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        text = str(body.get("text", "")).strip()
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        return JSONResponse(await robot.chat(text))

    async def api_robot_update(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(await robot.trigger_update())

    async def api_journal(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        q = request.query_params
        try:
            offset = max(0, int(q.get("offset", 0)))
            limit = min(200, max(1, int(q.get("limit", 50))))
        except ValueError:
            return JSONResponse({"error": "bad paging"}, status_code=400)
        flt = q.get("filter", "all")
        if flt not in ("all", "refused", "act", "read"):
            return JSONResponse({"error": "bad filter"}, status_code=400)
        return JSONResponse(audit.read_entries(flt=flt, offset=offset,
                                               limit=limit))

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

    async def _robot_poller() -> None:
        """Refresh the cached robot status; announce the pause-summary on
        the paused→active transition (SCN-010 alt)."""
        was_paused = consent.paused
        while True:
            if robot.configured:
                st = await robot.status()
                robot_state.clear()
                robot_state.update(st)
            if was_paused and not consent.paused and missed_while_paused["n"]:
                notify("vibe-bridge",
                       f"За время паузы: {missed_while_paused['n']} событий "
                       f"робота — они в ленте")
                missed_while_paused["n"] = 0
            was_paused = consent.paused
            await asyncio.sleep(10.0)

    async def _robot_event_consumer() -> None:
        """Consume the robot's proactive events; OS-notify unless paused
        (paused events collect silently — SCN-010). Reconnects calmly."""
        while True:
            if not robot.configured:
                await asyncio.sleep(30.0)
                continue
            async for ev in robot.events():
                ev = {"ts": ev.get("ts"), "kind": ev.get("kind", "event"),
                      "text": str(ev.get("text", ""))[:200]}
                robot_events.append(ev)
                if consent.paused:
                    missed_while_paused["n"] += 1
                else:
                    notify(robot.name, ev["text"] or ev["kind"])
            await asyncio.sleep(10.0)          # stream ended — quiet retry

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            tasks = [asyncio.create_task(bus.pump()),
                     asyncio.create_task(_robot_poller()),
                     asyncio.create_task(_robot_event_consumer())]
            try:
                yield
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/state", api_state),
            Route("/api/consent/decide", consent_decide, methods=["POST"]),
            Route("/api/pause", api_pause, methods=["POST"]),
            Route("/api/grants/revoke", api_revoke_grants, methods=["POST"]),
            Route("/api/capabilities", api_capabilities),
            Route("/api/journal", api_journal),
            Route("/api/robot/status", api_robot_status),
            Route("/api/robot/chat", api_robot_chat, methods=["POST"]),
            Route("/api/robot/update", api_robot_update, methods=["POST"]),
            Route("/events", events),
            Mount("/", app=BearerGuard(mcp.streamable_http_app(), state)),
        ],
        lifespan=lifespan,
    )
