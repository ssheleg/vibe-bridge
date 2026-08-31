"""The floating pet — a borderless window that hosts the mascot page.

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
    """Can this machine show the floating pet, and if not, why not."""
    if sys.platform != "darwin":
        return False, "плавающий питомец пока только на macOS — в панели он есть"
    try:
        import WebKit  # noqa: F401
    except ImportError:
        return False, ("нет pyobjc-framework-WebKit — поставьте "
                       "vibe-bridge[macos]")
    return True, ""


class MascotWindow:
    """Owns the panel. Safe to construct off macOS: `show` reports instead of
    raising, because a mascot failing to appear must never take the bridge
    down with it."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._panel = None
        self._webview = None

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
            # Borderless + non-activating: clickable without stealing focus
            # from whatever the owner is typing in.
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(False)          # the page draws its own
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setMovableByWindowBackground_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary)

        config = WebKit.WKWebViewConfiguration.alloc().init()
        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            Foundation.NSMakeRect(0, 0, width, height), config)
        webview.setValue_forKey_(False, "drawsBackground")   # see the desktop
        webview.loadRequest_(Foundation.NSURLRequest.requestWithURL_(
            Foundation.NSURL.URLWithString_(self._url)))

        panel.setContentView_(webview)
        panel.orderFrontRegardless()
        self._panel, self._webview = panel, webview
