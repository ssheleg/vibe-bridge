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
    if settings.mode == "standalone":
        from .net import standalone_bind_host
        bind_host = standalone_bind_host()
    else:
        bind_host = BRIDGE_HOST            # gateway mode: loopback, as M1–M4
    # Взводим границу по адресу пира ровно тогда, когда слушаем больше, чем
    # loopback. При loopback-бинде каждый пир и есть loopback, и проверка была
    # бы декорацией; при более широком бинде она единственная настоящая —
    # allowlist по `Host` присылает клиент.
    web_app = build_app(consent=consent, audit=audit, state=state,
                        notify=notify, settings=settings,
                        peer_guard=bind_host != BRIDGE_HOST)
    threading.Thread(target=_serve, args=(web_app, bind_host, settings.port),
                     name="vibe-bridge-web", daemon=True).start()


def start_autoupdate(state: BridgeState, audit: AuditLog, settings=None):
    """Arm the background checker. Returns None outside a signed bundle —
    there is no trust anchor there, so there is nothing to check against.

    Настройки передаются ЯВНО. Без них `update.interval_hours` не действовал
    вовсе, а выключатель обновлений читался только из state — и совпадение
    умолчаний (шесть часов там и там) это прятало: настройка «работала», пока
    её не меняли (A-16).
    """
    from vbboot import layout
    from vbboot.runner import shell_version

    from . import __version__
    from . import update as _update
    from .web import _bundle_resources

    updater = _update.AutoUpdater(
        root=layout.payload_root(), audit=audit, state=state,
        pubkey=_update.bundled_public_key(_bundle_resources()),
        shell_version=shell_version(),
        current=lambda: __version__,
        interval_s=getattr(settings, "update_interval_s", None),
        settings=settings)
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
    from .config import migrate_from_state, top_up

    migrate_from_state(state)
    settings = load_settings(create=True)
    # A file written by an older version lacks every setting added since; the
    # file is the manual, so bring it up to date without touching values.
    top_up()
    return settings


def wait_for_server(port: int, *, host: str = "127.0.0.1",
                    timeout: float = 10.0, step: float = 0.1) -> bool:
    """Block until the port accepts a connection, or say it never did.

    The widget's windows load their URL exactly ONCE: `WKWebView` shows its own
    "cannot connect" page on failure and never retries. Building them before
    uvicorn had bound the port left the owner looking at that page instead of
    the character — a small white box reading «Нет связи с…» where the head
    should be (measured 2026-09-01). Nothing in the journal said why, because
    from the bridge's side everything HAD started: the thread was running, the
    windows were up, and only the page inside them was dead.

    Returns False rather than raising: a bridge whose port is slow is still a
    bridge, and the caller records the fact instead of dying on it.
    """
    import socket
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(step)
            try:
                probe.connect((host, port))
                return True
            except OSError:
                _time.sleep(step)
    return False


def _remember_pet(state, pos, report=None) -> None:
    """Persist where the owner dragged the pet.

    Called once per gesture, on release. The value is only ever WRITTEN here
    and only ever read at launch, so a state file from another machine — or
    from a session with a second display — cannot move the pet mid-run; it can
    only offer a starting point, which `clamp_origin` then checks against the
    screen that actually exists.

    A failure is REPORTED, not swallowed. Two bugs today hid behind
    `except Exception: pass` written for exactly this reason — "the pet must
    never take the bridge down" is about continuing to run, not about staying
    quiet.
    """
    try:
        state.pet_pos = [float(pos[0]), float(pos[1])]
        state.save()
    except Exception as exc:                # noqa: BLE001 - reported
        if report is not None:
            report(f"питомец: позицию не удалось сохранить: {exc}")


def run() -> None:  # pragma: no cover - requires a GUI session
    audit = AuditLog()
    state = BridgeState.load()
    settings = prepare_settings(state)
    consent = ConsentEngine(ask_timeout_s=settings.ask_timeout_s,
                            grant_ttl_s=settings.grant_ttl_s,
                            ask_for_read=settings.ask_for_read)
    notify = make_notifier()
    backend = getattr(notify, "backend", "неизвестно")
    audit.record(tool="notify", tool_class="SYS", decision="auto", ok=True,
                 line=f"канал уведомлений: {backend}", detail="")
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

    # The windows below load their page once and keep whatever they get, so
    # the port must be answering BEFORE they are built.
    if not wait_for_server(settings.port):
        audit.record(
            tool="server", tool_class="SYS", decision="error", ok=False,
            line=f"порт {settings.port} не ответил за 10 с — окна откроются "
                 f"на странице ошибки",
            detail="wait_for_server timeout")

    start_autoupdate(state, audit, settings)

    from .desktop import MainWindow, MascotWindow

    base = _panel_url(state).split("/?")[0]
    # The app's own window. Without it, launching from /Applications produced
    # nothing visible at all: LSUIElement keeps it out of the Dock and the
    # panel lived in a browser.
    window = MainWindow(_panel_url(state))
    ok, why = window.show()
    if not ok:
        audit.record(tool="window", tool_class="SYS", decision="unavailable",
                     ok=False, line=f"окно приложения: {why}", detail=why)

    def _pet_report(line: str, ok: bool = False) -> None:
        audit.record(tool="mascot", tool_class="SYS",
                     decision="auto" if ok else "error", ok=ok,
                     line=line, detail=line)

    pet = None
    if settings.mascot_window:
        pet = MascotWindow(
            f"{base}/mascot?token={state.panel_token}",
            on_panel=window.show,
            report=_pet_report,
            position=state.pet_pos,
            on_move=lambda pos: _remember_pet(state, pos,
                                              report=_pet_report))
        ok, why = pet.show()
        audit.record(tool="mascot", tool_class="SYS",
                     decision="auto" if ok else "unavailable", ok=ok,
                     line=f"питомец на экране: {why}", detail=why)

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
                rumps.MenuItem("Открыть окно", callback=self.open_window),
                rumps.MenuItem("Панель в браузере", callback=self.open_panel),
                rumps.MenuItem("🤖 Показать питомца", callback=self.toggle_pet),
                # Текст этого пункта — ПРОИЗВОДНОЕ от состояния и пишется
                # только в `_poll`; см. там, почему.
                rumps.MenuItem("⏸ Поставить мост на паузу",
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
            # Тексты меню — ПРОИЗВОДНОЕ от состояния, и пишутся здесь, а не в
            # обработчике клика. Раньше их писал только `toggle_pause`, и
            # получалось хуже, чем «надпись отстала»: пауза, поставленная с
            # панели или с телефона, до меню-бара не доходила, пункт
            # по-прежнему предлагал «поставить на паузу» — а клик по нему
            # исполнял `paused = not True` и СНИМАЛ её. Kill switch
            # инвертировался на той единственной ОС, которая отгружена
            # (найдено аудитом 2026-09-01; в pystray-ветке для Win/Linux то же
            # самое сделано правильно — лямбдой).
            #
            # Заодно строка исправлена по существу: пауза выключает ЭТОТ
            # компьютер, робот продолжает жить (визия §5.3).
            self._sync_pause_labels()
            req = consent.pending()
            if req is None:
                return
            # Модальный лист не умеет тикать, поэтому окно называется
            # словом: молчание здесь — отказ, и владелец должен прочитать
            # это до того, как отойдёт от экрана (A-9).
            left = int(consent.remaining(req)) or int(consent.ask_timeout_s)
            resp = rumps.alert(
                title="Робот просит разрешение",
                message=f"{req.summary}\n\nБез ответа за {left} с — отказ.",
                ok="Разрешить",
                cancel="Отклонить",
                other="Такие 15 мин",
            )
            # rumps.alert: 1=ok, 0=cancel, 2=other
            decision = {1: Decision.ALLOW, 0: Decision.DENY,
                        2: Decision.ALLOW_GRANT}.get(resp, Decision.DENY)
            if not req.resolve(decision, by="dialog"):
                # Проиграли гонку — но КОМУ, владелец должен узнать. Модальный
                # лист не закрывается сам по истечении срока (платформа не
                # умеет), поэтому клик по нему через минуту — обычное дело, и
                # молчать в ответ нельзя (A-10).
                got = consent.outcome(req.id)
                rumps.notification(
                    "vibe-bridge", "",
                    "Запрос истёк — робот получил отказ по молчанию"
                    if got is not None and got.by == "timeout"
                    else "Запрос уже решён с другой поверхности")
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

        def open_window(self, _sender) -> None:
            ok, why = window.show()
            if not ok:
                rumps.notification("vibe-bridge", "", why)

        def open_panel(self, _sender) -> None:
            webbrowser.open(_panel_url(state))

        def toggle_pet(self, sender) -> None:
            """Show or hide the floating pet, and remember the answer — the
            owner should not have to re-decide it every launch."""
            from .config import update as save_settings
            from .desktop import MascotWindow

            nonlocal pet
            if pet is not None and pet.visible:
                pet.hide()
                sender.title = "🤖 Показать питомца"
                save_settings({"mascot_window": False})
                return
            if pet is None:
                pet = MascotWindow(f"{base}/mascot?token={state.panel_token}",
                                   on_panel=window.show)
            ok, why = pet.show()
            sender.title = ("🙈 Скрыть питомца" if ok
                            else "🤖 Питомец недоступен")
            if ok:
                save_settings({"mascot_window": True})
            else:
                rumps.notification("vibe-bridge", "", why)

        def _sync_pause_labels(self) -> None:
            """Единственный писатель обоих текстов паузы."""
            paused = consent.paused
            self.menu["⏸ Поставить мост на паузу"].title = (
                "▶️ Снять с паузы" if paused else "⏸ Поставить мост на паузу")
            self.menu["Мост активен"].title = (
                "Мост на ПАУЗЕ" if paused else "Мост активен")

        def toggle_pause(self, _sender) -> None:
            # Только переключает состояние: тексты синхронизирует `_poll`,
            # который видит и решения с других поверхностей.
            consent.paused = not consent.paused
            self._sync_pause_labels()

        def revoke(self, sender) -> None:
            consent.revoke_grants()
            rumps.notification("vibe-bridge", "",
                               "Разрешения сброшены — следующее действие спросит снова")

    BridgeApp().run()


if __name__ == "__main__":  # pragma: no cover
    run()
