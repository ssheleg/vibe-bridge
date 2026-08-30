"""Tray app — the human's window into the bridge.

macOS keeps rumps (main run loop + native NSAlert consent dialog); the MCP
HTTP server runs in a worker thread. Windows/Linux use the pystray backend
(tray.py) and answer consent on the web panel — a decision from any surface
wins. The server startup is shared; only the tray differs.
"""
from __future__ import annotations

import sys
import threading
import webbrowser

from .audit import AuditLog
from .consent import ConsentEngine, Decision
from .server import BRIDGE_HOST, BRIDGE_PORT
from .state import BridgeState
from .tray import make_notifier, run_pystray, tray_title
from .web import build_app


def _serve(app, host: str) -> None:  # pragma: no cover - thin uvicorn shell
    import uvicorn

    uvicorn.Server(uvicorn.Config(
        app, host=host, port=BRIDGE_PORT, log_level="warning",
    )).run()   # signal handlers are skipped off the main thread


def start_server(consent: ConsentEngine, audit: AuditLog, state: BridgeState,
                 notify) -> None:
    """Build the app and launch uvicorn in a worker thread (the tray owns
    the main thread on every OS). Shared by all backends."""
    web_app = build_app(consent=consent, audit=audit, state=state,
                        notify=notify)
    if state.mode == "standalone":
        from .net import standalone_bind_host
        bind_host = standalone_bind_host()
    else:
        bind_host = BRIDGE_HOST            # gateway mode: loopback, as M1–M4
    threading.Thread(target=_serve, args=(web_app, bind_host),
                     name="vibe-bridge-web", daemon=True).start()


def start_autoupdate(state: BridgeState, audit: AuditLog):
    """Arm the background checker. Returns None outside a signed bundle —
    there is no trust anchor there, so there is nothing to check against."""
    from vbboot import layout
    from vbboot.runner import shell_version

    from . import __version__
    from .update import AutoUpdater, bundled_public_key
    from .web import _bundle_resources

    updater = AutoUpdater(
        root=layout.payload_root(), audit=audit, state=state,
        pubkey=bundled_public_key(_bundle_resources()),
        shell_version=shell_version(),
        current=lambda: __version__)
    updater.start()
    return updater


def _panel_url(state: BridgeState) -> str:
    return f"http://{BRIDGE_HOST}:{BRIDGE_PORT}/?token={state.panel_token}"


def run() -> None:  # pragma: no cover - requires a GUI session
    consent = ConsentEngine()
    audit = AuditLog()
    state = BridgeState.load()
    notify = make_notifier()
    start_server(consent, audit, state, notify)

    # Ask the system to launch us at login — once, ever (SCN-022). Only from
    # a real bundle: in a development checkout `mainAppService` describes
    # whatever binary is hosting Python, and registering that would put the
    # wrong thing in the owner's Login Items.
    if sys.platform == "darwin" and ".app/Contents/" in __file__:
        from .autostart import ensure_registered
        ok, why = ensure_registered(state)
        if ok is not None:          # None = already settled, nothing happened
            audit.record(tool="autostart", tool_class="SYS",
                         decision="auto" if ok else "unavailable", ok=ok,
                         line=f"автозапуск при входе: {why}", detail=why)

    start_autoupdate(state, audit)

    if sys.platform != "darwin":
        # Win/Linux: consent is answered on the panel; the tray is status +
        # open-panel + pause + quit (tray.py, live check = board B-1).
        run_pystray(consent=consent, audit=audit, state=state,
                    open_panel=lambda: webbrowser.open(_panel_url(state)))
        return

    import rumps

    class BridgeApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("🤖", quit_button=None)
            self.menu = [
                rumps.MenuItem("Мост активен", callback=None),
                None,
                rumps.MenuItem("Открыть панель", callback=self.open_panel),
                rumps.MenuItem("⏸ Поставить робота на паузу",
                               callback=self.toggle_pause),
                rumps.MenuItem("Сбросить разрешения",
                               callback=self.revoke),
                None,
                rumps.MenuItem("Последние действия", callback=None),
                None,
                rumps.MenuItem("Выход", callback=rumps.quit_application),
            ]
            rumps.Timer(self._poll, 0.4).start()

        def _poll(self, _timer) -> None:
            self.title = tray_title(consent)   # SCR-01 states, shared helper
            req = consent.pending()
            if req is None:
                return
            resp = rumps.alert(
                title="Робот просит разрешение",
                message=req.summary,
                ok="Разрешить",
                cancel="Отклонить",
                other="Разрешить 15 мин",
            )
            # rumps.alert: 1=ok, 0=cancel, 2=other
            decision = {1: Decision.ALLOW, 0: Decision.DENY,
                        2: Decision.ALLOW_GRANT}.get(resp, Decision.DENY)
            if not req.resolve(decision, by="dialog"):
                # Lost the race: the panel/phone answered while the modal was
                # up — say so instead of silently ignoring the click.
                rumps.notification("vibe-bridge", "",
                                   "Запрос уже решён с другой поверхности")
            self._refresh_recent()

        def _refresh_recent(self) -> None:
            recent = audit.recent(8)
            lines = [
                f"{e['ts'][11:19]} {e['tool']} · {e['decision']}"
                f"{'' if e['ok'] else ' ✗'}"
                for e in reversed(recent)
            ] or ["— пока пусто —"]
            item = self.menu.get("Последние действия")
            if item is not None:
                item._menuitem.setTitle_("Последние действия:")
                # rebuild submenu-ish text into the tooltip line
                self.menu["Последние действия"].title = "Последние: " + \
                    (lines[0] if lines else "—")

        def open_panel(self, _sender) -> None:
            webbrowser.open(_panel_url(state))

        def toggle_pause(self, sender) -> None:
            consent.paused = not consent.paused
            sender.title = ("▶️ Снять с паузы" if consent.paused
                            else "⏸ Поставить робота на паузу")
            self.menu["Мост активен"].title = (
                "Мост на ПАУЗЕ" if consent.paused else "Мост активен")

        def revoke(self, sender) -> None:
            consent.revoke_grants()
            rumps.notification("vibe-bridge", "",
                               "Разрешения сброшены — следующее действие спросит снова")

    BridgeApp().run()


if __name__ == "__main__":  # pragma: no cover
    run()
