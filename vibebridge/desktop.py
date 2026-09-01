"""Native windows that host the bridge's own pages.

Three of them, all `WKWebView`: the main window shows the panel, and the
floating widget is TWO windows — the character in one, everything it says in
another. None of them draws anything itself; the pages are the same ones a
browser gets, so there is exactly one implementation of every screen.

**Why two windows for one widget.** The character and its bubble used to share
a single window that was resized to fit whatever was drawn. Every version of
that arrangement anchored the arithmetic so the head "could not" move — on the
frame first, then on the character's own corner — and the owner reported the
head jumping four times regardless: on open, on close, on a bubble appearing,
on a bubble expiring. The arithmetic was not the problem. A resize is a
relayout, a relayout is a chance for the thing inside to land somewhere else,
and no amount of anchoring removes the chance.

So the character's window is now **never resized by anything**. Its size is a
constant, and the only code that touches its origin is the drag handler. The
companion window resizes as freely as it likes, because nothing about where
the character sits depends on it.

Why not Electron, since it was asked: the app already carries a Python runtime
and its dependencies (146 MB). Electron would add a whole Chromium on top —
roughly double the download — plus a second toolchain to sign, notarize and
update, and it would not replace one line of the Python that does the actual
work: MCP, consent, the TCC-gated calls. `WKWebView` renders the same pages
with nothing new shipped. The one thing Electron would genuinely buy is a
single windowing story for Windows and Linux, and that is board row B-23.

Why a panel and not a window: an `NSPanel` marked non-activating can be
clicked without stealing focus from whatever the owner is typing in. A mascot
that pulls focus to say "робот в сети" is a mascot that gets switched off in a
week.

macOS only. Everywhere else this degrades to the panel's own character, which
is the honest outcome rather than a stub that pretends.
"""
from __future__ import annotations

import sys

#: The character's window, and it NEVER changes size — see the module note.
#: 104 holds a 72px character plus the drop-shadow's bleed and the two pixels
#: the breath travels.
PET_SIZE = (104, 104)
#: The companion's starting frame. 340 wide because the three consent answers
#: sit on one line at that width and «Отклонить» was clipped off the right
#: edge at 300 (seen on screen 2026-08-31) — a refusal button you cannot reach
#: is the worst one to lose. It resizes to content from there.
SIDE_START = (340, 140)
#: Floors for a measurement that arrived as zero mid-render.
SIDE_MIN = (200, 80)
#: How far the companion's bottom edge overlaps the character's top, so the
#: two read as one widget. It lands inside the pet page's transparent padding,
#: so nothing is covered.
GAP = 6
#: Where the pet sits on first run: bottom-right, clear of the Dock.
MARGIN = (28, 96)
#: Kept as the widget's nominal footprint for callers that quote a size.
DEFAULT_SIZE = PET_SIZE


def available() -> tuple[bool, str]:
    """Can this machine show a native window, and if not, why not."""
    if sys.platform != "darwin":
        return False, "плавающий питомец пока только на macOS — в панели он есть"
    try:
        import WebKit  # noqa: F401
    except ImportError:
        return False, ("нет pyobjc-framework-WebKit — поставьте "
                       "vibe-bridge[macos]")
    return True, ""


def side_frame(pet, size, screen=None, *, gap: float = GAP):
    """Where the companion window goes, given where the character is.

    Pure, and pure on purpose: this is the whole geometry of the widget, and a
    calculation that can only be checked by looking at a screen is a
    calculation that gets checked once. `pet` and `screen` are
    (x, y, w, h) in Cocoa coordinates — y grows upward. `screen` of None means
    "do not clamp", which is what a headless caller passes.

    The companion's bottom-RIGHT corner meets the character's top-right, so it
    grows up and to the left, away from the screen edge the pet defaults to.
    """
    px, py, pw, ph = pet
    sw, sh = size
    x = px + pw - sw               # right edges flush
    y = py + ph - gap              # just above the character
    if screen is None:
        return (x, y, sw, sh)
    scr_x, scr_y, scr_w, scr_h = screen
    if y + sh > scr_y + scr_h:     # no room above — hang it below instead
        y = py - sh + gap
    y = min(max(y, scr_y), scr_y + scr_h - sh)
    x = min(max(x, scr_x), scr_x + scr_w - sw)
    return (x, y, sw, sh)


def clamp_origin(origin, size, screen=None):
    """Keep a remembered position on a screen that may have changed.

    Pure, for the same reason `side_frame` is: the case that matters — a
    display unplugged between sessions — is the one you cannot reproduce by
    looking at the screen you have. `screen` of None means "cannot ask", and
    the origin is then trusted as-is rather than clamped to a fabricated
    display.
    """
    x, y = float(origin[0]), float(origin[1])
    if screen is None:
        return (x, y)
    w, h = float(size[0]), float(size[1])
    sx, sy, sw, sh = screen
    return (min(max(x, sx), sx + sw - w), min(max(y, sy), sy + sh - h))


def visible_frame():
    """The main screen's usable rect, or None when there is no screen to ask.

    None rather than a guess: `side_frame` treats it as "do not clamp", which
    is better than clamping a window to a fabricated display.
    """
    try:                                    # pragma: no cover - needs a screen
        import AppKit
        f = AppKit.NSScreen.mainScreen().visibleFrame()
        return (f.origin.x, f.origin.y, f.size.width, f.size.height)
    except Exception:                       # noqa: BLE001
        return None


#: Built once, on first use: registering an Objective-C class twice under one
#: name raises.
_WEBVIEW_CLASS = None


def _webview_class():                       # pragma: no cover - needs WebKit
    """A `WKWebView` that acts on the FIRST click.

    AppKit hands a non-key window's first mouse-down to the WINDOW rather than
    to the view, unless the view opts in through `acceptsFirstMouse:` — and
    `WKWebView` answers NO. Measured 2026-08-31: clicking the character did
    nothing at all and only a second click opened the companion. For a pet
    that deliberately never takes focus, every click is a first click, so the
    default is exactly wrong here.
    """
    global _WEBVIEW_CLASS
    if _WEBVIEW_CLASS is None:
        import WebKit

        class _FirstMouseWebView(WebKit.WKWebView):
            def acceptsFirstMouse_(self, _event):
                return True

        _WEBVIEW_CLASS = _FirstMouseWebView
    return _WEBVIEW_CLASS


class _Bridge:
    """Receives `window.webkit.messageHandlers.vb.postMessage(...)`.

    Dragging is done this way and not with CSS because the obvious property,
    `-webkit-app-region: drag`, is an ELECTRON extension: WKWebView ignores it
    entirely, which is why the pet sat immovable on the desktop until
    2026-08-31. `movableByWindowBackground` does not help either — the web
    view consumes the mouse events before the window sees them. So the page
    reports the delta and the window moves itself, which also keeps every
    button inside the page clickable.

    It is also the coordinator between the widget's two windows: the pet page
    cannot reach the companion page's DOM, so a click on the character arrives
    here as `toggle` and leaves as one line of JavaScript in the other view.
    """

    def __init__(self, pet, side=None, side_web=None, on_panel=None,
                 report=None, on_move=None) -> None:
        self._pet = pet
        self._side = side
        self._side_web = side_web
        self._on_panel = on_panel
        #: Where a failure goes. A widget that swallows its own errors is how
        #: a click on the character stopped opening anything and nothing
        #: anywhere said so — the failure looked exactly like a click that
        #: never happened (2026-08-31).
        self._report = report or (lambda _line, ok=False: None)
        #: Called once per drag GESTURE, on release — not once per delta. The
        #: position is worth persisting; sixty writes a second are not.
        self._on_move = on_move

    def handle(self, body) -> None:
        kind = None
        try:
            kind = body.get("type")
            if kind == "hello":
                # One line per window, and it is the line that would have
                # ended a four-round hunt in one: both surfaces reported
                # themselves as the pet, which said immediately that the
                # companion page had never loaded.
                self._report(f"виджет: окно «{body.get('surface')}» открылось",
                             ok=True)
            elif kind == "panel":
                if self._on_panel:
                    self._on_panel()
            elif kind == "drag":
                self._drag(float(body["dx"]), float(body["dy"]))
            elif kind == "toggle":
                # Throws rather than no-ops when the companion page is not
                # ready: `window.vbToggle && …` returned undefined and the
                # click vanished without a trace.
                self._eval("if(!window.vbToggle){throw new Error("
                           "'страница компаньона не готова')}window.vbToggle()")
            elif kind == "drop":
                self._remember()
            elif kind == "resize":
                self._place(body.get("w", 0), body.get("h", 0))
            elif kind == "side":
                self._reveal(bool(body.get("show")))
        except Exception as exc:            # noqa: BLE001 - reported, never fatal
            self._report(f"виджет: сообщение «{kind}» не отработало: {exc}")

    # ── the character ──────────────────────────────────────────────────────

    def _drag(self, dx: float, dy: float) -> None:
        """The ONLY thing in this file that moves the character's window."""
        frame = self._pet.frame()
        # Cocoa's Y grows upward; the page's grows downward.
        self._pet.setFrameOrigin_((frame.origin.x + dx, frame.origin.y - dy))
        if self._side is not None:          # the companion follows the head
            side = self._side.frame()
            self._place(side.size.width, side.size.height)

    def _remember(self) -> None:
        if self._on_move is None:
            return
        frame = self._pet.frame()
        self._on_move((float(frame.origin.x), float(frame.origin.y)))

    # ── the companion ──────────────────────────────────────────────────────

    def _place(self, w, h) -> None:
        """Position and size the COMPANION. Never the pet — that is the point.

        Called on every reported content size and after every drag, so the
        two windows stay one widget without either measuring the other.
        """
        if self._side is None:
            return
        import AppKit
        width = max(float(w or 0), SIDE_MIN[0])
        height = max(float(h or 0), SIDE_MIN[1])
        screen = visible_frame()
        if screen is not None:
            height = min(height, screen[3] - GAP * 2)
        p = self._pet.frame()
        x, y, width, height = side_frame(
            (p.origin.x, p.origin.y, p.size.width, p.size.height),
            (width, height), screen)
        self._side.setFrame_display_(
            AppKit.NSMakeRect(x, y, width, height), True)

    def _reveal(self, show: bool) -> None:
        """Show or hide the companion WITHOUT ordering it out.

        Alpha and mouse transparency rather than `orderOut:` deliberately: an
        off-screen WKWebView gets its timers throttled, and the companion is
        the surface that has to notice a notification arriving while nobody is
        looking at it. A window at alpha 0 that ignores the mouse is invisible
        and click-through while its page keeps running.
        """
        if self._side is None:
            return
        if show:
            frame = self._side.frame()
            self._place(frame.size.width, frame.size.height)
        self._side.setAlphaValue_(1.0 if show else 0.0)
        self._side.setIgnoresMouseEvents_(not show)

    def _eval(self, js: str) -> None:
        if self._side_web is None:
            self._report("виджет: окна-компаньона нет, клик по голове "
                         "передать некуда")
            return

        def done(_result, error):
            if error is not None:
                self._report(f"виджет: скрипт в компаньоне не выполнился: "
                             f"{error}")

        self._side_web.evaluateJavaScript_completionHandler_(js, done)


class MainWindow:
    """The app's own window, showing the panel.

    Without it the app opened to nothing: `LSUIElement` keeps it out of the
    Dock, the panel lived in a browser, and double-clicking the icon in
    /Applications produced no visible result at all — which is exactly how it
    was reported.
    """

    SIZE = (940, 720)

    def __init__(self, url: str) -> None:
        self._url = url
        self._window = None

    def show(self) -> tuple[bool, str]:  # pragma: no cover - needs a GUI
        ok, why = available()
        if not ok:
            return False, why
        try:
            if self._window is None:
                self._build()
            self._window.makeKeyAndOrderFront_(None)
            import AppKit
            AppKit.NSApp.activateIgnoringOtherApps_(True)
        except Exception as exc:              # noqa: BLE001
            return False, f"не удалось открыть окно: {exc}"
        return True, "окно открыто"

    @property
    def visible(self) -> bool:  # pragma: no cover - needs a GUI
        return bool(self._window is not None and self._window.isVisible())

    def _build(self) -> None:  # pragma: no cover - needs a GUI
        import AppKit
        import Foundation
        import WebKit

        width, height = self.SIZE
        rect = Foundation.NSMakeRect(0, 0, width, height)
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered, False)
        window.setTitle_("vibe-bridge")
        window.setReleasedWhenClosed_(False)   # closing hides, tray reopens
        window.center()
        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            rect, WebKit.WKWebViewConfiguration.alloc().init())
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable
                                     | AppKit.NSViewHeightSizable)
        webview.loadRequest_(Foundation.NSURLRequest.requestWithURL_(
            Foundation.NSURL.URLWithString_(self._url)))
        window.setContentView_(webview)
        self._window = window


class MascotWindow:
    """Owns both of the widget's windows.

    Safe to construct off macOS: `show` reports instead of raising, because a
    mascot failing to appear must never take the bridge down with it.
    """

    def __init__(self, url: str, on_panel=None, report=None, position=None,
                 on_move=None) -> None:
        self._url = url
        self._on_panel = on_panel
        self._report = report
        self._position = position
        self._on_move = on_move
        self._pet = None
        self._side = None
        self._views: list = []
        self._handler = None

    def show(self) -> tuple[bool, str]:  # pragma: no cover - needs a GUI
        ok, why = available()
        if not ok:
            return False, why
        try:
            self._build()
        except Exception as exc:              # noqa: BLE001 - never fatal
            return False, f"не удалось показать питомца: {exc}"
        return True, "питомец на экране"

    def hide(self) -> None:  # pragma: no cover - needs a GUI
        for panel in (self._pet, self._side):
            if panel is not None:
                panel.orderOut_(None)

    def close(self) -> None:  # pragma: no cover - needs a GUI
        self.hide()
        self._pet = self._side = None
        self._views = []

    @property
    def visible(self) -> bool:  # pragma: no cover - needs a GUI
        return bool(self._pet is not None and self._pet.isVisible())

    # ── construction ───────────────────────────────────────────────────────

    @staticmethod
    def _make_panel(rect):  # pragma: no cover - needs a GUI
        """One panel shape for both windows.

        Titled — not borderless — with the title bar made invisible. A
        borderless window returns NO from `canBecomeKeyWindow`, so the
        keyboard never reaches it: the widget's text field could not be typed
        into at all (reported 2026-08-31). Titled + hidden chrome looks
        identical and can take focus. Both windows share the shape so the pet
        keeps the click and drag behaviour that was measured to work.
        """
        import AppKit

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskFullSizeContentView
            | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        panel.setTitlebarAppearsTransparent_(True)
        panel.setTitleVisibility_(1)            # NSWindowTitleHidden
        for button in (AppKit.NSWindowCloseButton,
                       AppKit.NSWindowMiniaturizeButton,
                       AppKit.NSWindowZoomButton):
            widget = panel.standardWindowButton_(button)
            if widget is not None:
                widget.setHidden_(True)
        # Focus only when a control actually needs it: clicking the character
        # or dragging must still not pull focus out of what the owner is
        # typing elsewhere.
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(False)          # the page draws its own
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        # OFF on purpose. The page does the dragging through the message
        # handler; leaving AppKit's own background-drag on made it swallow the
        # mouse-down under a possible window drag, so `pointerup` never reached
        # the page and the click did nothing at all (2026-08-31).
        panel.setMovableByWindowBackground_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary)
        return panel

    def _make_view(self, panel, surface, config):  # pragma: no cover - GUI
        import AppKit
        import Foundation

        frame = panel.frame()
        view = _webview_class().alloc().initWithFrame_configuration_(
            Foundation.NSMakeRect(0, 0, frame.size.width, frame.size.height),
            config)
        # Follow the window. Without this the view kept its creation size while
        # the window resized around it: the dialogue opened onto blank white
        # margins and the character appeared to jump (reported 2026-08-31).
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable
                                  | AppKit.NSViewHeightSizable)
        view.setValue_forKey_(False, "drawsBackground")   # see the desktop
        sep = "&" if "?" in self._url else "?"
        view.loadRequest_(Foundation.NSURLRequest.requestWithURL_(
            Foundation.NSURL.URLWithString_(f"{self._url}{sep}surface={surface}")))
        panel.setContentView_(view)
        self._views.append(view)                # keep them alive
        return view

    def _build(self) -> None:  # pragma: no cover - needs a GUI
        import AppKit
        import Foundation
        import WebKit

        pw, ph = PET_SIZE
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        px = screen.origin.x + screen.size.width - pw - MARGIN[0]
        py = screen.origin.y + MARGIN[1]
        if self._position:                  # where the owner left it
            px, py = clamp_origin(
                self._position, PET_SIZE,
                (screen.origin.x, screen.origin.y,
                 screen.size.width, screen.size.height))

        pet = self._make_panel(Foundation.NSMakeRect(px, py, pw, ph))
        sx, sy, sw, sh = side_frame((px, py, pw, ph), SIDE_START,
                                    (screen.origin.x, screen.origin.y,
                                     screen.size.width, screen.size.height))
        side = self._make_panel(Foundation.NSMakeRect(sx, sy, sw, sh))
        # The character sits above its own bubble: the overlap lands in the pet
        # page's transparent padding, and a fixed level beats re-ordering on
        # every reveal.
        pet.setLevel_(AppKit.NSFloatingWindowLevel + 1)
        # Invisible and click-through until the companion page says it has
        # something to show — see `_Bridge._reveal` for why not `orderOut:`.
        side.setAlphaValue_(0.0)
        side.setIgnoresMouseEvents_(True)

        # The handler is registered BEFORE either view exists: `WKWebView`
        # copies its configuration at init, so a handler added afterwards
        # reaches neither page and every message is silently dropped.
        bridge = _Bridge(pet, side, None, self._on_panel, self._report,
                         self._on_move)

        class _Handler(Foundation.NSObject):
            def userContentController_didReceiveScriptMessage_(
                    self, _controller, message):
                bridge.handle(message.body())

        handler = _Handler.alloc().init()
        self._handler = handler                     # keep it alive
        config = WebKit.WKWebViewConfiguration.alloc().init()
        config.userContentController().addScriptMessageHandler_name_(
            handler, "vb")

        bridge._side_web = self._make_view(side, "side", config)
        self._make_view(pet, "pet", config)

        side.orderFrontRegardless()
        pet.orderFrontRegardless()
        self._pet, self._side = pet, side
