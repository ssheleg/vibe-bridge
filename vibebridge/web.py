"""One ASGI app, four doors: /mcp for the robot, / + /api + /events for the
owner's panel. The walking skeleton of vibe-bridge (spec §1, §7).

Mounting the FastMCP transport requires its session manager to run for the
process lifetime — that lives in this app's lifespan (research-notes §A).
Bearer auth for /mcp is OUR ASGI guard around the mount: with no
`token_verifier` the SDK installs no middleware of its own, so nothing
conflicts. Panel routes are gated by a cookie carrying the panel token.

Events: the walking skeleton polls the consent engine and audit tail into
snapshots and pushes them over SSE. T-CORE replaces polling with direct
engine hooks and per-request ids; the wire contract (event `state`, JSON
snapshot) is what the panel binds to and it survives that swap.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route

from .audit import AuditLog
from .capabilities import (
    AvailabilityMap,
    Capability,
    Runner,
    build_capabilities,
)
from .consent import ConsentEngine, Decision
from .feed import EventFeed
from .mascot import Mascot
from .push import PushSender, ensure_vapid_keys
from .robot import RobotClient
from .server import build_server
from .state import BridgeState


def _solid_png(size: int, rgb: tuple[int, int, int] = (0x2F, 0x6F, 0xEB)) -> bytes:
    """A solid-color PNG icon, stdlib-only (workbench accent). Good enough
    for a home-screen tile until M-PLATFORM ships real art."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    row = b"\x00" + bytes(rgb) * size
    idat = zlib.compress(row * size)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", idat) + chunk(b"IEND", b""))

#: Shown when someone opens the panel address without a token. It is a page
#: rather than `{"error":"unauthorized"}` because the reader is a person who
#: typed the address or lost the tab, and the answer they need is one
#: sentence. It carries no token: whoever sees this has proved nothing.
#: Дверь — первое, что видит человек без ключа. Раньше она жила ЗДЕСЬ,
#: питоновской строкой, и потому держала свою палитру и свои размеры: ни один
#: инструмент, который смотрит на CSS, в строку не заглядывает (V-1). Теперь
#: это файл среди прочих поверхностей, и гейт находит её сам.
_DOOR_FILE = "door.html"

PANEL_COOKIE = "vb_panel"
#: Сколько живёт кука панели. Сессионная умирала вместе с браузером, и PWA на
#: телефоне после перезапуска попадала на дверь, которая советует нажать значок
#: в меню-баре — совет, невыполнимый с iPhone (A-27).
PANEL_COOKIE_MAX_AGE = 90 * 24 * 3600


def _set_panel_cookie(resp, request: Request, token: str) -> None:
    """Кука с сроком жизни, и `secure` — только когда соединение и правда
    защищённое: на loopback по http флаг `secure` выбросил бы куку молча."""
    resp.set_cookie(PANEL_COOKIE, token, httponly=True, samesite="lax",
                    max_age=PANEL_COOKIE_MAX_AGE,
                    secure=request.url.scheme == "https")
_WEBUI = Path(__file__).parent / "webui"

_DECISIONS = {
    "allow": Decision.ALLOW,
    "allow_grant": Decision.ALLOW_GRANT,
    "deny": Decision.DENY,
}


class _RefusalJournal:
    """Отказ на границе попадает в журнал — но не превращает его в access-log.

    Визия §3 обещает журналировать «каждое обращение — исполненное и
    отклонённое», и до 2026-09-01 обе границы (`PeerGuard`, `BearerGuard`)
    отказывали МОЛЧА: владелец не мог узнать, что кто-то стучался. Обратная
    крайность так же плоха: сканер из локальной сети за минуту вытеснит из
    журнала всё, ради чего журнал существует.

    Поэтому политика та же, что у автообновления: одна строка на РАЗЛИЧНЫЙ
    отказ, повтор того же — не чаще раза в минуту.
    """

    WINDOW_S = 60.0

    def __init__(self, audit) -> None:
        self._audit = audit
        self._seen: dict[str, float] = {}

    def refuse(self, kind: str, line: str, detail: str = "") -> None:
        import time as _time
        now = _time.monotonic()
        last = self._seen.get(kind)
        if last is not None and now - last < self.WINDOW_S:
            return
        self._seen[kind] = now
        try:
            self._audit.record(tool="boundary", tool_class="SYS",
                               decision="deny", ok=False,
                               line=line, detail=detail or line)
        except Exception:                   # noqa: BLE001
            # молчим: отказ УЖЕ произошёл — это граница, а не запись о ней.
            # Пустить исключение отсюда значило бы отменить отказ ради
            # неудачной строчки в журнале. Сам провал журнала виден
            # владельцу в панели (`journal_error`).
            pass


def pending_version(installed: str | None, running: str) -> str | None:
    """Версия, которая применится после перезапуска — или None.

    Раньше здесь стояло `installed != running`, то есть сравнение СТРОК. После
    установки нового DMG каталог payload остаётся на прошлой версии, и панель
    начинала обещать обновление, которого нет: «обновление скачано и
    применится после перезапуска» — при том что перезапуск ничего не менял,
    потому что оболочка на равенстве и на старшинстве выбирает seed. Механизм
    обновления начинал врать ровно там, где ADR-0006 держит всё доверие.

    Чистая и снаружи — чтобы три случая (новее / равно / старее) проверялись
    без HTTP-стека.
    """
    if not installed:
        return None
    from vbboot import layout as _layout
    try:
        return installed if _layout.parse(installed) > _layout.parse(running) \
            else None
    except Exception:                       # noqa: BLE001 - непарсимое молчит
        return None


#: Пути, открытые БЕЗ ключа панели, и почему. Всё остальное закрыто
#: `PanelGuard` — по одному месту, а не по копии на обработчик (F-3).
PUBLIC_PATHS = frozenset({
    "/",                      # дверь: обменивает ?token= на куку
    "/mascot",                # вторая дверь, тот же обмен
    "/pair",                  # дверь РОБОТА, своя аутентификация
    "/sw.js", "/manifest.webmanifest", "/offline.html",
    "/mascot.js", "/tokens.css",
})
#: Префиксы того же смысла — путь с параметром.
PUBLIC_PREFIXES = ("/icon-", "/mcp")


class PanelGuard:
    """Ключ панели проверяется ОДИН раз, на входе, а не 34 копиями.

    Проверка стояла в каждом обработчике отдельно, и теста, который перебрал
    бы все маршруты, не было (F-3). Копия — это то, что забывают: новый
    маршрут добавляется без неё и работает, а значит никто не узнаёт.

    Копии в обработчиках сняты не «ради чистоты»: пока их 34, вопрос «закрыт
    ли этот путь» отвечается чтением тридцати четырёх мест, и ответ «да»
    ничего не гарантирует про тридцать пятое.
    """

    def __init__(self, app, *, is_authed, public=PUBLIC_PATHS,
                 prefixes=PUBLIC_PREFIXES) -> None:
        self.app = app
        self._is_authed = is_authed
        self._public = public
        self._prefixes = prefixes

    def _open(self, path: str) -> bool:
        return path in self._public or path.startswith(self._prefixes)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and not self._open(scope.get("path", "")):
            from starlette.requests import Request as _Request
            if not self._is_authed(_Request(scope, receive)):
                await JSONResponse({"error": "unauthorized"},
                                   status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class PeerGuard:
    """403 всему, что пришло НЕ из loopback и не из тейлнета.

    Взводится только когда мост слушает больше, чем loopback (режим
    standalone): если бинд и так loopback, каждый пир и есть loopback, и
    проверка была бы декорацией.

    Зачем вообще: до 2026-09-01 standalone биндил ОДИН tailnet-интерфейс, и
    это выглядело границей — но ломало приложение целиком, потому что панель и
    окна виджета обращаются к мосту по `127.0.0.1`. Бинд расширили до всех
    интерфейсов, и тогда единственной «границей» оставался allowlist по
    `Host` — заголовку, который присылает клиент. Настоящая граница — адрес
    пира, и вот она.
    """

    def __init__(self, app, armed: bool, journal=None) -> None:
        self.app, self.armed, self.journal = app, armed, journal

    async def __call__(self, scope, receive, send) -> None:
        if self.armed and scope["type"] in ("http", "websocket"):
            from .net import peer_allowed
            client = scope.get("client") or (None, None)
            if not peer_allowed(client[0]):
                if self.journal is not None:
                    self.journal.refuse(
                        f"peer:{client[0]}",
                        f"отказано соединению не из тейлнета: {client[0]}",
                        f"{scope.get('method', '?')} {scope.get('path', '?')}")
                await JSONResponse(
                    {"error": "forbidden",
                     "detail": "мост принимает соединения только из loopback "
                               "и из тейлнета владельца"},
                    status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class BearerGuard:
    """401 before the MCP transport unless the robot token rides the
    Authorization header — ТОЛЬКО в standalone-режиме (ADR-0002). В
    gateway-режиме границей служат loopback+agentgateway, и гейт обязан
    пропускать: ключеваться на «токен существует» нельзя — пейринг создаёт
    robot_token, не меняя режима, и 2026-08-29 это молча отдало роботу 401
    на его же mac_*-инструменты на ~15 часов (замечено post-rename
    проверкой цепи)."""

    def __init__(self, app, state: BridgeState, mode: str = "standalone",
                 journal=None) -> None:
        self.app, self.state, self.mode = app, state, mode
        self.journal = journal

    async def __call__(self, scope, receive, send) -> None:
        token = (self.state.robot_token
                 if self.mode == "standalone" else None)
        # standalone БЕЗ спаренного робота: раньше здесь стоял пропуск —
        # `if token and ...` — и на свежей установке /mcp отвечал вообще без
        # аутентификации. Это была единственная дверь, которую сторожил
        # host-allowlist, а он сторожить не может: `Host` приходит от клиента
        # (измерено 2026-09-01). До пейринга робота нет, значит и обслуживать
        # тут некого.
        if (self.mode == "standalone" and not token
                and scope["type"] == "http"):
            if self.journal is not None:
                self.journal.refuse(
                    "mcp:unpaired",
                    "к MCP обратились до связки с роботом — отказано",
                    "нет robot_token")
            await JSONResponse(
                {"error": "unpaired",
                 "detail": "робот ещё не связан с этим мостом — /mcp закрыт"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        if token and scope["type"] == "http":
            auth = ""
            for k, v in scope.get("headers", []):
                if k == b"authorization":
                    auth = v.decode("latin-1")
                    break
            if auth != f"Bearer {token}":
                if self.journal is not None:
                    self.journal.refuse(
                        "mcp:badtoken",
                        "к MCP обратились без верного токена робота — отказано",
                        "Authorization не совпал")
                await JSONResponse(
                    {"error": "unauthorized"}, status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


#: Сколько текста события доезжает до ленты. Робот шлёт человеческую фразу,
#: а не документ; всё, что длиннее, — это чей-то лог, попавший не туда.
EVENT_TEXT_MAX = 400


def normalise_robot_event(raw: dict, *, now_iso: str) -> dict[str, Any]:
    """Событие робота → строка ленты. Чистая функция (F-2).

    Жила внутри консьюмера в замыкании, и обе находки о ленте случились
    здесь: `channel` терялся при пересборке словаря (A-18), а вторая копия
    события появлялась строкой ниже (A-13). Проверить это можно было только
    подняв приложение и дождавшись SSE.
    """
    return {
        "ts": raw.get("ts") or now_iso,
        "kind": raw.get("kind", "event"),
        "text": str(raw.get("text", ""))[:EVENT_TEXT_MAX],
        # Канал робот кладёт ИМЕННО для ленты: показ на этом компьютере и
        # зеркало телеграма — разные новости, и мост его выбрасывал (A-18).
        "channel": raw.get("channel") or None,
        # Optional, for when the robot starts sending media:
        # {"url": …, "type": "image"|"audio"|"video"|"link"}.
        "media": raw.get("media") or None,
    }


def bridge_base_url(*, dns: str | None, https_ok: bool, port: int) -> str:
    """Адрес, по которому мост достижим ИЗВНЕ, — один расчёт на всех.

    A-21 нашёл его в трёх местах с константой вместо действующего порта.
    Здесь он один, и порт передаётся аргументом: забыть его нельзя, а
    подставить чужой — видно в вызове.
    """
    if https_ok and dns:
        return f"https://{dns}"
    return f"http://{dns or '127.0.0.1'}:{port}"


def mcp_url(*, dns: str | None, https_ok: bool, port: int) -> str:
    """Куда роботу ходить за MCP. Loopback, пока нет HTTPS в тейлнете."""
    if https_ok and dns:
        return f"https://{dns}/mcp"
    return f"http://127.0.0.1:{port}/mcp"


def attach_request(body: dict) -> tuple[dict[str, str] | None, str]:
    """Что владелец просит привязать — или почему это не адрес (F-2).

    Валидация жила в обработчике, и A-12 нашёл её слишком слабой: проверялся
    ОДИН префикс, после чего мост объявлял «привязан ✓». Отдельной функцией
    видно, что именно она проверяет, а чего — нет.
    """
    base_url = str(body.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        return None, ("нужен адрес робота (bridge-API), например "
                      "https://robot.tailnet.ts.net")
    if not base_url.startswith(("http://", "https://")):
        return None, "адрес должен начинаться с http:// или https://"
    return {
        "base_url": base_url,
        "chat_url": str(body.get("chat_url", "")).strip(),
        "key": str(body.get("key", "")).strip(),
        "name": str(body.get("name", "")).strip() or "робот",
    }, ""


def attach_words(name: str, probe: dict) -> tuple[str, str]:
    """Что сказать в журнал и в уведомление после попытки дозвониться.

    «Связан ✓» без ответа робота — обещание, которого мост не может
    выполнить: панель рядом скажет «не подключён» (A-12).
    """
    if probe.get("ok"):
        return (f"робот «{name}» привязан вручную и отвечает",
                f"Робот «{name}» связан с мостом ✓")
    return (f"робот «{name}» привязан вручную, но не отвечает: "
            f"{probe.get('error', '')}",
            f"Робот «{name}» записан, но пока не отвечает")


class PairVerdict(Enum):
    """Решение о предъявленном токене пейринга — и слова для обеих сторон.

    Вынесено из `build_app` (F-2). Внутри замыкания это была ветка в
    обработчике, и проверить её можно было только через HTTP; при этом
    решение чисто арифметическое и никакого сервера не требует.

    Отказы РАЗНЫЕ намеренно (A-22): истёкший токен и подделанный — разные
    новости. В первом случае владельцу надо выдать новый в панели, во
    втором — задуматься, кто стучится.
    """

    OK = ("", "")
    BAD_TOKEN = ("попытка пейринга с неверным токеном",
                 "неверный или погашенный токен")
    EXPIRED = ("токен пейринга истёк — выдайте новый в панели",
               "токен пейринга истёк — возьмите новый в панели")

    def __init__(self, journal: str, spoken: str) -> None:
        self.journal = journal
        self.spoken = spoken


def pairing_verdict(*, offered: str, expected: str | None,
                    issued_at: float | None, ttl_s: float,
                    now: float | None = None) -> PairVerdict:
    """Пускать ли этого робота. Чистая функция — время передаётся."""
    import secrets as _secrets

    # Сравниваем БАЙТЫ. `compare_digest` на строках отказывается работать с
    # не-ASCII и БРОСАЕТ TypeError — а `offered` приходит из сети, где может
    # быть что угодно. В обработчике это исключение улетало бы наружу, и
    # мост отвечал бы 500 там, где должен ответить 403. Найдено ровно тем,
    # что решение вынули из замыкания и смогли позвать напрямую (F-2).
    if not expected or not _secrets.compare_digest(
            offered.encode("utf-8", "surrogatepass"),
            expected.encode("utf-8", "surrogatepass")):
        return PairVerdict.BAD_TOKEN
    moment = time.time() if now is None else now
    if ttl_s > 0 and issued_at is not None and moment - issued_at > ttl_s:
        return PairVerdict.EXPIRED
    return PairVerdict.OK


#: Поля, чьё расхождение файла и процесса означает «нужен перезапуск».
_RESTART_FIELDS = ("port", "mode", "release_repo", "update_enabled",
                   "update_interval_s", "ask_timeout_s")


def settings_view(*, live, on_disk, bind_host: str, has_robot_token: bool,
                  gateway_ok: bool | None,
                  config_path_fn=None) -> dict[str, Any]:
    """Что панель показывает в «Доступе и настройках» — ЧИСТО.

    Вынесено из `build_app` (F-2): там это жило внутри замыкания на тысячу
    строк, и проверить, что «переключить в …» называет правильный режим,
    можно было только подняв HTTP-стек целиком. Пять находок этого рана
    (A-11, A-13, A-16, A-21, A-41) правились точечно ровно поэтому.

    `gateway_ok`: True/False в режиме gateway, None в standalone — там
    вопрос не задаётся, и выдумывать ответ значило бы врать.
    """
    from .config import config_path

    pending = [name for name in _RESTART_FIELDS
               if getattr(live, name) != getattr(on_disk, name)]
    loopback = bind_host in ("127.0.0.1", "localhost", "::1")
    body: dict[str, Any] = {
        "path": str((config_path_fn or config_path)()),
        "port": live.port,
        "mode": live.mode,
        "release_repo": live.release_repo,
        "update_enabled": live.update_enabled,
        "update_interval_hours": round(live.update_interval_s / 3600, 2),
        "ask_timeout_s": live.ask_timeout_s,
        "ask_for_read": live.ask_for_read,
        "robot_repo": live.robot_repo,
        "mascot_window": live.mascot_window,
        "mascot_skin": live.mascot_skin,
        "problems": on_disk.problems,
        "pending": pending,
        "restart_required": bool(pending),
        "mcp_url": (f"http://127.0.0.1:{live.port}/mcp"
                    if live.mode == "gateway"
                    else f"http://<адрес в tailnet>:{live.port}/mcp"),
        # Панель не говорила, какие интерфейсы мост СЛУШАЕТ, — а это и есть
        # граница, которую переключает одна кнопка (A-24).
        "bind_host": bind_host,
        "listens": ("только этот компьютер (loopback)" if loopback
                    else f"все интерфейсы этой машины ({bind_host})"),
        # Что случится, если нажать «Переключить» — ДО нажатия, а не после
        # перезапуска.
        "switch_to": "standalone" if live.mode == "gateway" else "gateway",
        "switch_effect": (
            "standalone: мост начнёт слушать сеть, и дверью станет "
            "bearer-токен робота — сам мост, а не шлюз."
            if live.mode == "gateway" else
            "gateway: мост уйдёт на loopback, а границей станет "
            "agentgateway — БЕЗ него /mcp останется без проверки токена."),
        "gateway_ok": gateway_ok,
    }
    if live.mode == "gateway":
        body["mcp_auth"] = "нет — границей служит agentgateway"
        if not gateway_ok:
            body["warning"] = (
                "режим gateway, но agentgateway на этой машине не отвечает: "
                "MCP-эндпоинт сейчас БЕЗ аутентификации. Переключитесь на "
                "standalone или запустите шлюз.")
    else:
        body["mcp_auth"] = ("bearer-токен робота" if has_robot_token else
                            "токен появится после связки с роботом")
    return body


def _grant_label(tool: str, caps: dict | None) -> str:
    """Человеческое имя способности для списка грантов. Берётся из шаблона
    строки согласия — до первой подстановки: «открыть ссылку {url}» →
    «открыть ссылку». Второго словаря имён здесь не заводится."""
    cap = (caps or {}).get(tool)
    if cap is None:
        return tool
    return cap.summary_template.split("{")[0].strip(" ,:«").strip() or tool


def _snapshot(consent: ConsentEngine, audit: AuditLog,
              caps: dict | None = None) -> dict[str, Any]:
    reqs = consent.pending_all()
    req = reqs[0] if reqs else None
    return {
        "paused": consent.paused,
        # Молчание — тоже решение, и поверхность обязана это показать:
        # три кнопки без отсчёта не говорят владельцу, что он уже отвечает
        # (A-9). `timeout_s` — настоящая настройка, не зашитые 60.
        "pending": ({"id": req.id, "tool": req.tool,
                     "class": req.tool_class.value,
                     "summary": req.summary,
                     "left_s": round(consent.remaining(req), 1),
                     "timeout_s": consent.ask_timeout_s} if req else None),
        "pending_count": len(reqs),
        # Гранты называются ПОИМЁННО: счётчик минут не отвечает на вопрос
        # «что мне сейчас разрешено без вопроса» (A-8, визия §1).
        "grants": [{"tool": t, "label": _grant_label(t, caps),
                    "left_s": int(left)}
                   for t, left in sorted(consent.grants().items())],
        "grant_left_s": int(max(consent.grants().values(), default=0)),
        # Мост, работающий БЕЗ журнала, — это не мост: «журналирует каждое
        # обращение» стоит в описании продукта. Если писать не выходит,
        # владелец узнаёт об этом здесь, а не никогда (A-36).
        "journal_error": getattr(audit, "last_error", None),
        "recent": audit.recent(20),
    }


class EventBus:
    """Snapshot poller → per-subscriber queues. Skeleton-grade by design."""

    def __init__(self, snapshot_fn, interval: float = 0.2) -> None:
        self._snapshot_fn = snapshot_fn
        self._interval = interval
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subs.add(q)
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(self._snapshot_fn())        # initial state, always
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def pump(self) -> None:
        last: dict | None = None
        while True:
            snap = self._snapshot_fn()
            if snap != last:
                last = snap
                for q in list(self._subs):
                    with contextlib.suppress(asyncio.QueueFull):
                        q.put_nowait(snap)
            await asyncio.sleep(self._interval)


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bundle_resources() -> Path | None:
    """`Contents/Resources` when running from a signed .app, else None.

    Anchored on `vbboot`, never on this file. `vbboot` is the shell and never
    leaves the bundle; `vibebridge` is the payload and lives in Application
    Support the moment the first update lands. Anchoring here would find the
    bundle exactly once — on a fresh install — and every later update would
    refuse itself for want of a public key.

    None is what a development checkout gets, and it makes `install` refuse
    deliberately: the trust anchor is the signed bundle, so code running
    outside one has no channel it is entitled to trust.
    """
    import vbboot
    here = Path(vbboot.__file__).resolve()
    for parent in here.parents:
        if parent.name == "Resources" and parent.parent.name == "Contents":
            return parent
    return None


#: Что оболочка сказала про выбор кода. `None` — её не спрашивали (голый
#: чекаут) либо она промолчала; тогда и только тогда мы гадаем.
_chosen = None


def set_chosen(chosen) -> None:
    """Принять ответ оболочки. Зовётся один раз, из `app.run`."""
    global _chosen
    _chosen = chosen


def _payload_source(root: Path, running: str) -> str:
    """Откуда взят работающий код — ОТВЕТ оболочки, а не догадка.

    Догадка была «есть каталог с такой версией → значит payload», и она
    врала ровно в том случае, ради которого источник и показывают: когда
    версия seed совпадает с уже установленным payload, каталог существует,
    а работает при этом seed (F-5).
    """
    if _chosen is not None:
        return _chosen.source
    if _bundle_resources() is None:
        return "dev"
    return "payload" if (root / running).is_dir() else "seed"


def source_note() -> str:
    """Пусто, когда источник ИЗВЕСТЕН; иначе — почему он догадка.

    Второе направление шва (B-45): оболочка старше payload не роняет мост, а
    молча лишает его ответа — и панель показывала догадку тем же шрифтом, что
    факт. Измерено 2026-09-02: мост 0.25.0 на оболочке 0.19.0.

    Вне бандла пусто: «запущен из исходников» — это и есть точный ответ.
    """
    if _chosen is not None or _bundle_resources() is None:
        return ""
    from vbboot.runner import shell_version

    from .shell_api import degradation, not_provided
    gaps = not_provided(chosen=None)
    return degradation(gaps, shell_version()) if gaps else ""


#: Our own code carries NO-STORE, and the reason is measured rather than
#: theoretical. `FileResponse` sends only `etag` and `last-modified`; with no
#: explicit freshness a client may fall back to a heuristic, and WKWebView
#: served the widget's page from its disk cache ACROSS an app restart. The pet
#: therefore ran yesterday's JavaScript inside today's app: a message the new
#: page was supposed to post never arrived, and the bug looked like a broken
#: native handler for three rounds — until `rm -rf ~/Library/WebKit/<bundle>`
#: made the identical drag work (2026-09-01).
#:
#: A payload update replaces exactly these files. A cache that outlives the
#: update makes the update mechanism a lie, which is a worse defect than any
#: single bug it hides. Images are deliberately NOT included: the PWA's
#: service worker caches them on purpose, for offline.
_NO_STORE = {"Cache-Control": "no-store, must-revalidate"}


def token_value(css: str, name: str) -> str:
    """Значение токена из `tokens.css` — СВЕТЛОЙ темы, первое вхождение.

    Манифест не умеет CSS-переменных, поэтому цвет в нём приходится
    повторять. Повторять его РУКАМИ — это четвёртая копия палитры (V-1), а
    копия, которую никто не сверяет, расходится молча: до 2026-09-02 в
    манифесте лежали `#f7f8fa` и `#2f6feb`, и совпадали они по случайности.

    Тёмная тема сюда не годится намеренно: манифест один, а тем две, и
    выбирать за систему нечего — берём базовую.
    """
    head = css.split("@media", 1)[0]
    found = re.search(rf"{re.escape(name)}\s*:\s*([^;}}]+)", head)
    if not found:
        raise KeyError(f"в tokens.css нет {name}")
    return found.group(1).strip()


def manifest_body(raw: str, css: str) -> bytes:
    """Манифест с цветами, взятыми из токенов, а не написанными рядом."""
    data = json.loads(raw)
    data["background_color"] = token_value(css, "--bg")
    data["theme_color"] = token_value(css, "--accent")
    return json.dumps(data, ensure_ascii=False, indent=2).encode()


def _code_file(path, media: str | None = None, *,
               status_code: int = 200) -> Response:
    """A page or script of ours, served so the browser cannot keep it."""
    if media is None:
        return FileResponse(path, headers=_NO_STORE, status_code=status_code)
    return FileResponse(path, media_type=media, headers=_NO_STORE,
                        status_code=status_code)


def build_app(*, consent: ConsentEngine, audit: AuditLog, state: BridgeState,
              runner: Runner | None = None,
              capabilities: dict[str, Capability] | None = None,
              mcp_allowed_hosts: list[str] | None = None,
              robot: RobotClient | None = None,
              notify=None,
              push_sender: PushSender | None = None,
              settings=None,
              feed: EventFeed | None = None,
              bind_host: str = "127.0.0.1",
              peer_guard: bool = False) -> Starlette:
    from .config import load as _load_settings
    from .net import allowed_hosts as _net_allowed_hosts

    if settings is None:
        settings = _load_settings()

    if push_sender is None:
        push_sender = PushSender(state)

    if robot is None:
        robot = RobotClient(base_url=state.robot_base_url,
                            chat_url=state.robot_chat_url,
                            # общий секрет: bridge_api робота гейтит ОБА
                            # канала одним robot_token (fallback для пар,
                            # заключённых до фикса chat_key-дефолта)
                            chat_key=(state.robot_chat_key
                                      or state.robot_token),
                            name=state.robot_name or "робот")
    notify = notify or (lambda title, text: None)
    _base_notify = notify

    def notify(title: str, text: str):
        """Everything the robot puts on this computer is one stream.

        The head says it too: a notification is the robot talking, and the
        owner asked for it to come from the character rather than only from a
        grey system banner. It still goes to the system — a toast survives a
        hidden pet.
        """
        line = f"{title}: {text}" if title else text
        try:
            robot_events.add({"ts": _now_iso(), "kind": "notify",
                              "text": line})
            mascot.say(line, kind="notify")
        except Exception:                       # noqa: BLE001
            # молчим: системный тост ниже — главное, а лента и питомец
            # украшение. Робот не должен терять уведомление владельцу из-за
            # того, что не нарисовалась вторая поверхность.
            pass
        return _base_notify(title, text)
    robot_state: dict = {"configured": robot.configured, "online": False,
                         "reason": "робот не подключён к панели"
                         if not robot.configured else "ещё не проверял"}
    # Лента переживает перезапуск: до 2026-09-02 она жила только в памяти, и
    # всё сказанное роботом, пока мост был выключен или перезапускался,
    # исчезало молча (A-19). Файл ложится рядом с журналом — тот же каталог,
    # та же судьба при переезде.
    robot_events = feed or EventFeed(audit.path.parent / "robot-feed.jsonl")
    if robot_events.last_error:
        audit.record(tool="feed", tool_class="SYS", decision="error",
                     ok=False, line=robot_events.last_error,
                     detail=robot_events.last_error)
    # The live thread, per conversation. Bounded and forgotten on a new
    # session: enough for the brain to follow what was just said, not an
    # archive (vision, «Не мессенджер»).
    chat_history: dict[str, deque] = {}
    chat_inflight: set[str] = set()      # один ход на сессию (A-6)
    missed_while_paused = {"n": 0}
    # The face. It derives pause and pending from the engine rather than
    # keeping its own copy — two sources of truth for "is the bridge paused"
    # is how a mascot ends up smiling at a stopped bridge.
    mascot = Mascot(consent=consent, robot_state=robot_state)

    # The robot's notifications go through the app's own notifier, so they
    # carry its name and icon instead of arriving unattributed.
    from .capabilities import RateLimit, _set_notify_limit, set_notifier
    set_notifier(notify)
    # Тормоз для единственного READ с наружным эффектом. Ставится здесь, а не
    # в модуле способностей: число — настройка владельца (A-25).
    _set_notify_limit(
        RateLimit(per_window=int(settings.notify_per_minute), window_s=60.0)
        if int(getattr(settings, "notify_per_minute", 0)) > 0 else None)
    caps = capabilities or build_capabilities()
    # Живая карта: она пере-опрашивает себя, поэтому выданные права
    # видны и в панели, и в мгновенном отказе роботу без перезапуска
    # моста (A-11).
    availability = AvailabilityMap(caps)
    _refusals = _RefusalJournal(audit)

    if mcp_allowed_hosts is None:
        mcp_allowed_hosts = _net_allowed_hosts(state)
    # SCN-020 шаг 1. Права выдаёт владелец — значит сказать надо ему, и в тот
    # момент, когда это понадобилось. Раз в десять минут на способность:
    # робот может звать инструмент в цикле, а уведомление, которое приходит
    # двадцать раз подряд, выключают вместе со всеми остальными.
    _asked_at: dict[str, float] = {}
    ASK_AGAIN_S = 600.0

    def _needs_permission(name: str, reason: str) -> None:
        import time as _t
        now = _t.monotonic()
        if now - _asked_at.get(name, -1e9) < ASK_AGAIN_S:
            return
        _asked_at[name] = now
        notify("vibe-bridge",
               f"Роботу нужны права: {reason}. Откройте «Доступ и настройки» "
               f"— там кнопка «Выдать права».")

    mcp = build_server(consent=consent, audit=audit, runner=runner,
                       capabilities=caps, availability=availability,
                       allowed_hosts=mcp_allowed_hosts,
                       on_needs_permission=_needs_permission)

    def _full_snapshot() -> dict[str, Any]:
        snap = _snapshot(consent, audit, caps)
        snap["robot"] = dict(robot_state)
        snap["robot_events"] = robot_events.tail(10)
        return snap
    # The transport keeps its own /mcp path and the inner app mounts at the
    # ROOT, after every panel route. Mounting at "/mcp" instead produces a
    # 307 → /mcp/ whose Location is built from the Host header — for the
    # gateway (Host = 100.x:4000, proxied verbatim) that redirect points AT
    # THE GATEWAY ITSELF and the robot's call never lands (measured
    # 2026-08-29). The wire contract is "/mcp exactly, no redirect" (M4).
    mcp.settings.streamable_http_path = "/mcp"
    bus = EventBus(_full_snapshot)

    def _authed(request: Request) -> bool:
        return request.cookies.get(PANEL_COOKIE) == state.panel_token

    async def index(request: Request) -> Response:
        token = request.query_params.get("token")
        if token is not None:
            if token != state.panel_token:
                return JSONResponse({"error": "forbidden"}, status_code=403)
            resp = RedirectResponse("/", status_code=303)
            _set_panel_cookie(resp, request, state.panel_token)
            return resp
        if not _authed(request):
            # A person, not a program, is reading this. The token is never in
            # the page: whoever can see it here has not proved anything yet.
            return _code_file(_WEBUI / _DOOR_FILE,
                              "text/html; charset=utf-8",
                              status_code=401)
        return _code_file(_WEBUI / "index.html")

    async def api_state(request: Request) -> Response:
        return JSONResponse(_full_snapshot())

    async def consent_decide(request: Request) -> Response:
        body = await request.json()
        decision = _DECISIONS.get(str(body.get("decision", "")))
        if decision is None:
            return JSONResponse({"error": "bad decision"}, status_code=400)
        req_id = body.get("id")
        if req_id:
            done = consent.resolve_by_id(str(req_id), decision, by="panel")
        else:
            req = consent.pending()
            done = req.resolve(decision, by="panel") if req else False
        if not done:
            # «Решён ИЛИ истёк» — две разные новости. Истёк значит, что робот
            # УЖЕ получил отказ по молчанию, и владельцу надо знать именно
            # это, а не гадать, кто нажал раньше (A-10).
            got = consent.outcome(str(req_id)) if req_id else None
            why = ("запрос истёк — робот получил отказ по молчанию"
                   if got is not None and got.by == "timeout"
                   else "запрос уже решён с другой поверхности")
            return JSONResponse({"error": why, "expired":
                                 bool(got is not None and got.by == "timeout")},
                                status_code=404)
        return JSONResponse({"ok": True})

    async def api_pause(request: Request) -> Response:
        body = await request.json()
        consent.paused = bool(body.get("paused"))
        return JSONResponse({"ok": True, "paused": consent.paused})

    async def api_revoke_grants(request: Request) -> Response:
        consent.revoke_grants()
        return JSONResponse({"ok": True})

    def _cap_map() -> dict[str, dict]:
        return {name: {"class": caps[name].tool_class.value, **info}
                for name, info in availability.items() if name in caps}

    async def api_capabilities(request: Request) -> Response:
        return JSONResponse(_cap_map())

    async def api_capability_grant(request: Request) -> Response:
        """Кнопка «Выдать права» — то, что SCN-020 обещал и чего не было:
        путь от «требует прав» к правам, не выходя из панели."""
        name = request.path_params["name"]
        if name not in caps:
            return JSONResponse({"error": "нет такой способности"},
                                status_code=404)
        from .capabilities import request_permission
        ok, why = await asyncio.to_thread(request_permission, name)
        # Права могли появиться прямо сейчас — не заставляем ждать TTL.
        if hasattr(availability, "refresh"):
            await asyncio.to_thread(availability.refresh)
        audit.record(tool=name, tool_class=caps[name].tool_class.value,
                     decision="allow" if ok else "unavailable", ok=ok,
                     line=f"запрошены права для «{name}»: {why}")
        return JSONResponse({"ok": ok, "why": why,
                             "capabilities": _cap_map()})

    async def api_phone_serve(request: Request) -> Response:
        """Включить HTTPS для телефона — кнопкой, а не командой в терминал.

        Визия §4: владелец «не настраивает туннели и не читает конфиги», а
        панель выдавала ему `tailscale serve --bg` копипастой (A-28).
        """
        from .net import serve_enable
        ok, why = await asyncio.to_thread(serve_enable, settings.port)
        audit.record(tool="phone", tool_class="SYS",
                     decision="allow" if ok else "error", ok=ok,
                     line=f"HTTPS для телефона: {why}")
        return JSONResponse({"ok": ok, "why": why}, status_code=200 if ok
                            else 502)

    async def api_robot_status(request: Request) -> Response:
        st = await robot.status()
        robot_state.clear()
        robot_state.update(st)
        return JSONResponse(st)

    async def api_robot_chat(request: Request) -> Response:
        body = await request.json()
        text = str(body.get("text", "")).strip()
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        # A session id lets "новый диалог" mean something: the brain keeps the
        # context, so a new id is how the owner starts a fresh one. The panel
        # and the pet each pass their own.
        session = str(body.get("session") or "panel")[:64]
        # Один ход на сессию за раз. Ход мозга не идемпотентен: пока он идёт,
        # он мог уже опубликовать пост, снять кадр со вспышкой и открыть
        # вкладку. Второй клик по «Отправить» (или совет «повторите позже»,
        # понятый буквально) исполнил бы это второй раз — A-6.
        if session in chat_inflight:
            return JSONResponse(
                {"ok": False, "undelivered": True,
                 "error": "ход уже идёт — дождитесь ответа"}, status_code=409)
        chat_inflight.add(session)
        thread = chat_history.setdefault(session, deque(maxlen=20))
        mascot.thinking(True)
        try:
            answer = await robot.chat(text, session=session,
                                      history=list(thread))
        finally:
            mascot.thinking(False)
            chat_inflight.discard(session)
        # The brain's own reply, spoken by the face. Nothing is composed here.
        # The key is `reply` — `RobotClient.chat` has always returned that, and
        # reading `text` here meant the mascot silently never spoke a single
        # chat answer (caught live 2026-08-31: it went to "thinking" and then
        # said nothing, while the chat itself was working).
        if answer.get("ok") and answer.get("reply"):
            thread.append({"role": "user", "content": text})
            thread.append({"role": "assistant",
                           "content": str(answer["reply"])})
            mascot.say(str(answer["reply"]), kind="chat")
        return JSONResponse(answer)

    async def api_robot_media(request: Request) -> Response:
        """Отдать странице файл робота. Имя берётся из события; проверяется
        и здесь, и у робота — обход каталога не должен зависеть от того,
        насколько две стороны доверяют друг другу."""
        name = request.path_params["name"]
        got = await robot.media(name)
        if not got.get("ok"):
            # Четыре разные беды больше не называются одним словом: у каждой
            # свой следующий шаг для владельца (A-20). Наружу уходит причина,
            # а не устройство моста — путей и внутренностей здесь нет.
            status = {"bad-name": 400, "not-found": 404,
                      "too-large": 413, "unauthorized": 502,
                      "unconfigured": 409}.get(got.get("kind"), 502)
            return JSONResponse({"error": got.get("error", "файл недоступен"),
                                 "kind": got.get("kind", "unreachable")},
                                status_code=status)
        return Response(got["body"], media_type=got["type"],
                        headers={"Cache-Control": "private, max-age=3600"})

    async def api_robot_system(request: Request) -> Response:
        """Состояние системы робота — то, ради чего панель перестаёт быть
        хуже телеграм-бота: температура, нагрузка, память, диск, воздух и
        живость сервисов, из его же канонического снимка."""
        return JSONResponse(await robot.system())

    async def api_robot_update(request: Request) -> Response:
        return JSONResponse(await robot.trigger_update())

    async def api_push_vapid(request: Request) -> Response:
        await asyncio.to_thread(ensure_vapid_keys, state)
        return JSONResponse({"key": state.vapid_public})

    async def api_push_subscribe(request: Request) -> Response:
        body = await request.json()
        sub = body.get("subscription")
        if not isinstance(sub, dict):
            return JSONResponse({"error": "bad subscription"}, status_code=400)
        try:
            push_sender.add_subscription(sub)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True,
                             "count": len(state.push_subscriptions)})

    async def api_push_unsubscribe(request: Request) -> Response:
        body = await request.json()
        removed = push_sender.remove_subscription(str(body.get("endpoint", "")))
        return JSONResponse({"ok": removed})

    async def api_phone(request: Request) -> Response:
        """Everything the settings card needs to guide phone setup: the
        MagicDNS name, whether `tailscale serve` already fronts the panel,
        and the exact command when it does not. The bridge never runs the
        command itself — changing serve config is the owner's move."""
        from .net import serve_active, tailnet_dns_name
        dns = await asyncio.to_thread(tailnet_dns_name)
        active = (await asyncio.to_thread(serve_active, settings.port)
                  if dns else False)
        https_url = f"https://{dns}/" if dns and active else None
        return JSONResponse({
            "dns_name": dns,
            "serve_active": active,
            "https_url": https_url,
            # The tokened link IS the phone onboarding: the owner sends it
            # to their own phone (AirDrop/Notes). Served only behind panel
            # auth — whoever sees this already holds the token.
            "phone_link": (f"{https_url}?token={state.panel_token}"
                           if https_url else None),
            "setup_command": f"tailscale serve --bg {settings.port}",
            "subscriptions": len(state.push_subscriptions),
        })

    async def pair(request: Request) -> Response:
        """The robot's door (spec §3). Auth = the one-shot pairing token —
        NOT the panel cookie: the caller is the robot's provision script.
        Burned on first success; a replay gets 403."""
        import secrets as _secrets
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "bad json"}, status_code=400)
        verdict = pairing_verdict(
            offered=str(body.get("token", "")),
            expected=state.pending_pair_token,
            issued_at=state.pending_pair_token_at,
            ttl_s=float(settings.pairing_ttl_hours) * 3600)
        if verdict is not PairVerdict.OK:
            if verdict is PairVerdict.EXPIRED:
                # Истёкший токен гасится: он уже не ключ, и держать его —
                # значит держать на карте то, что ничего не открывает.
                state.pending_pair_token = None
                state.pending_pair_token_at = None
                state.save()
            audit.record(tool="pair", tool_class="act", decision="deny",
                         ok=False, line=verdict.journal)
            return JSONResponse({"error": verdict.spoken}, status_code=403)
        state.pending_pair_token = None            # one-shot: burned now
        state.pending_pair_token_at = None
        # СВЕЖИЙ ключ на каждый пейринг. Раньше стояло `or`, и робот-замена
        # наследовал кредитив предшественника — а пути ротации не было вовсе.
        # Перепейринг И ЕСТЬ ротация: робот получает новый ключ в этом ответе.
        state.robot_token = _secrets.token_urlsafe(32)
        name = str(body.get("name", "")).strip() or "робот"
        state.robot_name = name
        if body.get("base_url"):
            state.robot_base_url = str(body["base_url"])
        if body.get("chat_url"):
            state.robot_chat_url = str(body["chat_url"])
        if body.get("chat_key"):
            state.robot_chat_key = str(body["chat_key"])
        else:
            # bridge_api робота авторизует оба канала одним robot_token —
            # он и есть чат-ключ, если робот не назвал отдельный.
            state.robot_chat_key = state.robot_token
        state.save()
        robot.configure(base_url=state.robot_base_url,
                        chat_url=state.robot_chat_url,
                        chat_key=state.robot_chat_key, name=name)
        robot_state.update({"configured": robot.configured})
        # «Связан ✓» без адреса — ложь на весь остаток жизни установки:
        # панель рядом скажет «не подключён», и владелец будет искать причину
        # в сети. Токен принят — но так и говорим (A-5).
        if robot.configured:
            line = f"робот «{name}» связан с мостом"
            toast = f"Робот «{name}» связан с мостом ✓"
        else:
            line = (f"робот «{name}» предъявил токен, но не назвал свой "
                    f"адрес — панель покажет «не подключён»")
            toast = (f"Робот «{name}» принят, но адреса не назвал — "
                     f"подключения не будет")
        audit.record(tool="pair", tool_class="act", decision="allow", ok=True,
                     line=line)
        notify("vibe-bridge", toast)
        from .net import serve_active, tailnet_dns_name
        dns = await asyncio.to_thread(tailnet_dns_name)
        https_ok = (await asyncio.to_thread(serve_active, settings.port)
                    if dns else False)
        return JSONResponse({"robot_token": state.robot_token,
                             "mcp_url": mcp_url(
                                 dns=dns, https_ok=https_ok,
                                 port=settings.port)})

    async def api_wizard_pairing_start(request: Request) -> Response:
        """Arm pairing: mint the one-shot token and hand the wizard the
        payload it writes to the SD (or shows as a code path later)."""
        from . import wizard as wiz
        from .net import serve_active, tailnet_dns_name
        token = wiz.pairing_token()
        state.pending_pair_token = token
        state.pending_pair_token_at = time.time()
        state.save()
        dns = await asyncio.to_thread(tailnet_dns_name)
        https_ok = (await asyncio.to_thread(serve_active, settings.port)
                    if dns else False)
        bridge_url = bridge_base_url(dns=dns, https_ok=https_ok,
                                     port=settings.port)
        return JSONResponse({"token": token, "bridge_url": bridge_url})

    async def api_wizard_disks(request: Request) -> Response:
        from . import wizard as wiz
        return JSONResponse({
            "disks": await asyncio.to_thread(wiz.list_removable_disks),
            "boot_volumes": await asyncio.to_thread(wiz.find_boot_volumes)})

    async def api_wizard_prepare(request: Request) -> Response:
        """Prepare an ALREADY-FLASHED boot partition (stock Raspberry Pi OS)
        mounted at `mount_path`: firstrun + Wi-Fi + provision unit + token.
        Full image download+write is WIZARD-b (needs elevation UX)."""
        from . import wizard as wiz
        body = await request.json()
        mount = Path(str(body.get("mount_path", "")))
        if not mount.is_dir() or not (mount / "cmdline.txt").exists():
            return JSONResponse(
                {"error": "это не boot-раздел Raspberry Pi OS — не вижу "
                          "cmdline.txt"}, status_code=400)
        for fld in ("ssid", "psk", "name"):
            if not str(body.get(fld, "")).strip():
                return JSONResponse({"error": f"нужно поле {fld}"},
                                    status_code=400)
        start = await api_wizard_pairing_start(request)
        info = json.loads(bytes(start.body))
        hostname = str(body.get("hostname") or "robot-" +
                       str(body["name"]).lower())[:32]
        try:
            written = await asyncio.to_thread(
                wiz.prepare_boot_partition, mount,
                hostname=hostname, ssid=str(body["ssid"]),
                psk=str(body["psk"]), token=info["token"],
                bridge_url=info["bridge_url"], name=str(body["name"]),
                # The panel never passed this, so the wizard always cloned
                # the hardcoded default — a fork's robot got someone else's
                # repository written onto its card.
                repo_url=settings.robot_repo)
        except OSError as exc:
            return JSONResponse(
                {"error": f"не удалось записать на карту: {exc}"},
                status_code=500)
        return JSONResponse({"ok": True, "written": written,
                             "bridge_url": info["bridge_url"]})

    async def api_version(request: Request) -> Response:
        """What code is running, where it came from, what is waiting.

        `pending` is the honest half: an installed version is NOT the running
        one until the next launch (ADR-0006), and a panel that showed only
        the newest number on disk would report an update the robot is not
        actually talking to.
        """
        from vbboot import layout as _layout

        from . import __version__
        from .autostart import status as autostart_status

        root = _layout.payload_root()
        installed = await asyncio.to_thread(_layout.active_version, root)
        pending = pending_version(installed, __version__)
        auto = await asyncio.to_thread(autostart_status)
        return JSONResponse({
            "running": __version__,
            "source": _payload_source(root, __version__),
            # Догадка не имеет права выглядеть как ответ (B-45).
            "source_note": source_note(),
            "pending": pending,
            "pending_note": ("обновление скачано и применится после "
                             "перезапуска моста" if pending else ""),
            # Настройка, а не константа: владелец форка меняет
            # `release.repo`, и две карточки панели показывали
            # два разных репозитория.
            "repo": settings.release_repo,
            "auto_update": bool(getattr(state, "auto_update", True)),
            "autostart": {"state": auto.state, "human": auto.human,
                          "supported": auto.supported, "detail": auto.detail},
        })

    async def api_update_check(request: Request) -> Response:
        """Ask the channel, and take what it offers. Every outcome — nothing
        newer, unreachable, refused signature, installed — leaves the bridge
        running and, when it matters, a line in the journal."""
        from vbboot import layout as _layout

        from . import __version__, update

        result = await asyncio.to_thread(update.check, current=__version__)
        if result.found is None:
            # Two different answers, and the owner is told which one it is.
            message = (result.error or
                       "обновлений нет — установлена последняя версия")
            if result.error:
                audit.record(tool="update", tool_class="SYS",
                             decision="unavailable", ok=False,
                             line=f"проверка обновлений: {result.error}",
                             detail=result.error)
            return JSONResponse({"found": False, "installed": False,
                                 "reachable": not result.error,
                                 "message": message})
        found = result.found

        from vbboot.runner import shell_version
        shell = shell_version()
        if shell is None:
            return JSONResponse({
                "found": True, "installed": False, "version": found.version,
                "message": ("мост запущен не из установленного приложения — "
                            "обновляться нечему"),
            })
        ok, why = await asyncio.to_thread(
            update.fetch_and_install, found, _layout.payload_root(),
            pubkey=update.bundled_public_key(_bundle_resources()),
            shell_version=shell)
        audit.record(tool="update", tool_class="SYS",
                     decision="auto" if ok else "unavailable", ok=ok,
                     line=(f"обновление {found.version}: {why}"), detail=why)
        return JSONResponse({"found": True, "installed": ok,
                             "version": found.version, "message": why})

    async def api_robot_attach(request: Request) -> Response:
        """Attach a robot that already exists.

        The SD-card wizard covers a NEW Raspberry Pi; a person whose robot is
        already running had no path at all — the panel's only door was
        "прошейте карту". This is the other door SCN-017 promised.
        """
        body = await request.json() if await request.body() else {}
        wanted, why = attach_request(body)
        if wanted is None:
            return JSONResponse({"error": why}, status_code=400)

        key = wanted["key"]
        state.robot_base_url = wanted["base_url"]
        state.robot_chat_url = wanted["chat_url"] or state.robot_chat_url
        # standalone gates /mcp on this token, so it must exist the moment a
        # robot is attached — not later, when someone remembers.
        import secrets as _secrets
        state.robot_token = state.robot_token or _secrets.token_urlsafe(32)
        state.robot_chat_key = key or state.robot_token
        name = wanted["name"]
        state.robot_name = name
        await asyncio.to_thread(state.save)

        robot.configure(base_url=state.robot_base_url,
                        chat_url=state.robot_chat_url,
                        chat_key=state.robot_chat_key, name=name)
        robot_state.update({"configured": robot.configured})

        # Настройка сохранена — владелец её и просил. Но «привязан ✓» без
        # единого обращения к роботу было обещанием, которое мост не мог
        # выполнить: валидировался только префикс адреса, а пустой ключ
        # подставлял свежий `robot_token`, которого робот никогда не видел.
        # Три разные беды выглядели одинаково; теперь каждая называется (A-12).
        got = await robot.probe()
        reached = bool(got.get("ok"))
        robot_state.update({"configured": robot.configured, "online": reached})
        line, toast = attach_words(name, got)
        audit.record(tool="pair", tool_class="act", decision="allow", ok=True,
                     line=line)
        notify("vibe-bridge", toast)
        return JSONResponse({
            "ok": True, "name": name,
            # Дозвонились или нет — отдельным полем: форма показывает РАЗНОЕ.
            "reached": reached,
            "probe": {"kind": got.get("kind", "ok"),
                      "error": got.get("error", "")},
            # What the owner must copy into the robot's own configuration.
            "robot_token": state.robot_token,
            "bridge_url": f"http://127.0.0.1:{settings.port}",
        })

    async def api_mascot(request: Request) -> Response:
        """What the character shows right now, for both surfaces."""
        # The skin travels with the state so a surface never has to ask twice.
        return JSONResponse({**mascot.snapshot(),
                             "skin": settings.mascot_skin})

    async def api_mascot_session(request: Request) -> Response:
        """The pet's conversation id — read it, or mint a new one.

        Server-side because the feed is: a page-local id was regenerated on
        every reload, so the owner kept their visible history while the robot
        started over. One of them had to move, and the id is the cheap one.
        """
        import secrets as _s
        if request.method == "POST" or not state.pet_session:
            state.pet_session = "pet-" + _s.token_hex(5)
            await asyncio.to_thread(state.save)
            if request.method == "POST":
                # Новый РАЗГОВОР — это чистый контекст мозга, а не амнезия.
                # Раньше здесь стиралась лента с объяснением «старые ходы
                # остаются в журнале»: журнал — это аудит решений по
                # инструментам, событий робота в нём нет вовсе, и сказанное
                # им исчезало насовсем (A-19). Вместо стирания — граница,
                # чтобы владелец видел, где начался новый разговор.
                robot_events.add({"ts": _now_iso(), "kind": "session",
                                  "text": "— новый разговор —"})
                chat_history.clear()
        return JSONResponse({"session": state.pet_session})

    async def api_mascot_stream(request: Request) -> Response:
        """Everything the robot has said or shown lately, newest last.

        One stream: replies, events and notifications are all the robot
        communicating with its owner, and splitting them across surfaces is
        what made the widget feel like three half-features.
        """
        return JSONResponse({"items": robot_events.tail(40)})

    async def api_mascot_dismiss(request: Request) -> Response:
        """The owner clicked the bubble away. Better than waiting out a timer
        they did not set."""
        mascot.dismiss()
        return JSONResponse(mascot.snapshot())

    async def mascot_page(request: Request) -> Response:
        """The floating window's page. Same auth as the panel: it answers
        consent requests, so it is the panel by another name."""
        token = request.query_params.get("token")
        if token is not None and token == state.panel_token:
            # Carry every OTHER parameter across the redirect. Dropping them
            # cost the widget its `surface` marker: both of its windows loaded
            # as the pet, so the companion page never existed, and a click on
            # the head reached a document with nothing to open. Nothing failed
            # loudly — the click simply did nothing (measured 2026-08-31).
            rest = [(k, v) for k, v in request.query_params.multi_items()
                    if k != "token"]
            target = "/mascot" + (f"?{urlencode(rest)}" if rest else "")
            resp = RedirectResponse(target, status_code=303)
            _set_panel_cookie(resp, request, state.panel_token)
            return resp
        # ДВЕРЬ, а не обычный маршрут: `PanelGuard` её пропускает, потому что
        # она сама обменивает `?token=` на куку. Значит проверять права —
        # здесь, и снять эту проверку вместе с остальными 36 было бы отдать
        # страницу питомца кому угодно (поймано тестом при F-3).
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return _code_file(_WEBUI / "mascot.html")

    async def api_mascot_actions(request: Request) -> Response:
        """The quick phrases for the pet's menu."""
        from .config import load
        live = await asyncio.to_thread(load)
        return JSONResponse({"actions": list(live.mascot_actions)})

    async def api_onboarding(request: Request) -> Response:
        """What is still missing, as an ordered list the panel can render."""
        attached = bool(state.robot_base_url)
        steps = [
            {"id": "robot", "title": "Подключить робота",
             "done": attached,
             "hint": ("робот на связи" if attached else
                      "новая Raspberry Pi — через карту; уже работающий "
                      "робот — вручную, по адресу и ключу")},
            {"id": "phone", "title": "Открыть панель на телефоне",
             "done": bool(state.push_subscriptions),
             "hint": "нужен Tailscale и включённый serve — см. «Телефон»"},
        ]
        return JSONResponse({"robot_attached": attached, "steps": steps,
                             "done": all(s["done"] for s in steps)})

    async def api_settings(request: Request) -> Response:
        """The settings in force — never the file's wish.

        Тонкий слой: аутентификация, два похода наружу (файл и шлюз) и
        передача решения в `settings_view`. Само решение — чистая функция
        на модульном уровне, и потому проверяется без HTTP-стека (F-2).
        """
        from .config import load
        from .net import gateway_reachable

        # In force = what this process started with. The file is read only to
        # report its problems and to say whether a restart is owed. Reporting
        # the FILE would mean the panel shows a port the bridge is not
        # listening on the moment someone edits it.
        on_disk = await asyncio.to_thread(load)
        gateway_ok = (await asyncio.to_thread(gateway_reachable)
                      if settings.mode == "gateway" else None)
        return JSONResponse(settings_view(
            live=settings, on_disk=on_disk, bind_host=bind_host,
            has_robot_token=bool(state.robot_token), gateway_ok=gateway_ok))


    async def api_settings_save(request: Request) -> Response:
        """Change a setting from the panel. Values the reader would reject are
        refused here, so the panel never reports a success the bridge will not
        honour."""
        from .config import update as save_settings
        body = await request.json() if await request.body() else {}
        try:
            await asyncio.to_thread(save_settings, dict(body))
        except (ValueError, OSError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=400)
        audit.record(tool="settings", tool_class="SYS", decision="auto",
                     ok=True, line=f"настройки изменены: {', '.join(body)}",
                     detail="")
        return JSONResponse({"ok": True, "restart_required": True})

    async def api_autoupdate(request: Request) -> Response:
        """The owner's switch for background updating (SCN-021)."""
        body = await request.json() if await request.body() else {}
        state.auto_update = bool(body.get("enabled", True))
        await asyncio.to_thread(state.save)
        audit.record(tool="update", tool_class="SYS", decision="auto", ok=True,
                     line=("автообновление включено" if state.auto_update
                           else "автообновление выключено владельцем"),
                     detail="")
        return JSONResponse({"auto_update": state.auto_update})

    async def api_autostart_settings(request: Request) -> Response:
        """Открыть системную панель «Объекты входа».

        Переключатель системы старше нашего: владелец может выключить
        автозапуск там, и мост об этом не спросят. Функция была написана и
        протестирована, но её никто не звал — класс «написано, не вызвано»
        (A-37); теперь у неё есть кнопка.
        """
        from .autostart import open_settings
        ok = await asyncio.to_thread(open_settings)
        return JSONResponse(
            {"ok": ok,
             "why": "" if ok else "системная панель недоступна на этой ОС"},
            status_code=200 if ok else 501)

    async def api_autostart(request: Request) -> Response:
        """Turn launch-at-login on or off from the panel. The system switch
        in Login Items stays authoritative — this only asks."""
        from .autostart import disable, enable
        from .autostart import status as autostart_status

        body = await request.json() if await request.body() else {}
        want = bool(body.get("enabled", True))
        ok, why = await asyncio.to_thread(enable if want else disable)
        if ok and want:
            state.autostart_registered = True
            await asyncio.to_thread(state.save)
        audit.record(tool="autostart", tool_class="SYS",
                     decision="auto" if ok else "unavailable", ok=ok,
                     line=(f"автозапуск {'включён' if want else 'выключен'} "
                           f"из панели: {why}"), detail=why)
        auto = await asyncio.to_thread(autostart_status)
        return JSONResponse({"ok": ok, "message": why, "state": auto.state,
                             "human": auto.human,
                             "supported": auto.supported})

    async def api_journal(request: Request) -> Response:
        q = request.query_params
        try:
            offset = max(0, int(q.get("offset", 0)))
            limit = min(200, max(1, int(q.get("limit", 50))))
        except ValueError:
            return JSONResponse({"error": "bad paging"}, status_code=400)
        flt = q.get("filter", "all")
        if flt not in ("all", "refused", "act", "read"):
            return JSONResponse({"error": "bad filter"}, status_code=400)
        return JSONResponse(audit.read_entries(flt=flt, offset=offset,
                                               limit=limit))

    async def events(request: Request) -> Response:

        async def stream():
            q = bus.subscribe()
            try:
                while True:
                    snap = await q.get()
                    yield f"event: state\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    async def _consent_push_watcher() -> None:
        """Every NEW pending consent goes out as a web push (SCN-004). The
        blocking pywebpush call runs in a worker thread; a lost push costs
        nothing — the timeout default stands."""
        seen: set[str] = set()
        while True:
            reqs = consent.pending_all()
            for req in reqs:
                if req.id in seen:
                    continue
                seen.add(req.id)
                if state.push_subscriptions and not consent.paused:
                    await asyncio.to_thread(push_sender.send_to_all, {
                        "kind": "consent", "id": req.id,
                        "title": "Робот просит разрешение",
                        "summary": req.summary,
                    })
            if len(seen) > 500:
                live = {r.id for r in reqs}
                seen.intersection_update(live)
            await asyncio.sleep(0.5)

    async def _robot_poller() -> None:
        """Refresh the cached robot status; announce the pause-summary on
        the paused→active transition (SCN-010 alt)."""
        was_paused = consent.paused
        while True:
            if robot.configured:
                st = await robot.status()
                robot_state.clear()
                robot_state.update(st)
            if was_paused and not consent.paused and missed_while_paused["n"]:
                notify("vibe-bridge",
                       f"За время паузы: {missed_while_paused['n']} событий "
                       f"робота — они в ленте")
                missed_while_paused["n"] = 0
            was_paused = consent.paused
            await asyncio.sleep(10.0)

    async def _robot_event_consumer() -> None:
        """Consume the robot's proactive events; OS-notify unless paused
        (paused events collect silently — SCN-010). Reconnects calmly."""
        while True:
            if not robot.configured:
                await asyncio.sleep(30.0)
                continue
            async for raw in robot.events():
                ev = normalise_robot_event(raw, now_iso=_now_iso())
                robot_events.add(ev)
                if consent.paused:
                    missed_while_paused["n"] += 1
                else:
                    # ТОЛЬКО системный тост. Обёртка `notify` существует для
                    # того, что приходит в обход ленты: она сама добавляет
                    # строку и говорит её питомцу. Здесь событие уже в ленте
                    # СО СВОИМ медиа, поэтому обёртка добавляла вторую копию
                    # без него и заставляла питомца сказать всё дважды (A-13).
                    _base_notify(robot.name, ev["text"] or ev["kind"])
                    # The robot's own words, verbatim — the mascot composes
                    # nothing («Не второй мозг»).
                    mascot.say(ev["text"] or ev["kind"], kind="event")
            await asyncio.sleep(10.0)          # stream ended — quiet retry

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            tasks = [asyncio.create_task(bus.pump()),
                     asyncio.create_task(_robot_poller()),
                     asyncio.create_task(_robot_event_consumer()),
                     asyncio.create_task(_consent_push_watcher())]
            try:
                yield
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    # PWA shell files live at the ORIGIN ROOT (a service worker's scope
    # cannot exceed its path). No auth: they contain no secrets, and the
    # SW must load before any cookie exists on the phone.
    def _static(name: str, media: str):
        async def handler(request: Request) -> Response:
            return _code_file(_WEBUI / name, media)
        return handler

    async def manifest(request: Request) -> Response:
        return Response(
            manifest_body((_WEBUI / "manifest.webmanifest").read_text(
                              encoding="utf-8"),
                          (_WEBUI / "tokens.css").read_text(encoding="utf-8")),
            media_type="application/manifest+json", headers=_NO_STORE)

    async def icon(request: Request) -> Response:
        size = int(request.path_params["size"])
        if size not in (180, 192, 512):
            return JSONResponse({"error": "not found"}, status_code=404)
        # The real mark, generated by scripts/make_icon.py and committed.
        # The solid tile stays as a fallback so a phone that installs the PWA
        # never gets a broken image if the asset is missing from a payload.
        art = _WEBUI / f"icon-{size}.png"
        body = art.read_bytes() if art.is_file() else _solid_png(size)
        return Response(body, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/sw.js", _static("sw.js", "application/javascript")),
            Route("/manifest.webmanifest", manifest),
            Route("/offline.html", _static("offline.html", "text/html")),
            Route("/icon-{size:int}.png", icon),
            Route("/api/state", api_state),
            Route("/api/consent/decide", consent_decide, methods=["POST"]),
            Route("/api/pause", api_pause, methods=["POST"]),
            Route("/api/grants/revoke", api_revoke_grants, methods=["POST"]),
            Route("/api/capabilities/{name}/grant",
                  api_capability_grant, methods=["POST"]),
            Route("/api/capabilities", api_capabilities),
            Route("/api/version", api_version),
            Route("/api/update/check", api_update_check, methods=["POST"]),
            Route("/api/autostart", api_autostart, methods=["POST"]),
            Route("/api/autostart/settings", api_autostart_settings,
                  methods=["POST"]),
            Route("/api/autoupdate", api_autoupdate, methods=["POST"]),
            Route("/api/robot/attach", api_robot_attach, methods=["POST"]),
            Route("/api/onboarding", api_onboarding),
            Route("/api/mascot", api_mascot),
            Route("/api/mascot/actions", api_mascot_actions),
            Route("/api/mascot/dismiss", api_mascot_dismiss,
                  methods=["POST"]),
            Route("/api/mascot/stream", api_mascot_stream),
            Route("/api/mascot/session", api_mascot_session),
            Route("/api/mascot/session", api_mascot_session,
                  methods=["POST"]),
            Route("/mascot", mascot_page),
            Route("/mascot.js", _static("mascot.js", "application/javascript")),
            Route("/tokens.css", _static("tokens.css", "text/css")),
            Route("/api/settings", api_settings),
            Route("/api/settings", api_settings_save, methods=["POST"]),
            Route("/api/journal", api_journal),
            Route("/api/robot/status", api_robot_status),
            Route("/api/robot/chat", api_robot_chat, methods=["POST"]),
            Route("/api/robot/update", api_robot_update, methods=["POST"]),
            Route("/api/robot/system", api_robot_system),
            Route("/api/robot/media/{name:str}", api_robot_media),
            Route("/api/push/vapid", api_push_vapid),
            Route("/api/push/subscribe", api_push_subscribe,
                  methods=["POST"]),
            Route("/api/push/unsubscribe", api_push_unsubscribe,
                  methods=["POST"]),
            Route("/api/phone", api_phone),
            Route("/api/phone/serve", api_phone_serve,
                  methods=["POST"]),
            Route("/pair", pair, methods=["POST"]),
            Route("/api/wizard/pairing/start", api_wizard_pairing_start,
                  methods=["POST"]),
            Route("/api/wizard/disks", api_wizard_disks),
            Route("/api/wizard/prepare", api_wizard_prepare,
                  methods=["POST"]),
            Route("/events", events),
            Mount("/", app=BearerGuard(mcp.streamable_http_app(), state,
                                       settings.mode, _refusals)),
        ],
        lifespan=lifespan,
    )
    # Ключ панели — на входе, одним местом. Дальше внутрь идёт уже
    # аутентифицированный запрос или ничего.
    guarded = PanelGuard(app, is_authed=_authed)
    # Взводится вызывающим — он один знает, какой интерфейс занят.
    return PeerGuard(guarded, peer_guard, _refusals) if peer_guard else guarded
