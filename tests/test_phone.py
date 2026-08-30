"""T-PHONE: VAPID keys, subscriptions, the consent-push watcher and the PWA
shell routes — SCN-004's push half and SCN-019's offline shell.
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
from vibebridge.consent import ConsentEngine, Decision
from vibebridge.push import PushSender, ensure_vapid_keys
from vibebridge.server import dispatch
from vibebridge.state import BridgeState
from vibebridge.web import build_app


def _state(tmp_path) -> BridgeState:
    return BridgeState(path=tmp_path / "s.json", panel_token="panel-secret")


# ── keys + subscriptions ────────────────────────────────────────────────────

def test_vapid_keys_generated_once_and_persisted(tmp_path):
    st = _state(tmp_path)
    ensure_vapid_keys(st)
    assert st.vapid_private and "PRIVATE KEY" in st.vapid_private
    assert st.vapid_public and len(st.vapid_public) > 40
    pub = st.vapid_public
    ensure_vapid_keys(st)                     # idempotent
    assert st.vapid_public == pub
    reloaded = BridgeState.load(st.path)
    assert reloaded.vapid_public == pub


def test_subscription_add_replace_remove(tmp_path):
    st = _state(tmp_path)
    s = PushSender(st, webpush=lambda **kw: None)
    s.add_subscription({"endpoint": "https://push/e1", "keys": {}})
    s.add_subscription({"endpoint": "https://push/e1", "keys": {"a": 1}})
    assert len(st.push_subscriptions) == 1    # same endpoint replaces
    s.add_subscription({"endpoint": "https://push/e2", "keys": {}})
    assert len(st.push_subscriptions) == 2
    assert s.remove_subscription("https://push/e1") is True
    assert s.remove_subscription("https://push/e1") is False


def test_send_prunes_dead_subscriptions(tmp_path):
    from pywebpush import WebPushException

    st = _state(tmp_path)
    ensure_vapid_keys(st)

    class Resp:
        status_code = 410

    def webpush(*, subscription_info, **kw):
        if subscription_info["endpoint"].endswith("dead"):
            raise WebPushException("gone", response=Resp())

    s = PushSender(st, webpush=webpush)
    s.add_subscription({"endpoint": "https://push/dead"})
    s.add_subscription({"endpoint": "https://push/alive"})
    delivered = s.send_to_all({"kind": "consent", "summary": "x"})
    assert delivered == 1
    assert [x["endpoint"] for x in st.push_subscriptions] == ["https://push/alive"]


def test_send_never_raises_on_chaos(tmp_path):
    st = _state(tmp_path)
    ensure_vapid_keys(st)

    def webpush(**kw):
        raise RuntimeError("network on fire")

    s = PushSender(st, webpush=webpush)
    s.add_subscription({"endpoint": "https://push/e1"})
    assert s.send_to_all({"a": 1}) == 0       # kept, no crash
    assert len(st.push_subscriptions) == 1


# ── watcher: a new pending consent becomes a push ───────────────────────────

def test_consent_push_watcher_sends_on_new_pending(tmp_path):
    st = _state(tmp_path)
    sent: list[dict] = []

    class FakeSender(PushSender):
        def __init__(self):
            super().__init__(st, webpush=lambda **kw: None)

        def send_to_all(self, payload):
            sent.append(payload)
            return 1

    st.push_subscriptions = [{"endpoint": "https://push/e1"}]
    consent = ConsentEngine(ask_timeout_s=3.0)
    cap = Capability("do", ToolClass.ACT, "открыть «{app}»",
                     lambda r, a: "done", {"app": {"type": "string"}})
    app = build_app(consent=consent, audit=AuditLog(tmp_path / "a.log"),
                    state=st, capabilities={"do": cap},
                    push_sender=FakeSender(),
                    mcp_allowed_hosts=["testserver", "127.0.0.1:*"])

    class R:
        def run(self, argv, **kw):
            return "ok"

    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        results: list = []
        t = threading.Thread(
            target=lambda: results.append(dispatch(
                cap, {"app": "Safari"}, consent=consent,
                audit=AuditLog(tmp_path / "a2.log"), runner=R())),
            daemon=True)
        t.start()
        deadline = time.time() + 3
        while time.time() < deadline and not sent:
            time.sleep(0.05)
        req = consent.pending()
        if req:
            req.resolve(Decision.DENY)
        t.join(timeout=3)
    assert sent and sent[0]["kind"] == "consent"
    assert "Safari" in sent[0]["summary"] and sent[0]["id"]


# ── PWA shell routes ────────────────────────────────────────────────────────

def test_pwa_shell_served_without_auth(tmp_path):
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=_state(tmp_path), capabilities={},
                    mcp_allowed_hosts=["testserver", "127.0.0.1:*"])
    with TestClient(app) as c:
        sw = c.get("/sw.js")
        assert sw.status_code == 200
        assert "javascript" in sw.headers["content-type"]
        man = c.get("/manifest.webmanifest")
        assert man.status_code == 200 and man.json()["display"] == "standalone"
        off = c.get("/offline.html")
        assert off.status_code == 200 and "Tailscale" in off.text
        icon = c.get("/icon-192.png")
        assert icon.status_code == 200
        assert icon.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert c.get("/icon-999.png").status_code == 404


def test_push_endpoints(tmp_path):
    st = _state(tmp_path)
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=st, capabilities={},
                    mcp_allowed_hosts=["testserver", "127.0.0.1:*"])
    with TestClient(app) as c:
        assert c.get("/api/push/vapid").status_code == 401
        c.get("/?token=panel-secret")
        key = c.get("/api/push/vapid").json()["key"]
        assert len(key) > 40
        r = c.post("/api/push/subscribe",
                   json={"subscription": {"endpoint": "https://push/e1"}})
        assert r.json() == {"ok": True, "count": 1}
        bad = c.post("/api/push/subscribe", json={"subscription": "nope"})
        assert bad.status_code == 400
        un = c.post("/api/push/unsubscribe",
                    json={"endpoint": "https://push/e1"})
        assert un.json()["ok"] is True
        phone = c.get("/api/phone").json()
        assert "subscriptions" in phone and "setup_command" in phone
