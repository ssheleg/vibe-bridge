"""The floating pet's contract, minus the pixels.

A GUI window cannot be asserted on in a headless suite, so what is tested is
everything around it: that an unavailable platform is REPORTED rather than
crashed on, that a failure to appear never takes the bridge with it, and that
the window is a view onto the bridge rather than a second implementation of
the character.
"""
from __future__ import annotations

from tests.webui_rules import reduced_motion_kills_animation
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
    """Дыхание есть, и оно гасится по просьбе системы.

    Прежняя версия проверяла `"prefers-reduced-motion" in page` — и строка
    есть в файле ДВАЖДЫ: в правиле и в поясняющем комментарии. Доказано
    подсадкой (аудит 2026-09-01): удаление настоящего `@media`-правила
    оставляло тест зелёным, то есть регрессия доступности прошла бы насквозь.
    Смотрим на правила, комментарии вырезаны.
    """
    from pathlib import Path

    import vibebridge

    webui = Path(vibebridge.__file__).parent / "webui"
    js = (webui / "mascot.js").read_text()
    assert "@keyframes vb-breathe" in js         # not frozen…

    for name in ("mascot.html", "mascot.js"):
        # Чтение правил — одно на все тесты (tests/webui_rules.py): тот же
        # слабый ассерт жил во втором тесте и держался только на том, что
        # строка пока встречается один раз.
        assert reduced_motion_kills_animation(name), \
            f"{name}: reduced-motion не гасит анимацию"


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
    assert source.count("setAutoresizingMask_") == 2      # main + widget views
    assert "NSViewWidthSizable" in source and "NSViewHeightSizable" in source


def test_the_pet_window_can_take_the_keyboard():
    """A borderless window returns NO from `canBecomeKeyWindow`, so the
    widget's text field could not be typed into at all. Titled with hidden
    chrome looks the same and can take focus."""
    from pathlib import Path

    source = Path(mw.__file__).read_text()
    assert "NSWindowStyleMaskBorderless" not in source
    assert "NSWindowStyleMaskTitled" in source
    assert "setTitlebarAppearsTransparent_(True)" in source
    # …but only when a control needs it, so dragging still does not steal it.
    assert "setBecomesKeyOnlyIfNeeded_(True)" in source


def test_appkit_does_not_compete_with_the_page_for_the_mouse():
    """`movableByWindowBackground` swallowed the mouse-down under a possible
    window drag, so `pointerup` never reached the page: clicking the pet did
    nothing. The page drags through the message handler instead."""
    from pathlib import Path

    source = Path(mw.__file__).read_text()
    assert "setMovableByWindowBackground_(False)" in source


# ── two windows, and the character's one never moves ───────────────────────
#
# Every earlier version resized ONE window around the character and anchored
# the arithmetic so the head "could not" move. The owner reported it jumping
# four times regardless — on open, on close, on a bubble appearing, on a
# bubble expiring. These tests hold the fix that replaced the arithmetic: the
# character's window is never resized by anything.


class _Rect:
    def __init__(self, x, y, w, h):
        self.origin = type("O", (), {"x": x, "y": y})()
        self.size = type("S", (), {"width": w, "height": h})()


class _Win:
    """Enough of an NSPanel to record what was done to it."""

    def __init__(self, x=1000.0, y=100.0, w=104.0, h=104.0):
        self._rect = _Rect(x, y, w, h)
        self.frames: list = []
        self.origins: list = []
        self.alpha = None
        self.ignores = None
        self.ordered_out = 0

    def frame(self):
        return self._rect

    def setFrame_display_(self, rect, _flag):
        self.frames.append(rect)
        self._rect = _Rect(rect.origin.x, rect.origin.y,
                           rect.size.width, rect.size.height)

    def setFrameOrigin_(self, point):
        self.origins.append((point[0], point[1]))
        self._rect = _Rect(point[0], point[1],
                           self._rect.size.width, self._rect.size.height)

    def setAlphaValue_(self, value):
        self.alpha = value

    def setIgnoresMouseEvents_(self, value):
        self.ignores = value

    def orderOut_(self, _sender):
        self.ordered_out += 1


def test_the_companion_hangs_off_the_characters_top_right_corner():
    x, y, w, _h = mw.side_frame((1000.0, 100.0, 104.0, 104.0), (340.0, 200.0))
    assert x + w == 1000.0 + 104.0             # right edges flush
    assert y == 100.0 + 104.0 - mw.GAP         # just above, with the overlap


def test_the_companion_stays_on_the_screen_it_was_given():
    """Above the character normally; below when there is no room above. A
    bubble half off the top of the display is a bubble nobody reads."""
    screen = (0.0, 0.0, 1440.0, 900.0)
    x, y, w, h = mw.side_frame((1300.0, 700.0, 104.0, 104.0),
                               (340.0, 400.0), screen)
    assert y >= 0.0 and y + h <= 900.0
    assert x >= 0.0 and x + w <= 1440.0


def test_a_resize_never_touches_the_characters_window(monkeypatch):
    """The whole point of the split. If this test can fail, the head can
    jump."""
    monkeypatch.setattr(mw, "visible_frame", lambda: None)
    pet, side = _Win(), _Win(w=340.0, h=140.0)
    mw._Bridge(pet, side).handle({"type": "resize", "w": 340, "h": 520})
    assert pet.frames == [] and pet.origins == []
    assert side.frames and side.frames[-1].size.height == 520.0


def test_the_companion_follows_the_character_when_it_is_dragged(monkeypatch):
    monkeypatch.setattr(mw, "visible_frame", lambda: None)
    pet, side = _Win(), _Win(w=340.0, h=140.0)
    mw._Bridge(pet, side).handle({"type": "drag", "dx": -50, "dy": -30})
    # Cocoa's Y grows upward, the page's downward — hence the sign flip.
    assert pet.origins == [(950.0, 130.0)]
    rect = side.frames[-1]
    assert rect.origin.x + rect.size.width == 950.0 + 104.0
    assert rect.origin.y == 130.0 + 104.0 - mw.GAP


def test_an_absurd_resize_is_clamped_not_obeyed(monkeypatch):
    monkeypatch.setattr(mw, "visible_frame", lambda: None)
    side = _Win(w=340.0, h=140.0)
    mw._Bridge(_Win(), side).handle({"type": "resize", "w": 0, "h": 0})
    assert side.frames[-1].size.width >= mw.SIDE_MIN[0]
    assert side.frames[-1].size.height >= mw.SIDE_MIN[1]


def test_the_companion_is_hidden_by_alpha_not_by_being_ordered_out(monkeypatch):
    """An off-screen WKWebView gets its timers throttled, and the companion is
    the surface that must notice a notification arriving while nobody looks at
    it. Alpha 0 plus mouse transparency is invisible AND still running."""
    monkeypatch.setattr(mw, "visible_frame", lambda: None)
    side = _Win(w=340.0, h=140.0)
    bridge = mw._Bridge(_Win(), side)
    bridge.handle({"type": "side", "show": False})
    assert side.alpha == 0.0 and side.ignores is True
    assert side.ordered_out == 0
    bridge.handle({"type": "side", "show": True})
    assert side.alpha == 1.0 and side.ignores is False


def test_a_click_on_the_character_reaches_the_other_window():
    """Two documents cannot see each other's DOM, so the click travels through
    the native side."""
    class _View:
        def __init__(self):
            self.js: list = []

        def evaluateJavaScript_completionHandler_(self, js, _handler):
            self.js.append(js)

    view = _View()
    mw._Bridge(_Win(), _Win(), view).handle({"type": "toggle"})
    assert view.js and "vbToggle" in view.js[0]


def test_a_bridge_without_a_companion_still_never_raises():
    """The single-window path is gone, but a message arriving before the
    companion exists must not take the widget down."""
    pet = _Win()
    bridge = mw._Bridge(pet)
    bridge.handle({"type": "resize", "w": 300, "h": 300})
    bridge.handle({"type": "side", "show": True})
    bridge.handle({"type": "toggle"})
    bridge.handle({"type": "drag", "dx": 4, "dy": 0})
    assert pet.frames == []                    # still never resized
    assert pet.origins == [(1004.0, 100.0)]


def test_the_pet_page_never_asks_to_be_resized():
    """A page that can ask for a resize is a page that can move the head."""
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui"
            / "mascot.html").read_text()
    fit = page.split("function fitWindow(){", 1)[1].split("\n}", 1)[0]
    assert "IS_PET" in fit                      # guarded on the first line
    # …and the pet's branch of `paint` returns before anything is measured.
    head = page.split("function paint(snap, force){", 1)[1] \
               .split("const showBubble", 1)[0]
    assert "IS_PET" in head and "return;" in head
    assert "fitWindow" not in head and "syncSide" not in head


def test_the_widget_is_two_documents_and_the_native_side_names_them():
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui"
            / "mascot.html").read_text()
    source = Path(mw.__file__).read_text()
    assert "dataset.surface" in page
    assert '"pet"' in source and '"side"' in source
    assert "surface=" in source


def test_the_widget_acts_on_the_first_click():
    """AppKit gives a non-key window's first mouse-down to the window, not to
    the view, unless the view opts in — and `WKWebView` answers NO. Measured
    2026-08-31: click one did nothing, click two opened the companion. A pet
    that never takes focus gets nothing but first clicks."""
    from pathlib import Path

    source = Path(mw.__file__).read_text()
    assert "acceptsFirstMouse_" in source
    # …and it is the class the WIDGET uses. The main window is an ordinary key
    # window and keeps the plain one.
    widget = source.split("def _make_view", 1)[1].split("def _build", 1)[0]
    assert "_webview_class().alloc()" in widget
    assert "WebKit.WKWebView.alloc()" not in widget


# ── the pet stays where the owner put it ───────────────────────────────────


def test_a_remembered_origin_is_used_as_is_when_it_fits():
    assert mw.clamp_origin((1200.0, 300.0), (104, 104),
                           (0.0, 0.0, 1728.0, 1080.0)) == (1200.0, 300.0)


def test_a_remembered_origin_from_a_display_that_is_gone_is_pulled_back():
    """The case that matters and cannot be reproduced by looking at the screen
    you have: a position saved on a second monitor. A pet nobody can see is a
    pet nobody can drag back."""
    x, y = mw.clamp_origin((3400.0, -200.0), (104, 104),
                           (0.0, 0.0, 1728.0, 1080.0))
    assert 0.0 <= x <= 1728.0 - 104 and 0.0 <= y <= 1080.0 - 104


def test_without_a_screen_the_origin_is_trusted_rather_than_invented():
    assert mw.clamp_origin((3400.0, -200.0), (104, 104)) == (3400.0, -200.0)


def test_the_release_of_a_drag_reports_the_new_position_once():
    """One write per gesture. The page posts `drop` on `pointerup`, not a
    message per delta — sixty writes a second is not persistence, it is
    thrash."""
    seen = []
    pet = _Win(x=950.0, y=130.0)
    mw._Bridge(pet, on_move=seen.append).handle({"type": "drop"})
    assert seen == [(950.0, 130.0)]


def test_a_drop_without_a_listener_never_raises():
    pet = _Win()
    mw._Bridge(pet).handle({"type": "drop"})
    assert pet.frames == []


def test_the_position_saver_is_actually_wired_to_both_seams():
    """Three functions in this project have been written, unit-tested and never
    called (`prune`, `migrate_from_state`, `top_up`). The unit test above
    proves the handler works; these two lines prove somebody calls it."""
    from pathlib import Path

    import vibebridge

    desktop = Path(mw.__file__).read_text()
    app = (Path(vibebridge.__file__).parent / "app.py").read_text()
    assert "self._on_move)" in desktop           # window -> bridge
    assert "on_move=lambda pos: _remember_pet" in app   # app -> window
    assert "position=state.pet_pos" in app             # …and it is restored


def test_the_page_separates_a_drag_from_a_click():
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui"
            / "mascot.html").read_text()
    # Up to the END of the listener, not the first "});" — that one closes
    # `postMessage({...})` and cut the branch in half.
    branch = page.split("const wasDrag", 1)[1].split("\n  });", 1)[0]
    assert 'type:"drop"' in branch.replace(" ", "")
    assert "requestToggle()" in branch

