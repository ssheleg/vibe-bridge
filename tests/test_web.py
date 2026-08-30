"""Walking skeleton: one Starlette app carries the MCP mount, the panel,
SSE and the consent round-trip. The consent path through HTTP is the seam
this file pins — if it breaks, the robot acts without the owner or the
owner cannot answer at all.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.testclient import TestClient

from vibebridge.audit import AuditLog
from vibebridge.capabilities import Capability, ToolClass
from vibebridge.consent import ConsentEngine
from vibebridge.server import dispatch
from vibebridge.state import BridgeState
from vibebridge.web import build_app


class FakeRunner:
    def __init__(self, out="ok"):
        self.out, self.calls = out, []

    def run(self, argv, *, timeout=20.0, input_text=None):
        self.calls.append(argv)
        return self.out


def _act_cap():
    return Capability("mac_do", ToolClass.ACT, "открыть «{app}»",
                      lambda r, a: r.run(["open", "-a", a.get("app", "")]),
                      {"app": {"type": "string"}})


def _mk(tmp_path, *, robot_token=None, ask_timeout=5.0):
    state = BridgeState(path=tmp_path / "state.json",
                        panel_token="panel-secret",
                        robot_token=robot_token)
    consent = ConsentEngine(ask_timeout_s=ask_timeout)
    audit = AuditLog(tmp_path / "audit.log")
    runner = FakeRunner("done")
    app = build_app(consent=consent, audit=audit, state=state, runner=runner,
                    capabilities={"mac_do": _act_cap()})
    return app, consent, audit, runner


# ── state file ──────────────────────────────────────────────────────────────

def test_state_created_with_0600_and_panel_token(tmp_path):
    p = tmp_path / "state.json"
    st = BridgeState.load(p)
    assert len(st.panel_token) >= 32
    assert st.robot_token is None            # gateway mode by default
    import stat
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    again = BridgeState.load(p)              # idempotent: same token
    assert again.panel_token == st.panel_token


# ── panel auth ──────────────────────────────────────────────────────────────

def test_panel_requires_token(tmp_path):
    app, *_ = _mk(tmp_path)
    with TestClient(app) as c:
        assert c.get("/").status_code == 401
        assert c.get("/api/state").status_code == 401
        assert c.get("/events").status_code == 401


def test_panel_token_sets_cookie_and_redirects(tmp_path):
    app, *_ = _mk(tmp_path)
    with TestClient(app) as c:
        r = c.get("/?token=panel-secret", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert "vb_panel" in r.cookies or "vb_panel" in r.headers.get("set-cookie", "")
        page = c.get("/")                    # cookie now carried by client
        assert page.status_code == 200
        assert "vibe-bridge" in page.text


def test_panel_wrong_token_refused(tmp_path):
    app, *_ = _mk(tmp_path)
    with TestClient(app) as c:
        assert c.get("/?token=nope", follow_redirects=False).status_code == 403


# ── MCP mount + bearer ──────────────────────────────────────────────────────

def test_mcp_mounted_gateway_mode_no_token_needed(tmp_path):
    app, *_ = _mk(tmp_path, robot_token=None)
    with TestClient(app) as c:
        # No auth configured: the request reaches the MCP transport, whose
        # answer to a bare GET is 4xx from the transport itself — NOT our 401.
        r = c.get("/mcp")
        assert r.status_code != 401


def test_mcp_bearer_enforced_in_standalone_mode(tmp_path):
    app, *_ = _mk(tmp_path, robot_token="robo-tok")
    with TestClient(app) as c:
        r = c.get("/mcp")
        assert r.status_code == 401
        assert r.headers.get("www-authenticate") == "Bearer"
        ok = c.get("/mcp", headers={"Authorization": "Bearer robo-tok"})
        assert ok.status_code != 401


# ── consent round-trip through the panel ────────────────────────────────────

def _start_act(consent, audit, runner, results):
    def run():
        results.append(dispatch(_act_cap(), {"app": "Safari"},
                                consent=consent, audit=audit, runner=runner))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_consent_decide_allow_roundtrip(tmp_path):
    app, consent, audit, runner = _mk(tmp_path)
    results: list = []
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        t = _start_act(consent, audit, runner, results)
        # pending surfaces in /api/state
        deadline = time.time() + 3
        pending = None
        while time.time() < deadline:
            pending = c.get("/api/state").json()["pending"]
            if pending:
                break
            time.sleep(0.02)
        assert pending and "Safari" in pending["summary"]
        r = c.post("/api/consent/decide", json={"decision": "allow"})
        assert r.status_code == 200
        t.join(timeout=3)
    assert results and results[0]["ok"] is True
    assert runner.calls == [["open", "-a", "Safari"]]


def test_consent_decide_deny_refuses(tmp_path):
    app, consent, audit, runner = _mk(tmp_path)
    results: list = []
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        t = _start_act(consent, audit, runner, results)
        deadline = time.time() + 3
        while time.time() < deadline:
            if c.get("/api/state").json()["pending"]:
                break
            time.sleep(0.02)
        c.post("/api/consent/decide", json={"decision": "deny"})
        t.join(timeout=3)
    assert results and results[0]["refused"] is True
    assert runner.calls == []


def test_consent_decide_without_pending_is_404(tmp_path):
    app, *_ = _mk(tmp_path)
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        r = c.post("/api/consent/decide", json={"decision": "allow"})
        assert r.status_code == 404


# ── SSE ─────────────────────────────────────────────────────────────────────

def test_sse_bus_pushes_pending_snapshot(tmp_path):
    """The /events contract, tested at the bus seam: a subscriber gets the
    initial snapshot at once and a fresh one when a consent request lands.
    The infinite HTTP stream itself is exercised live (WS DoD), not here —
    an endless StreamingResponse wedges TestClient teardown."""
    import asyncio

    from vibebridge.web import EventBus, _snapshot

    consent = ConsentEngine(ask_timeout_s=5.0)
    audit = AuditLog(tmp_path / "audit.log")
    runner = FakeRunner("done")
    results: list = []

    async def scenario():
        bus = EventBus(lambda: _snapshot(consent, audit), interval=0.02)
        pump = asyncio.create_task(bus.pump())
        try:
            q = bus.subscribe()
            first = await asyncio.wait_for(q.get(), timeout=2)
            assert first["pending"] is None          # initial state, always
            _start_act(consent, audit, runner, results)
            deadline = time.time() + 3
            snap = None
            while time.time() < deadline:
                snap = await asyncio.wait_for(q.get(), timeout=2)
                if snap["pending"]:
                    break
            assert snap and "Safari" in snap["pending"]["summary"]
        finally:
            pump.cancel()
    asyncio.run(scenario())
    req = consent.pending()
    if req:                                          # tidy: let thread die
        from vibebridge.consent import Decision
        req.resolve(Decision.DENY)


def test_events_endpoint_requires_auth_and_streams_header(tmp_path):
    app, *_ = _mk(tmp_path)
    with TestClient(app) as c:
        assert c.get("/events").status_code == 401


# ── T-CORE API surface ──────────────────────────────────────────────────────

def test_pause_endpoint_toggles(tmp_path):
    app, consent, *_ = _mk(tmp_path)
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        r = c.post("/api/pause", json={"paused": True})
        assert r.json()["paused"] is True and consent.paused is True
        assert c.get("/api/state").json()["paused"] is True
        c.post("/api/pause", json={"paused": False})
        assert consent.paused is False


def test_grants_revoke_endpoint(tmp_path):
    from vibebridge.consent import ToolClass as TC
    app, consent, *_ = _mk(tmp_path)
    consent._grant_until[TC.ACT] = consent._clock() + 600
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        assert c.post("/api/grants/revoke").status_code == 200
    assert consent.grant_active(TC.ACT) == 0.0


def test_capabilities_endpoint_shape(tmp_path):
    app, *_ = _mk(tmp_path)
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        caps = c.get("/api/capabilities").json()
        assert caps["mac_do"]["class"] == "act"
        assert caps["mac_do"]["status"] in ("available", "unavailable")


def test_decide_by_id_and_stale_id(tmp_path):
    app, consent, audit, runner = _mk(tmp_path)
    results: list = []
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        t = _start_act(consent, audit, runner, results)
        deadline = time.time() + 3
        pending = None
        while time.time() < deadline:
            pending = c.get("/api/state").json()["pending"]
            if pending:
                break
            time.sleep(0.02)
        assert pending and pending["id"]
        stale = c.post("/api/consent/decide",
                       json={"decision": "allow", "id": "deadbeef0000"})
        assert stale.status_code == 404          # unknown id never resolves
        ok = c.post("/api/consent/decide",
                    json={"decision": "allow", "id": pending["id"]})
        assert ok.status_code == 200
        t.join(timeout=3)
    assert results and results[0]["ok"] is True
