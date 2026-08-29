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


_SUMMARY_DEFAULTS = {"app": "?", "url": "?", "name": "?", "text": "?"}


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


def _notify(r: Runner, args: dict) -> str:
    text = str(args.get("text", "")).replace('"', "'")
    title = str(args.get("title", "Робот")).replace('"', "'")
    r.run(["osascript", "-e",
           f'display notification "{text}" with title "{title}"'])
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
    caps = [
        Capability("mac_screenshot", ToolClass.READ,
                   "смотрю на экран Мака", _screenshot, {},
                   binaries=("screencapture",)),
        Capability("mac_list_apps", ToolClass.READ,
                   "смотрю список запущенных приложений", _list_apps, {},
                   binaries=("osascript",)),
        Capability("mac_frontmost", ToolClass.READ,
                   "смотрю активное приложение", _frontmost, {},
                   binaries=("osascript",)),
        Capability("mac_notify", ToolClass.READ,
                   "показываю уведомление на Маке", _notify,
                   {"text": _STR, "title": _STR}, binaries=("osascript",)),
        Capability("mac_open_app", ToolClass.ACT,
                   "открыть приложение «{app}»", _open_app, {"app": _STR},
                   binaries=("open",)),
        Capability("mac_open_url", ToolClass.ACT,
                   "открыть ссылку {url}", _open_url, {"url": _STR},
                   binaries=("open",)),
        Capability("mac_shortcut_run", ToolClass.ACT,
                   "запустить Shortcut «{name}»", _shortcut,
                   {"name": _STR, "input": _STR}, binaries=("shortcuts",)),
        Capability("mac_applescript", ToolClass.ACT,
                   "выполнить AppleScript на Маке", _applescript,
                   {"script": _STR}, binaries=("osascript",)),
        Capability("mac_clipboard_read", ToolClass.ACT,
                   "прочитать буфер обмена Мака", _clipboard_read, {},
                   binaries=("pbpaste",)),
        Capability("mac_clipboard_write", ToolClass.ACT,
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
        status, reason = "available", ""
        if name == "mac_screenshot" and _screen_capture_granted() is False:
            status = "needs-permission"
            reason = ("нужны права «Запись экрана» — Настройки → "
                      "Конфиденциальность и безопасность → Запись экрана")
        out[name] = {"status": status, "reason": reason}
    return out
