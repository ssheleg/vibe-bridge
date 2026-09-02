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
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .consent import ToolClass

# AppleScript can reach anything scriptable; these targets are refused at the
# bridge regardless of consent — a compromised prompt must not read Keychain
# or spawn a terminal through us.
#
# **Two holes lived here until 2026-09-01, and both were named in the audit.**
# `do shell script` was absent, so `automation` handed out exactly the shell
# that this module's own docstring, the robot's skill file and the product's
# anti-vision all declare impossible («Никакого shell»). And the last entry
# read `system events» keystroke` — a `»` where `to` belongs — so it matched no
# real script ever: keystroke synthesis, the thing that entry exists to stop,
# was never blocked. Verified by running the real strings through the list.
#
# **What this list is, honestly.** A substring blocklist is not a sandbox. It
# stops the obvious and the accidental; a determined prompt can build
# `do shell script` from concatenation and this will not see it. The real
# boundary is that `automation` is ACT — the owner approves it, in the moment,
# with THE SCRIPT ITSELF in front of them (see the summary below). This list
# narrows the blast radius; the owner's eye is the gate.
APPLESCRIPT_BLOCKED = (
    "do shell script",          # the shell this product says it does not have
    "keystroke",                # synthesised input: типing into any app
    "key code",                 # the same by code
    "terminal",
    "keychain access",
    "keychain",
    "iterm",
    "script editor",
    "system events",            # the generic automation surface, by name
)


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

    #: Долгий аргумент в строке согласия перестаёт читаться, а нечитаемая
    #: строка согласия — это кнопка «Разрешить» без вопроса.
    SUMMARY_ARG_MAX = 160

    def summary(self, args: dict) -> str:
        trimmed = {k: (v if not isinstance(v, str)
                       or len(v) <= self.SUMMARY_ARG_MAX
                       else v[:self.SUMMARY_ARG_MAX] + "…")
                   for k, v in args.items()}
        try:
            return self.summary_template.format(
                **{**_SUMMARY_DEFAULTS, **trimmed})
        except Exception:
            return self.summary_template


_SUMMARY_DEFAULTS = {"app": "?", "url": "?", "name": "?", "text": "?",
                     "title": "без заголовка", "script": "?"}


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


#: Ширина, до которой снимок ужимается по умолчанию. Мозгу нужно понять, ЧТО
#: на экране, а не прочитать десятый пункт меню: 1280 px хватает на первое и
#: экономит на порядок против ретины 5К.
SCREENSHOT_MAX_WIDTH = 1280
#: Жёсткий потолок того, что уезжает в контекст. FastMCP сериализует ответ
#: инструмента в text content и НИКОГДА в `ImageContent`, поэтому полноэкранный
#: PNG приезжал мозгу как 3–11 МБ текста — на плате с 4 ГБ это не «медленно»,
#: это конец хода (A-17).
SCREENSHOT_MAX_BYTES = 1_500_000


def clamp_width(raw: object) -> int:
    """Ширина приходит от МОЗГА, а не от владельца: «20000» не должно
    обходить потолок, «0» — ронять масштабатор."""
    try:
        want = int(str(raw).strip())
    except (TypeError, ValueError):
        return SCREENSHOT_MAX_WIDTH
    if want <= 0:
        return 320
    return min(want, SCREENSHOT_MAX_WIDTH)


def encode_screenshot(data: bytes, mime: str) -> str:
    """Data-URL под потолком — или говоримый отказ.

    Отдать мозгу гигантскую строку хуже, чем не отдать ничего: он не может
    её ни отбросить, ни укоротить, а ход после неё уже не состоится.
    """
    import base64
    url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    if len(url) > SCREENSHOT_MAX_BYTES:
        raise CapabilityError(
            f"снимок слишком велик даже после сжатия "
            f"({len(url) // 1_000_000} МБ) — попросите меньшую ширину")
    return url


def _screenshot(r: Runner, args: dict) -> str:
    import os
    import tempfile

    width = clamp_width(args.get("max_width", ""))
    raw = tempfile.mktemp(suffix=".png")
    small = tempfile.mktemp(suffix=".jpg")
    # -x: no sound. -C: capture cursor off by default. Whole screen unless a
    # window id is given later; V1 keeps it to the main display.
    r.run(["screencapture", "-x", raw])
    try:
        try:
            # `sips` — системный инструмент macOS: масштаб и формат одним
            # вызовом, без единой сторонней зависимости в подписанном бандле.
            r.run(["sips", "-Z", str(width), raw, "--out", small,
                   "-s", "format", "jpeg", "-s", "formatOptions", "70"],
                  timeout=20.0)
            with open(small, "rb") as fh:
                data, mime = fh.read(), "image/jpeg"
        except (CapabilityError, OSError):
            # Честная деградация: снимок нужнее, чем идеальный размер. Потолок
            # ниже всё равно устоит — просто откажет громко, если не влезло.
            with open(raw, "rb") as fh:
                data, mime = fh.read(), "image/png"
    except OSError as exc:
        raise CapabilityError(f"screenshot unreadable: {exc}") from exc
    finally:
        # A full-screen PNG of the owner's desktop must not outlive the call.
        # It did: this path used `mktemp` and never unlinked, so every
        # screenshot the robot ever took stayed in /var/folders forever — while
        # the Linux pack next door deletes correctly. The owner allowed a look,
        # not a collection. Файлов теперь двое — уйти обязаны оба.
        for path in (raw, small):
            try:
                os.unlink(path)
            except OSError:
                pass
    return encode_screenshot(data, mime)


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


class RateLimit:
    """Скользящее окно. Столько-то раз за столько-то секунд, и не больше.

    Заведено ради `notify` (A-25), но намеренно общего вида: это первая
    способность класса READ с наружным эффектом, и она вряд ли последняя.
    """

    def __init__(self, *, per_window: int, window_s: float,
                 clock=time.monotonic) -> None:
        self._n = per_window
        self._window = window_s
        self._clock = clock
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self._clock()
            while self._hits and now - self._hits[0] >= self._window:
                self._hits.popleft()
            if len(self._hits) >= self._n:
                return False
            self._hits.append(now)
            return True

    def left_s(self) -> float:
        """Через сколько освободится место — чтобы отказ был говоримым."""
        with self._lock:
            if not self._hits:
                return 0.0
            return max(0.0, self._window - (self._clock() - self._hits[0]))


#: Тормоз для `notify`. Ставится приложением; в голом чекауте его нет, и
#: поведение остаётся прежним.
_notify_limit: RateLimit | None = None


def _set_notify_limit(limit: RateLimit | None) -> None:
    global _notify_limit
    _notify_limit = limit


def _notify(r: Runner, args: dict) -> str:
    text = str(args.get("text", ""))
    title = str(args.get("title", "Робот"))
    # READ исполняется без вопроса — а это единственный READ, который пишет
    # на экран владельца. Единственным тормозом был kill switch, то есть
    # «выключить всё»: между «пусть показывает» и «пусть замолчит совсем» не
    # было ничего (A-25).
    if _notify_limit is not None and not _notify_limit.allow():
        raise CapabilityError(
            f"слишком часто: подождите {int(_notify_limit.left_s()) + 1} с — "
            f"владелец не должен разгребать поток уведомлений")
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
                   "смотрю на экран Мака", _screenshot,
                   {"max_width": _STR},
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
        # The most dangerous tool had the least informative consent line:
        # «выполнить AppleScript на Маке» told the owner nothing about what
        # they were approving, while `notify` — far less dangerous — already
        # carried its text. The script goes in the line, truncated, and in
        # full into the journal's detail.
        Capability("automation", ToolClass.ACT,
                   "выполнить на Маке AppleScript: {script}", _applescript,
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


class AvailabilityMap:
    """Карта способностей, которая пере-опрашивает себя сама.

    Раньше это был обычный dict, снятый ОДИН раз при регистрации, и он
    служил источником сразу для двух вещей: таблицы в панели и мгновенного
    отказа роботу. Владелец выдавал «Запись экрана» в системных настройках —
    и до перезапуска моста не менялось ничего: панель продолжала писать
    «требует прав», а робот продолжал получать отказ на способность, которая
    уже работает (A-11).

    TTL нужен по существу: опрос дёргает `shutil.which` и preflight TCC, а
    робот может вызвать двадцать инструментов подряд. Пять секунд — это
    «владелец не заметит задержки» и «система не опрашивается в цикле».

    Ведёт себя как dict для обоих читателей (`get`, `items`), поэтому
    подмена на настоящий dict в тестах остаётся возможной.
    """

    TTL_S = 5.0

    def __init__(self, caps: dict[str, Capability], *,
                 clock=time.monotonic, probe=None) -> None:
        self._caps = caps
        self._clock = clock
        self._probe = probe or probe_availability
        self._lock = threading.Lock()
        self._at = 0.0
        self._map: dict[str, dict] = {}
        self.refresh()

    def refresh(self) -> dict[str, dict]:
        """Опросить систему прямо сейчас. После «Выдать права» владелец не
        должен ждать TTL."""
        fresh = self._probe(self._caps)
        with self._lock:
            self._map = fresh
            self._at = self._clock()
            return dict(self._map)

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            fresh_enough = self._clock() - self._at < self.TTL_S
            if fresh_enough:
                return dict(self._map)
        return self.refresh()

    def get(self, name: str, default=None):
        return self.snapshot().get(name, default)

    def items(self):
        return self.snapshot().items()

    def __contains__(self, name: str) -> bool:
        return name in self.snapshot()

    def __iter__(self):
        return iter(self.snapshot())


def request_permission(name: str) -> tuple[bool, str]:
    """Провести владельца к системному диалогу прав (SCN-020 шаг 2).

    Сценарий обещал «уведомление с кнопкой в системные настройки», и в коде
    не было ни того ни другого: карта просто говорила «требует прав» без
    единого пути её изменить.

    macOS показывает свой запрос ОДИН раз за жизнь бандла. Поэтому сначала
    просим систему (`CGRequestScreenCaptureAccess` — он и есть тот диалог), а
    если она молча отказала, открываем нужную панель настроек: второго
    системного окна уже не будет, и без этой ветки кнопка была бы кнопкой,
    которая иногда ничего не делает.
    """
    if sys.platform != "darwin" or name != "screenshot":
        return False, "у этой способности нет системного диалога прав"
    try:  # pragma: no cover - зависит от pyobjc и живой ОС
        import Quartz
        if bool(Quartz.CGRequestScreenCaptureAccess()):
            return True, "права записи экрана выданы"
    except Exception as exc:  # noqa: BLE001 - причина уходит владельцу
        return False, f"системный диалог недоступен: {exc}"
    pane = ("x-apple.systempreferences:com.apple.preference.security"
            "?Privacy_ScreenCapture")
    try:  # pragma: no cover - открывает окно на живой машине
        subprocess.run(["open", pane], check=False, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"не удалось открыть настройки прав: {exc}"
    return False, ("открыл «Конфиденциальность и безопасность → Запись "
                   "экрана» — включите там vibe-bridge")


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
