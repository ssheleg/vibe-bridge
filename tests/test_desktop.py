"""The floating pet's contract, minus the pixels.

A GUI window cannot be asserted on in a headless suite, so what is tested is
everything around it: that an unavailable platform is REPORTED rather than
crashed on, that a failure to appear never takes the bridge with it, and that
the window is a view onto the bridge rather than a second implementation of
the character.
"""
from __future__ import annotations

from vibebridge import desktop as mw


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


# ── the app's own window ───────────────────────────────────────────────────

def test_the_app_has_a_window_of_its_own():
    """Reported 2026-08-31: "приложение никак не открывается, не видно на
    экране". It was true — `LSUIElement` keeps it out of the Dock, the panel
    lived in a browser, and the app owned no window at all."""
    win = mw.MainWindow("http://127.0.0.1:48620/")
    assert win.visible is False
    assert mw.MainWindow.SIZE[0] > 0


def test_the_window_reports_instead_of_raising_when_unavailable(monkeypatch):
    monkeypatch.setattr(mw, "available", lambda: (False, "нет условий"))
    ok, why = mw.MainWindow("http://x").show()
    assert ok is False and why == "нет условий"


def test_closing_the_window_does_not_destroy_it():
    """The tray reopens it; a released window would be gone for the session."""
    from pathlib import Path
    source = Path(mw.__file__).read_text()
    assert "setReleasedWhenClosed_(False)" in source


# ── the pet's drag, which CSS could not do ─────────────────────────────────

class _Frame:
    def __init__(self):
        self.origin = type("P", (), {"x": 100.0, "y": 200.0})()


class _Panel:
    def __init__(self):
        self._frame = _Frame()
        self.moved_to = None

    def frame(self):
        return self._frame

    def setFrameOrigin_(self, point):
        self.moved_to = point


def test_a_drag_message_moves_the_window():
    panel = _Panel()
    mw._Bridge(panel).handle({"type": "drag", "dx": 10, "dy": 5})
    # Cocoa's Y grows upward, the page's downward — hence the sign flip.
    assert panel.moved_to == (110.0, 195.0)


def test_an_unknown_message_is_ignored():
    panel = _Panel()
    mw._Bridge(panel).handle({"type": "что-то новое"})
    assert panel.moved_to is None


def test_a_malformed_message_never_raises():
    panel = _Panel()
    mw._Bridge(panel).handle({"type": "drag"})          # no dx/dy
    mw._Bridge(panel).handle("не словарь")
    assert panel.moved_to is None


def test_the_menu_can_ask_for_the_main_window():
    called = []
    mw._Bridge(_Panel(), on_panel=lambda: called.append(1)).handle(
        {"type": "panel"})
    assert called == [1]


def test_the_page_does_not_rely_on_an_electron_only_property():
    """`-webkit-app-region: drag` is an Electron extension. WKWebView ignores
    it, which is exactly why the pet could not be moved."""
    from pathlib import Path

    import vibebridge
    page = (Path(vibebridge.__file__).parent / "webui" / "mascot.html").read_text()
    # Look at the RULE, not the file: the comment above it names the property
    # precisely in order to explain why it is not used.
    rule = page.split(".mascot-body{", 1)[1].split("}", 1)[0]
    assert "app-region" not in rule
    assert "messageHandlers.vb" in page


def test_the_pet_breathes_but_stops_when_asked():
    from pathlib import Path

    import vibebridge
    page = (Path(vibebridge.__file__).parent / "webui" / "mascot.html").read_text()
    assert "vb-breathe" in page                 # not frozen
    assert "prefers-reduced-motion" in page     # …and it degrades to calm


def test_a_drag_is_not_read_as_a_click():
    """Moving the pet must not open its menu on release."""
    from pathlib import Path

    import vibebridge
    page = (Path(vibebridge.__file__).parent / "webui" / "mascot.html").read_text()
    assert "moved > 4" in page


def test_the_window_is_told_to_fit_what_is_drawn():
    """A fixed 360x240 frame clipped the quick menu's first rows, and every
    empty pixel of a transparent window swallows a click meant for the
    desktop behind it."""
    from pathlib import Path

    import vibebridge
    page = (Path(vibebridge.__file__).parent / "webui" / "mascot.html").read_text()
    assert "type:\"resize\"" in page.replace(" ", "")
    assert "scrollHeight" in page


def test_a_resize_keeps_the_bottom_edge_so_the_pet_does_not_jump():
    class _Rect:
        def __init__(self):
            self.origin = type("P", (), {"x": 1000.0, "y": 100.0})()
            self.size = type("S", (), {"width": 360.0, "height": 240.0})()

    class _P:
        def __init__(self):
            self.frame_set = None

        def frame(self):
            return _Rect()

        def setFrame_display_(self, rect, _flag):
            self.frame_set = rect

    panel = _P()
    mw._Bridge(panel).handle({"type": "resize", "w": 300, "h": 500})
    assert panel.frame_set is not None
    # Cocoa's origin is bottom-left: same y means the pet stays put and the
    # window grows upward, away from the Dock.
    assert panel.frame_set.origin.y == 100.0
    assert panel.frame_set.size.height == 500.0
    # Right edge stays anchored too, so it does not drift off-screen.
    assert panel.frame_set.origin.x + 300 == 1000.0 + 360.0


def test_an_absurd_resize_is_clamped_not_obeyed():
    class _P:
        def __init__(self):
            self.frame_set = None

        def frame(self):
            r = type("R", (), {})()
            r.origin = type("P", (), {"x": 0.0, "y": 0.0})()
            r.size = type("S", (), {"width": 360.0, "height": 240.0})()
            return r

        def setFrame_display_(self, rect, _flag):
            self.frame_set = rect

    panel = _P()
    mw._Bridge(panel).handle({"type": "resize", "w": 0, "h": 0})
    assert panel.frame_set.size.height >= 90
    assert panel.frame_set.size.width >= 120


def test_the_page_does_not_stretch_to_the_window_it_is_measuring():
    """`height:100%` made the two size to each other: the window fitted the
    body, the body filled the window, and it never shrank back."""
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "mascot.html").read_text()
    css = page.split("<style>", 1)[1].split("</style>", 1)[0]
    # Comments explain why the property is absent; look at the rules only.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S).replace(" ", "")
    for rule in re.findall(r"(?:^|\})\s*(html|body)\s*\{([^}]*)", css):
        assert "height:100%" not in rule[1]


def test_the_web_view_follows_the_window_it_lives_in():
    """It kept its creation size while the window resized around it: the
    dialogue opened onto blank white margins and the character jumped."""
    from pathlib import Path

    source = Path(mw.__file__).read_text()
    assert source.count("setAutoresizingMask_") == 2      # both windows
    assert "NSViewWidthSizable" in source and "NSViewHeightSizable" in source
