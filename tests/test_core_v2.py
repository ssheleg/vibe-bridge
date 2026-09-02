"""T-CORE: consent v2 (ids, first-decision-wins), capability availability,
audit humanization + rotation. These are the SCN-002/003/005/011/018 seams.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.audit import AuditLog
from vibebridge.capabilities import (
    Capability,
    ToolClass,
    build_capabilities,
    probe_availability,
)
from vibebridge.consent import ConsentEngine, Decision
from vibebridge.server import dispatch


class FakeRunner:
    def __init__(self, out="ok"):
        self.out, self.calls = out, []

    def run(self, argv, *, timeout=20.0, input_text=None):
        self.calls.append(argv)
        return self.out


def _act():
    return Capability("mac_do", ToolClass.ACT, "открыть «{app}»",
                      lambda r, a: r.run(["open", a.get("app", "")]),
                      {"app": {"type": "string"}}, binaries=("open",))


# ── consent v2: ids and first-decision-wins ─────────────────────────────────

def test_request_carries_unique_id():
    eng = ConsentEngine(ask_timeout_s=1.0)
    seen = []

    def answer():
        for _ in range(200):
            req = eng.pending()
            if req is not None:
                seen.append(req.id)
                req.resolve(Decision.ALLOW, by="test")
                return
            time.sleep(0.005)

    threading.Thread(target=answer, daemon=True).start()
    eng.request("t", _act().tool_class, "s")
    assert seen and isinstance(seen[0], str) and len(seen[0]) >= 8


def test_first_decision_wins_second_is_noop():
    eng = ConsentEngine(ask_timeout_s=2.0)
    outcome = []

    def answer():
        for _ in range(400):
            req = eng.pending()
            if req is not None:
                assert req.resolve(Decision.DENY, by="phone") is True
                assert req.resolve(Decision.ALLOW, by="desktop") is False
                outcome.append(req.decided_by)
                return
            time.sleep(0.005)

    threading.Thread(target=answer, daemon=True).start()
    decision = eng.request("t", _act().tool_class, "s")
    assert decision is Decision.DENY            # the phone's DENY held
    assert outcome == ["phone"]


def test_resolve_by_id_unknown_returns_false():
    eng = ConsentEngine()
    assert eng.resolve_by_id("nope", Decision.ALLOW, by="x") is False


# ── availability: probe once, refuse honestly, never consult the owner ──────

def test_probe_missing_binary_is_unavailable():
    caps = {"mac_do": _act()}
    avail = probe_availability(caps, which=lambda b: None)
    assert avail["mac_do"]["status"] == "unavailable"
    assert "open" in avail["mac_do"]["reason"]


def test_probe_present_binary_is_available():
    caps = {"mac_do": _act()}
    avail = probe_availability(caps, which=lambda b: "/usr/bin/" + b)
    assert avail["mac_do"]["status"] == "available"


def test_dispatch_unavailable_refuses_fast_without_consent(tmp_path):
    eng = ConsentEngine(ask_timeout_s=30.0)   # a dialog would hang the test
    aud = AuditLog(tmp_path / "a.log")
    r = FakeRunner()
    res = dispatch(_act(), {"app": "X"}, consent=eng, audit=aud, runner=r,
                   availability={"mac_do": {"status": "unavailable",
                                            "reason": "нет «open»"}})
    assert res["ok"] is False and res["unavailable"] is True
    assert "нет «open»" in res["error"]
    assert r.calls == []                          # handler never touched
    assert aud.recent()[-1]["decision"] == "unavailable"


def test_real_capability_set_probes_on_macos():
    avail = probe_availability(build_capabilities())
    assert set(avail) == set(build_capabilities())
    assert all(v["status"] in ("available", "needs-permission", "unavailable")
               for v in avail.values())


# ── audit: human line + rotation ────────────────────────────────────────────

def test_audit_records_human_line(tmp_path):
    aud = AuditLog(tmp_path / "a.log")
    aud.record(tool="mac_do", tool_class="act", decision="allow", ok=True,
               line="открыть «Safari»")
    assert aud.recent()[-1]["line"] == "открыть «Safari»"
    assert "открыть «Safari»" in (tmp_path / "a.log").read_text()


def test_audit_rotates_at_max_bytes(tmp_path):
    aud = AuditLog(tmp_path / "a.log", max_bytes=500)
    for i in range(30):
        aud.record(tool=f"t{i}", tool_class="read", decision="auto", ok=True,
                   line="x" * 40)
    assert (tmp_path / "a.log.1").exists()
    assert (tmp_path / "a.log").stat().st_size < 1000


def test_dispatch_writes_summary_line(tmp_path):
    eng = ConsentEngine(ask_timeout_s=1.0)
    aud = AuditLog(tmp_path / "a.log")

    def answer():
        for _ in range(200):
            req = eng.pending()
            if req is not None:
                req.resolve(Decision.ALLOW, by="test")
                return
            time.sleep(0.005)

    threading.Thread(target=answer, daemon=True).start()
    dispatch(_act(), {"app": "Safari"}, consent=eng, audit=aud,
             runner=FakeRunner())
    assert aud.recent()[-1]["line"] == "открыть «Safari»"


# ── A-26: меню-бар показывал UTC как настенное время ───────────────────────

def test_the_menu_bar_shows_the_owners_clock_not_utc():
    """A-26: `ts[11:19]` печатал UTC как местное. Ровно та ошибка, которую в
    панели уже чинили (`localTs`), — одна беда в двух реализациях, и вторая
    пережила фикс первой."""
    from datetime import UTC, datetime

    from vibebridge.audit import local_hhmmss

    moment = datetime(2026, 9, 2, 18, 45, 32, tzinfo=UTC)
    expected = moment.astimezone().strftime("%H:%M:%S")
    assert local_hhmmss(moment.isoformat(timespec="seconds")) == expected
    # ...со смещением в строке — тоже
    assert local_hhmmss("2026-09-02T20:45:32+02:00") == expected
    # ...а наивное время читается как UTC: журнал пишет именно его
    assert local_hhmmss("2026-09-02T18:45:32") == expected


def test_a_broken_timestamp_does_not_invent_a_time():
    from vibebridge.audit import local_hhmmss

    assert local_hhmmss("мусор") == "--:--:--"
    assert local_hhmmss("") == "--:--:--"


def test_the_menu_bar_shows_several_lines_newest_first():
    """README обещает «последние несколько», а показывалась ОДНА строка."""
    from vibebridge.audit import recent_lines

    entries = [{"ts": f"2026-09-02T10:0{i}:00+00:00", "tool": f"t{i}",
                "decision": "allow", "ok": True} for i in range(6)]
    lines = recent_lines(entries, limit=5)
    assert len(lines) == 5
    assert "t5" in lines[0] and "t1" in lines[-1]      # новое сверху
    assert recent_lines([]) == ["— пока пусто —"]
    assert recent_lines([{"ts": "2026-09-02T10:00:00+00:00", "tool": "x",
                          "decision": "deny", "ok": False}])[0].endswith("✗")


def test_the_tray_no_longer_slices_the_timestamp_itself():
    """Класс, а не случай: две реализации одной беды — это то, из-за чего
    фикс панели не долетел до меню-бара. Формат живёт в одном месте."""
    import re
    from pathlib import Path

    import vibebridge

    src = (Path(vibebridge.__file__).parent / "app.py").read_text()
    code = re.sub(r"^\s*#.*$", "", src, flags=re.M)
    assert "[11:19]" not in code, "меню-бар снова режет ISO-строку сам"
    assert "recent_lines" in code, "меню-бар не пользуется общим форматом"
