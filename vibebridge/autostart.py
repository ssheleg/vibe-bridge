"""Launch at login — the platform's own switch, not a plist we hide.

`SMAppService` (macOS 13+) registers the app as a Login Item that the owner
sees and can turn off in System Settings → General → Login Items. That
visibility is the reason to prefer it over a `LaunchAgent` the bridge writes
into the owner's home: a bridge that holds the screen and the clipboard must
not be a thing that starts itself in a place the owner cannot find.

Every function degrades honestly. Off macOS, without PyObjC, or on macOS 12
there is no API to call — that is reported as `unsupported` with a reason,
never as a silent failure and never as success.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

# SMAppServiceStatus, as published by the framework.
_STATUS = {0: "not-registered", 1: "enabled", 2: "requires-approval",
           3: "not-found"}

_HUMAN = {
    "enabled": "включён — мост стартует при входе",
    "requires-approval": "ждёт разрешения в System Settings → General → Login Items",
    "not-registered": "выключен — мост не стартует сам",
    # `notFound` does NOT mean "not installed": macOS returns it for an app
    # that has never registered itself, which is every fresh install. Saying
    # "не установлено" here told the owner something false about an app they
    # were looking at in /Applications (caught live 2026-08-30).
    "not-found": "ещё не настроен — мост не стартует сам",
    "unsupported": "недоступно на этой системе",
}


def ensure_registered(state, mod=None) -> tuple[bool | None, str]:
    """Register as a Login Item once, on the first launch that can.

    Once — because `state.autostart_registered` records that the system was
    asked, and an owner who then switches it off in System Settings must find
    it still off next time. A bridge that re-enables its own autostart every
    launch is a bridge that overrides the owner.

    Returns None for "nothing to do" — distinct from False, which is a real
    failure. Every ordinary launch takes the None path, and journalling that
    as a failure put a red ✗ on normal behaviour, which is how a journal
    teaches its reader to ignore red marks.
    """
    if getattr(state, "autostart_registered", False):
        return None, "автозапуск уже настроен — решение за владельцем"
    ok, message = enable(mod)
    if ok:
        state.autostart_registered = True
        try:
            state.save()
        except OSError:                     # pragma: no cover - unwritable
            # молчим: система УЖЕ зарегистрировала автозапуск, и это главное.
            # Не запомнили — при следующем старте зарегистрируем снова, шаг
            # идемпотентный. Вернуть здесь ошибку значило бы сказать «не
            # получилось» про то, что получилось.
            pass
    return ok, message


@dataclass(frozen=True)
class Autostart:
    state: str
    detail: str
    supported: bool = True

    @property
    def human(self) -> str:
        return _HUMAN.get(self.state, self.state)


def _framework(mod=None):
    """The ServiceManagement bridge, or None with the reason it is absent."""
    if mod is not None:
        return mod, ""
    if sys.platform != "darwin":
        return None, "автозапуск через Login Items есть только на macOS"
    try:
        import ServiceManagement  # noqa: PLC0415 - optional, macOS-only
    except ImportError:
        return None, ("нет pyobjc-framework-ServiceManagement — "
                      "поставьте vibe-bridge[macos]")
    if not hasattr(ServiceManagement, "SMAppService"):
        return None, "SMAppService требует macOS 13 или новее"
    return ServiceManagement, ""


def status(mod=None) -> Autostart:
    sm, why = _framework(mod)
    if sm is None:
        return Autostart("unsupported", why, supported=False)
    try:
        raw = sm.SMAppService.mainAppService().status()
    except Exception as exc:                    # noqa: BLE001 - never raises
        return Autostart("unsupported", f"ServiceManagement недоступен: {exc}",
                         supported=False)
    state = _STATUS.get(int(raw), "not-found")
    return Autostart(state, _HUMAN.get(state, ""))


def enable(mod=None) -> tuple[bool, str]:
    """Register the app. macOS may answer `requires-approval` — that is not a
    failure, it is the owner being asked, and we say so in those words."""
    sm, why = _framework(mod)
    if sm is None:
        return False, why
    try:
        ok, err = sm.SMAppService.mainAppService().registerAndReturnError_(None)
    except Exception as exc:                    # noqa: BLE001
        return False, f"не удалось включить автозапуск: {exc}"
    if not ok:
        return False, _err_text(err, "не удалось включить автозапуск")
    return True, status(mod).human


def disable(mod=None) -> tuple[bool, str]:
    sm, why = _framework(mod)
    if sm is None:
        return False, why
    try:
        ok, err = sm.SMAppService.mainAppService().unregisterAndReturnError_(
            None)
    except Exception as exc:                    # noqa: BLE001
        return False, f"не удалось выключить автозапуск: {exc}"
    if not ok:
        return False, _err_text(err, "не удалось выключить автозапуск")
    return True, "автозапуск выключен"


def open_settings(mod=None) -> bool:
    """Send the owner to the Login Items panel — the switch is theirs."""
    sm, _ = _framework(mod)
    if sm is None or not hasattr(sm, "SMAppService"):
        return False
    try:
        sm.SMAppService.openSystemSettingsLoginItems()
        return True
    except Exception:                           # noqa: BLE001
        return False


def _err_text(err, fallback: str) -> str:
    try:
        return str(err.localizedDescription()) if err is not None else fallback
    except Exception:                           # noqa: BLE001
        return fallback
