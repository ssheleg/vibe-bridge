"""Dispatch core: consent gates the handler, audit records both paths, errors
never leak. HTTP layer not stood up here — dispatch() is the seam.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macbridge.audit import AuditLog
from macbridge.capabilities import Capability, CapabilityError, Runner
from macbridge.consent import ConsentEngine, Decision, ToolClass
from macbridge.server import build_server, dispatch


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
    assert "mac_screenshot" in names
    assert "mac_open_app" in names
    assert len(names) == 10


def test_audit_persists_to_disk(tmp_path):
    eng = ConsentEngine()
    aud = _audit(tmp_path)
    dispatch(_read_cap(), {}, consent=eng, audit=aud, runner=FakeRunner("x"))
    log = (tmp_path / "audit.log").read_text()
    assert "mac_look" in log
    import stat
    mode = stat.S_IMODE((tmp_path / "audit.log").stat().st_mode)
    assert mode == 0o600
