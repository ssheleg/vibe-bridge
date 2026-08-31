"""T-PLATFORM: Win/Linux capability-паки — единый контракт имён, честные
причины по таблице паритета (spec §5), блоклисты. Хост-ОС не нужна: паки
импортируются напрямую, среда сессии и which подменяются.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.capabilities import CapabilityError, build_capabilities
from vibebridge.platforms import linux as lx
from vibebridge.platforms import windows as win


class FakeRunner:
    def __init__(self, out="ok"):
        self.out, self.calls = out, []

    def run(self, argv, *, timeout=20.0, input_text=None):
        self.calls.append({"argv": argv, "input": input_text})
        return self.out


# ── единый контракт имён на всех трёх ОС ────────────────────────────────────

def test_all_packs_expose_identical_tool_names():
    darwin_names = set(build_capabilities())          # хост — macOS
    assert set(win.build_capabilities()) == darwin_names
    assert set(lx.build_capabilities()) == darwin_names


# ── windows ─────────────────────────────────────────────────────────────────

def test_win_automation_blocklist():
    r = FakeRunner()
    for bad in ("cmd.exe /c dir", "Start-Process powershell",
                "reg add HKLM\\...", "cmdkey /list"):
        with pytest.raises(CapabilityError, match="заблокирован"):
            win._automation(r, {"script": bad})
    assert r.calls == []


def test_win_open_url_via_powershell_and_scheme_guard():
    r = FakeRunner()
    win._open_url(r, {"url": "https://example.com"})
    assert r.calls[0]["argv"][0] == "powershell"
    assert "Start-Process 'https://example.com'" in r.calls[0]["argv"][-1]
    with pytest.raises(CapabilityError):
        win._open_url(r, {"url": "file:///etc/passwd"})


def test_win_clipboard_roundtrip_argv():
    r = FakeRunner("буфер")
    assert win._clipboard_read(r, {}) == "буфер"
    win._clipboard_write(r, {"text": "привет"})
    assert r.calls[1]["input"] == "привет"
    assert "Set-Clipboard" in r.calls[1]["argv"][-1]


def test_win_probe_shortcut_unavailable_and_missing_pkg_honest(monkeypatch):
    st, reason = win.probe_extras("shortcut_run")
    assert st == "unavailable" and "macOS" in reason
    monkeypatch.setenv("SESSIONNAME", "Console")
    monkeypatch.setitem(sys.modules, "mss", None)   # → ImportError on import
    st, reason = win.probe_extras("screenshot")
    assert st == "needs-permission" and "vibe-bridge[windows]" in reason


def test_win_notify_without_package_is_honest():
    with pytest.raises(CapabilityError, match=r"vibe-bridge\[windows\]"):
        win._notify(FakeRunner(), {"text": "x"})


# ── linux: маршрутизация по сессии ──────────────────────────────────────────

@pytest.fixture()
def x11(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")


@pytest.fixture()
def wayland_gnome(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(lx.shutil, "which", lambda b: None)


def test_linux_clipboard_routes_by_session(x11, monkeypatch):
    r = FakeRunner("из буфера")
    assert lx._clipboard_read(r, {}) == "из буфера"
    assert r.calls[0]["argv"][0] == "xclip"
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    lx._clipboard_write(r, {"text": "т"})
    assert r.calls[1]["argv"][0] == "wl-copy"


def test_linux_frontmost_honest_on_gnome_wayland(wayland_gnome):
    with pytest.raises(CapabilityError, match="Wayland"):
        lx._frontmost(FakeRunner(), {})
    st, reason = lx.probe_extras("frontmost")
    assert st == "unavailable" and "kdotool" in reason


def test_linux_screenshot_wayland_needs_grim(wayland_gnome):
    st, reason = lx.probe_extras("screenshot")
    assert st == "unavailable" and "grim" in reason
    with pytest.raises(CapabilityError, match="grim/spectacle"):
        lx._screenshot(FakeRunner(), {})


def test_linux_x11_list_apps_parses_wmctrl(x11):
    r = FakeRunner("0x01  0 host Термінал\n0x02  0 host Браузер\n")
    out = lx._list_apps(r, {})
    assert out.splitlines() == ["Термінал", "Браузер"]
    assert r.calls[0]["argv"][0] == "wmctrl"


def test_linux_automation_deferred_honest():
    st, reason = lx.probe_extras("automation")
    assert st == "unavailable" and "ydotool" in reason
    with pytest.raises(CapabilityError, match="отложена"):
        lx._automation_stub(FakeRunner(), {"script": "x"})


def test_linux_no_session_is_unavailable(monkeypatch):
    for var in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    st, reason = lx.probe_extras("screenshot")
    assert st == "unavailable" and "сесси" in reason


# ── tray backend: selection + pure helpers (no GUI) ─────────────────────────

def test_tray_title_states():
    from vibebridge.consent import ConsentEngine, ToolClass
    from vibebridge.tray import tray_title
    eng = ConsentEngine()
    assert tray_title(eng) == "🤖"
    eng._grant_until[ToolClass.ACT] = eng._clock() + 600
    assert tray_title(eng) == "🤖⏳"
    eng.paused = True
    assert tray_title(eng) == "⏸"          # pause wins over grant


def test_notifier_never_raises(monkeypatch):
    from vibebridge import tray
    # force the no-op / osascript fallback path by hiding desktop_notifier
    monkeypatch.setitem(sys.modules, "desktop_notifier", None)
    notify = tray.make_notifier()
    notify("заголовок", "текст")            # must not raise on any OS


# ── the probe must not turn "cannot answer" into "available" ───────────────

def test_unknown_screen_permission_is_not_reported_as_available(monkeypatch):
    """Caught live 2026-08-30 on the packaged .app: Quartz was not in the
    bundle, so the preflight returned None, and the map advertised
    `screenshot: available` while the call failed with "could not create
    image from display". A capability map that guesses optimistically is
    worse than none — the robot plans around it.
    """
    from vibebridge import capabilities as caps_mod

    monkeypatch.setattr(caps_mod, "_screen_capture_granted", lambda: None)
    caps = caps_mod.build_capabilities()
    got = caps_mod.probe_availability(caps, which=lambda b: f"/usr/bin/{b}")
    assert got["screenshot"]["status"] == "needs-permission"
    assert got["screenshot"]["reason"]


def test_granted_screen_permission_still_reads_available(monkeypatch):
    from vibebridge import capabilities as caps_mod

    monkeypatch.setattr(caps_mod, "_screen_capture_granted", lambda: True)
    caps = caps_mod.build_capabilities()
    got = caps_mod.probe_availability(caps, which=lambda b: f"/usr/bin/{b}")
    assert got["screenshot"]["status"] == "available"


# ── notifications carry the app's identity and land in the journal ─────────

def test_the_journal_says_what_the_robot_put_on_the_screen():
    """It read "показываю уведомление на Маке" — true and useless. The
    journal's whole job is answering what the robot actually did."""
    from vibebridge.capabilities import build_capabilities

    cap = build_capabilities()["notify"]
    line = cap.summary({"title": "заголовок", "text": "текст"})
    assert "заголовок" in line and "текст" in line


def test_a_notification_without_a_title_still_reads(monkeypatch):
    from vibebridge.capabilities import build_capabilities

    cap = build_capabilities()["notify"]
    assert "без заголовка" in cap.summary({"text": "только текст"})


def test_notifications_go_through_the_app_notifier_when_there_is_one():
    """osascript posts as Script Editor, so the toast arrived with no name
    and a generic icon — as if it came from nowhere."""
    from vibebridge import capabilities as caps

    seen = []
    caps.set_notifier(lambda t, x: seen.append((t, x)))
    try:
        class _R:
            def run(self, *a, **kw):
                raise AssertionError("не должен звать osascript")

        caps._notify(_R(), {"title": "Вася", "text": "привет"})
        assert seen == [("Вася", "привет")]
    finally:
        caps.set_notifier(None)


def test_without_an_app_notifier_it_still_notifies():
    from vibebridge import capabilities as caps

    caps.set_notifier(None)
    calls = []

    class _R:
        def run(self, argv, **kw):
            calls.append(argv)
            return ""

    caps._notify(_R(), {"title": 'он сказал "да"', "text": "текст"})
    assert calls and calls[0][0] == "osascript"
    # Quotes are neutralised so the AppleScript literal cannot be broken open.
    assert '"да"' not in calls[0][2]
