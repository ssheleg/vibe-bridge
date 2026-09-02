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

from .consent import ConsentEngine


def _bundled_icon():
    """The app's own mark, passed to the notifier where that is honoured.

    **The icon a macOS toast shows is the icon of the BUNDLE THAT POSTED IT**,
    not anything passed at call time. That much was measured right; the
    conclusion drawn from it was wrong. The bee on every notification was not
    the notifier library's icon — it was OURS. `briefcase create` installs the
    app's resources AFTER its requirement step, and that step always errors on
    this project (see scripts/build_app.sh), so the run stopped before the icon
    was ever copied and the bundle carried Briefcase's default bee for
    fourteen releases: in /Applications, and therefore on every toast.

    Fixed where it belonged — `briefcase update --update-resources`, gated by
    a byte comparison in the build script. The argument here is kept because
    Linux and Windows honour it, and kept documented because the next reader
    will otherwise repeat the same wrong turn: looking inside the library for
    an icon the operating system takes from the bundle.
    """
    import sys as _sys
    if _sys.platform != "darwin":
        return None
    try:
        from pathlib import Path as _P

        from desktop_notifier import Icon

        import vbboot
        for parent in _P(vbboot.__file__).resolve().parents:
            if parent.name == "Resources" and parent.parent.name == "Contents":
                png = parent / "vibe-bridge-128.png"
                if png.is_file():
                    return Icon(path=png)
                icns = parent / "vibe-bridge.icns"
                return Icon(path=icns) if icns.is_file() else None
    except Exception:                          # noqa: BLE001 - decoration
        return None
    return None


def make_notifier():
    """One notifier for all three OSes, and it says which one it is.

    Prefers desktop-notifier (native UNUserNotificationCenter / WinRT / DBus)
    so the toast carries the app's name and icon; falls back to osascript on
    macOS, which posts as Script Editor — recognisable, and worth knowing
    about rather than discovering from a screenshot.

    Every failure is REPORTED, not swallowed. The previous version returned
    None whatever happened, so the capability told the robot "notification
    shown" while nothing appeared on screen (reported 2026-08-31: «в
    системное от apple scripts и там ничего нет»). Lying to the robot about
    a delivered message is exactly what «честный отказ» forbids.

    Returns a callable with a `.backend` attribute; calling it returns
    (ok, reason).
    """
    try:  # desktop-notifier is async-native; we drive it synchronously
        from desktop_notifier import DesktopNotifierSync
        notifier = DesktopNotifierSync(app_name="vibe-bridge")
        icon = _bundled_icon()

        def _native(title: str, text: str) -> tuple[bool, str]:
            try:
                notifier.send(title=str(title)[:60], message=str(text)[:180],
                              **({"icon": icon} if icon else {}))
                return True, ""
            except Exception as exc:          # noqa: BLE001 - reported
                return False, f"уведомление не показано: {exc}"
        _native.backend = "desktop-notifier"   # type: ignore[attr-defined]
        return _native
    except Exception as exc:                   # noqa: BLE001
        why = str(exc)

    if sys.platform == "darwin":
        import subprocess

        def _osa(title: str, text: str) -> tuple[bool, str]:
            t = str(text).replace('"', "'")[:180]
            h = str(title).replace('"', "'")[:60]
            try:
                p = subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{t}" with title "{h}"'],
                    capture_output=True, timeout=5, text=True)
            except Exception as exc:           # noqa: BLE001
                return False, f"уведомление не показано: {exc}"
            if p.returncode != 0:
                return False, (p.stderr or "osascript отказал").strip()[:160]
            return True, ""
        _osa.backend = f"osascript (без имени приложения: {why[:60]})"  # type: ignore[attr-defined]
        return _osa

    def _none(title: str, text: str) -> tuple[bool, str]:
        return False, "на этой системе показывать уведомления нечем"
    _none.backend = "нет"                      # type: ignore[attr-defined]
    return _none


def tray_title(consent: ConsentEngine) -> str:
    """Icon/tooltip state (SCR-01): paused · attention · grant · idle.
    Pure — tested without a GUI."""
    if consent.paused:
        return "⏸"
    if consent.pending() is not None:
        return "🤖❗"
    if consent.grants():
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
