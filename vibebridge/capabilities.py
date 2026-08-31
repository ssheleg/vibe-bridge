"""The tool surface — each capability is (name, class, summary, handler).

Handlers are thin shells over macOS CLIs (screencapture, osascript, open,
shortcuts, pbcopy/pbpaste). They are injected so the whole surface is
testable without a screen: the default injection is the real macOS runner,
tests pass a fake.

Deliberately absent: shell, arbitrary file read/write. Giving a robot an
unattended shell on the owner's laptop is a different league of
irreversibility; AppleScript already covers "drive an app" and stays behind
an ACT gate with an app blocklist.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from .consent import ToolClass

# AppleScript can reach anything scriptable; these targets are refused at the
# bridge regardless of consent — a compromised prompt must not read Keychain
# or spawn a terminal through us.
APPLESCRIPT_BLOCKED = ("terminal", "keychain access", "keychain", "iterm",
                       "script editor", "system events» keystroke")


class CapabilityError(Exception):
    pass


# Fleet wire-compat: the robots deployed today call the mac_* names (M1–M4
# contract). Every alias keeps answering until the Hermes/mcp bump retires
# them in one move — board B-7. New callers use the canonical names.
ALIASES: dict[str, str] = {
    "mac_screenshot": "screenshot",
    "mac_list_apps": "list_apps",
    "mac_frontmost": "frontmost",
    "mac_notify": "notify",
    "mac_open_app": "open_app",
    "mac_open_url": "open_url",
    "mac_shortcut_run": "shortcut_run",
    "mac_applescript": "automation",
    "mac_clipboard_read": "clipboard_read",
    "mac_clipboard_write": "clipboard_write",
}


@dataclass
class Capability:
    name: str
    tool_class: ToolClass
    summary_template: str          # .format(**args) → human consent line
    handler: Callable[[Runner, dict], str]
    input_schema: dict
    binaries: tuple[str, ...] = ()   # external commands the handler shells to

    def summary(self, args: dict) -> str:
        try:
            return self.summary_template.format(**{**_SUMMARY_DEFAULTS, **args})
        except Exception:
            return self.summary_template


_SUMMARY_DEFAULTS = {"app": "?", "url": "?", "name": "?", "text": "?",
                     "title": "без заголовка"}


class Runner:
    """Real macOS side. One method per external command, each swappable."""

    def run(self, argv: list[str], *, timeout: float = 20.0,
            input_text: str | None = None) -> str:
        exe = shutil.which(argv[0]) or argv[0]
        try:
            p = subprocess.run(
                [exe, *argv[1:]], capture_output=True, text=True,
                timeout=timeout, input=input_text)
        except subprocess.TimeoutExpired as exc:
            raise CapabilityError(f"{argv[0]} timed out after {timeout}s") from exc
        except OSError as exc:
            raise CapabilityError(f"{argv[0]} not available: {exc}") from exc
        if p.returncode != 0:
            raise CapabilityError(
                (p.stderr or p.stdout or f"{argv[0]} exit {p.returncode}").strip())
        return p.stdout


# ── handlers ────────────────────────────────────────────────────────────────


def _screenshot(r: Runner, args: dict) -> str:
    import base64
    import tempfile

    path = tempfile.mktemp(suffix=".png")
    # -x: no sound. -C: capture cursor off by default. Whole screen unless a
    # window id is given later; V1 keeps it to the main display.
    r.run(["screencapture", "-x", path])
    try:
        with open(path, "rb") as fh:
            b = fh.read()
    except OSError as exc:
        raise CapabilityError(f"screenshot unreadable: {exc}") from exc
    return f"data:image/png;base64,{base64.b64encode(b).decode('ascii')}"


# System Events calls need Accessibility (TCC) — granted to the packaged .app,
# not to a bare python run. Fail FAST (8s) so a missing grant surfaces as an
# honest error the robot can speak, instead of parking its tool thread for 20s
# on a TCC prompt that never comes in a non-GUI context (measured 2026-08-28).
_SE_TIMEOUT = 8.0


def _list_apps(r: Runner, args: dict) -> str:
    out = r.run(["osascript", "-e",
                 'tell application "System Events" to get name of '
                 '(every process whose background only is false)'],
                timeout=_SE_TIMEOUT)
    return out.strip()


def _frontmost(r: Runner, args: dict) -> str:
    out = r.run(["osascript", "-e",
                 'tell application "System Events" to get name of '
                 'first process whose frontmost is true'],
                timeout=_SE_TIMEOUT)
    return out.strip()


#: Set at startup to the app's own notifier (`tray.make_notifier`). Without
#: it the capability shelled out to osascript, and the toast arrived with no
#: name and a generic icon because osascript posts as Script Editor — the
#: robot's message looked like it came from nowhere (reported 2026-08-31).
_notifier = None


def set_notifier(fn) -> None:
    global _notifier
    _notifier = fn


def _notify(r: Runner, args: dict) -> str:
    text = str(args.get("text", ""))
    title = str(args.get("title", "Робот"))
    if _notifier is not None:
        got = _notifier(title, text)
        # The notifier reports; older callables returned None. A robot told
        # "shown" about a toast nobody saw is a lie, and this surface exists
        # to refuse those.
        if isinstance(got, tuple):
            ok, why = got
            if not ok:
                raise CapabilityError(why or "уведомление не показано")
        return "notification shown"
    # No app notifier (a bare checkout): osascript still works, it just shows
    # up unattributed.
    r.run(["osascript", "-e",
           f'display notification "{text.replace(chr(34), chr(39))}" '
           f'with title "{title.replace(chr(34), chr(39))}"'])
    return "notification shown"


def _open_app(r: Runner, args: dict) -> str:
    app = str(args.get("app", "")).strip()
    if not app:
        raise CapabilityError("app name required")
    r.run(["open", "-a", app])
    return f"opened {app}"


def _open_url(r: Runner, args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise CapabilityError("only http(s) urls are allowed")
    r.run(["open", url])
    return f"opened {url}"


def _shortcut(r: Runner, args: dict) -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        raise CapabilityError("shortcut name required")
    inp = args.get("input")
    argv = ["shortcuts", "run", name]
    if inp:
        return r.run(argv, input_text=str(inp))
    return r.run(argv) or f"ran shortcut {name}"


def _applescript(r: Runner, args: dict) -> str:
    script = str(args.get("script", ""))
    low = script.lower()
    for bad in APPLESCRIPT_BLOCKED:
        if bad in low:
            raise CapabilityError(
                f"AppleScript targeting '{bad}' is blocked at the bridge")
    return r.run(["osascript", "-e", script]) or "ran applescript"


def _clipboard_read(r: Runner, args: dict) -> str:
    return r.run(["pbpaste"])


def _clipboard_write(r: Runner, args: dict) -> str:
    r.run(["pbcopy"], input_text=str(args.get("text", "")))
    return "clipboard set"


_STR = {"type": "string"}


def build_capabilities() -> dict[str, Capability]:
    """Единый MCP-контракт, платформенный пак по sys.platform (spec §5)."""
    if sys.platform == "darwin":
        return _build_darwin()
    if sys.platform.startswith("win"):  # pragma: no cover - win host only
        from .platforms import windows
        return windows.build_capabilities()
    from .platforms import linux
    return linux.build_capabilities()


def _build_darwin() -> dict[str, Capability]:
    caps = [
        Capability("screenshot", ToolClass.READ,
                   "смотрю на экран Мака", _screenshot, {},
                   binaries=("screencapture",)),
        Capability("list_apps", ToolClass.READ,
                   "смотрю список запущенных приложений", _list_apps, {},
                   binaries=("osascript",)),
        Capability("frontmost", ToolClass.READ,
                   "смотрю активное приложение", _frontmost, {},
                   binaries=("osascript",)),
        # The summary carries what was actually shown: "показываю уведомление
        # на Маке" told the owner nothing about what the robot put on their
        # screen, and the journal exists precisely to answer that.
        Capability("notify", ToolClass.READ,
                   "показываю уведомление «{title}»: {text}", _notify,
                   {"text": _STR, "title": _STR}, binaries=("osascript",)),
        Capability("open_app", ToolClass.ACT,
                   "открыть приложение «{app}»", _open_app, {"app": _STR},
                   binaries=("open",)),
        Capability("open_url", ToolClass.ACT,
                   "открыть ссылку {url}", _open_url, {"url": _STR},
                   binaries=("open",)),
        Capability("shortcut_run", ToolClass.ACT,
                   "запустить Shortcut «{name}»", _shortcut,
                   {"name": _STR, "input": _STR}, binaries=("shortcuts",)),
        Capability("automation", ToolClass.ACT,
                   "выполнить AppleScript на Маке", _applescript,
                   {"script": _STR}, binaries=("osascript",)),
        Capability("clipboard_read", ToolClass.ACT,
                   "прочитать буфер обмена Мака", _clipboard_read, {},
                   binaries=("pbpaste",)),
        Capability("clipboard_write", ToolClass.ACT,
                   "записать в буфер обмена Мака", _clipboard_write,
                   {"text": _STR}, binaries=("pbcopy",)),
    ]
    return {c.name: c for c in caps}


# ── availability: probed once at startup, never discovered at call time ─────


def _screen_capture_granted() -> bool | None:
    """True/False when the OS can answer, None when it cannot (no Quartz).
    Never triggers the TCC prompt itself — preflight only."""
    try:  # pragma: no cover - depends on pyobjc presence and OS
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def probe_availability(caps: dict[str, Capability], *,
                       which=shutil.which) -> dict[str, dict]:
    """The capability map (spec §4): available / needs-permission /
    unavailable, each with a reason the ROBOT can speak out loud. Probing
    happens at registration, so an impossible call is refused instantly
    instead of hanging into a timeout (vision, принцип 2)."""
    out: dict[str, dict] = {}
    for name, cap in caps.items():
        missing = [b for b in cap.binaries if not which(b)]
        if missing:
            out[name] = {"status": "unavailable",
                         "reason": f"на этом компьютере нет команды "
                                   f"«{missing[0]}»"}
            continue
        extra = _platform_probe_extras(name)
        if extra is not None:
            out[name] = {"status": extra[0], "reason": extra[1]}
            continue
        out[name] = {"status": "available", "reason": ""}
    return out


def _platform_probe_extras(name: str) -> tuple[str, str] | None:
    """Пере-статусы платформенного пака поверх бинарной probe."""
    if sys.platform == "darwin":
        if name == "screenshot" and _screen_capture_granted() is not True:
            # `is not True` covers False AND None. None means the preflight
            # could not run at all, and calling that "available" is how the
            # packaged app advertised a screenshot it could not take
            # (2026-08-30): the robot then plans around a capability that
            # fails at the worst moment. Unknown degrades toward the answer
            # that costs least when wrong.
            return ("needs-permission",
                    "нужны права «Запись экрана» — Настройки → "
                    "Конфиденциальность и безопасность → Запись экрана")
        return None
    if sys.platform.startswith("win"):  # pragma: no cover - win host only
        from .platforms import windows
        return windows.probe_extras(name)
    from .platforms import linux
    return linux.probe_extras(name)
