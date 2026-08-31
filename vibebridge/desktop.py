"""Native windows that host the bridge's own pages.

Two of them, both `WKWebView`: the main window shows the panel, the floating
pet shows the mascot. Neither draws anything itself — the pages are the same
ones a browser gets, so there is exactly one implementation of every screen.

Why not Electron, since it was asked: the app already carries a Python runtime
and its dependencies (146 MB). Electron would add a whole Chromium on top —
roughly double the download — plus a second toolchain to sign, notarize and
update, and it would not replace one line of the Python that does the actual
work: MCP, consent, the TCC-gated calls. `WKWebView` renders the same pages
with nothing new shipped. The one thing Electron would genuinely buy is a
single windowing story for Windows and Linux, and that is board row B-23.

The floating pet is a borderless panel hosting the mascot page.

It deliberately renders NOTHING itself: it is an `NSPanel` with a `WKWebView`
pointed at the bridge's own `/mascot`. The character, its states and its rules
live in one place for both surfaces, so the panel and the desktop pet cannot
develop two moods for one bridge.

Why a panel and not a window: an `NSPanel` marked non-activating can be
clicked without stealing focus from whatever the owner is typing in. A mascot
that pulls focus to say "робот в сети" is a mascot that gets switched off in a
week.

macOS only. Everywhere else this degrades to the panel's own character, which
is the honest outcome rather than a stub that pretends.
"""
from __future__ import annotations

import sys

#: Where the pet sits on first run: bottom-right, clear of the Dock.
#: Wide enough for the three consent buttons on one line: at 300 they
#: overflowed the window and «Отклонить» was clipped off the right edge —
#: a refusal button you cannot reach is the worst one to lose.
DEFAULT_SIZE = (360, 240)
MARGIN = (28, 96)


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


class _Bridge:
    """Receives `window.webkit.messageHandlers.vb.postMessage(...)`.

    Dragging is done this way and not with CSS because the obvious property,
    `-webkit-app-region: drag`, is an ELECTRON extension: WKWebView ignores it
    entirely, which is why the pet sat immovable on the desktop until
    2026-08-31. `movableByWindowBackground` does not help either — the web
    view consumes the mouse events before the window sees them. So the page
    reports the delta and the window moves itself, which also keeps every
    button inside the page clickable.
    """

    def __init__(self, panel, on_panel=None) -> None:  # pragma: no cover - GUI
        self._panel = panel
        self._on_panel = on_panel
        #: Where the CHARACTER sits on screen (its bottom-right corner in
        #: Cocoa coordinates). The window is grown and shrunk around this
        #: point instead of around its own edges: anchoring on the frame made
        #: the head shift whenever a resize was measured mid-render, and the
        #: owner reported it three times ("скачет в бок"). An anchor only
        #: moves when the owner drags it.
        self._anchor: tuple[float, float] | None = None

    def handle(self, body) -> None:         # pragma: no cover - needs a GUI
        try:
            kind = body.get("type")
            if kind == "panel":
                if self._on_panel:
                    self._on_panel()
                return
            if kind == "resize":
                self._resize(body)
                return
            if kind != "drag":
                return
            frame = self._panel.frame()
            # Cocoa's Y grows upward; the page's grows downward.
            x = frame.origin.x + float(body["dx"])
            y = frame.origin.y - float(body["dy"])
            self._panel.setFrameOrigin_((x, y))
            self._anchor = (x + frame.size.width, y)
        except Exception:                   # noqa: BLE001 - never fatal
            pass

    def _resize(self, body) -> None:        # pragma: no cover - needs a GUI
        """Fit the window to what the page actually draws.

        Two things this fixes at once. The quick menu grew taller than a fixed
        360×240 window and its first rows were clipped off the top edge. And a
        transparent WKWebView still swallows clicks: every empty pixel of an
        oversized window is a hole in the owner's desktop. Hugging the content
        keeps that hole as small as the character itself.
        """
        import AppKit
        height = max(float(body.get("h", 0)), 90.0)
        width = max(float(body.get("w", 0)), 120.0)
        frame = self._panel.frame()
        if self._anchor is None:
            self._anchor = (frame.origin.x + frame.size.width, frame.origin.y)
        right, bottom = self._anchor
        # The character lives at the page's bottom-right, so pinning that
        # corner pins the character: the window grows upward and leftward
        # around a point that does not move.
        self._panel.setFrame_display_(
            AppKit.NSMakeRect(right - width, bottom, width, height), True)


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
    """Owns the panel. Safe to construct off macOS: `show` reports instead of
    raising, because a mascot failing to appear must never take the bridge
    down with it."""

    def __init__(self, url: str, on_panel=None) -> None:
        self._url = url
        self._on_panel = on_panel
        self._panel = None
        self._webview = None
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
        if self._panel is not None:
            self._panel.orderOut_(None)

    def close(self) -> None:  # pragma: no cover - needs a GUI
        self.hide()
        self._panel = self._webview = None

    @property
    def visible(self) -> bool:  # pragma: no cover - needs a GUI
        return bool(self._panel is not None and self._panel.isVisible())

    def _build(self) -> None:  # pragma: no cover - needs a GUI
        import AppKit
        import Foundation
        import WebKit

        width, height = DEFAULT_SIZE
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        x = screen.origin.x + screen.size.width - width - MARGIN[0]
        y = screen.origin.y + MARGIN[1]
        rect = Foundation.NSMakeRect(x, y, width, height)

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            # Titled — not borderless — with the title bar made invisible.
            # A borderless window returns NO from `canBecomeKeyWindow`, so the
            # keyboard never reaches it: the widget's text field could not be
            # typed into at all (reported 2026-08-31). Titled + hidden chrome
            # looks identical and can take focus.
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

        config = WebKit.WKWebViewConfiguration.alloc().init()

        # The page reports drag deltas here; see `_Bridge` for why CSS cannot.
        import objc
        bridge = _Bridge(panel, self._on_panel)

        class _Handler(Foundation.NSObject):
            def userContentController_didReceiveScriptMessage_(
                    self, _controller, message):
                bridge.handle(message.body())

        handler = _Handler.alloc().init()
        self._handler = handler                     # keep it alive
        config.userContentController().addScriptMessageHandler_name_(
            handler, "vb")
        del objc

        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            Foundation.NSMakeRect(0, 0, width, height), config)
        # Follow the window. Without this the view kept its creation size while
        # the window resized around it: the dialogue opened onto blank white
        # margins and the character appeared to jump (reported 2026-08-31).
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable
                                     | AppKit.NSViewHeightSizable)
        webview.setValue_forKey_(False, "drawsBackground")   # see the desktop
        webview.loadRequest_(Foundation.NSURLRequest.requestWithURL_(
            Foundation.NSURL.URLWithString_(self._url)))

        panel.setContentView_(webview)
        panel.orderFrontRegardless()
        self._panel, self._webview = panel, webview
