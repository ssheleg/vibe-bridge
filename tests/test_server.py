"""Dispatch core: consent gates the handler, audit records both paths, errors
never leak. HTTP layer not stood up here — dispatch() is the seam.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.audit import AuditLog
from vibebridge.capabilities import Capability, CapabilityError, Runner
from vibebridge.consent import ConsentEngine, Decision, ToolClass
from vibebridge.server import build_server, dispatch


class FakeRunner(Runner):
    def __init__(self, out="ok", boom=False):
        self.out, self.boom, self.calls = out, boom, []

    def run(self, argv, *, timeout=20.0, input_text=None):
        self.calls.append(argv)
        if self.boom:
            raise CapabilityError("device busy")
        return self.out


def _audit(tmp_path):
    return AuditLog(tmp_path / "audit.log")


def _read_cap():
    return Capability("mac_look", ToolClass.READ, "смотрю",
                      lambda r, a: r.run(["screencapture"]), {})


def _act_cap():
    return Capability("mac_do", ToolClass.ACT, "делаю",
                      lambda r, a: r.run(["open", "-a", a.get("app", "")]),
                      {"app": {"type": "string"}})


def _answer(engine, decision):
    def run():
        for _ in range(200):
            if engine.pending():
                engine.pending().resolve(decision)
                return
            time.sleep(0.005)
    threading.Thread(target=run, daemon=True).start()


def test_read_runs_without_dialog(tmp_path):
    eng = ConsentEngine()
    aud = _audit(tmp_path)
    res = dispatch(_read_cap(), {}, consent=eng, audit=aud,
                   runner=FakeRunner("shot"))
    assert res == {"ok": True, "result": "shot"}
    assert aud.recent()[-1]["decision"] == "auto"
    assert aud.recent()[-1]["ok"] is True


def test_act_denied_does_not_run_handler(tmp_path):
    eng = ConsentEngine(ask_timeout_s=1.0)
    aud = _audit(tmp_path)
    r = FakeRunner()
    _answer(eng, Decision.DENY)
    res = dispatch(_act_cap(), {"app": "Safari"}, consent=eng, audit=aud, runner=r)
    assert res["refused"] is True
    assert "отклонил" in res["reason"]
    assert r.calls == []                     # handler never touched
    assert aud.recent()[-1]["ok"] is False


def test_act_allowed_runs_and_audits(tmp_path):
    eng = ConsentEngine(ask_timeout_s=1.0)
    aud = _audit(tmp_path)
    r = FakeRunner("opened")
    _answer(eng, Decision.ALLOW)
    res = dispatch(_act_cap(), {"app": "Safari"}, consent=eng, audit=aud, runner=r)
    assert res == {"ok": True, "result": "opened"}
    assert r.calls == [["open", "-a", "Safari"]]


def test_pause_refuses_and_records(tmp_path):
    eng = ConsentEngine()
    eng.paused = True
    aud = _audit(tmp_path)
    res = dispatch(_read_cap(), {}, consent=eng, audit=aud, runner=FakeRunner())
    assert res["refused"] is True
    assert "паузу" in res["reason"]


def test_handler_error_is_caught(tmp_path):
    eng = ConsentEngine(ask_timeout_s=1.0)
    aud = _audit(tmp_path)
    _answer(eng, Decision.ALLOW)
    res = dispatch(_act_cap(), {"app": "X"}, consent=eng, audit=aud,
                   runner=FakeRunner(boom=True))
    assert res["ok"] is False
    assert "device busy" in res["error"]


def test_server_registers_all_tools():
    eng = ConsentEngine()
    aud = AuditLog(Path("/tmp/mac-bridge-test-audit.log"))
    mcp = build_server(consent=eng, audit=aud, runner=FakeRunner())
    # FastMCP exposes registered tools; every capability must be present.
    import asyncio
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert "screenshot" in names            # canonical
    assert "mac_screenshot" in names        # fleet alias, until B-7
    assert len(names) == 20                 # 10 canonical + 10 aliases


def test_audit_persists_to_disk(tmp_path):
    eng = ConsentEngine()
    aud = _audit(tmp_path)
    dispatch(_read_cap(), {}, consent=eng, audit=aud, runner=FakeRunner("x"))
    log = (tmp_path / "audit.log").read_text()
    assert "mac_look" in log
    import stat
    mode = stat.S_IMODE((tmp_path / "audit.log").stat().st_mode)
    assert mode == 0o600


# ── the windows must not load before the port answers ──────────────────────


def test_wait_for_server_returns_when_the_port_answers():
    """The widget loads its URL exactly once — `WKWebView` shows its own
    "cannot connect" page on failure and never retries. Measured 2026-09-01:
    the pet was a white box reading «Нет связи с…» because the windows were
    built before uvicorn had bound the port, and the journal said nothing,
    since from the bridge's side everything had started."""
    import socket

    from vibebridge.app import wait_for_server

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert wait_for_server(port, timeout=3.0) is True
    finally:
        srv.close()


def test_wait_for_server_gives_up_and_says_so():
    """False, not an exception: a slow port is not a reason to take the bridge
    down, and the caller records the fact."""
    import socket

    from vibebridge.app import wait_for_server

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()                       # nothing is listening there now
    assert wait_for_server(port, timeout=0.4, step=0.05) is False


def test_the_wait_is_actually_called_before_the_windows():
    """Written-and-never-called has happened three times in this project. The
    order matters as much as the call: after the server starts, before the
    windows are built."""
    from pathlib import Path

    import vibebridge

    src = (Path(vibebridge.__file__).parent / "app.py").read_text()
    body = src.split("def run(", 1)[1]
    assert "wait_for_server(settings.port)" in body
    assert body.index("start_server(") < body.index("wait_for_server(")
    assert body.index("wait_for_server(") < body.index("MascotWindow(")


def test_the_bind_always_includes_the_address_the_ui_uses():
    """The invariant that was violated, stated as a test.

    Every local surface — the panel, the app window, both widget windows, the
    tray's "open panel" — addresses the bridge as `BRIDGE_HOST`. If the bind
    does not include that address, the application is broken while the server
    is up: measured 2026-09-01, standalone bound the tailnet IPv4 alone and
    `127.0.0.1:48620` was refused.
    """
    from vibebridge.net import standalone_bind_host
    from vibebridge.server import BRIDGE_HOST

    host = standalone_bind_host()
    assert host in ("0.0.0.0", BRIDGE_HOST), (
        f"standalone биндит {host}, а интерфейс идёт на {BRIDGE_HOST}")


def test_a_tailnet_no_longer_narrows_the_bind(monkeypatch):
    """The old behaviour, pinned so it cannot come back by looking clever."""
    from vibebridge import net

    monkeypatch.setattr(net, "tailscale_ips", lambda: ["100.72.246.104"])
    assert net.standalone_bind_host() == "0.0.0.0"



def test_a_permission_refusal_tells_the_owner_not_just_the_robot():
    """SCN-020 шаг 1: робот получает честный отказ мгновенно — но права
    выдаёт ВЛАДЕЛЕЦ, и до 2026-09-02 он не узнавал об этом ничего, пока сам
    не открывал панель (A-11)."""
    import tempfile
    from pathlib import Path

    from vibebridge.audit import AuditLog
    from vibebridge.capabilities import Capability, Runner
    from vibebridge.consent import ConsentEngine, ToolClass
    from vibebridge.server import dispatch

    told: list[tuple[str, str]] = []
    cap = Capability("screenshot", ToolClass.READ, "смотрю на экран",
                     lambda r, a: {"ok": True}, {})
    with tempfile.TemporaryDirectory() as d:
        audit = AuditLog(Path(d) / "a.log")
        out = dispatch(
            cap, {}, consent=ConsentEngine(), audit=audit, runner=Runner(),
            availability={"screenshot": {"status": "needs-permission",
                                         "reason": "нет прав записи экрана"}},
            on_needs_permission=lambda n, r: told.append((n, r)))
    assert out["unavailable"] is True
    assert told == [("screenshot", "нет прав записи экрана")]


def test_an_unavailable_capability_does_not_nag_about_permissions():
    """«Нет команды» правами не лечится — уведомление тут было бы шумом."""
    import tempfile
    from pathlib import Path

    from vibebridge.audit import AuditLog
    from vibebridge.capabilities import Capability, Runner
    from vibebridge.consent import ConsentEngine, ToolClass
    from vibebridge.server import dispatch

    told: list = []
    cap = Capability("shortcut_run", ToolClass.ACT, "запустить Shortcut",
                     lambda r, a: {"ok": True}, {})
    with tempfile.TemporaryDirectory() as d:
        audit = AuditLog(Path(d) / "a.log")
        dispatch(cap, {}, consent=ConsentEngine(), audit=audit, runner=Runner(),
                 availability={"shortcut_run": {"status": "unavailable",
                                                "reason": "нет команды"}},
                 on_needs_permission=lambda n, r: told.append(n))
    assert told == []


def test_a_broken_notifier_never_costs_the_robot_its_answer():
    """Уведомление владельцу — вежливость, а не условие ответа."""
    import tempfile
    from pathlib import Path

    from vibebridge.audit import AuditLog
    from vibebridge.capabilities import Capability, Runner
    from vibebridge.consent import ConsentEngine, ToolClass
    from vibebridge.server import dispatch

    def boom(name, reason):
        raise RuntimeError("нотифаер лежит")

    cap = Capability("screenshot", ToolClass.READ, "смотрю", lambda r, a: {}, {})
    with tempfile.TemporaryDirectory() as d:
        out = dispatch(
            cap, {}, consent=ConsentEngine(),
            audit=AuditLog(Path(d) / "a.log"), runner=Runner(),
            availability={"screenshot": {"status": "needs-permission",
                                         "reason": "нет прав"}},
            on_needs_permission=boom)
    assert out["unavailable"] is True and "нет прав" in out["error"]
