"""Menu-bar app — the human's window into the bridge.

rumps owns the macOS main run loop; the MCP HTTP server runs in a worker
thread. The menu shows: live status, the kill switch, the pending consent
dialog (Allow / Allow 15 min / Deny), active grants, and the last calls.

A pending ACT request parks the robot's tool thread inside ConsentEngine;
this loop surfaces it as an alert and resolves it. macOS alerts must be
raised from the main thread, so a rumps.Timer polls consent.pending().
"""
from __future__ import annotations

import threading

from .audit import AuditLog
from .consent import ConsentEngine, Decision
from .server import build_server


def run() -> None:  # pragma: no cover - requires a Mac GUI session
    import rumps

    consent = ConsentEngine()
    audit = AuditLog()

    server = build_server(consent=consent, audit=audit)
    threading.Thread(
        target=lambda: server.run(transport="streamable-http"),
        name="mac-bridge-mcp", daemon=True).start()

    class BridgeApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("🤖", quit_button=None)
            self.menu = [
                rumps.MenuItem("Мост активен", callback=None),
                None,
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
            self.title = "⏸" if consent.paused else "🤖"
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
            req.resolve(decision)
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

        def toggle_pause(self, sender) -> None:
            consent.paused = not consent.paused
            sender.title = ("▶️ Снять с паузы" if consent.paused
                            else "⏸ Поставить робота на паузу")
            self.menu["Мост активен"].title = (
                "Мост на ПАУЗЕ" if consent.paused else "Мост активен")

        def revoke(self, sender) -> None:
            consent.revoke_grants()
            rumps.notification("mac-bridge", "",
                               "Разрешения сброшены — следующее действие спросит снова")

    BridgeApp().run()


if __name__ == "__main__":  # pragma: no cover
    run()
