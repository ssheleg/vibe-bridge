"""Linux capability pack — X11 и Wayland честно различаются
(research-notes §E, spec §5).

Маршрутизация по сессии: `XDG_SESSION_TYPE` / `WAYLAND_DISPLAY` / `DISPLAY`.
Wayland прячет чужие окна by design — list_apps/frontmost там работают
только на KDE (kdotool); GNOME отвечает честным «недоступно», а не
таймаутом. Automation отложен (ydotool требует демона+root — spec §5
defer). Скриншот: X11 → mss; wlroots → grim; KDE → spectacle; GNOME
Wayland — unavailable до портал-интеграции (см. B-9-соседний бэклог).
"""
from __future__ import annotations

import os
import shutil

from ..capabilities import (
    _STR,
    Capability,
    CapabilityError,
    Runner,
    encode_screenshot,
)
from ..consent import ToolClass


def _session() -> str:
    st = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if st in ("wayland", "x11"):
        return st
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"




def _screenshot(r: Runner, args: dict) -> str:
    ses = _session()
    if ses == "x11":
        try:
            import mss
            import mss.tools
        except ImportError as exc:
            raise CapabilityError(
                "пакет mss не установлен "
                "(pip install 'vibe-bridge[linux]')") from exc
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            png = mss.tools.to_png(shot.rgb, shot.size)
        return encode_screenshot(png, "image/png")
    if ses == "wayland":
        import tempfile
        path = tempfile.mktemp(suffix=".png")
        if shutil.which("grim"):
            r.run(["grim", path], timeout=10.0)
        elif shutil.which("spectacle"):
            r.run(["spectacle", "-b", "-n", "-o", path], timeout=15.0)
        else:
            raise CapabilityError(
                "на этом Wayland-компоузере нет grim/spectacle — скриншот "
                "недоступен (GNOME: ждёт портал-интеграции)")
        with open(path, "rb") as fh:
            data = fh.read()
        os.unlink(path)
        return encode_screenshot(data, "image/png")
    raise CapabilityError("нет графической сессии")


def _list_apps(r: Runner, args: dict) -> str:
    ses = _session()
    if ses == "x11":
        out = r.run(["wmctrl", "-l"], timeout=8.0)
        return "\n".join(line.split(None, 3)[-1]
                         for line in out.splitlines() if line.strip())
    if ses == "wayland" and shutil.which("kdotool"):
        return r.run(["kdotool", "search", "--name", "."], timeout=8.0)
    raise CapabilityError(
        "список окон недоступен на этом Wayland-столе (Wayland прячет чужие "
        "окна; KDE — kdotool, GNOME — нет пути)")


def _frontmost(r: Runner, args: dict) -> str:
    ses = _session()
    if ses == "x11":
        wid = r.run(["xdotool", "getactivewindow"], timeout=8.0).strip()
        return r.run(["xdotool", "getwindowname", wid], timeout=8.0).strip()
    if ses == "wayland" and shutil.which("kdotool"):
        wid = r.run(["kdotool", "getactivewindow"], timeout=8.0).strip()
        return r.run(["kdotool", "getwindowname", wid], timeout=8.0).strip()
    raise CapabilityError("активное окно недоступно на этом Wayland-столе")


def _notify(r: Runner, args: dict) -> str:
    text = str(args.get("text", ""))
    title = str(args.get("title", "Робот"))
    r.run(["notify-send", title, text], timeout=8.0)
    return "notification shown"


def _open_app(r: Runner, args: dict) -> str:
    app = str(args.get("app", "")).strip()
    if not app:
        raise CapabilityError("app name required")
    if shutil.which("gtk-launch"):
        r.run(["gtk-launch", app], timeout=10.0)
    else:
        r.run(["xdg-open", app], timeout=10.0)
    return f"opened {app}"


def _open_url(r: Runner, args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise CapabilityError("only http(s) urls are allowed")
    r.run(["xdg-open", url], timeout=10.0)
    return f"opened {url}"


def _automation_stub(r: Runner, args: dict) -> str:
    raise CapabilityError(
        "автоматизация на Linux отложена (ydotool требует демона и root)")


def _shortcut_stub(r: Runner, args: dict) -> str:
    raise CapabilityError("Shortcuts — только на macOS")


def _clipboard_read(r: Runner, args: dict) -> str:
    if _session() == "wayland":
        return r.run(["wl-paste", "--no-newline"], timeout=8.0)
    return r.run(["xclip", "-selection", "clipboard", "-o"], timeout=8.0)


def _clipboard_write(r: Runner, args: dict) -> str:
    text = str(args.get("text", ""))
    if _session() == "wayland":
        r.run(["wl-copy"], input_text=text, timeout=8.0)
    else:
        r.run(["xclip", "-selection", "clipboard"], input_text=text,
              timeout=8.0)
    return "clipboard set"


def build_capabilities() -> dict[str, Capability]:
    ses = _session()
    clip = ("wl-copy",) if ses == "wayland" else ("xclip",)
    caps = [
        Capability("screenshot", ToolClass.READ,
                   "смотрю на экран компьютера", _screenshot, {}),
        Capability("list_apps", ToolClass.READ,
                   "смотрю список открытых окон", _list_apps, {}),
        Capability("frontmost", ToolClass.READ,
                   "смотрю активное окно", _frontmost, {}),
        Capability("notify", ToolClass.READ,
                   "показываю уведомление на компьютере", _notify,
                   {"text": _STR, "title": _STR}, binaries=("notify-send",)),
        Capability("open_app", ToolClass.ACT,
                   "открыть приложение «{app}»", _open_app, {"app": _STR},
                   binaries=("xdg-open",)),
        Capability("open_url", ToolClass.ACT,
                   "открыть ссылку {url}", _open_url, {"url": _STR},
                   binaries=("xdg-open",)),
        Capability("shortcut_run", ToolClass.ACT,
                   "запустить Shortcut «{name}»", _shortcut_stub,
                   {"name": _STR, "input": _STR}),
        Capability("automation", ToolClass.ACT,
                   "выполнить команду автоматизации", _automation_stub,
                   {"script": _STR}),
        Capability("clipboard_read", ToolClass.ACT,
                   "прочитать буфер обмена", _clipboard_read, {},
                   binaries=clip if ses != "none" else ()),
        Capability("clipboard_write", ToolClass.ACT,
                   "записать в буфер обмена", _clipboard_write,
                   {"text": _STR}, binaries=clip if ses != "none" else ()),
    ]
    return {c.name: c for c in caps}


def probe_extras(name: str) -> tuple[str, str] | None:
    ses = _session()
    if name == "shortcut_run":
        return "unavailable", "Shortcuts существуют только на macOS"
    if name == "automation":
        return ("unavailable",
                "автоматизация на Linux отложена (ydotool требует демона)")
    if ses == "none":
        return "unavailable", "нет графической сессии"
    if ses == "wayland":
        if name in ("list_apps", "frontmost") and not shutil.which("kdotool"):
            return ("unavailable",
                    "Wayland прячет чужие окна; на этом столе нет kdotool")
        if name == "screenshot" and not (shutil.which("grim")
                                         or shutil.which("spectacle")):
            return ("unavailable",
                    "нет grim/spectacle — скриншот на этом Wayland-столе "
                    "недоступен")
    if ses == "x11" and name in ("list_apps", "frontmost"):
        missing = [b for b in ("wmctrl", "xdotool") if not shutil.which(b)]
        if missing:
            return ("needs-permission",
                    f"установите {missing[0]} (sudo apt install {missing[0]})")
    if name == "screenshot" and ses == "x11":
        try:
            __import__("mss")
        except ImportError:
            return ("needs-permission",
                    "установите зависимости: pip install 'vibe-bridge[linux]'")
    return None
