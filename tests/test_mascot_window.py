"""The floating pet's contract, minus the pixels.

A GUI window cannot be asserted on in a headless suite, so what is tested is
everything around it: that an unavailable platform is REPORTED rather than
crashed on, that a failure to appear never takes the bridge with it, and that
the window is a view onto the bridge rather than a second implementation of
the character.
"""
from __future__ import annotations

from vibebridge import mascot_window as mw


def test_off_macos_it_declines_with_a_reason(monkeypatch):
    monkeypatch.setattr(mw.sys, "platform", "linux")
    ok, why = mw.available()
    assert not ok and "macOS" in why and "в панели" in why


def test_a_missing_webkit_is_named_precisely(monkeypatch):
    monkeypatch.setattr(mw.sys, "platform", "darwin")
    import builtins
    real = builtins.__import__

    def no_webkit(name, *a, **kw):
        if name == "WebKit":
            raise ImportError("нет WebKit")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_webkit)
    ok, why = mw.available()
    assert not ok and "WebKit" in why


def test_show_reports_instead_of_raising_when_unavailable(monkeypatch):
    monkeypatch.setattr(mw, "available", lambda: (False, "нет условий"))
    ok, why = mw.MascotWindow("http://127.0.0.1:48620/mascot").show()
    assert ok is False and why == "нет условий"


def test_a_window_that_cannot_build_does_not_take_the_bridge_down(monkeypatch):
    """The mascot is decoration on a security tool. It must never be the
    reason the bridge is not running."""
    monkeypatch.setattr(mw, "available", lambda: (True, ""))

    win = mw.MascotWindow("http://127.0.0.1:48620/mascot")
    monkeypatch.setattr(win, "_build",
                        lambda: (_ for _ in ()).throw(RuntimeError("AppKit нет")))
    ok, why = win.show()
    assert ok is False and "AppKit нет" in why


def test_the_window_is_a_view_not_a_second_character():
    """It loads the bridge's own page; the drawing lives in mascot.js."""
    from pathlib import Path

    source = Path(mw.__file__).read_text()
    assert "WKWebView" in source
    assert "svg" not in source.lower()        # no drawing here
    assert "loadRequest_" in source


def test_it_does_not_steal_focus():
    """A pet that pulls focus mid-sentence gets switched off in a week."""
    from pathlib import Path

    source = Path(mw.__file__).read_text()
    assert "NSWindowStyleMaskNonactivatingPanel" in source
    assert "orderFrontRegardless" in source
