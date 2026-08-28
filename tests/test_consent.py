"""Consent engine — the safety core. If this is wrong, the robot acts without
the owner, so every branch is pinned.

Runs with a fake clock and driven Events, no UI and no sleeping.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macbridge.consent import (
    ConsentEngine,
    Decision,
    ToolClass,
    allowed,
    refusal_text,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _answer_async(engine, decision, delay=0.02):
    """Resolve the first pending request after a short real delay."""
    def run():
        for _ in range(200):
            req = engine.pending()
            if req is not None:
                req.resolve(decision)
                return
            time.sleep(0.005)
    threading.Thread(target=run, daemon=True).start()


def test_read_is_auto_never_asks():
    eng = ConsentEngine()
    d = eng.request("mac_screenshot", ToolClass.READ, "смотрю экран")
    assert d is Decision.AUTO
    assert allowed(d)


def test_act_allow_once():
    eng = ConsentEngine(ask_timeout_s=2.0)
    _answer_async(eng, Decision.ALLOW)
    d = eng.request("mac_open_app", ToolClass.ACT, "открыть Safari")
    assert d is Decision.ALLOW
    assert allowed(d)


def test_act_deny():
    eng = ConsentEngine(ask_timeout_s=2.0)
    _answer_async(eng, Decision.DENY)
    d = eng.request("mac_open_app", ToolClass.ACT, "открыть Safari")
    assert d is Decision.DENY
    assert not allowed(d)
    assert "отклонил" in refusal_text(d)


def test_act_timeout_denies():
    eng = ConsentEngine(ask_timeout_s=0.05)
    d = eng.request("mac_open_app", ToolClass.ACT, "открыть Safari")
    assert d is Decision.TIMEOUT
    assert not allowed(d)
    assert "не ответил" in refusal_text(d)
    # request must not linger in the queue after timeout
    assert eng.pending() is None


def test_grant_suppresses_second_dialog():
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=2.0, grant_ttl_s=900.0, clock=clk)
    _answer_async(eng, Decision.ALLOW_GRANT)
    d1 = eng.request("mac_open_app", ToolClass.ACT, "открыть Safari")
    assert d1 is Decision.ALLOW_GRANT
    assert eng.grant_active(ToolClass.ACT) > 0
    # second ACT within TTL is auto — no dialog, so no answerer needed
    d2 = eng.request("mac_open_url", ToolClass.ACT, "открыть ссылку")
    assert d2 is Decision.AUTO


def test_grant_expires():
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=0.05, grant_ttl_s=900.0, clock=clk)
    _answer_async(eng, Decision.ALLOW_GRANT)
    eng.request("mac_open_app", ToolClass.ACT, "x")
    clk.advance(901.0)
    assert eng.grant_active(ToolClass.ACT) == 0
    # next ACT asks again → times out (no answerer)
    d = eng.request("mac_open_app", ToolClass.ACT, "x")
    assert d is Decision.TIMEOUT


def test_pause_refuses_everything_including_read():
    eng = ConsentEngine()
    eng.paused = True
    assert eng.request("mac_screenshot", ToolClass.READ, "x") is Decision.PAUSED
    assert eng.request("mac_open_app", ToolClass.ACT, "x") is Decision.PAUSED
    assert "паузу" in refusal_text(Decision.PAUSED)


def test_revoke_grants():
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=2.0, grant_ttl_s=900.0, clock=clk)
    _answer_async(eng, Decision.ALLOW_GRANT)
    eng.request("mac_open_app", ToolClass.ACT, "x")
    assert eng.grant_active(ToolClass.ACT) > 0
    eng.revoke_grants()
    assert eng.grant_active(ToolClass.ACT) == 0
