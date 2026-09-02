"""Settings the owner can change without editing source.

Until this module existed every operational value — the port, the network
mode, the release channel, every timeout — was a constant in a Python file,
while `docs/spec/architecture.md` §2 said the two modes are "выбираются
конфигом". A promise a user cannot act on is worse than an absent feature:
they go looking for the switch.

**Precedence, stated once:** environment variable → `config.toml` → default.
The environment wins because it is how a launcher or a test overrides one run
without touching the owner's file.

**A settings file never stops the bridge.** Every failure — missing,
unreadable, malformed, wrong type, from a newer version — degrades to the
default and lands in `Settings.problems`, which the panel shows and the
journal records. A remote control that refuses to start over a typo is worse
than one running last week's settings.

`state.json` keeps secrets and facts (tokens, pairing, subscriptions); this
keeps preferences. Two files because they have two different owners: one is
written by the bridge, the other by the person.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

#: Bumped only when an existing file would be read wrongly by new code.
VERSION = 1

MODES = ("standalone", "gateway")

#: Skins the bundled `mascot.js` registers. Kept here so a typo is refused at
#: write time instead of silently drawing the default.
SKINS = ("vasya", "dot")

_TEMPLATE = '''# vibe-bridge — настройки. Меняются здесь; перезапустите мост после правки.
# Любое значение можно переопределить переменной окружения (см. ниже) —
# переменная сильнее файла, файл сильнее умолчания.
# Непонятное или неверное значение НЕ останавливает мост: он берёт умолчание
# и пишет причину в панель (Настройки → Приложение) и в журнал.
version = {version}

# Порт панели и MCP-эндпоинта.            env: VIBE_BRIDGE_PORT
port = 48620

# Как робот дотягивается до этого компьютера.   env: VIBE_BRIDGE_MODE
#   standalone — мост слушает адрес в tailnet и требует у робота bearer-токен.
#                Это режим обычной установки.
#   gateway    — мост слушает только loopback и НЕ проверяет токен на /mcp:
#                границей служит agentgateway на этой же машине. Без него
#                эндпоинт остаётся без аутентификации — мост скажет об этом
#                в панели.
mode = "standalone"

[release]
# Откуда приезжают обновления. Своя сборка = свой репозиторий, иначе мост
# будет качать чужие payload'ы и отвергать каждый по несовпадению подписи.
repo = "ssheleg/vibe-bridge"

[update]
enabled = true            # фоновая проверка обновлений
interval_hours = 6
first_delay_minutes = 5   # пауза после старта: сначала робот, потом GitHub

[consent]
ask_timeout_s = 60        # молчание владельца дольше этого = отказ
grant_ttl_s = 900         # «разрешить ТАКИЕ на 15 минут» (грант поимённый)
# Сколько живёт одноразовый токен пейринга. Его копия лежит на FAT-разделе
# карты, и карта в ящике стола не должна оставаться ключом «стань роботом».
pairing_ttl_hours = 24
# Спрашивать и перед READ-инструментами (скриншот, список окон)? По умолчанию
# нет: они мгновенны и видны в журнале сразу после (vision §9.1). Включите,
# если хотите, чтобы про экран спрашивали отдельно.
ask_for_read = false

[robot]
# Что визард клонирует на новую Raspberry Pi. Свой форк робота — свой адрес.
repo = "https://github.com/ssheleg/rpi-ai-assistant.git"

[mascot]
# Внешний вид персонажа. Состояния задаёт мост, скин решает только как они
# ВЫГЛЯДЯТ. Известные: "vasya" (робот), "dot" (минимальная точка).
skin = "vasya"

# Быстрые фразы в меню питомца (клик по нему). Уходят роботу тем же каналом,
# что и чат в панели. Не больше восьми — это меню, а не панель.
actions = ["Как дела?", "Что нового?", "Расскажи, чем занят"]

# Показывать питомца отдельным окном поверх экрана? По умолчанию нет — окно,
# которое появляется поверх всего при первом запуске, владелец не заказывал.
# В панели персонаж есть всегда. Только macOS.
window = false
'''


@dataclass(frozen=True)
class Settings:
    port: int = 48620
    mode: str = "standalone"
    release_repo: str = "ssheleg/vibe-bridge"
    update_enabled: bool = True
    update_interval_s: int = 6 * 3600
    update_first_delay_s: int = 5 * 60
    ask_timeout_s: float = 60.0
    grant_ttl_s: float = 900.0
    pairing_ttl_hours: float = 24.0
    #: Ask before READ tools too (screenshot, list of windows). Off because
    #: vision §9.1 answers READ with "видно в журнале сразу после"; on because
    #: some owners want to be asked before their screen is read.
    ask_for_read: bool = False
    #: What the SD-card wizard clones onto a new robot. A fork needs its own.
    robot_repo: str = "https://github.com/ssheleg/rpi-ai-assistant.git"
    #: The floating pet on the desktop. Off by default: a window that appears
    #: over everything on first launch is a window the owner did not ask for.
    mascot_window: bool = False
    #: Which drawing the character uses. The states are the bridge's; a skin
    #: only decides how they LOOK — that line is what separates a skin from a
    #: fork of the mascot.
    mascot_skin: str = "vasya"
    #: Quick phrases in the pet's menu. They are sent to the robot's brain
    #: through the chat channel that already exists — a faster surface for it,
    #: not a new capability.
    mascot_actions: tuple[str, ...] = ("Как дела?", "Что нового?",
                                       "Расскажи, чем занят")
    #: Human-readable reasons a value was not honoured. Empty is the good case.
    problems: list[str] = field(default_factory=list)


def config_path() -> Path:
    from .state import _config_base
    return _config_base("vibe-bridge") / "config.toml"


# Each entry: (section, key, attribute, coercer). One table so the template,
# the reader and the writer cannot drift apart.
_FIELDS = (
    (None, "port", "port", "port"),
    (None, "mode", "mode", "mode"),
    ("release", "repo", "release_repo", "repo"),
    ("update", "enabled", "update_enabled", "bool"),
    ("update", "interval_hours", "update_interval_s", "hours"),
    ("update", "first_delay_minutes", "update_first_delay_s", "minutes"),
    ("consent", "ask_timeout_s", "ask_timeout_s", "seconds"),
    ("consent", "grant_ttl_s", "grant_ttl_s", "seconds"),
    ("consent", "pairing_ttl_hours", "pairing_ttl_hours", "hours"),
    ("consent", "ask_for_read", "ask_for_read", "bool"),
    ("robot", "repo", "robot_repo", "url"),
    ("mascot", "window", "mascot_window", "bool"),
    ("mascot", "skin", "mascot_skin", "skin"),
    ("mascot", "actions", "mascot_actions", "phrases"),
)

#: The comment each key carries when it is added to a file that predates it.
#: Same words as the template — the file is the manual, so a setting appended
#: later must arrive explained rather than as a bare line.
_NOTES = {
    "port": "Порт панели и MCP-эндпоинта.            env: VIBE_BRIDGE_PORT",
    "mode": ("Как робот дотягивается: standalone (tailnet + токен) или "
             "gateway (loopback,\n# границей служит agentgateway). "
             "env: VIBE_BRIDGE_MODE"),
    "release.repo": "Откуда приезжают обновления. Свой форк — свой репозиторий.",
    "update.enabled": "Фоновая проверка обновлений.",
    "update.interval_hours": "Как часто проверять.",
    "update.first_delay_minutes": "Пауза после старта: сначала робот, потом GitHub.",
    "consent.ask_timeout_s": "Молчание владельца дольше этого = отказ.",
    "consent.grant_ttl_s": "«Разрешить такие на 15 минут» — грант на ОДНО действие, не на класс.",
    "consent.pairing_ttl_hours": "Сколько живёт одноразовый токен пейринга.",
    "consent.ask_for_read": (
        "Спрашивать и перед READ (скриншот, список окон)? По умолчанию нет:\n"
        "# они мгновенны и видны в журнале сразу после (vision §9.1)."),
    "robot.repo": "Что визард клонирует на новую Raspberry Pi.",
    "mascot.actions": (
        "Быстрые фразы в меню питомца. Уходят роботу тем же каналом, что и\n"
        "# чат в панели. Не больше восьми — это меню, а не панель."),
    "mascot.skin": "Внешний вид персонажа: vasya (робот) или dot (точка).",
    "mascot.window": (
        "Показывать питомца отдельным окном поверх экрана? По умолчанию нет —\n"
        "# в панели он есть всегда. Только macOS."),
}

_ENV = {"VIBE_BRIDGE_PORT": ("port", "port"),
        "VIBE_BRIDGE_MODE": ("mode", "mode")}


def load(*, create: bool = False) -> Settings:
    """Read the settings in force. Never raises."""
    problems: list[str] = []
    path = config_path()
    raw: dict = {}

    if create and not path.exists():
        _seed(path, problems)

    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"config.toml не прочитан ({exc}) — взяты умолчания")
        except tomllib.TOMLDecodeError as exc:
            problems.append(f"config.toml не разобран ({exc}) — взяты умолчания")

    version = raw.get("version", VERSION)
    if isinstance(version, int) and version > VERSION:
        problems.append(
            f"config.toml версии {version} новее, чем понимает этот мост "
            f"(версия {VERSION}) — файл не применён, взяты умолчания. "
            f"Обновите приложение или удалите файл, чтобы создать его заново.")
        raw = {}

    values: dict = {}
    for section, key, attr, kind in _FIELDS:
        holder = raw.get(section, {}) if section else raw
        if not isinstance(holder, dict) or key not in holder:
            continue
        coerced = _coerce(holder[key], kind, f"{section + '.' if section else ''}{key}",
                          problems)
        if coerced is not None:
            values[attr] = coerced

    _report_unknown(raw, problems)

    for env_name, (attr, kind) in _ENV.items():
        if env_name not in os.environ:
            continue
        coerced = _coerce(os.environ[env_name], kind, env_name, problems)
        if coerced is not None:
            values[attr] = coerced

    return Settings(**values, problems=problems)


def top_up() -> None:
    """Add settings this version knows to a file written by an older one.

    The template is only written when the file is created, so every setting
    added afterwards is invisible to anyone who already had a config — they
    would have to read release notes to learn a switch exists. Values are
    never touched; only missing keys are appended, each with its comment.
    """
    path = config_path()
    if not path.exists():
        return
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return                       # a broken file is not ours to rewrite

    text = path.read_text(encoding="utf-8")
    changed = False
    for section, key, attr, kind in _FIELDS:
        holder = raw.get(section, {}) if section else raw
        if isinstance(holder, dict) and key in holder:
            continue
        note = _NOTES.get(f"{section}.{key}" if section else key)
        literal = _to_toml(getattr(Settings, attr), kind)
        text = _rewrite(text, section, key, literal, note=note)
        changed = True
    if changed:
        _write(path, text)


def migrate_from_state(state) -> None:
    """Carry a mode chosen before settings existed into the settings file.

    The distribution default became `standalone`; a machine already running
    `gateway` must keep it. Standalone binds a different interface and demands
    a bearer token the local agentgateway does not send, so silently switching
    an existing install would take the robot offline with nothing to point at.

    Runs once: an owner who has already written a config file owns that
    answer, and this must not argue with it.
    """
    if config_path().exists():
        return
    mode = getattr(state, "mode", None)
    if mode in MODES and mode != Settings.mode:
        load(create=True)
        update({"mode": mode})


def update(changes: dict) -> None:
    """Persist settings changed from the panel.

    Validates BEFORE writing: a value this module would reject on the next
    read must never reach the file, or the panel would report success and the
    bridge would quietly keep the old behaviour.
    """
    problems: list[str] = []
    by_attr = {attr: (section, key, kind)
               for section, key, attr, kind in _FIELDS}
    for attr, value in changes.items():
        if attr not in by_attr:
            raise ValueError(f"неизвестная настройка: {attr}")
        _, _, kind = by_attr[attr]
        if _coerce(value, kind, attr, problems) is None:
            raise ValueError(f"недопустимое значение {attr}={value!r}: "
                             f"{problems[-1] if problems else ''}")

    path = config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text:
        _seed(path, problems)
        text = path.read_text(encoding="utf-8")

    for attr, value in changes.items():
        section, key, kind = by_attr[attr]
        text = _rewrite(text, section, key, _to_toml(value, kind))
    _write(path, text)


# ------------------------------------------------------------------ helpers

def _coerce(value, kind: str, name: str, problems: list[str]):
    """Return the usable value, or None having explained why not."""
    try:
        if kind == "port":
            port = int(value)
            if not (1 <= port <= 65535):
                raise ValueError("вне диапазона 1–65535")
            return port
        if kind == "mode":
            mode = str(value)
            if mode not in MODES:
                raise ValueError(f"должно быть {' или '.join(MODES)}")
            return mode
        if kind == "url":
            url = str(value)
            if not url.startswith(("https://", "http://")):
                raise ValueError("ожидается http:// или https://")
            return url
        if kind == "repo":
            repo = str(value)
            if repo.count("/") != 1 or not all(repo.split("/")):
                raise ValueError("ожидается «владелец/репозиторий»")
            return repo
        if kind == "skin":
            name = str(value).strip()
            if name not in SKINS:
                raise ValueError(f"известные скины: {', '.join(SKINS)}")
            return name
        if kind == "phrases":
            if not isinstance(value, (list, tuple)):
                raise ValueError("ожидается список строк")
            items = tuple(str(v).strip() for v in value if str(v).strip())
            if len(items) > 8:
                raise ValueError("не больше восьми — это меню, а не панель")
            return items
        if kind == "bool":
            if not isinstance(value, bool):
                raise ValueError("ожидается true или false")
            return value
        if kind in ("hours", "minutes", "seconds"):
            number = float(value)
            if number <= 0:
                raise ValueError("должно быть больше нуля")
            scale = {"hours": 3600, "minutes": 60, "seconds": 1}[kind]
            scaled = number * scale
            return scaled if kind == "seconds" else int(scaled)
    except (TypeError, ValueError) as exc:
        problems.append(f"{name}={value!r} не принято ({exc}) — взято умолчание")
        return None
    return None                                  # pragma: no cover - guarded


def _report_unknown(raw: dict, problems: list[str]) -> None:
    """Name a typo instead of ignoring it — an ignored `prot = 9000` costs an
    hour of wondering why nothing changed."""
    known_top = {"version"} | {k for s, k, _, _ in _FIELDS if s is None}
    known_sections = {s for s, _, _, _ in _FIELDS if s}
    for key, value in raw.items():
        if key in known_top or key in known_sections:
            if key in known_sections and isinstance(value, dict):
                allowed = {k for s, k, _, _ in _FIELDS if s == key}
                for sub in value:
                    if sub not in allowed:
                        problems.append(
                            f"[{key}] {sub} — такой настройки нет, пропущена")
            continue
        problems.append(f"{key} — такой настройки нет, пропущена")


def _seed(path: Path, problems: list[str]) -> None:
    try:
        _write(path, _TEMPLATE.format(version=VERSION))
    except OSError as exc:                       # pragma: no cover
        problems.append(f"не удалось создать config.toml ({exc})")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _to_toml(value, kind: str) -> str:
    if kind == "phrases":
        inner = ", ".join('"' + str(v).replace('"', "'") + '"' for v in value)
        return f"[{inner}]"
    if kind == "bool":
        return "true" if value else "false"
    if kind in ("port", "hours", "minutes", "seconds"):
        return str(value)
    return f'"{value}"'


def _rewrite(text: str, section: str | None, key: str, literal: str,
             note: str | None = None) -> str:
    """Set `key` in `section`, preserving every comment around it.

    Rewriting rather than re-serialising: the template's comments are the only
    documentation most owners will read, and a round-trip through a TOML
    writer would delete all of them.
    """
    out: list[str] = []
    current: str | None = None
    done = False

    def is_our_key(line: str) -> bool:
        head = line.strip().split("=", 1)[0].strip()
        return current == section and head == key

    for line in text.splitlines():
        stripped = line.strip()
        entering_new_section = (stripped.startswith("[")
                                and stripped.endswith("]"))
        if entering_new_section:
            # Leaving our section without having found the key: append it at
            # the end of that section rather than after the whole file, or it
            # would land under someone else's header and mean something else.
            if not done and current == section:
                out.append(f"{key} = {literal}")
                done = True
            current = stripped[1:-1]
        elif not done and is_our_key(line):
            # Keep the trailing comment: it is what explains the value, and
            # replacing the whole line would delete the explanation the owner
            # needs precisely when they are changing the setting.
            _, _, trailing = line.partition("#")
            suffix = f"  # {trailing.strip()}" if trailing.strip() else ""
            out.append(f"{key} = {literal}{suffix}")
            done = True
            continue
        out.append(line)

    if not done:
        if section and f"[{section}]" not in text:
            out.append(f"\n[{section}]")
        if note:
            out.append(f"# {note}")
        out.append(f"{key} = {literal}")
    return "\n".join(out) + "\n"
