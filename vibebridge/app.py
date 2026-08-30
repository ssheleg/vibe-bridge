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
from .server import BRIDGE_HOST
from .state import BridgeState
from .tray import make_notifier, run_pystray, tray_title
from .web import build_app


def _serve(app, host: str, port: int) -> None:  # pragma: no cover - uvicorn
    import uvicorn

    uvicorn.Server(uvicorn.Config(
        app, host=host, port=port, log_level="warning",
    )).run()   # signal handlers are skipped off the main thread


def start_server(consent: ConsentEngine, audit: AuditLog, state: BridgeState,
                 notify, settings=None) -> None:
    """Build the app and launch uvicorn in a worker thread (the tray owns
    the main thread on every OS). Shared by all backends."""
    if settings is None:
        settings = prepare_settings(state)
    web_app = build_app(consent=consent, audit=audit, state=state,
                        notify=notify, settings=settings)
    if settings.mode == "standalone":
        from .net import standalone_bind_host
        bind_host = standalone_bind_host()
    else:
        bind_host = BRIDGE_HOST            # gateway mode: loopback, as M1–M4
    threading.Thread(target=_serve, args=(web_app, bind_host, settings.port),
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
    from .server import bridge_port
    return f"http://{BRIDGE_HOST}:{bridge_port()}/?token={state.panel_token}"


def prepare_settings(state: BridgeState):
    """Settings for this run — after carrying over a pre-settings mode.

    Order matters and is the whole reason this is a function: the migration
    must see the state file and must run BEFORE anything creates config.toml,
    because it declines to argue with a file that already exists. Getting it
    backwards flipped this machine from `gateway` to `standalone` on the first
    launch after settings landed, and the bridge bound the tailnet interface
    instead of loopback — the robot's whole path.
    """
    from .config import load as load_settings
    from .config import migrate_from_state

    migrate_from_state(state)
    return load_settings(create=True)


def run() -> None:  # pragma: no cover - requires a GUI session
    audit = AuditLog()
    state = BridgeState.load()
    settings = prepare_settings(state)
    consent = ConsentEngine(ask_timeout_s=settings.ask_timeout_s,
                            grant_ttl_s=settings.grant_ttl_s)
    notify = make_notifier()
    start_server(consent, audit, state, notify, settings=settings)

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
