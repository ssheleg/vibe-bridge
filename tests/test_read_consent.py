"""Asking before a READ — off by default, and that default is the vision's.

The alignment test in `docs/ux/vision.md` §9 answers this itself: every agent
action is visible to the owner "до исполнения (ACT) или в журнале сразу после
(READ)". So READ executing immediately is the product's decision, not an
oversight, and it stays the default.

What was missing is the choice. `screenshot` is a READ, and a person who wants
to be asked before their screen is read had no way to say so — the setting did
not exist in any form. Principle 1 warns against hiding "разрешить всё" in
settings; this is the opposite move, and it is switchable from the panel
because alignment test §5 requires the owner to manage it without a terminal.
"""
from __future__ import annotations

import threading

from vibebridge.consent import ConsentEngine, Decision, ToolClass


def test_read_runs_immediately_by_default():
    engine = ConsentEngine()
    assert engine.request("screenshot", ToolClass.READ, "смотрю") is Decision.AUTO
    assert engine.pending() is None


def test_read_asks_when_the_owner_turned_it_on():
    engine = ConsentEngine(ask_for_read=True, ask_timeout_s=5)
    result: list[Decision] = []
    t = threading.Thread(
        target=lambda: result.append(
            engine.request("screenshot", ToolClass.READ, "смотрю")))
    t.start()
    for _ in range(200):
        if engine.pending() is not None:
            break
        threading.Event().wait(0.01)

    req = engine.pending()
    assert req is not None and req.tool == "screenshot"
    req.resolve(Decision.ALLOW, by="test")
    t.join(timeout=5)
    assert result == [Decision.ALLOW]


def test_a_refused_read_is_refused_not_downgraded():
    engine = ConsentEngine(ask_for_read=True, ask_timeout_s=5)
    result: list[Decision] = []
    t = threading.Thread(
        target=lambda: result.append(
            engine.request("screenshot", ToolClass.READ, "смотрю")))
    t.start()
    for _ in range(200):
        if engine.pending() is not None:
            break
        threading.Event().wait(0.01)
    engine.pending().resolve(Decision.DENY, by="test")
    t.join(timeout=5)
    assert result == [Decision.DENY]


def test_silence_still_means_refusal_for_reads():
    engine = ConsentEngine(ask_for_read=True, ask_timeout_s=0.1)
    assert engine.request("screenshot", ToolClass.READ,
                          "смотрю") is Decision.TIMEOUT


def test_a_grant_covers_later_calls_of_the_same_read():
    """Грант работает и в строгом режиме — но поимённо: разрешив «смотрю на
    экран», владелец не разрешил заодно перечислять его приложения (A-8)."""
    engine = ConsentEngine(ask_for_read=True, ask_timeout_s=0.2)
    result: list[Decision] = []
    t = threading.Thread(
        target=lambda: result.append(
            engine.request("screenshot", ToolClass.READ, "смотрю")))
    t.start()
    for _ in range(200):
        if engine.pending() is not None:
            break
        threading.Event().wait(0.01)
    engine.pending().resolve(Decision.ALLOW_GRANT, by="test")
    t.join(timeout=5)
    # Тот же снимок едет на гранте...
    assert engine.request("screenshot", ToolClass.READ, "снова") is Decision.AUTO
    # ...а сосед спрашивает заново: отвечать некому, значит истекает.
    assert engine.request("list_apps", ToolClass.READ, "окна") is Decision.TIMEOUT


def test_pause_still_swallows_reads_whole():
    """Principle 3: on pause the device is indistinguishable from switched
    off. Asking would tell the agent something is there."""
    engine = ConsentEngine(ask_for_read=True)
    engine.paused = True
    assert engine.request("screenshot", ToolClass.READ,
                          "смотрю") is Decision.PAUSED
