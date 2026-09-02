"""Windows 10/11 capability pack (research-notes §E, spec §5).

Реализации по таблице паритета: mss (скриншот, только интерактивная
сессия — Session-0 не видит десктоп), PyGetWindow (окна), windows-toasts
(уведомления), PowerShell для open/automation/clipboard. Automation — тот
же класс риска, что osascript: ACT + блоклист на спавн консолей и
хранилища кредов. Все сторонние импорты ленивые: отсутствие пакета — это
honest `needs-permission`-класс ошибки установки, а не крэш моста.
"""
from __future__ import annotations

import os

from ..capabilities import (
    _STR,
    Capability,
    CapabilityError,
    Runner,
    encode_screenshot,
)
from ..consent import ToolClass

# Спавн шеллов и доступ к хранилищам кредов заблокированы на мосту —
# скомпрометированный промпт не должен открывать консоль или тащить пароли
# (зеркало APPLESCRIPT_BLOCKED).
POWERSHELL_BLOCKED = ("powershell", "pwsh", "cmd.exe", "cmd /", "wscript",
                      "cscript", "mshta", "reg add", "reg delete", "regedit",
                      "cmdkey", "vaultcmd", "schtasks", "start-process cmd",
                      "start-process powershell")

_PS = ("powershell", "-NoProfile", "-NonInteractive", "-Command")


def _screenshot(r: Runner, args: dict) -> str:
    try:
        import mss  # lazy: extras [windows]
        import mss.tools
    except ImportError as exc:
        raise CapabilityError(
            "пакет mss не установлен (pip install 'vibe-bridge[windows]')"
        ) from exc
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        png = mss.tools.to_png(shot.rgb, shot.size)
    return encode_screenshot(png, "image/png")


def _list_apps(r: Runner, args: dict) -> str:
    try:
        import pygetwindow  # lazy: extras [windows]
    except ImportError as exc:
        raise CapabilityError(
            "пакет PyGetWindow не установлен "
            "(pip install 'vibe-bridge[windows]')") from exc
    titles = sorted({t for t in pygetwindow.getAllTitles() if t.strip()})
    return "\n".join(titles)


def _frontmost(r: Runner, args: dict) -> str:
    try:
        import pygetwindow
    except ImportError as exc:
        raise CapabilityError(
            "пакет PyGetWindow не установлен "
            "(pip install 'vibe-bridge[windows]')") from exc
    w = pygetwindow.getActiveWindow()
    if w is None or not (w.title or "").strip():
        raise CapabilityError("активное окно не определяется")
    return w.title


def _notify(r: Runner, args: dict) -> str:
    text = str(args.get("text", ""))
    title = str(args.get("title", "Робот"))
    try:
        from windows_toasts import Toast, WindowsToaster  # lazy
    except ImportError:
        # деградация без пакета: msg через PowerShell-балун слишком шумный —
        # честная ошибка с путём установки
        raise CapabilityError(
            "пакет windows-toasts не установлен "
            "(pip install 'vibe-bridge[windows]')") from None
    toaster = WindowsToaster(title)
    toast = Toast([text])
    toaster.show_toast(toast)
    return "notification shown"


def _open_app(r: Runner, args: dict) -> str:
    app = str(args.get("app", "")).strip()
    if not app:
        raise CapabilityError("app name required")
    r.run([*_PS, f"Start-Process {_ps_quote(app)}"])
    return f"opened {app}"


def _open_url(r: Runner, args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise CapabilityError("only http(s) urls are allowed")
    r.run([*_PS, f"Start-Process {_ps_quote(url)}"])
    return f"opened {url}"


def _automation(r: Runner, args: dict) -> str:
    script = str(args.get("script", ""))
    low = script.lower()
    for bad in POWERSHELL_BLOCKED:
        if bad in low:
            raise CapabilityError(
                f"PowerShell, целящий в '{bad}', заблокирован на мосту")
    return r.run([*_PS, script], timeout=30.0) or "ran powershell"


def _clipboard_read(r: Runner, args: dict) -> str:
    return r.run([*_PS, "Get-Clipboard"])


def _clipboard_write(r: Runner, args: dict) -> str:
    r.run([*_PS, "Set-Clipboard -Value $input"],
          input_text=str(args.get("text", "")))
    return "clipboard set"


def _shortcut_stub(r: Runner, args: dict) -> str:
    raise CapabilityError("Shortcuts — только на macOS")


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def build_capabilities() -> dict[str, Capability]:
    caps = [
        Capability("screenshot", ToolClass.READ,
                   "смотрю на экран компьютера", _screenshot, {}),
        Capability("list_apps", ToolClass.READ,
                   "смотрю список открытых окон", _list_apps, {}),
        Capability("frontmost", ToolClass.READ,
                   "смотрю активное окно", _frontmost, {}),
        Capability("notify", ToolClass.READ,
                   "показываю уведомление на компьютере", _notify,
                   {"text": _STR, "title": _STR}),
        Capability("open_app", ToolClass.ACT,
                   "открыть приложение «{app}»", _open_app, {"app": _STR},
                   binaries=("powershell",)),
        Capability("open_url", ToolClass.ACT,
                   "открыть ссылку {url}", _open_url, {"url": _STR},
                   binaries=("powershell",)),
        Capability("shortcut_run", ToolClass.ACT,
                   "запустить Shortcut «{name}»", _shortcut_stub,
                   {"name": _STR, "input": _STR}),
        Capability("automation", ToolClass.ACT,
                   "выполнить PowerShell-команду", _automation,
                   {"script": _STR}, binaries=("powershell",)),
        Capability("clipboard_read", ToolClass.ACT,
                   "прочитать буфер обмена", _clipboard_read, {},
                   binaries=("powershell",)),
        Capability("clipboard_write", ToolClass.ACT,
                   "записать в буфер обмена", _clipboard_write,
                   {"text": _STR}, binaries=("powershell",)),
    ]
    return {c.name: c for c in caps}


def probe_extras(name: str) -> tuple[str, str] | None:
    """Платформенные пере-статусы поверх бинарной probe (spec §4)."""
    if name == "shortcut_run":
        return "unavailable", "Shortcuts существуют только на macOS"
    if name == "screenshot" and not os.environ.get("SESSIONNAME"):
        # pragma: no cover - win-only: Session-0 isolation
        return ("unavailable",
                "нет интерактивной сессии — запустите мост как приложение "
                "пользователя, не как службу")
    for pkg, tools in (("mss", ("screenshot",)),
                       ("pygetwindow", ("list_apps", "frontmost")),
                       ("windows_toasts", ("notify",))):
        if name in tools:
            try:
                __import__(pkg)
            except ImportError:
                return ("needs-permission",
                        f"установите зависимости: pip install "
                        f"'vibe-bridge[windows]' (нет пакета {pkg})")
    return None
