"""Tray backend abstraction (spec §1, research-notes §D).

macOS keeps rumps: it owns the main run loop AND gives native NSAlert
consent dialogs. Windows/Linux use pystray for the icon; there the consent
request is answered on the WEB PANEL (a decision from any surface wins —
consent.py), and the tray's job is status + "open panel" + pause + quit.
Both backends own the MAIN THREAD; the uvicorn server always runs in a
worker thread (started by app.run before the backend takes over).

The pystray path is real but unverified on a live Win/Linux host — that
live check is board B-1. What IS tested here is backend SELECTION and the
platform-agnostic notifier.
"""
from __future__ import annotations

import sys

from .consent import ConsentEngine, ToolClass


def make_notifier():
    """One notifier for all three OSes. Prefers desktop-notifier (native
    UNUserNotificationCenter / WinRT / DBus); falls back to osascript on
    macOS, then to a no-op — a lost toast must never hurt the bridge."""
    try:  # desktop-notifier is async-native; we drive it synchronously
        from desktop_notifier import DesktopNotifierSync
        notifier = DesktopNotifierSync(app_name="vibe-bridge")

        def _notify(title: str, text: str) -> None:
            try:
                notifier.send(title=str(title)[:60], message=str(text)[:180])
            except Exception:
                pass
        return _notify
    except Exception:
        pass
    if sys.platform == "darwin":
        import subprocess

        def _notify(title: str, text: str) -> None:
            t = str(text).replace('"', "'")[:180]
            h = str(title).replace('"', "'")[:60]
            try:
                subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{t}" with title "{h}"'],
                    capture_output=True, timeout=5)
            except Exception:
                pass
        return _notify
    return lambda title, text: None


def tray_title(consent: ConsentEngine) -> str:
    """Icon/tooltip state (SCR-01): paused · attention · grant · idle.
    Pure — tested without a GUI."""
    if consent.paused:
        return "⏸"
    if consent.pending() is not None:
        return "🤖❗"
    if consent.grant_active(ToolClass.ACT) > 0:
        return "🤖⏳"
    return "🤖"


def run_pystray(*, consent: ConsentEngine, audit, state,
                open_panel) -> None:  # pragma: no cover - needs Win/Linux GUI
    """Win/Linux tray. Consent is answered on the panel; here we surface
    status, open-panel, pause and quit. run() blocks on the main thread."""
    import pystray
    from PIL import Image, ImageDraw

    def _icon_image(paused: bool) -> "Image.Image":
        img = Image.new("RGB", (64, 64),
                        (0x5b, 0x64, 0x72) if paused else (0x2f, 0x6f, 0xeb))
        d = ImageDraw.Draw(img)
        d.ellipse((20, 20, 44, 44), fill=(255, 255, 255))
        return img

    def _toggle_pause(icon, _item):
        consent.paused = not consent.paused
        icon.icon = _icon_image(consent.paused)
        icon.title = tray_title(consent)

    def _open(icon, _item):
        open_panel()

    def _revoke(icon, _item):
        consent.revoke_grants()

    menu = pystray.Menu(
        pystray.MenuItem("Открыть панель", _open, default=True),
        pystray.MenuItem(
            lambda _i: "▶ Снять паузу" if consent.paused else "⏸ Пауза",
            _toggle_pause),
        pystray.MenuItem("Сбросить разрешения", _revoke),
        pystray.MenuItem("Выход", lambda icon, _i: icon.stop()),
    )
    icon = pystray.Icon("vibe-bridge", _icon_image(consent.paused),
                        "vibe-bridge", menu)

    def _setup(icon):
        icon.visible = True
        import time
        while icon._running:
            icon.title = tray_title(consent)
            time.sleep(0.5)

    icon.run(setup=_setup)
